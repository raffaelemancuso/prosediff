"""Compare two versions of a set of files, file by file, into side-by-side rows.

The sides are commits, the index (staged changes) or the working tree of a
git repository, or two files or two folders. git aligns the lines of every
changed file (histogram algorithm, one git process for all files); within a
block of replaced lines, each old line is paired with its most similar new
line and the two are compared word by word, and letter by letter within a
changed word. A removed line that reappears elsewhere in the file, as it was
or lightly edited, is shown as moved, and so is a passage removed from one
place and added in another, within a line or between two.
"""

import base64
import codecs
import difflib
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from typing import Literal

import cchardet
import git
import psutil
from charset_normalizer import from_bytes
from markupsafe import Markup
from patiencediff import PatienceSequenceMatcher
from rapidfuzz import fuzz
from rapidfuzz.distance import Indel

from prosediff import document, footnotes
from prosediff.comments import (  # noqa: F401  (re-exported)
    COMMENT_MARK,
    PLACEHOLDER,
    CommentEntry,
    Comments,
    fold_comments,
    placeholders_in,
    plain,
    show_comments,
)
from prosediff.document import (
    CommentMark,
    Document,
    Line,
    comment_markdown,
    short_date,
    sub,
)
from prosediff.language import (
    DEFAULT,
    DOCUMENT,
    normalize_language,
    resolve_language,
)
from prosediff.mdstyle import md_styles, styled
from prosediff.sentences import split_sentences
from prosediff.sources import (
    DOCUMENT_SUFFIXES,
    FOLDER_FILES,
    SourceError,
    describe_side,
    is_document,
    read_document,
    read_side,
)

# The styles of a document's line the text formats write: its formatting,
# and its tracked changes; and a heading's style (h1 ... h6), its level.
TEXT_MARKS = frozenset(document.FORMATTING) | {"tc-ins", "tc-del"}
HEADING = re.compile(r"h([1-6])")

# Windows opens a console for a console program (git, cmd.exe) started from
# a process without one, such as the window of prosediff-gui: a terminal
# that flashes up while it runs. This flag starts it without.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# How long git may take to line up every file of a comparison, and a
# filter to rewrite one (seconds): far beyond what either takes on the
# longest manuscript, so that a hung process fails instead of waiting forever.
GIT_TIMEOUT = 300
FILTER_TIMEOUT = 300
# How many leading bytes are inspected to decide whether a file is binary,
# as git itself does.
BINARY_SNIFF = 8000
# The legacy encoding a file is read in when others fit it as well.
WESTERN = "cp1252"
# The encoding option that guesses it; C1 controls betray a text misread.
AUTO_ENCODING = "auto"
C1_CONTROLS = re.compile("[\x80-\x9f]")
BOMS = (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
# Changed words, spaces and punctuation are compared as separate tokens, so a
# changed word is highlighted alone and not the whole run it sits in.
TOKEN = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)
WORD = re.compile(r"\w+", re.UNICODE)
# Two lines of a replaced block are paired when at least half of their words
# and punctuation match, in order. The pairing scores every old line of the
# block against every new one; beyond this many pairs, lines are paired in
# order instead.
PAIRING_THRESHOLD = 0.5
PAIRING_MAX_CELLS = 40_000
# Lines left between the pairs are paired in order when they have at least
# this much in common; below it, two unrelated lines of a block are shown as
# one removed and one added rather than face to face (a single line replaced
# by a single line is always a pair: a rewrite in place).
PAIRING_FLOOR = 0.25
# A changed word is highlighted letter by letter when at least half of its
# letters survive ("repeat" -> "repeated"); otherwise as a whole.
CHAR_THRESHOLD = 0.5
# A removed line that reappears elsewhere in the file counts as moved when it
# holds at least this many non-space characters (shorter lines, "}" or
# "---", recur by chance) and is at least this similar to where it reappears
# (1 = identical, spacing aside), by MOVE_ALGORITHM: the pair that did best
# on simulated revisions, for precision, recall and speed
# (docs/move_sensitivity.py). The fuzzy matching scores every remaining
# removed line against every remaining added one, up to this many pairs.
MIN_MOVE_CHARS = 20
MOVE_SIMILARITY = 0.7
MOVE_MAX_CELLS = 250_000
# Unchanged lines beyond the context are embedded so the HTML report can reveal
# them; a longer run than this is left out, to keep the HTML report light.
MAX_HIDDEN = 500
# Unchanged lines shown around each change when the context is "auto": of
# code, as git diff does, and of prose (Markdown and Word documents), where a
# line is a whole paragraph.
CONTEXT = 3
PROSE_CONTEXT = 0
# A number of lines for every file, "auto", or None for every line.
Context = int | Literal["auto"] | None


def context_for(context: Context, prose: bool) -> int | None:
    """The unchanged lines to show around each change of one file."""
    if context == "auto":
        return PROSE_CONTEXT if prose else CONTEXT
    return context


# Images up to this size are embedded in the HTML report, old and new side by side.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_LISTED_COMMITS = 50

Opcode = tuple[str, int, int, int, int]
Styles = list[set[str]] | None


@dataclass
class Revision:
    """One side of the comparison."""

    hexsha: str
    short: str
    subject: str
    author: str
    date: str


WORKTREE = Revision(
    hexsha="the files as they stand on disk, uncommitted",
    short="working tree",
    subject="uncommitted changes",
    author="",
    date="",
)
INDEX = Revision(
    hexsha="the staged changes, as the next commit would record them",
    short="index",
    subject="staged changes",
    author="",
    date="",
)


@dataclass
class Row:
    """One line of the side-by-side table.

    kind is "equal", "delete", "insert", "replace", "moved-out" (a removed
    line that reappears elsewhere), "moved-in" (where it reappears), or
    "skip" for a run of unchanged lines left out between two changes;
    hidden holds that run, so the HTML report can reveal it, or omitted counts it
    when it is too long to embed.
    """

    kind: str
    left_no: int | None = None
    left: Markup = field(default_factory=Markup)
    right_no: int | None = None
    right: Markup = field(default_factory=Markup)
    hidden: list["Row"] = field(default_factory=list)
    omitted: int = 0
    # What changed in the line, in plain English (the row's tooltip).
    changes: list[str] = field(default_factory=list)
    words_added: int = 0
    words_removed: int = 0
    # The first row of a run of changed rows: one stop of the HTML report's
    # next/previous change navigation.
    first_of_change: bool = False
    # The line as text, before markup (to recognise moved lines).
    text: str = ""
    # id of the row in the HTML report, when something links to it.
    anchor: str = ""
    # What the gutter shows for each side: the line number, or with
    # sentence-by-sentence comparison the line and the sentence ("12.3").
    left_label: str = ""
    right_label: str = ""
    # The language a Word or OpenDocument file marks each side's paragraph
    # with, if any.
    left_lang: str = ""
    right_lang: str = ""
    # Its formatting changes, in plain English: text of a document made bold,
    # underlined, ... (the HTML report shows them on demand).
    format_changes: list[str] = field(default_factory=list)
    # The same number on the two rows of a moved line (0: not moved), for the
    # HTML report to draw a line between them.
    move_pair: int = 0
    # The passages of the line moved from or to another line (or elsewhere
    # in this one), on each side: mark_moves.
    old_moves: list["MovedSpan"] = field(default_factory=list)
    new_moves: list["MovedSpan"] = field(default_factory=list)
    # What those moves are, in plain English, and the words edited on the way.
    move_changes: list[str] = field(default_factory=list)
    passage_words_removed: int = 0
    passage_words_added: int = 0
    # How the row looked before its passages were shown as moved (None: it
    # has none): the HTML report shows it when moved passages are hidden,
    # each passage then removed or added in its own place.
    without_passages: "RowView | None" = None

    @property
    def only_moved(self) -> bool:
        """A line removed or added whose words all moved, as passages."""
        return (self.kind == "delete" and bool(self.old_moves) and not self.words_removed) or (
            self.kind == "insert" and bool(self.new_moves) and not self.words_added
        )

    @property
    def skipped(self) -> int:
        return len(self.hidden) + self.omitted

    @property
    def changed(self) -> bool:
        return self.kind not in ("equal", "skip")


def all_rows(rows: list[Row]) -> list[Row]:
    """The rows and, after each skip row, the unchanged rows it hides."""
    return [row for r in rows for row in (r, *r.hidden)]


@dataclass
class RowView:
    """A row's HTML, changes and word counts as they were before its moved
    passages were set apart (Row.without_passages)."""

    left: Markup
    right: Markup
    changes: list[str]
    words_added: int
    words_removed: int


@dataclass
class Counts:
    """The counts of a file or a comparison as the HTML report shows them
    with moved passages hidden: each passage removed and added in place."""

    additions: int = 0
    deletions: int = 0
    words_added: int = 0
    words_removed: int = 0
    moved: int = 0
    moved_passages: int = 0
    changed_lines: int = 0
    inserted_lines: int = 0
    deleted_lines: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        pairs = zip(vars(self).values(), vars(other).values(), strict=True)
        return Counts(*(a + b for a, b in pairs))


@dataclass
class FileDiff:
    """The comparison of one file."""

    change: str  # added, deleted, modified, renamed, type changed, untracked
    old_path: str | None
    new_path: str | None
    binary: bool = False
    # Lines added and removed; moved lines count in neither.
    additions: int = 0
    deletions: int = 0
    rows: list[Row] = field(default_factory=list)
    # data: URIs of the two versions of an image, when small enough.
    old_image: str | None = None
    new_image: str | None = None
    # How the file was read, when not as text (e.g. from Word).
    note: str = ""
    markdown: bool = False
    # The language of its prose (a BCP 47 tag, e.g. "it"), when known, and
    # where it came from: "given", "document" (marked in the Word or
    # OpenDocument file) or "guessed" (from the text).
    language: str = ""
    language_source: str = ""
    # Whether some of its paragraphs are marked with another language than
    # the file's: the HTML report then shows each paragraph's.
    mixed_languages: bool = False
    # The line pairing every format shows (line_pairs): (old, new) line
    # indexes, 0-based, None on the side a line is missing from; and the
    # lines as text (diff_line), for the formats written as text.
    pairs: list[tuple[int | None, int | None]] = field(default_factory=list)
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    # The comments folded out of those lines, behind their placeholders.
    comments: Comments | None = None
    # Set apart the ids of its rows, when a report holds two comparisons.
    anchor_prefix: str = ""

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def words_added(self) -> int:
        return sum(r.words_added for r in self.rows)

    @property
    def words_removed(self) -> int:
        return sum(r.words_removed for r in self.rows)

    @property
    def moved(self) -> int:
        return sum(r.kind == "moved-in" for r in self.rows)

    @property
    def moved_passages(self) -> int:
        """Passages moved within or between lines (mark_moves)."""
        return sum(len(r.new_moves) for r in self.rows)

    @property
    def changed_lines(self) -> int:
        """Lines (paragraphs, sentences) edited in place."""
        return sum(r.kind == "replace" for r in self.rows)

    @property
    def inserted_lines(self) -> int:
        return sum(r.kind == "insert" and not r.only_moved for r in self.rows)

    @property
    def deleted_lines(self) -> int:
        return sum(r.kind == "delete" and not r.only_moved for r in self.rows)

    @property
    def change_count(self) -> int:
        return sum(r.first_of_change for r in self.rows)

    @property
    def formatted_rows(self) -> int:
        """How many lines' formatting changed."""
        return sum(bool(row.format_changes) for row in all_rows(self.rows))

    @property
    def without_passages(self) -> Counts:
        """Its counts with moved passages hidden: a line whose words all moved
        counts as removed or added again, and the passages' words too."""
        plain = [r.without_passages or r for r in self.rows]
        return Counts(
            additions=self.additions + sum(r.kind == "insert" and r.only_moved for r in self.rows),
            deletions=self.deletions + sum(r.kind == "delete" and r.only_moved for r in self.rows),
            words_added=sum(p.words_added for p in plain),
            words_removed=sum(p.words_removed for p in plain),
            moved=self.moved,
            moved_passages=0,
            changed_lines=self.changed_lines,
            inserted_lines=sum(r.kind == "insert" for r in self.rows),
            deleted_lines=sum(r.kind == "delete" for r in self.rows),
        )

    @property
    def anchor(self) -> str:
        return "file-" + self.anchor_prefix + re.sub(r"[^A-Za-z0-9_-]", "-", self.path)


@dataclass
class Comparison:
    repo_name: str
    base: Revision
    target: Revision
    files: list[FileDiff]
    # Commits reachable from the target (HEAD for the index and the working
    # tree) and not from the base, newest first, at most MAX_LISTED_COMMITS.
    commits: list[Revision] = field(default_factory=list)
    commits_total: int = 0
    # Folded comments, for the comments panel.
    comments: list[CommentEntry] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    @property
    def words_added(self) -> int:
        return sum(f.words_added for f in self.files)

    @property
    def words_removed(self) -> int:
        return sum(f.words_removed for f in self.files)

    @property
    def moved(self) -> int:
        return sum(f.moved for f in self.files)

    @property
    def moved_passages(self) -> int:
        return sum(f.moved_passages for f in self.files)

    @property
    def changed_lines(self) -> int:
        return sum(f.changed_lines for f in self.files)

    @property
    def inserted_lines(self) -> int:
        return sum(f.inserted_lines for f in self.files)

    @property
    def deleted_lines(self) -> int:
        return sum(f.deleted_lines for f in self.files)

    @property
    def change_count(self) -> int:
        return sum(f.change_count for f in self.files)

    @property
    def without_passages(self) -> Counts:
        """Its counts with moved passages hidden (FileDiff.without_passages)."""
        return sum((f.without_passages for f in self.files), Counts())

    def comments_with(self, status: str) -> list[CommentEntry]:
        return [c for c in self.comments if c.status == status]


CHANGE_NAMES = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type changed",
    "C": "copied",
}


class FilterError(RuntimeError):
    """The Markdown filter command failed."""


def revision(commit: git.Commit) -> Revision:
    summary = commit.summary
    return Revision(
        hexsha=commit.hexsha,
        short=commit.hexsha[:7],
        subject=summary if isinstance(summary, str) else summary.decode(),
        author=commit.author.name or "",
        date=commit.committed_datetime.strftime("%Y-%m-%d %H:%M"),
    )


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_SNIFF]


def blob_bytes(blob: git.Blob | None) -> bytes:
    return b"" if blob is None else blob.data_stream.read()


def check_encoding(encoding: str) -> str:
    """The encoding option in a canonical form: "auto", or a codec's name;
    ValueError when Python knows no such codec."""
    encoding = (encoding or AUTO_ENCODING).strip().lower()
    if encoding == AUTO_ENCODING:
        return encoding
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        raise ValueError(
            f"unknown encoding: {encoding!r} (e.g. utf-8, cp1252, latin-1, or auto)"
        ) from None


def decode_text(data: bytes, encoding: str = AUTO_ENCODING) -> tuple[str, str]:
    """The text of a file, and the encoding it was read in ("" for UTF-8).

    Given an encoding, the file is read in it, a byte it cannot read standing
    in as U+FFFD. With "auto", UTF-8 is assumed, unless the file cannot be
    read in it, or reads with C1 control characters (U+0080 to U+009F),
    which text never holds. Such a file is read in the encoding cchardet
    (uchardet, Mozilla's detector) finds: right even on a few words, where
    charset-normalizer's guesses are little better than chance ("“yes”"
    read as "УyesФ"). Latin-1 is read as Windows-1252, its superset, whose
    quotes and dashes it would read as control characters.

    When cchardet's guess does not read the file (it takes some Polish for
    UTF-8), charset-normalizer's likeliest encoding is used. UTF-16 and
    UTF-32 need their byte-order mark, since a few bytes of Latin text
    otherwise read as UTF-16 too. When its encodings fit equally well (a
    short Italian text is as good in Central European cp1250, "caffč", as in
    cp1252), Windows-1252 wins if it reads the text as one of them does: the
    usual encoding of Western European text. (charset-normalizer lists one of
    the encodings that read a text alike, not necessarily it.)
    """
    if encoding != AUTO_ENCODING:
        text = data.decode(encoding, errors="replace")
        return text, "" if codecs.lookup(encoding).name == "utf-8" else encoding
    try:
        text = data.decode("utf-8")
        if not C1_CONTROLS.search(text):
            return text, ""
    except UnicodeDecodeError:
        pass
    if found := detected(data):
        return found
    try:
        western = data.decode(WESTERN)
    except UnicodeDecodeError:
        western = None
    matches = list(from_bytes(data))
    fits = [m for m in matches if m.bom or not m.encoding.startswith(("utf_16", "utf_32"))]
    if not fits:
        return data.decode(WESTERN, errors="replace"), WESTERN
    best = fits[0]
    tied = (m for m in matches if (m.chaos, m.coherence) == (best.chaos, best.coherence))
    if western is not None and any(
        str(m) == western or WESTERN in m.could_be_from_charset for m in tied
    ):
        return western, WESTERN
    return str(best), best.encoding


def detected(data: bytes) -> tuple[str, str] | None:
    """The file read in the encoding cchardet finds, and that encoding's
    name; None when it finds none, or one that does not read the file as
    text (bytes it cannot decode, control characters, UTF-16 or UTF-32
    without a byte-order mark)."""
    guess = cchardet.detect(data).get("encoding")
    try:
        name = codecs.lookup(guess).name if guess else ""
    except LookupError:
        return None
    if name == "iso8859-1":
        name = WESTERN
    if not name or name == "utf-8":
        return None  # UTF-8 has been tried
    if name.startswith(("utf-16", "utf-32")) and not data.startswith(BOMS):
        return None
    try:
        text = data.decode(name)
    except UnicodeDecodeError:
        return None
    if C1_CONTROLS.search(text):
        return None
    # Named as Windows-1252 when it reads the file alike: "“yes”" is as
    # good in cp1250, but a Western text more likely.
    try:
        if name != WESTERN and data.decode(WESTERN) == text:
            name = WESTERN
    except UnicodeDecodeError:
        pass
    return text, name


def split_lines(text: str) -> list[str]:
    """Lines as git counts them: split on LF only (CRLF counts as LF)."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def image_uri(path: str, data: bytes) -> str | None:
    mime = IMAGE_TYPES.get(Path(path).suffix.lower())
    if not mime or not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def run(args: str | list[str], *, timeout: float, **options) -> subprocess.CompletedProcess:
    """subprocess.run(args, capture_output=True, ...) without a console window,
    but a process that outlives timeout is stopped with every process it
    started, then subprocess.TimeoutExpired raised. subprocess.run stops only
    the process it started: a shell's command would go on running, holding
    the pipes open, and the wait on them would never end."""
    data = options.pop("input", None)
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
        **options,
    ) as proc:
        try:
            out, err = proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                tree = psutil.Process(proc.pid)
                for child in tree.children(recursive=True):
                    child.kill()
                tree.kill()
            except psutil.NoSuchProcess:
                pass
            proc.communicate()
            raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def run_filter(command: str, text: str, path: str) -> str:
    """Pipe text through a shell command (cmd.exe on Windows, sh elsewhere)."""
    try:
        proc = run(command, shell=True, input=text.encode("utf-8"), timeout=FILTER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise FilterError(
            f"filter took more than {FILTER_TIMEOUT} seconds on {path}, and was stopped"
        ) from None
    if proc.returncode != 0:
        raise FilterError(
            f"filter failed on {path} (exit {proc.returncode}): "
            + proc.stderr.decode("utf-8", errors="replace").strip()
        )
    return proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")


# Word level -------------------------------------------------------------------


# Longest quotation in a change description before it is cut with "…".
MAX_QUOTE = 60


def quote(text: str) -> str:
    text = " ".join(footnotes.plain(plain(text)).split())
    if len(text) > MAX_QUOTE:
        text = text[: MAX_QUOTE - 1] + "…"
    return f'"{text}"'


def describe(old: str, new: str) -> str:
    """One change within a line, in plain English."""
    old_blank, new_blank = not old.strip(), not new.strip()
    if old and new:
        if old_blank and new_blank:
            return "changed spacing"
        return f"changed {quote(old)} to {quote(new)}"
    if new:
        return "added a space" if new_blank else f"added {quote(new)}"
    return "removed a space" if old_blank else f"removed {quote(old)}"


# The styles whose change is a formatting change, and how it reads: made
# "text" bold, made "text" not bold. A heading's level is its own.
FORMATS = {
    "strong": ("made {} bold", "made {} not bold"),
    "em": ("made {} italic", "made {} not italic"),
    "u": ("underlined {}", "took the underline off {}"),
    "strike": ("struck {} through", "took the strikethrough off {}"),
    "sup": ("made {} superscript", "made {} not superscript"),
    "sub": ("made {} subscript", "made {} not subscript"),
    "link": ("made {} a link", "made {} not a link"),
}
FMT = Markup('<span class="fmt" data-fmt="{}">{}</span>')


def formatting(styles: set[str] | frozenset[str]) -> frozenset[str]:
    """The styles of a character that are formatting."""
    return frozenset(s for s in styles if s in FORMATS or HEADING.fullmatch(s))


def describe_format(text: str, old: frozenset[str], new: frozenset[str]) -> list[str]:
    """How the formatting of a piece of text changed, in plain English."""
    q = quote(text)
    out = []
    old_heading = next((int(s[1]) for s in old if HEADING.fullmatch(s)), 0)
    new_heading = next((int(s[1]) for s in new if HEADING.fullmatch(s)), 0)
    if old_heading != new_heading:
        out.append(
            f"made {q} a level {new_heading} heading" if new_heading else f"made {q} not a heading"
        )
    for style, (made, unmade) in FORMATS.items():
        if (style in new) != (style in old):
            out.append((made if style in new else unmade).format(q))
    return out


def format_marks(
    text: str, old_styles: Styles, new_styles: Styles
) -> tuple[Markup, Markup, list[str]] | None:
    """Text both sides have, each side in its own styles, with the pieces
    whose formatting changed marked (class fmt, what changed in data-fmt);
    and the changes in plain English. None when the formatting is the same
    (or unknown: text without styles of its own)."""
    if old_styles is None or new_styles is None:
        return None
    old_f = [formatting(s) for s in old_styles]
    new_f = [formatting(s) for s in new_styles]
    if old_f == new_f:
        return None
    left, right, changes = [], [], []
    start = 0
    for k in range(1, len(text) + 1):
        if k < len(text) and (old_f[k], new_f[k]) == (old_f[start], new_f[start]):
            continue
        piece = text[start:k]
        o, n = styled(piece, old_styles[start:k]), styled(piece, new_styles[start:k])
        if old_f[start] != new_f[start] and piece.strip():
            what = describe_format(piece, old_f[start], new_f[start])
            changes += what
            o, n = FMT.format("; ".join(what), o), FMT.format("; ".join(what), n)
        left.append(o)
        right.append(n)
        start = k
    if not changes:
        return None
    return Markup("").join(left), Markup("").join(right), changes


def _slice(styles: Styles, start: int, end: int) -> Styles:
    return None if styles is None else styles[start:end]


def char_marks(
    old: str, new: str, old_styles: Styles = None, new_styles: Styles = None
) -> tuple[Markup, Markup] | None:
    """A changed word with only its changed letters in <mark>, or None when
    too few letters survive for that to help."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if matcher.ratio() < CHAR_THRESHOLD:
        return None
    left, right = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        o = styled(old[i1:i2], _slice(old_styles, i1, i2))
        n = styled(new[j1:j2], _slice(new_styles, j1, j2))
        if op == "equal":
            left.append(o)
            right.append(n)
        else:
            if i2 > i1:
                left.append(Markup("<mark>") + o + Markup("</mark>"))
            if j2 > j1:
                right.append(Markup("<mark>") + n + Markup("</mark>"))
    return Markup("").join(left), Markup("").join(right)


@dataclass
class WordDiff:
    """Two versions of a line compared word by word."""

    left: Markup
    right: Markup
    # The changes in plain English, in order.
    changes: list[str]
    words_added: int
    words_removed: int
    # The formatting changes of the words both have.
    format_changes: list[str] = field(default_factory=list)


DEL = Markup("<del>{}</del>")
INS = Markup("<ins>{}</ins>")
PARTIAL_DEL = Markup('<del class="partial">{}</del>')
PARTIAL_INS = Markup('<ins class="partial">{}</ins>')


def _offsets(tokens: list[str]) -> list[int]:
    out = [0]
    for t in tokens:
        out.append(out[-1] + len(t))
    return out


def comments_only(old: str, new: str) -> bool:
    """Whether a change is made of comment markers alone (and blanks)."""
    return (
        not PLACEHOLDER.sub("", old).strip()
        and not PLACEHOLDER.sub("", new).strip()
        and bool(PLACEHOLDER.search(old) or PLACEHOLDER.search(new))
    )


def merge_across_spaces(ops: list[Opcode], a: list[str]) -> list[Opcode]:
    """Join two changes separated only by whitespace into one change.

    "makes incumbents" changed word by word would be highlighted as two
    pieces with an unchanged space between them; it reads as one change.
    """
    merged: list[Opcode] = []
    k = 0
    while k < len(ops):
        op = ops[k]
        if (
            merged
            and merged[-1][0] != "equal"
            and op[0] == "equal"
            and k + 1 < len(ops)
            and all(t.isspace() for t in a[op[1] : op[2]])
        ):
            nxt = ops[k + 1]
            _, i1, _, j1, _ = merged.pop()
            _, _, i2, _, j2 = nxt
            tag = "replace" if i2 > i1 and j2 > j1 else "delete" if i2 > i1 else "insert"
            merged.append((tag, i1, i2, j1, j2))
            k += 2
        else:
            merged.append(op)
            k += 1
    return merged


def word_ops(old: str, new: str) -> list[Opcode]:
    """The changes between two lines word by word, as opcodes over their
    characters: the one word pairing of prosediff, that of the HTML report's
    changed lines and of the word diff. Changes separated only by whitespace
    are one change."""
    return list(_word_ops(old, new))


# Almost all the time of a comparison is spent here, and a changed line is
# compared word by word up to three times (drawn, searched for moved
# passages, drawn again around them): each pair of lines is compared once.
WORD_OPS_CACHE = 4096


@lru_cache(maxsize=WORD_OPS_CACHE)
def _word_ops(old: str, new: str) -> tuple[Opcode, ...]:
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    ao, bo = _offsets(a), _offsets(b)
    # patiencediff's patience diff (in Rust): difflib's pairing but on 4 of
    # 2,776 changed lines, 14 times as fast (docs/word_matcher_benchmark.md)
    matcher = PatienceSequenceMatcher(None, a, b)
    ops = [
        (op, ao[i1], ao[i2], bo[j1], bo[j2])
        for op, i1, i2, j1, j2 in merge_across_spaces(matcher.get_opcodes(), a)
    ]
    return tuple(slide_ops(ops, old, new))


# Where a change reads best: at the start or end of the line; after the end
# of a sentence, ending with one; failing that, after and with a comma.
SENTENCE_END = frozenset(".!?;:")
CLAUSE_END = frozenset(",")
# What may follow the end of a sentence and still belong to it: footnote
# references ("A.[^1]", or the stand-in footnotes.set_aside puts for its
# number) and closing quotes and brackets ("yes.”", "above.)").
AFTER_END = re.compile(rf"(?:\[\^[^\]\s]*\]|{footnotes.STAND_IN.pattern}|[)\]}}\"'”’»])+$")


def _ends(text: str) -> int:
    """3 when text ends a sentence, 1 a clause, else 0 (footnote references
    and closing quotes and brackets after its last sign aside)."""
    text = AFTER_END.sub("", text)
    if text and text[-1] in SENTENCE_END:
        return 3
    if text and text[-1] in CLAUSE_END:
        return 1
    return 0


def _edge_score(line: str, start: int, end: int) -> int:
    """How well the change start:end of line sits on the bounds a reader
    sees: the line starting or ending with it scores most, a sentence a
    little less (its full stop may be an abbreviation's, as in "Mr."), a
    clause less, a word cut in two never."""
    before = line[:start].rstrip()
    score = 4 if not before else _ends(before)
    score += 4 if end >= len(line.rstrip()) else _ends(line[start:end].rstrip())
    if start < end and line[start].isspace():
        score -= 1  # starting with the space of the text before it
    for at in (start, end):
        if 0 < at < len(line) and line[at - 1].isalnum() and line[at].isalnum():
            score -= 5
    return score


def slide_ops(ops: list[Opcode], old: str, new: str) -> list[Opcode]:
    """Words removed (or added) between two runs of unchanged text slid to
    where they read best, as git slides a diff's hunks. A removal can start
    anywhere its text repeats around it: a sentence removed from between two
    others is ". The committee met ... budget" as difflib finds it, the full
    stop of the sentence before it and not its own, and a sentence moved
    next to another starting with the same word may be found as "council
    met ... afternoon. The". Of all the places it can slide to (text the same
    on both sides of it), the one whose edges score best (_edge_score) is
    taken, the nearest on a tie: "The committee met ... budget. ", which
    reads, and moves as a passage, as a sentence. Only a pure removal or
    addition slides."""
    ops = list(ops)
    out: list[Opcode] = []
    for k, (tag, o1, o2, n1, n2) in enumerate(ops):
        if tag not in ("delete", "insert"):
            out.append((tag, o1, o2, n1, n2))
            continue
        prev = out[-1] if out and out[-1][0] == "equal" else None
        nxt = ops[k + 1] if k + 1 < len(ops) and ops[k + 1][0] == "equal" else None
        side = 1 if tag == "delete" else 3  # the side the change has text on
        line = old if tag == "delete" else new
        start, end = (o1, o2) if tag == "delete" else (n1, n2)
        # how far it can slide: within the unchanged runs next to it, leaving
        # a character of each between it and another change
        # (all of the run before, when it starts the line; all of the run
        # after, when it ends it)
        if not prev:
            low = start
        elif prev[1] == 0 and prev[3] == 0:
            low = 0
        else:
            low = prev[side] + 1
        last = k + 1 == len(ops) - 1
        high = nxt[side + 1] - (0 if last else 1) if nxt else end
        left = 0
        while start - left - 1 >= low and line[start - left - 1] == line[end - left - 1]:
            left += 1
        right = 0
        while end + right < high and line[start + right] == line[end + right]:
            right += 1
        best = min(
            range(-left, right + 1),
            key=lambda s: (-_edge_score(line, start + s, end + s), abs(s), -s),
        )
        if not best:
            out.append((tag, o1, o2, n1, n2))
            continue
        # the unchanged runs around it give or take the text slid past (the
        # same on both sides): the one before ends, the one after starts,
        # where the change now does
        if prev:
            out.pop()
            if prev[2] + best > prev[1] or prev[4] + best > prev[3]:
                out.append(("equal", prev[1], prev[2] + best, prev[3], prev[4] + best))
        else:
            out.append(("equal", o1, o1 + best, n1, n1 + best))
        out.append((tag, o1 + best, o2 + best, n1 + best, n2 + best))
        if nxt:
            _, e1, e2, f1, f2 = nxt
            ops[k + 1] = ("equal", e1 + best, e2, f1 + best, f2)
        elif best < 0:
            # at the end of the line: the text slid past is unchanged after it
            out.append(("equal", o2 + best, o2, n2 + best, n2))
    return [op for op in out if op[0] != "equal" or op[2] > op[1] or op[4] > op[3]]


@dataclass
class MovedSpan:
    """A passage of a line moved to or from another place (mark_moves):
    its characters start:end in the line, the number it shares with the
    other end (for the HTML report to draw a line between them), and its
    HTML, the passage compared with the other end."""

    start: int
    end: int
    pair: int
    html: Markup


def outside(start: int, end: int, moves: list[MovedSpan]) -> list[tuple[int, int]]:
    """The pieces of characters start:end that no moved passage covers."""
    pieces, at = [], start
    for m in sorted(moves, key=lambda m: m.start):
        if m.end <= at or m.start >= end:
            continue
        if m.start > at:
            pieces.append((at, m.start))
        at = max(at, m.end)
    if at < end:
        pieces.append((at, end))
    return pieces


def word_diff(
    old: str,
    new: str,
    old_styles: Styles = None,
    new_styles: Styles = None,
    old_moves: list[MovedSpan] | None = None,
    new_moves: list[MovedSpan] | None = None,
) -> WordDiff:
    """Both lines as HTML, the changed tokens wrapped in <del> and <ins>.

    Changes separated only by whitespace are one change. A word changed into
    a similar word ("repeat" -> "repeated") is marked class="partial", with
    only the changed letters in <mark>. The styles, if given, are the
    Markdown styles of each character of the two lines. The passages moved
    (old_moves, new_moves) show as their own HTML, and are no change of the
    line.
    """
    old_moves, new_moves = old_moves or [], new_moves or []
    # (where in the line, HTML): the pieces of each side, put in order at the end
    left: list[tuple[int, Markup]] = [(m.start, m.html) for m in old_moves]
    right: list[tuple[int, Markup]] = [(m.start, m.html) for m in new_moves]
    changes, formats = [], []
    added = removed = 0
    for op, o1, o2, n1, n2 in word_ops(old, new):
        old_part, new_part = old[o1:o2], new[n1:n2]
        old_st, new_st = _slice(old_styles, o1, o2), _slice(new_styles, n1, n2)
        old_pieces, new_pieces = outside(o1, o2, old_moves), outside(n1, n2, new_moves)
        if old_pieces != ([(o1, o2)] if o2 > o1 else []) or new_pieces != (
            [(n1, n2)] if n2 > n1 else []
        ):
            # partly moved: what is left of it is the change
            old_rest = "".join(old[a:b] for a, b in old_pieces)
            new_rest = "".join(new[a:b] for a, b in new_pieces)
            change = (
                op != "equal"
                and (old_rest.strip() or new_rest.strip())
                and not comments_only(old_rest, new_rest)
            )
            if change:
                removed += len(WORD.findall(old_rest))
                added += len(WORD.findall(new_rest))
                changes.append(describe(old_rest, new_rest))
            for pieces, text, styles, out, tag in (
                (old_pieces, old, old_styles, left, DEL),
                (new_pieces, new, new_styles, right, INS),
            ):
                for a, b in pieces:
                    html = styled(text[a:b], _slice(styles, a, b))
                    out.append((a, tag.format(html) if change and text[a:b].strip() else html))
            continue
        if op == "equal" or comments_only(old_part, new_part):
            # Comment markers are not text: whether a comment is new, gone or
            # moved (to the next paragraph, when its own was deleted) shows in
            # its icon and in the comments panel, not as a changed word.
            if op == "equal" and (marked := format_marks(old_part, old_st, new_st)):
                left.append((o1, marked[0]))
                right.append((n1, marked[1]))
                formats += marked[2]
                continue
            left.append((o1, styled(old_part, old_st)))
            right.append((n1, styled(new_part, new_st)))
            continue
        removed += len(WORD.findall(old_part))
        added += len(WORD.findall(new_part))
        changes.append(describe(old_part, new_part))
        marks = None
        if (
            # one word for one (a word is one token)
            op == "replace" and WORD.fullmatch(old_part) and WORD.fullmatch(new_part)
        ):
            marks = char_marks(old_part, new_part, old_st, new_st)
        if marks:
            left.append((o1, PARTIAL_DEL.format(marks[0])))
            right.append((n1, PARTIAL_INS.format(marks[1])))
            continue
        if old_part:
            left.append((o1, DEL.format(styled(old_part, old_st))))
        if new_part:
            right.append((n1, INS.format(styled(new_part, new_st))))

    def joined(pieces: list[tuple[int, Markup]]) -> Markup:
        return Markup("").join(html for _, html in sorted(pieces, key=lambda p: p[0]))

    return WordDiff(joined(left), joined(right), changes, added, removed, formats)


# Line similarity ----------------------------------------------------------------


def similarity_tokens(line: str) -> list[str]:
    """What line similarity compares: words and punctuation, spacing aside."""
    return [t for t in TOKEN.findall(line) if not t.isspace()]


def token_similarity(a: list[str], b: list[str], cutoff: float) -> float:
    """Share of tokens in common, in order (2 x longest common subsequence /
    total length), 0 below cutoff. rapidfuzz computes it in C."""
    if not a and not b:
        return 1.0
    return Indel.normalized_similarity(a, b, score_cutoff=cutoff)


def footnote_similarity(old: str, new: str) -> float:
    """How alike the texts of two footnotes are, 0 to 1."""
    return token_similarity(similarity_tokens(old), similarity_tokens(new), 0)


def pair_lines(old: list[str], new: list[str]) -> list[tuple[int | None, int | None]]:
    """Pair the lines of a replaced block, in order.

    Lines similar enough are paired first, so that as many similar lines as
    possible face each other (dynamic programming over the similarity
    matrix). The lines left between two such pairs are then paired in
    order when they have at least PAIRING_FLOOR in common, and what remains
    stands alone: (i, None) is a deleted line, (None, j) an inserted one.
    """
    n, m = len(old), len(new)
    anchors: list[tuple[int, int]] = []
    if 0 < n * m <= PAIRING_MAX_CELLS:
        old_tokens = [similarity_tokens(line) for line in old]
        new_tokens = [similarity_tokens(line) for line in new]
        sim = [[token_similarity(a, b, PAIRING_THRESHOLD) for b in new_tokens] for a in old_tokens]
        best = [[0.0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            row, above, sim_row = best[i], best[i - 1], sim[i - 1]
            for j in range(1, m + 1):
                score = above[j] if above[j] > row[j - 1] else row[j - 1]
                s = sim_row[j - 1]
                if s and above[j - 1] + s > score:
                    score = above[j - 1] + s
                row[j] = score
        i, j = n, m
        while i > 0 and j > 0:
            s = sim[i - 1][j - 1]
            if s and best[i][j] == best[i - 1][j - 1] + s:
                anchors.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif best[i][j] == best[i - 1][j]:
                i -= 1
            else:
                j -= 1
        anchors.reverse()

    pairs: list[tuple[int | None, int | None]] = []
    pi = pj = 0
    single = n == 1 and m == 1  # one line rewritten in place: always a pair
    for ai, aj in [*anchors, (n, m)]:
        gap_old, gap_new = list(range(pi, ai)), list(range(pj, aj))
        for k in range(max(len(gap_old), len(gap_new))):
            i = gap_old[k] if k < len(gap_old) else None
            j = gap_new[k] if k < len(gap_new) else None
            if (
                i is None
                or j is None
                or single
                or token_similarity(
                    similarity_tokens(old[i]), similarity_tokens(new[j]), PAIRING_FLOOR
                )
            ):
                pairs.append((i, j))
            else:
                # too little in common to be the same line edited: shown, in
                # its place, as one removed and one added, not face to face
                pairs += [(i, None), (None, j)]
        if (ai, aj) != (n, m):
            pairs.append((ai, aj))
        pi, pj = ai + 1, aj + 1
    return pairs


# Line level -------------------------------------------------------------------


def difflib_opcodes(old: list[str], new: list[str]) -> list[Opcode]:
    """Line alignment in pure Python, for when git's is not at hand."""
    return difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()


def opcodes_from_changes(
    changes: list[tuple[int, int, int, int]], n_old: int, n_new: int
) -> list[Opcode]:
    """Whole-file opcodes from the changed ranges, the gaps being equal."""
    ops: list[Opcode] = []
    i = j = 0
    for i1, i2, j1, j2 in changes:
        if i1 > i or j1 > j:
            # With --ignore-all-space a gap may hold lines that differ in
            # whitespace only; its two sides still have as many lines.
            tag = "equal" if i1 - i == j1 - j else "replace"
            ops.append((tag, i, i1, j, j1))
        if i2 > i1 and j2 > j1:
            tag = "replace"
        elif i2 > i1:
            tag = "delete"
        else:
            tag = "insert"
        ops.append((tag, i1, i2, j1, j2))
        i, j = i2, j2
    if i < n_old or j < n_new:
        tag = "equal" if n_old - i == n_new - j else "replace"
        ops.append((tag, i, n_old, j, n_new))
    return ops


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
FILE_HEADER = re.compile(r"^diff --git .*/(\d+) .*/(\d+)$")


def hunk_range(start: str, count: str | None) -> tuple[int, int]:
    """0-based half-open line range of one side of a -U0 hunk header."""
    s, c = int(start), 1 if count is None else int(count)
    # An empty side names the line after which the other side's lines go.
    return (s, s) if c == 0 else (s - 1, s - 1 + c)


def git_opcodes(
    pairs: list[tuple[list[str], list[str]]], ignore_whitespace: bool = False
) -> list[list[Opcode]]:
    """Line alignment of every (old, new) pair by git, in one process.

    The pairs are written to two temporary trees and compared with git diff
    --no-index --histogram --unified=0: only the hunk headers are read, and
    the lines between hunks are the unchanged ones.
    """
    if not pairs:
        return []
    changes: list[list[tuple[int, int, int, int]]] = [[] for _ in pairs]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for side in ("a", "b"):
            (root / side).mkdir()
        for k, (old, new) in enumerate(pairs):
            for side, lines in (("a", old), ("b", new)):
                body = "".join(line + "\n" for line in lines)
                (root / side / str(k)).write_bytes(body.encode("utf-8"))
        cmd = [
            git.Git.GIT_PYTHON_GIT_EXECUTABLE or "git",
            "-c",
            "core.quotepath=off",
            "diff",
            "--no-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--histogram",
            "--unified=0",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        ]
        if ignore_whitespace:
            cmd.append("--ignore-all-space")
        try:
            proc = run([*cmd, "a", "b"], cwd=tmp, timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"git diff took more than {GIT_TIMEOUT} seconds, and was stopped"
            ) from None
    # git diff --no-index exits 1 when the trees differ.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            "git diff failed: " + proc.stderr.decode("utf-8", errors="replace").strip()
        )
    current = None
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if m := FILE_HEADER.match(line):
            current = int(m.group(1))
        elif current is not None and (h := HUNK.match(line)):
            changes[current].append(hunk_range(h[1], h[2]) + hunk_range(h[3], h[4]))
    return [
        opcodes_from_changes(ch, len(old), len(new))
        for ch, (old, new) in zip(changes, pairs, strict=False)
    ]


# Rows -------------------------------------------------------------------------


class Styler:
    """The styles of lines: a document's own, those of Markdown's syntax
    (computed once each), or none at all."""

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown
        self._cache: dict[str, list[set[str]]] = {}

    def __call__(self, line: str) -> Styles:
        if isinstance(line, Line):  # a document's: its own styles
            return line.styles
        if not self.markdown:
            return None
        if line not in self._cache:
            self._cache[line] = md_styles(line)
        return self._cache[line]


def deleted_row(number: int, line: str, style: Styler) -> Row:
    return Row(
        "delete",
        number,
        styled(line, style(line)),
        changes=["removed this line"],
        words_removed=len(WORD.findall(line)),
        text=line,
    )


def inserted_row(number: int, line: str, style: Styler) -> Row:
    return Row(
        "insert",
        right_no=number,
        right=styled(line, style(line)),
        changes=["added this line"],
        words_added=len(WORD.findall(line)),
        text=line,
    )


def _spaced(line: str) -> str:
    return " ".join(line.split())


def move_key(line: str) -> str | None:
    """What identifies a line as moved: its words, spacing aside."""
    key = _spaced(line)
    return key if len(key.replace(" ", "")) >= MIN_MOVE_CHARS else None


def _move_ends(out: Row, into: Row) -> tuple[str, str]:
    """Where a line or passage moved to (into's line) and came from (out's)."""
    return into.right_label or f"{into.right_no:,}", out.left_label or f"{out.left_no:,}"


def _make_move(out: Row, into: Row, style: Styler, pair: int = 0) -> None:
    out.kind, into.kind = "moved-out", "moved-in"
    out.move_pair = into.move_pair = pair
    to_line, from_line = _move_ends(out, into)
    if move_key(out.text) == move_key(into.text):
        out.changes = [f"moved to line {to_line}"]
        into.changes = [f"moved from line {from_line}"]
        out.words_removed = into.words_added = 0
        return
    w = word_diff(out.text, into.text, style(out.text), style(into.text))
    out.left, into.right = w.left, w.right
    out.changes = [f"moved to line {to_line}, edited:", *w.changes]
    into.changes = [f"moved from line {from_line}, edited:", *w.changes]
    out.words_removed, into.words_added = w.words_removed, w.words_added


def _sorted_tokens(line: str) -> list[str]:
    return sorted(similarity_tokens(line))


# How alike two lines are for the moved-line matching, 0 to 1, each measure
# as (what a line becomes before it is compared, the score of two of them,
# 0 below the cutoff). Both of rapidfuzz, in C, and both follow a line whose
# sentences were reordered; the order-bound measures (in common in order, of
# words or characters; Levenshtein) did worse on docs/move_sensitivity.py's
# revisions, and were dropped.
MOVE_ALGORITHMS = {
    # words and punctuation in common, whatever their order: 2 x longest
    # common subsequence of the sorted tokens / total length
    "token-sort": (
        _sorted_tokens,
        lambda a, b, cutoff: Indel.normalized_similarity(a, b, score_cutoff=cutoff),
    ),
    # the words both lines share against the rest of each, whatever their
    # order (a line inside a longer one scores high)
    "token-set": (
        _spaced,
        lambda a, b, cutoff: fuzz.token_set_ratio(a, b, score_cutoff=cutoff * 100) / 100,
    ),
}
MOVE_ALGORITHM = "token-sort"
# The defaults when lines are sentences (--by-sentence): shorter lines, whose
# likeness moves more with each word edited (docs/move_sensitivity.py).
SENTENCE_MOVE_SIMILARITY = 0.55
SENTENCE_MOVE_ALGORITHM = "token-sort"
MOVE_TOLERANCE = 1e-9
MOVE_MARGIN = 0.01


def move_defaults(by_sentence: bool) -> tuple[float, str]:
    """The moved-line similarity and algorithm prosediff uses unless told
    otherwise: those for paragraphs, or for sentences (--by-sentence)."""
    if by_sentence:
        return SENTENCE_MOVE_SIMILARITY, SENTENCE_MOVE_ALGORITHM
    return MOVE_SIMILARITY, MOVE_ALGORITHM


def check_move_algorithm(name: str) -> None:
    if name not in MOVE_ALGORITHMS:
        raise ValueError(
            f"move algorithm must be one of {', '.join(MOVE_ALGORITHMS)}, not {name!r}"
        )


def move_scorer(similarity: float, algorithm: str) -> tuple[Callable, Callable]:
    """algorithm's (prepare, score): what a line becomes before it is
    compared, and the score of two of them, 0 below similarity."""
    prepare, score = MOVE_ALGORITHMS[algorithm]
    # "at least similarity": rapidfuzz's cutoff can turn down a score right
    # at it, so the scores are taken from a little below and then checked
    cutoff = max(0.0, similarity - MOVE_MARGIN)

    def at_least(a, b) -> float:
        s = score(a, b, cutoff)
        return s if s and s >= similarity - MOVE_TOLERANCE else 0.0

    return prepare, at_least


def line_move_score(similarity: float, algorithm: str) -> Callable[[str, str], float]:
    """How alike two lines are as a move, 0 to 1: 1 for the same words
    (spacing aside); else algorithm's score, 0 below similarity (and always
    at similarity 1); 0 for a line too short to tell a move from chance
    (move_key). Each line is prepared once."""
    prepare, score = move_scorer(similarity, algorithm)
    seen: dict[str, tuple] = {}

    def of(line: str) -> tuple:
        if line not in seen:
            key = move_key(line)
            seen[line] = (key, prepare(line) if key and similarity < 1 else None)
        return seen[line]

    def alike(a: str, b: str) -> float:
        (key_a, prepared_a), (key_b, prepared_b) = of(a), of(b)
        if not key_a or not key_b:
            return 0.0
        if key_a == key_b:
            return 1.0
        return score(prepared_a, prepared_b) if similarity < 1 else 0.0

    return alike


def best_pairs(candidates: list[tuple]) -> list[tuple]:
    """The candidate pairs (rank, a, b, match) taken, as (a, b, match): the
    lowest rank first (the most alike), each a and each b in one pair at
    most."""
    used_a, used_b = set(), set()
    taken = []
    for _, a, b, match in sorted(candidates, key=lambda c: c[0]):
        if a not in used_a and b not in used_b:
            used_a.add(a)
            used_b.add(b)
            taken.append((a, b, match))
    return taken


def candidate_pairs(
    outs: list,
    ins: list,
    words_of: Callable,
    max_pairs: int,
    rare_min: int,
    rare_share: float,
) -> list[tuple[int, int]]:
    """The pairs (i, j) of a removed outs[i] and an added ins[j] worth
    matching: every one, or past max_pairs of them those sharing rare words
    (words_of: the content words of one; rare, in at most rare_share of
    them or rare_min), the most rare words shared first, at most max_pairs."""
    if len(outs) * len(ins) <= max_pairs:
        return [(i, j) for i in range(len(outs)) for j in range(len(ins))]
    out_words = [set(words_of(x)) for x in outs]
    in_words = [set(words_of(x)) for x in ins]
    df = Counter(w for ws in [*out_words, *in_words] for w in ws)
    limit = max(rare_min, rare_share * (len(outs) + len(ins)))
    index: dict[str, list[int]] = defaultdict(list)
    for j, ws in enumerate(in_words):
        for w in ws:
            if df[w] <= limit:
                index[w].append(j)
    shared: Counter = Counter()
    for i, ws in enumerate(out_words):
        for w in ws:
            if df[w] <= limit:
                for j in index[w]:
                    shared[i, j] += 1
    return [pair for pair, _ in shared.most_common(max_pairs)]


# Moved passages ------------------------------------------------------------------

# A passage is a run of text removed from (or added to) a line: the changes
# of its word diff, separated by at most MAX_HOLE_WORDS unchanged words (a
# moved sentence lands next to words it happens to share with its new
# place), at least MIN_PASSAGE_WORDS words long (and MIN_PASSAGE_CHARS
# characters, spaces aside). A passage shorter than PARTIAL_SHARE of the other is also
# looked for inside it, as a window of it as long as itself: a sentence
# moved out of a paragraph deleted or rewritten. What is left of a passage
# around such a window is matched again, up to PASSAGE_ROUNDS times.
MIN_PASSAGE_WORDS = 4
MIN_PASSAGE_CHARS = 15
# Two passages are one moved only if they share MIN_SHARED_CONTENT words of
# CONTENT_LETTERS letters or more: short ones alike in their articles and
# prepositions alone ("in several members of the same", "in both of the
# same") are not (docs/passage_benchmark.py).
MIN_SHARED_CONTENT = 2
CONTENT_LETTERS = 4
MAX_HOLE_WORDS = 2
PARTIAL_SHARE = 0.8
PASSAGE_ROUNDS = 4
MOVE_PASSAGES = True
# The smallest run of words and punctuation two passages share that can
# start or end the part of them that moved: a word or two in common by
# chance at an edge is not.
MIN_EDGE_RUN = 2
# Past MOVE_MAX_CELLS pairs of removed and added passages (a long document
# revised throughout), not every pair is tried: only those sharing a rare
# word (of CONTENT_LETTERS letters or more, in at most RARE_SHARE of the
# passages or RARE_MIN of them), most rare words shared first, at most
# MOVE_MAX_CELLS of them. A passage moved keeps most of its words, rare ones
# included; two passages alike by chance seldom share one.
RARE_SHARE = 0.01
RARE_MIN = 20


def _setting(default: float, label: str, help: str, low: int = 1, share: bool = False):
    """A field of MovedPassageSettings: its default, its label in the GUI,
    what it does (the command line's help, the GUI's tooltip), its least
    value (low), or a share, above 0 and at most 1."""
    return field(
        default=default, metadata={"label": label, "help": help, "low": low, "share": share}
    )


@dataclass(frozen=True)
class MovedPassageSettings:
    """How moved passages are found (mark_moves); each default is the
    constant of the same meaning above, chosen with docs/passage_benchmark.py.
    Set from the command line (--passage-NAME, NAME a field with - for _) and
    the GUI's advanced settings, both made from these fields."""

    min_words: int = _setting(
        MIN_PASSAGE_WORDS,
        "Shortest passage, words",
        "the fewest words a moved passage has: shorter runs of words alike are taken for chance",
    )
    min_chars: int = _setting(
        MIN_PASSAGE_CHARS,
        "Shortest passage, characters",
        "the fewest characters, spaces aside, a moved passage has",
    )
    max_gap: int = _setting(
        MAX_HOLE_WORDS,
        "Unchanged words inside a passage",
        "how many unchanged words may sit between two changes of one passage (a "
        "moved sentence lands next to words it happens to share with its new place), "
        "and between the edge of a passage and a word edited next to it",
        low=0,
    )
    shared_words: int = _setting(
        MIN_SHARED_CONTENT,
        "Words two passages share",
        "how many words of --passage-content-letters letters or more two passages must "
        "share to be one passage moved: not only their articles and prepositions",
    )
    content_letters: int = _setting(
        CONTENT_LETTERS,
        "Letters of a shared word",
        "how many letters a word needs to count among the words two passages share",
    )
    edge_run: int = _setting(
        MIN_EDGE_RUN,
        "Words in common at an edge",
        "the fewest words and punctuation in a row, the same in both passages, that "
        "can start or end the part of them that moved",
    )
    partial_share: float = _setting(
        PARTIAL_SHARE,
        "Passage looked for inside a longer one",
        "a passage shorter than this share of the other (0 to 1) is also looked for "
        "inside it: a sentence moved out of a paragraph deleted or rewritten",
        share=True,
    )
    rounds: int = _setting(
        PASSAGE_ROUNDS,
        "Rounds of matching",
        "how many times what is left of a passage around a part of it found moved is matched again",
    )
    max_pairs: int = _setting(
        MOVE_MAX_CELLS,
        "Pairs of passages tried",
        "past this many pairs of a removed and an added passage (a long document "
        "revised throughout), only the pairs sharing a rare word are tried, at most "
        "this many",
    )
    rare_share: float = _setting(
        RARE_SHARE,
        "Rare word, share of passages",
        "with too many pairs, a word is rare when it is in at most this share of the "
        "passages (0 to 1) or --passage-rare-min of them",
        share=True,
    )
    rare_min: int = _setting(
        RARE_MIN,
        "Rare word, passages",
        "with too many pairs, a word in at most this many passages is rare",
    )

    def check(self) -> None:
        """Refuse values that make no sense (SettingError, naming the setting)."""
        for f in fields(self):
            value = getattr(self, f.name)
            if f.metadata["share"] and not 0 < value <= 1:
                raise SettingError(f.name, "must be above 0 and at most 1")
            if not f.metadata["share"] and value < f.metadata["low"]:
                raise SettingError(f.name, f"must be {f.metadata['low']} or more")


class SettingError(ValueError):
    """A moved-passage setting out of range; name is its field's."""

    def __init__(self, name: str, problem: str) -> None:
        super().__init__(f"passage setting {name} {problem}")
        self.name = name


MOVED_PASSAGE_DEFAULTS = MovedPassageSettings()


def setting_type(f) -> type:
    """The type of a MovedPassageSettings field's values: float for a
    share, else int."""
    return float if f.metadata["share"] else int


@dataclass(eq=False)  # each passage is itself, hashed by identity
class Passage:
    row: Row
    old: bool  # on the old side of the row, or the new
    start: int
    end: int
    # the changes of the row's word diff it spans, to tell a move from an
    # edit in place (-1: a whole line removed or added, or part of one)
    first_op: int
    last_op: int
    # a whole line removed or added: it may move as a line (long enough for
    # move_key), and as a passage (long_enough)
    whole: bool = False
    line: bool = False
    passage: bool = True


def _passage(
    row: Row,
    old: bool,
    line: str,
    start: int,
    end: int,
    ops: tuple[int, int],
    ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS,
) -> Passage | None:
    """The passage start:end of the line, its spaces trimmed; None when too
    short to be told from a chance likeness."""
    while start < end and line[start].isspace():
        start += 1
    while end > start and line[end - 1].isspace():
        end -= 1
    return Passage(row, old, start, end, *ops) if long_enough(line[start:end], ps) else None


def long_enough(text: str, ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS) -> bool:
    """Whether a passage is long enough to be told from a chance likeness."""
    return len(WORD.findall(text)) >= ps.min_words and len("".join(text.split())) >= ps.min_chars


def content_words(text: str, ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS) -> Counter:
    """The words of text long enough to carry meaning (ps.content_letters or
    more), case aside: what two passages must share, beyond "of the" and
    "in the", to be one passage moved."""
    return Counter(w.casefold() for w in WORD.findall(text) if len(w) >= ps.content_letters)


def shares_content(a: str, b: str, ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS) -> bool:
    shared = content_words(a, ps) & content_words(b, ps)
    return sum(shared.values()) >= ps.shared_words


def passages_of(
    row: Row, old_line: str, new_line: str, ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS
) -> list[Passage]:
    """The passages removed from and added to one changed row: a line
    removed or added whole is one passage, an edited one has its runs of
    changes (holes of up to ps.max_gap words allowed)."""
    whole = (-1, -1)
    if row.kind == "delete":
        return [p for p in [_passage(row, True, old_line, 0, len(old_line), whole, ps)] if p]
    if row.kind == "insert":
        return [p for p in [_passage(row, False, new_line, 0, len(new_line), whole, ps)] if p]
    ops = word_ops(old_line, new_line)
    found = []
    for old in (True, False):
        line = old_line if old else new_line
        # the changed runs of this side: (start, end, index of the change)
        runs = [
            (o1, o2, k) if old else (n1, n2, k)
            for k, (tag, o1, o2, n1, n2) in enumerate(ops)
            if tag != "equal"
            and (o2 > o1 if old else n2 > n1)
            and not comments_only(old_line[o1:o2], new_line[n1:n2])
        ]
        group: list[tuple[int, int, int]] = []
        for run in [*runs, None]:
            if run and (not group or len(WORD.findall(line[group[-1][1] : run[0]])) <= ps.max_gap):
                group.append(run)
                continue
            if group:
                ops_spanned = (group[0][2], group[-1][2])
                if p := _passage(row, old, line, group[0][0], group[-1][1], ops_spanned, ps):
                    found.append(p)
            group = [run] if run else []
    return found


def _tokens(line: str, start: int, end: int) -> list[tuple[int, int, str]]:
    """The words and punctuation of line[start:end], and where each sits."""
    return [
        (start + m.start(), start + m.end(), m.group())
        for m in TOKEN.finditer(line[start:end])
        if not m.group().isspace()
    ]


# Punctuation a moved passage does not start with: that of the sentence
# before it.
LEADING_PUNCTUATION = frozenset(".,;:!?")


def _core(
    la: str,
    lb: str,
    a1: int,
    a2: int,
    b1: int,
    b2: int,
    ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS,
) -> tuple[int, int, int, int] | None:
    """Parts a1:a2 of line la and b1:b2 of line lb cut down to what they
    have in common: from the first run of at least ps.edge_run tokens both
    share, in order, to the last, widened over the shorter runs just outside
    it (at most ps.max_gap tokens away on both sides: a word edited next to
    the edge of the passage, not a word in common by chance). None when they
    share none."""
    ta, tb = _tokens(la, a1, a2), _tokens(lb, b1, b2)
    matcher = difflib.SequenceMatcher(
        None, [t for *_, t in ta], [t for *_, t in tb], autojunk=False
    )
    blocks = [m for m in matcher.get_matching_blocks() if m.size]
    runs = [k for k, m in enumerate(blocks) if m.size >= ps.edge_run]
    if not runs:
        return None
    first, last = runs[0], runs[-1]
    while first > 0 and (
        blocks[first].a - (blocks[first - 1].a + blocks[first - 1].size) <= ps.max_gap
        and blocks[first].b - (blocks[first - 1].b + blocks[first - 1].size) <= ps.max_gap
    ):
        first -= 1
    while last < len(blocks) - 1 and (
        blocks[last + 1].a - (blocks[last].a + blocks[last].size) <= ps.max_gap
        and blocks[last + 1].b - (blocks[last].b + blocks[last].size) <= ps.max_gap
    ):
        last += 1
    i1, j1 = blocks[first].a, blocks[first].b
    i2, j2 = blocks[last].a + blocks[last].size, blocks[last].b + blocks[last].size
    while i1 < i2 and j1 < j2 and ta[i1][2] in LEADING_PUNCTUATION and ta[i1][2] == tb[j1][2]:
        i1, j1 = i1 + 1, j1 + 1
    if i1 >= i2 or j1 >= j2:
        return None
    return ta[i1][0], ta[i2 - 1][1], tb[j1][0], tb[j2 - 1][1]


def mark_moves(
    rows: list[Row],
    old: list[str],
    new: list[str],
    style: Styler | None = None,
    similarity: float = MOVE_SIMILARITY,
    algorithm: str = MOVE_ALGORITHM,
    passages: bool = MOVE_PASSAGES,
    ps: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS,
) -> None:
    """Find what moved: removed lines that reappear as added lines and, with
    passages, the passages removed in one place and added in another, within
    a line or between two.

    The identical lines (spacing aside) first, each removed line with the
    first unmatched identical added line. Then one matching of the removed
    and the added text left: whole lines removed and added, and the runs of
    changes of the edited lines (passages_of). A pair of whole lines at least
    similarity alike by algorithm (one of MOVE_ALGORITHMS) is a moved line;
    any other pair is a moved passage when the part the two share
    (_core) is at least as alike, and ps tells it from chance likeness. The
    moved lines are taken first, then the passages, each the most alike
    first; what is left of a passage around the part that moved is matched
    again, up to ps.rounds times. Past ps.max_pairs pairs, only those sharing
    rare words are tried (candidate_pairs).

    A moved line becomes a moved-out and a moved-in row. The ends of a moved
    passage stay in their rows, the passage shown as moved there, its words
    no longer counted as removed or added (but for those edited on the way);
    such a row keeps, in without_passages, how it looked before.
    """
    style = style or Styler(False)
    pair = 0  # the moves found, numbered for the HTML report
    removed: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.kind == "delete" and (key := move_key(r.text)):
            removed[key].append(r)
    for r in rows:
        if r.kind == "insert" and (key := move_key(r.text)) and removed.get(key):
            pair += 1
            _make_move(removed[key].pop(0), r, style, pair)

    changed = [r for r in rows if r.kind in ("delete", "insert", "replace")]
    position = {id(r): k for k, r in enumerate(rows)}
    free: list[Passage] = []
    for r in changed:
        o, n = _row_lines(r, old, new)
        if r.kind == "replace":
            if passages:
                free += passages_of(r, o, n, ps)
            continue
        line = o if r.kind == "delete" else n
        as_line = similarity < 1 and bool(move_key(line))
        as_passage = passages and long_enough(line.strip(), ps)
        if as_line or as_passage:
            if as_passage:
                (p,) = passages_of(r, o, n, ps)
            else:
                p = Passage(r, r.kind == "delete", 0, len(line), -1, -1)
            p.whole, p.line, p.passage = True, as_line, as_passage
            free.append(p)
    line_score = line_move_score(similarity, algorithm)
    prepare, score = move_scorer(similarity, algorithm)

    def line_of(p: Passage) -> str:
        return old[p.row.left_no - 1] if p.old else new[p.row.right_no - 1]

    # each passage's words and punctuation, and how many of each
    tokens: dict[Passage, list[tuple[int, int, str]]] = {}
    counts: dict[Passage, Counter] = {}

    def index(found: list[Passage]) -> None:
        for p in found:
            tokens[p] = _tokens(line_of(p), p.start, p.end)
            counts[p] = Counter(t for *_, t in tokens[p])

    index(free)

    def text(p: Passage) -> str:
        return line_of(p)[p.start : p.end]

    def alike(a: str, b: str) -> float:
        # the same words, spacing aside (not move_key: None for any two
        # passages under MIN_MOVE_CHARS, which would make them all alike)
        if _spaced(a) == _spaced(b):
            return 1.0
        return score(prepare(a), prepare(b)) if similarity < 1 else 0.0

    def scored(a: Passage, b: Passage, region: tuple[int, int, int, int]):
        """How alike the parts a1:a2 of a and b1:b2 of b are, cut down to
        what they have in common: (score, a1, a2, b1, b2), or None."""
        la, lb = line_of(a), line_of(b)
        core = _core(la, lb, *region, ps)
        if core is None:
            return None
        a1, a2, b1, b2 = core
        if not (long_enough(la[a1:a2], ps) and long_enough(lb[b1:b2], ps)):
            return None
        if not shares_content(la[a1:a2], lb[b1:b2], ps):
            return None
        s = alike(la[a1:a2], lb[b1:b2])
        return (s, a1, a2, b1, b2) if s else None

    def passage_match(a: Passage, b: Passage) -> tuple[float, int, int, int, int] | None:
        """How alike removed passage a and added passage b are, and the part
        of each that matches: of the whole of both, or of the longer one a
        window as long as the other."""
        ta, tb = tokens[a], tokens[b]
        common = sum((counts[a] & counts[b]).values())
        if common < max(ps.min_words, similarity * min(len(ta), len(tb)) / 2):
            return None
        regions = [(a.start, a.end, b.start, b.end)]
        if min(len(ta), len(tb)) < ps.partial_share * max(len(ta), len(tb)):
            short, long = (ta, tb) if len(ta) < len(tb) else (tb, ta)
            window = fuzz.partial_ratio_alignment([t for *_, t in short], [t for *_, t in long])
            if window and window.dest_end > window.dest_start:
                # a little wider, for _core to find where the match ends
                margin = len(short) // 2
                w1 = long[max(0, window.dest_start - margin)][0]
                w2 = long[min(len(long), window.dest_end + margin) - 1][1]
                regions.append((w1, w2, b.start, b.end) if long is ta else (a.start, a.end, w1, w2))
        found = [m for region in regions if (m := scored(a, b, region))]
        return max(found) if found else None

    def match(a: Passage, b: Passage) -> tuple[tuple, tuple | None] | None:
        """The rank of removed a and added b as a move, and the parts that
        moved (None: the whole lines); None when they are no move. A moved
        line ranks before any passage."""
        if a.line and b.line and (s := line_score(text(a), text(b))):
            return (0, -s, position[id(a.row)], position[id(b.row)]), None
        if a.passage and b.passage and (m := passage_match(a, b)):
            return (1, -m[0], a.start, b.start), m
        return None

    tried: set[tuple[Passage, Passage]] = set()  # the pairs already scored
    for _ in range(ps.rounds):
        outs = [p for p in free if p.old]
        ins = [p for p in free if not p.old]
        if not outs or not ins:
            break
        candidates = []
        for i, j in candidate_pairs(
            outs,
            ins,
            lambda p: content_words(text(p), ps),
            ps.max_pairs,
            ps.rare_min,
            ps.rare_share,
        ):
            a, b = outs[i], ins[j]
            if (a, b) in tried:
                continue
            tried.add((a, b))
            if a.row is b.row and a.first_op <= b.last_op and b.first_op <= a.last_op:
                continue  # the same change: an edit in place, not a move
            if found := match(a, b):
                candidates.append((found[0], a, b, found[1]))
        taken = best_pairs(candidates)
        leftovers: list[Passage] = []
        for a, b, m in taken:
            pair += 1
            if m is None:
                _make_move(a.row, b.row, style, pair)
                continue
            _, a1, a2, b1, b2 = m
            _make_passage_move(a.row, a1, a2, b.row, b1, b2, old, new, style, pair)
            # what is left of the longer one, around the window
            for p, s, e in ((a, a1, a2), (b, b1, b2)):
                for lo, hi in ((p.start, s), (e, p.end)):
                    ops_spanned = (p.first_op, p.last_op)
                    if rest := _passage(p.row, p.old, line_of(p), lo, hi, ops_spanned, ps):
                        leftovers.append(rest)
        if not taken:
            break
        used = {p for a, b, _ in taken for p in (a, b)}
        free = [p for p in free if p not in used] + leftovers
        index(leftovers)
    for r in changed:
        if r.old_moves or r.new_moves:
            r.without_passages = RowView(
                r.left, r.right, list(r.changes), r.words_added, r.words_removed
            )
            _redraw(r, old, new, style)


# A moved passage, and where it went or came from written after it, shown on
# paper only (as for a moved line).
MOVED_SPAN = Markup(
    '<span class="moved" data-pair="{0}" data-move="{1}"{2}>{3}</span>'
    '<span class="print-note passage" aria-hidden="true"> ({1})</span>'
)


def _make_passage_move(
    out: Row,
    a1: int,
    a2: int,
    into: Row,
    b1: int,
    b2: int,
    old: list[str],
    new: list[str],
    style: Styler,
    pair: int,
) -> None:
    """Record passage a1:a2 of row out's old line as moved to passage b1:b2
    of row into's new line: each end's HTML, the passage compared with the
    other, and where it went or came from."""
    o, n = old[out.left_no - 1], new[into.right_no - 1]
    a, b = o[a1:a2], n[b1:b2]
    to_line, from_line = _move_ends(out, into)
    a_styles, b_styles = _slice(style(o), a1, a2), _slice(style(n), b1, b2)
    edited = _spaced(a) != _spaced(b)
    if edited:
        w = word_diff(a, b, a_styles, b_styles)
        left, right = w.left, w.right
        # the words edited on the way count, as in a moved line
        out.passage_words_removed += w.words_removed
        into.passage_words_added += w.words_added
    else:
        left, right = styled(a, a_styles), styled(b, b_styles)
    flag = Markup(" data-move-edited") if edited else Markup("")
    out.old_moves.append(
        MovedSpan(a1, a2, pair, MOVED_SPAN.format(pair, f"Moved to line {to_line}", flag, left))
    )
    into.new_moves.append(
        MovedSpan(
            b1, b2, pair, MOVED_SPAN.format(pair, f"Moved from line {from_line}", flag, right)
        )
    )
    out.move_changes.append(f"moved {quote(a)} to line {to_line}" + (", edited" if edited else ""))
    into.move_changes.append(
        f"moved {quote(b)} from line {from_line}" + (", edited" if edited else "")
    )


def _row_lines(row: Row, old: list[str], new: list[str]) -> tuple[str, str]:
    """A row's old and new line ("" for the side it has none of)."""
    o = old[row.left_no - 1] if row.left_no is not None else ""
    n = new[row.right_no - 1] if row.right_no is not None else ""
    return o, n


def _redraw(row: Row, old: list[str], new: list[str], style: Styler) -> None:
    """A row's HTML, changes and word counts again, its moved passages set
    apart."""
    o, n = _row_lines(row, old, new)
    if row.kind == "replace":
        w = word_diff(o, n, style(o), style(n), row.old_moves, row.new_moves)
        row.left, row.right = w.left, w.right
        row.changes = w.changes + row.move_changes
        row.words_removed = w.words_removed + row.passage_words_removed
        row.words_added = w.words_added + row.passage_words_added
        return
    line, moves = (o, row.old_moves) if row.kind == "delete" else (n, row.new_moves)
    rest = outside(0, len(line), moves)
    pieces = [(m.start, m.html) for m in moves] + [
        (a, styled(line[a:b], _slice(style(line), a, b))) for a, b in rest
    ]
    html = Markup("").join(h for _, h in sorted(pieces, key=lambda p: p[0]))
    words = sum(len(WORD.findall(line[a:b])) for a, b in rest)
    row.changes = [*row.changes[:1], *row.move_changes] if words else row.move_changes
    if row.kind == "delete":
        row.left = html
        row.words_removed = words + row.passage_words_removed
    else:
        row.right = html
        row.words_added = words + row.passage_words_added


Pairing = list[tuple[str, int | None, int | None]]


def line_pairs(
    ops: list[Opcode],
    old: list[str],
    new: list[str],
    move_similarity: float = MOVE_SIMILARITY,
    move_algorithm: str = MOVE_ALGORITHM,
) -> Pairing:
    """The lines of two versions in the order every format shows them, as
    (tag, old index, new index), 0-based: the opcodes' tag ("equal",
    "delete", "insert" or "replace"), None for the side a line is missing
    from; a replaced block's lines paired as pair_lines pairs them. The one
    line pairing of prosediff: the HTML report's rows and the unified diff's
    hunks are both made from it.

    Two lines paired with less than PAIRING_THRESHOLD in common (paired in
    order, or a line replaced by a single line, a rewrite in place) stand
    alone when either one is moved: it matches a line removed or added
    elsewhere, by move_similarity and move_algorithm, as mark_moves will
    find it. A sentence moved into another paragraph is thus not taken for
    a rewrite of the unrelated sentence it lands next to, which may share
    little more than its punctuation and a year in brackets.
    """
    out: Pairing = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            out += [(tag, i1 + t, j1 + t) for t in range(i2 - i1)]
        elif tag == "delete":
            out += [(tag, i, None) for i in range(i1, i2)]
        elif tag == "insert":
            out += [(tag, None, j) for j in range(j1, j2)]
        else:
            out += [
                (tag, None if i is None else i1 + i, None if j is None else j1 + j)
                for i, j in pair_lines(old[i1:i2], new[j1:j2])
            ]
    return unpair_moved(out, old, new, move_similarity, move_algorithm)


def unpair_moved(
    pairs: Pairing, old: list[str], new: list[str], similarity: float, algorithm: str
) -> Pairing:
    """The pairing with each weak pair (less than PAIRING_THRESHOLD in
    common) split in two when either of its lines matches, as a move, a line
    left alone elsewhere or a side of another weak pair."""
    weak = [
        k
        for k, (tag, i, j) in enumerate(pairs)
        if tag == "replace"
        and i is not None
        and j is not None
        and not token_similarity(
            similarity_tokens(old[i]), similarity_tokens(new[j]), PAIRING_THRESHOLD
        )
    ]
    if not weak or similarity > 1:
        return pairs
    alike = line_move_score(similarity, algorithm)

    removed = {i for tag, i, j in pairs if j is None and i is not None}
    added = {j for tag, i, j in pairs if i is None and j is not None}
    weak_old = {pairs[k][1] for k in weak}
    weak_new = {pairs[k][2] for k in weak}
    split = set()
    for k in weak:
        _, i, j = pairs[k]
        others_new = added | (weak_new - {j})
        others_old = removed | (weak_old - {i})
        if any(alike(old[i], new[b]) for b in others_new) or any(
            alike(old[a], new[j]) for a in others_old
        ):
            split.add(k)
    out: Pairing = []
    for k, (tag, i, j) in enumerate(pairs):
        out += [(tag, i, None), (tag, None, j)] if k in split else [(tag, i, j)]
    return out


def align(
    old: list[str],
    new: list[str],
    context: int | None,
    opcodes: list[Opcode] | None = None,
    *,
    markdown: bool = False,
    max_hidden: int | None = MAX_HIDDEN,
    move_similarity: float = MOVE_SIMILARITY,
    move_algorithm: str = MOVE_ALGORITHM,
    old_labels: list[str] | None = None,
    new_labels: list[str] | None = None,
    pairs: Pairing | None = None,
    move_passages: bool = MOVE_PASSAGES,
    moved_passage_settings: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS,
) -> tuple[list[Row], int, int]:
    """Side-by-side rows for two versions of a file, with the line counts.

    context is the number of unchanged lines shown around each change; the
    others are gathered in "skip" rows, which embed at most max_hidden lines
    (None: all). None shows the whole file. opcodes is the line alignment
    (difflib's if not given), pairs the pairing made from it (line_pairs,
    made here if not given). markdown styles the lines for the HTML report's
    formatted view. move_passages also finds the passages moved within a
    line or between two (mark_moves). The counts are the lines added
    and removed; moved lines count in neither, nor do lines whose words all
    moved as passages.
    """
    ops = difflib_opcodes(old, new) if opcodes is None else opcodes
    style = Styler(markdown)
    if all(tag == "equal" for tag, *_ in ops) and not any(
        format_marks(o, style(o), style(n)) for o, n in zip(old, new, strict=False)
    ):
        return [], 0, 0

    def equal_row(i: int, j: int) -> Row:
        """An unchanged row, of old line i and new line j (0-based): its text
        the same, its formatting maybe not."""
        o, n = old[i], new[j]
        if marked := format_marks(o, style(o), style(n)):
            return Row("equal", i + 1, marked[0], j + 1, marked[1], format_changes=marked[2])
        return Row("equal", i + 1, styled(o, style(o)), j + 1, styled(n, style(n)))

    def equal_rows(i: int, j: int, count: int) -> list[Row]:
        """count unchanged rows, from old line i and new line j (0-based)."""
        return [equal_row(i + t, j + t) for t in range(count)]

    def changed_row(i: int | None, j: int | None) -> Row:
        if j is None:
            return deleted_row(i + 1, old[i], style)
        if i is None:
            return inserted_row(j + 1, new[j], style)
        w = word_diff(old[i], new[j], style(old[i]), style(new[j]))
        return Row(
            # no change in the text (only comments came or went): an
            # unchanged line, not an edit
            "replace" if w.changes else "equal",
            i + 1,
            w.left,
            j + 1,
            w.right,
            changes=w.changes,
            words_added=w.words_added,
            words_removed=w.words_removed,
            format_changes=w.format_changes,
        )

    if pairs is None:
        pairs = line_pairs(ops, old, new, move_similarity, move_algorithm)
    # runs of unchanged lines, and of changed ones
    runs = [(equal, list(run)) for equal, run in groupby(pairs, lambda p: p[0] == "equal")]
    rows: list[Row] = []
    for k, (equal, run) in enumerate(runs):
        if not equal:
            rows += [changed_row(i, j) for _, i, j in run]
            continue
        i1, j1, n = run[0][1], run[0][2], len(run)
        lead = 0 if context is None or k == 0 else context
        trail = 0 if context is None or k == len(runs) - 1 else context
        if context is not None and n > lead + trail:
            gap = n - lead - trail
            rows += equal_rows(i1, j1, lead)
            if max_hidden is not None and gap > max_hidden:
                rows.append(Row("skip", omitted=gap))
            else:
                rows.append(Row("skip", hidden=equal_rows(i1 + lead, j1 + lead, gap)))
            rows += equal_rows(i1 + n - trail, j1 + n - trail, trail)
        else:
            rows += equal_rows(i1, j1, n)

    for row in all_rows(rows):
        if row.left_no is not None:
            row.left_label = old_labels[row.left_no - 1] if old_labels else str(row.left_no)
        if row.right_no is not None:
            row.right_label = new_labels[row.right_no - 1] if new_labels else str(row.right_no)
    mark_moves(
        rows,
        old,
        new,
        style,
        move_similarity,
        move_algorithm,
        move_passages,
        moved_passage_settings,
    )
    # The stops of the HTML report's next/previous navigation: a run of changed
    # lines of code, but each changed paragraph of prose (its blank lines
    # are left out, so changed paragraphs are neighbours).
    for prev, r in zip([None, *rows], rows, strict=False):
        r.first_of_change = r.changed and (markdown or not (prev and prev.changed))
    deletions = sum(r.kind in ("delete", "replace") and not r.only_moved for r in rows)
    additions = sum(r.kind in ("insert", "replace") and not r.only_moved for r in rows)
    return rows, additions, deletions


# From bytes to rows -------------------------------------------------------------


def without_blank_lines(lines: list[str], labels: list[str] | None) -> tuple[list[str], list[str]]:
    """The non-blank lines of prose, each with the label of its place in the
    file (its line number, or the one it was given before).

    In Markdown a blank line only separates paragraphs. Left in, every one of
    them matches every other, so an inserted paragraph shifts the alignment
    of all the paragraphs after it, each paired with its neighbour's
    counterpart; without them, the paragraphs themselves are aligned.
    """
    kept = [k for k, line in enumerate(lines) if line.strip()]
    return (
        [lines[k] for k in kept],
        [labels[k] if labels else str(k + 1) for k in kept],
    )


def paragraph_numbers(labels: list[str]) -> list[str]:
    """Line labels ("5", "7", "7.2" ...) renumbered as paragraphs, in order:
    1, 2, 2.2 ... (a line's sentences keep their place after the dot)."""
    numbers: dict[str, str] = {}
    out = []
    for label in labels:
        line, dot, sentence = label.partition(".")
        number = numbers.setdefault(line, str(len(numbers) + 1))
        out.append(number + dot + sentence)
    return out


def set_row_languages(rows: list[Row], old: list[str], new: list[str], language: str) -> bool:
    """Each row's language, on each side: that of the paragraph of a
    document it comes from, if marked. Whether any is not the file's."""
    mixed = False
    for row in all_rows(rows):
        if row.left_no is not None:
            row.left_lang = getattr(old[row.left_no - 1], "lang", "")
        if row.right_no is not None:
            row.right_lang = getattr(new[row.right_no - 1], "lang", "")
        mixed = mixed or any(x and x != language for x in (row.left_lang, row.right_lang))
    return mixed


def remove_marks(line: str, pattern: re.Pattern) -> str:
    """The line without the comment placeholders pattern matches (with the
    blanks around them): one space is left where they stood between two
    words, none at an edge or before punctuation."""

    def gap(m: re.Match[str]) -> str:
        at_edge = m.start() == 0 or m.end() == len(line)
        spaced = any(c in " \t" for c in m.group())
        before_punct = not at_edge and line[m.end()] in ".,;:!?)]}"
        return " " if spaced and not at_edge and not before_punct else ""

    return sub(pattern, gap, line)


def without_shared_comments(
    old: list[str],
    new: list[str],
    old_labels: list[str] | None,
    new_labels: list[str] | None,
    every: bool = False,
) -> tuple[list[str], list[str], list[str] | None, list[str] | None]:
    """The lines without the comments both sides have, moved or not: only
    new and removed comments are shown (every: without any comment at all).
    A comment goes with the spaces around it, leaving one where it stood
    between two words; a line left empty goes too, with its label."""
    in_old, in_new = set(placeholders_in("\n".join(old))), set(placeholders_in("\n".join(new)))
    shared = in_old | in_new if every else in_old & in_new
    if not shared:
        return old, new, old_labels, new_labels
    marks = "".join(sorted(shared))
    pattern = re.compile(f"[ \\t]*(?:[{marks}][ \\t]*)+")

    def strip(line: str) -> str:
        return remove_marks(line, pattern)

    def side(lines: list[str], labels: list[str] | None) -> tuple[list[str], list[str] | None]:
        kept, kept_labels = [], []
        for i, line in enumerate(lines):
            stripped = strip(line)
            if stripped.strip() or not line.strip():
                kept.append(stripped)
                if labels is not None:
                    kept_labels.append(labels[i])
        return kept, kept_labels if labels is not None else None

    old, old_labels = side(old, old_labels)
    new, new_labels = side(new, new_labels)
    return old, new, old_labels, new_labels


# What becomes of the comments (--comments): set apart as markers, compared
# as text, or left out.
COMMENT_MODES = ("markers", "text", "none")


def check_move_similarity(value: float | None) -> None:
    if value is not None and not 0 < value <= 1:
        raise ValueError(f"move similarity must be above 0 and at most 1, not {value}")


@dataclass(frozen=True)
class MoveSettings:
    """When a line counts as moved: at least similarity alike (above 0, at
    most 1: only lines moved unchanged) to where it reappears, as algorithm
    (one of MOVE_ALGORITHMS) measures it. None: prosediff's default for
    such lines, paragraphs or sentences (move_defaults)."""

    similarity: float | None = None
    algorithm: str | None = None

    def check(self) -> None:
        check_move_similarity(self.similarity)
        if self.algorithm is not None:
            check_move_algorithm(self.algorithm)

    def resolved(self, sentences: bool) -> tuple[float, str]:
        """The similarity and the algorithm, the defaults filled in."""
        default_similarity, default_algorithm = move_defaults(sentences)
        return (
            default_similarity if self.similarity is None else self.similarity,
            self.algorithm or default_algorithm,
        )


@dataclass(frozen=True)
class Options:
    """How files are compared and shown: every option of compare() and
    compare_paths() but what is compared.

    context is the number of unchanged lines shown around each change, in
    every file; "auto" (the default) is 0 in Markdown files and Word
    documents, whose lines are paragraphs, and 3 in the others; None shows
    every line. max_hidden caps the unchanged lines embedded per gap.
    md_filter is a shell command both versions of every Markdown file are
    piped through before they are compared. ignore_whitespace compares lines
    as git diff --ignore-all-space does. comments, one of COMMENT_MODES:
    "markers" (the default) shows each comment added or removed, of a Word
    or OpenDocument file or a pandoc comment span of a Markdown file
    ([note]{.comment-start ...}), as a marker whose tooltip is the comment,
    and lists the comments in a panel; "text" compares the comment markup
    as text; "none" leaves every comment out, in every format. Comments
    without text are left out, unless empty_comments. docx_changes settles
    the tracked changes of Word and OpenDocument documents: "accept",
    "reject" or "all" (kept as markup).

    by_sentence compares the prose of Markdown files (and Word documents)
    sentence by sentence instead of line by line; each sentence is labelled
    with its line and its place in it ("12.3"). paragraph_moves says when
    an edited line counts as moved where lines are compared whole
    (paragraphs, in prose), sentence_moves where prose is compared sentence
    by sentence (MoveSettings). move_passages (on by default) also follows the
    passages of text moved within a line or between two, by the same
    measure: a run of words removed in one place and added in another, as
    it was or lightly edited, is shown as moved (mark_moves).
    moved_passage_settings tunes how those passages are told from chance
    likeness (MovedPassageSettings).

    language is that of the prose, whose rules split sentences and
    hyphenate lines: a code (en, it, ...); "document", the language Word and
    OpenDocument files mark their text with (SourceError when another text
    file is compared); "guess", to guess it from each file's text; or
    "default" (the default), a Word or OpenDocument file's own, the others
    guessed. encoding is that of the text files (Word and OpenDocument files
    carry their own): a codec's name, or "auto" (the default), UTF-8 unless
    the file shows it is not, then guessed (decode_text).
    """

    context: Context = "auto"
    max_hidden: int | None = MAX_HIDDEN
    md_filter: str | None = None
    ignore_whitespace: bool = False
    comments: str = "markers"
    empty_comments: bool = False
    docx_changes: str = "accept"
    by_sentence: bool = False
    paragraph_moves: MoveSettings = MoveSettings()
    sentence_moves: MoveSettings = MoveSettings()
    move_passages: bool = MOVE_PASSAGES
    moved_passage_settings: MovedPassageSettings = MOVED_PASSAGE_DEFAULTS
    language: str = DEFAULT
    encoding: str = AUTO_ENCODING

    def checked(self) -> "Options":
        """The options with the language and the encoding in canonical form;
        ValueError for those that make no sense."""
        if self.comments not in COMMENT_MODES:
            raise ValueError(f"comments must be one of {', '.join(COMMENT_MODES)}")
        self.paragraph_moves.check()
        self.sentence_moves.check()
        self.moved_passage_settings.check()
        return replace(
            self,
            language=normalize_language(self.language),
            encoding=check_encoding(self.encoding),
        )

    def move_settings(self, sentences: bool) -> tuple[float, str]:
        """The moved-line similarity and algorithm where lines are sentences
        (or paragraphs, lines): those chosen, else prosediff's defaults."""
        return (self.sentence_moves if sentences else self.paragraph_moves).resolved(sentences)


DEFAULT_OPTIONS = Options()


Labels = list[str] | None


def row_views(rows: list[Row]) -> list["Row | RowView"]:
    """Every HTML of the rows the report may show: each row's (all_rows),
    and how it looks with its moved passages hidden."""
    return [v for row in all_rows(rows) for v in (row, row.without_passages) if v is not None]


def _check_documents(entries: list[tuple[FileDiff, bytes, bytes]]) -> None:
    """For language "document": refuse a text file, which marks none."""
    for fd, old_bytes, new_bytes in entries:
        if not (is_document(fd.old_path) or is_document(fd.new_path)) and not (
            is_binary(old_bytes) or is_binary(new_bytes)
        ):
            raise SourceError(
                f"language document: {fd.path} is not a Word or OpenDocument file, "
                "the kind that records the language of its text; give a language "
                "code (en, it, ...) or guess"
            )


def build_files(
    entries: list[tuple[FileDiff, bytes, bytes]], options: Options = DEFAULT_OPTIONS
) -> list[CommentEntry]:
    """Fill in the rows of every file; returns the comments for the panel.

    Word and OpenDocument texts are read into paragraphs of styled text
    (prosediff.word, prosediff.odt, prosediff.document), compared as lines
    that carry their styles, languages and kinds; a document that cannot be
    read is listed as a binary file, with the reason.
    """
    if options.language == DOCUMENT:
        _check_documents(entries)
    comments = Comments()
    # to leave the comments out, they are first found, as when folding them
    fold = options.comments != "text"
    drop_comments = options.comments == "none"
    # each file to compare: its lines, their labels, and its footnotes
    texts: list[
        tuple[FileDiff, list[str], list[str], Labels, Labels, footnotes.Footnotes | None]
    ] = []
    # The languages a document marks are used, or the one given or guessed.
    marked_languages = options.language in (DOCUMENT, DEFAULT)

    def comment(c: CommentMark) -> str:
        """What a document's comment is in its text: a placeholder, folded;
        nothing, when it has no text to show; or the span pandoc writes."""
        if not fold:
            return comment_markdown(c)
        if not c.text and not options.empty_comments:
            return ""
        mark = comments.placeholder(c.author.replace('"', "'"), c.text, short_date(c.date))
        return mark if mark is not None else comment_markdown(c)

    def document_lines(doc: Document | None) -> list[Line]:
        found = document.lines(doc, comment) if doc is not None else []
        if not marked_languages:
            for line in found:
                line.lang = ""
        return found

    for fd, old_bytes, new_bytes in entries:
        # A Word or OpenDocument side is read into lines of styled text
        # (prosediff.document); a side of any other file is text.
        from_word = is_document(fd.old_path) or is_document(fd.new_path)
        old_doc = new_doc = None
        if from_word:
            try:
                if old_bytes and is_document(fd.old_path):
                    old_doc = read_document(old_bytes, fd.old_path, options.docx_changes)
                if new_bytes and is_document(fd.new_path):
                    new_doc = read_document(new_bytes, fd.new_path, options.docx_changes)
            except SourceError as e:
                fd.binary = True
                fd.note = str(e)
                continue
            fd.markdown = True
            kinds = {
                DOCUMENT_SUFFIXES[Path(p).suffix.lower()]
                for p in (fd.old_path, fd.new_path)
                if is_document(p)
            }
            fd.note = (
                f"read from {' and '.join(sorted(kinds))}, tracked changes "
                + {"accept": "accepted", "reject": "rejected", "all": "shown as markup"}[
                    options.docx_changes
                ]
            )
        if (old_doc is None and is_binary(old_bytes)) or (new_doc is None and is_binary(new_bytes)):
            fd.binary = True
            if fd.old_path:
                fd.old_image = image_uri(fd.old_path, old_bytes)
            if fd.new_path:
                fd.new_image = image_uri(fd.new_path, new_bytes)
            continue
        fd.markdown = fd.markdown or fd.path.lower().endswith(".md")
        old_text = new_text = ""
        old_encoding = new_encoding = ""
        if old_doc is None:
            old_text, old_encoding = decode_text(old_bytes, options.encoding)
        if new_doc is None:
            new_text, new_encoding = decode_text(new_bytes, options.encoding)
        if read_as := " and ".join(dict.fromkeys(e for e in (old_encoding, new_encoding) if e)):
            fd.note = "; ".join(filter(None, (fd.note, f"read as {read_as}")))
        # Folded before filtering, so a filter cannot cut a comment in two.
        if fold and fd.markdown:
            old_text = fold_comments(old_text, comments, options.empty_comments)
            new_text = fold_comments(new_text, comments, options.empty_comments)
        if options.md_filter and fd.markdown:
            if old_text:
                old_text = run_filter(options.md_filter, old_text, fd.path)
            if new_text:
                new_text = run_filter(options.md_filter, new_text, fd.path)
        old_lines = document_lines(old_doc) if old_doc is not None else split_lines(old_text)
        new_lines = document_lines(new_doc) if new_doc is not None else split_lines(new_text)
        if fd.markdown:
            # One language for both sides: the new one's, unless it is gone.
            marked = None
            if marked_languages:
                marked = next((d.language for d in (new_doc, old_doc) if d and d.language), None)
            fd.language, fd.language_source = resolve_language(
                options.language, "\n".join(new_lines or old_lines), marked
            )
        old_labels = new_labels = None
        notes = None
        if options.by_sentence and fd.markdown:
            rules = fd.language or "en"
            old_lines, old_labels = split_sentences(old_lines, rules)
            new_lines, new_labels = split_sentences(new_lines, rules)
        if fd.markdown:
            old_lines, old_labels = without_blank_lines(old_lines, old_labels)
            new_lines, new_labels = without_blank_lines(new_lines, new_labels)
            if from_word:
                # A document has paragraphs, not lines: they are numbered.
                old_labels = paragraph_numbers(old_labels)
                new_labels = paragraph_numbers(new_labels)
            if fold:
                old_lines, new_lines, old_labels, new_labels = without_shared_comments(
                    old_lines, new_lines, old_labels, new_labels, every=drop_comments
                )
            # Footnote numbers set aside: a renumbered footnote is no change.
            old_lines, new_lines, notes = footnotes.set_aside(
                old_lines, new_lines, footnote_similarity
            )
        texts.append((fd, old_lines, new_lines, old_labels, new_labels, notes))

    all_ops = git_opcodes([(old, new) for _, old, new, *_ in texts], options.ignore_whitespace)
    for (fd, old, new, old_labels, new_labels, fn), ops in zip(texts, all_ops, strict=False):
        token = footnotes.use_for_tooltips(fn)
        # prose compared sentence by sentence, or line by line (paragraphs)
        similarity, algorithm = options.move_settings(options.by_sentence and fd.markdown)
        pairs = line_pairs(ops, old, new, similarity, algorithm)
        fd.pairs = [(i, j) for _, i, j in pairs]
        if fd.markdown:
            fd.old_lines = [diff_line(x, fn.old if fn else {}) for x in old]
            fd.new_lines = [diff_line(x, fn.new if fn else {}) for x in new]
            fd.comments = comments
        else:
            fd.old_lines, fd.new_lines = list(old), list(new)
        fd.rows, fd.additions, fd.deletions = align(
            old,
            new,
            context_for(options.context, fd.markdown),
            ops,
            markdown=fd.markdown,
            max_hidden=options.max_hidden,
            move_similarity=similarity,
            move_algorithm=algorithm,
            old_labels=old_labels,
            new_labels=new_labels,
            pairs=pairs,
            move_passages=options.move_passages,
            moved_passage_settings=options.moved_passage_settings,
        )
        footnotes.reset_tooltips(token)
        fd.mixed_languages = set_row_languages(fd.rows, old, new, fd.language)
        if fn is not None and (fn.old or fn.new):
            for view in row_views(fd.rows):
                view.left = footnotes.restore(view.left, fn.old)
                view.right = footnotes.restore(view.right, fn.new)

    panel: list[CommentEntry] = []
    if len(comments):
        for fd, old, new, *_ in texts:
            panel += comment_entries(fd, old, new, comments)
            # added since the base: only the new side has them
            added = frozenset(placeholders_in("\n".join(new))) - frozenset(
                placeholders_in("\n".join(old))
            )
            # The comments both sides had are gone: a row still showing one
            # has a new or removed comment, and is a stop of the navigation
            # like an edited one.
            for r in fd.rows:
                if r.kind != "skip" and (placeholders_in(r.left) or placeholders_in(r.right)):
                    r.first_of_change = True
            for view in row_views(fd.rows):
                view.left = show_comments(view.left, comments)
                view.right = show_comments(view.right, comments, added)
    return panel


def line_markdown(line: str) -> str:
    """A document's line with its formatting written in pandoc's Markdown, as
    --to-markdown writes it (a heading's level as its #s), and its tracked
    changes in CriticMarkup: {++inserted++}, {--deleted--}. Any other line
    as it is."""
    if not isinstance(line, Line):
        return line
    out, k = [], 0
    for key, run in groupby(line.styles, lambda st: st & TEXT_MARKS):
        n = len(list(run))
        text = document.markdown([document.Text(line[k : k + n], key)])
        if "tc-ins" in key:
            text = f"{{++{text}++}}"
        elif "tc-del" in key:
            text = f"{{--{text}--}}"
        out.append(text)
        k += n
    text = "".join(out)
    level = next((int(st[1]) for st in (line.styles[:1] or [()])[0] if HEADING.fullmatch(st)), 0)
    return f"{'#' * level} {text}" if line.kind == "heading" and level else text


def diff_line(line: str, notes: dict[str, str]) -> str:
    """A compared line of prose as the text formats write it: its formatting
    in Markdown (line_markdown) and its footnotes by their own numbers; its
    comments stay placeholders, for comment_text."""
    return footnotes.numbered(line_markdown(line), notes)


def comment_text(line: str, comments: Comments | None) -> str:
    """A line of the text formats with its comments written out in
    CriticMarkup, {>>Author (date): text<<}."""
    if comments is None or not len(comments) or not PLACEHOLDER.search(line):
        return line

    def comment(m: re.Match) -> str:
        c = comments.get(m[0])
        who = " ".join(filter(None, (c.author, f"({c.date})" if c.date else "")))
        return f"{{>>{who}: {c.text}<<}}" if who else f"{{>>{c.text}<<}}"

    return PLACEHOLDER.sub(comment, line)


def comment_entries(
    fd: FileDiff, old: list[str], new: list[str], comments: Comments
) -> list[CommentEntry]:
    """The comments of one file, new, removed or unchanged, each linked to
    the first row that shows it (on the new side, or the old for a removed
    one). A comment in a file without changes, or in a run of unchanged
    lines too long to embed, links to the file."""
    old_set = set(placeholders_in("\n".join(old)))
    new_set = set(placeholders_in("\n".join(new)))
    located: dict[tuple[str, str], Row] = {}
    rows = all_rows(fd.rows)
    for row in rows:
        for side, markup in (("new", row.right), ("old", row.left)):
            for ph in placeholders_in(markup):
                located.setdefault((side, ph), row)
    entries = []
    for ph in sorted(old_set | new_set, key=lambda p: (p not in new_set, p)):
        status = (
            "unchanged"
            if ph in old_set and ph in new_set
            else "new"
            if ph in new_set
            else "removed"
        )
        row = located.get(("old" if status == "removed" else "new", ph))
        anchor, line, label = fd.anchor, None, ""
        if row is not None:
            if not row.anchor:
                row.anchor = f"{fd.anchor}-row{rows.index(row) + 1}"
            anchor = row.anchor
            line = row.left_no if status == "removed" else row.right_no
            label = row.left_label if status == "removed" else row.right_label
        c = comments.get(ph)
        entries.append(CommentEntry(c.author, c.text, status, fd.path, anchor, line, c.date, label))
    entries.sort(key=lambda e: (e.line is None, e.line or 0))
    return entries


# Repository level -------------------------------------------------------------


def in_paths(path: str, paths: list[str] | None) -> bool:
    if not paths:
        return True
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in paths)


def compare(
    repo_path: str | Path,
    base: str,
    target: str | None = None,
    options: Options = DEFAULT_OPTIONS,
    *,
    paths: list[str] | None = None,
    cached: bool = False,
    untracked: bool = False,
) -> Comparison:
    """Compare two versions of the repository at repo_path.

    base and target are anything git resolves to a commit (hash, branch, tag,
    HEAD~2). Without target, base is compared with the working tree, as git
    diff <commit> does, or with the index when cached is set (git diff
    --cached <commit>). untracked adds the untracked files of the working
    tree that .gitignore does not exclude. paths restricts the comparison to
    those paths. options says how the files are compared and shown (Options).
    """
    options = options.checked()
    if cached and target is not None:
        raise ValueError("cached compares a commit with the index: give no target")
    if untracked and (target is not None or cached):
        raise ValueError("untracked files exist only in the working tree: give no target")

    repo = git.Repo(repo_path, search_parent_directories=True)
    root = Path(repo.working_tree_dir or repo.git_dir)
    base_commit = repo.commit(base)
    target_commit = repo.commit(target) if target is not None else None
    other = target_commit if target_commit is not None else (git.INDEX if cached else None)

    def new_side(d) -> bytes:
        if d.deleted_file:
            return b""
        if other is None:
            return (root / d.b_path).read_bytes()
        return blob_bytes(d.b_blob)

    # (file, old bytes, new bytes); diff(None) compares with the working
    # tree, diff(INDEX) with the index. M=True is git diff -M: a renamed file
    # is one entry, not a deletion plus an addition.
    entries: list[tuple[FileDiff, bytes, bytes]] = []
    for d in base_commit.diff(other, paths=paths or None, M=True):
        fd = FileDiff(
            change=CHANGE_NAMES.get(d.change_type, d.change_type),
            old_path=None if d.new_file else d.a_path,
            new_path=None if d.deleted_file else d.b_path,
        )
        entries.append((fd, b"" if d.new_file else blob_bytes(d.a_blob), new_side(d)))
    if untracked:
        for p in repo.untracked_files:
            if in_paths(p, paths):
                entries.append((FileDiff("untracked", None, p), b"", (root / p).read_bytes()))

    panel = build_files(entries, options)
    files = sorted((fd for fd, _, _ in entries), key=lambda f: f.path)

    # The commits the comparison spans. The working tree and the index sit
    # on top of HEAD.
    commits: list[Revision] = []
    total = 0
    try:
        tip = target_commit if target_commit is not None else repo.head.commit
        span = f"{base_commit.hexsha}..{tip.hexsha}"
        total = int(repo.git.rev_list("--count", span))
        commits = [revision(c) for c in repo.iter_commits(span, max_count=MAX_LISTED_COMMITS)]
    except (ValueError, git.GitCommandError):
        pass

    if target_commit is not None:
        target_rev = revision(target_commit)
    else:
        target_rev = INDEX if cached else WORKTREE
    return Comparison(
        repo_name=root.name,
        base=revision(base_commit),
        target=target_rev,
        files=files,
        commits=commits,
        commits_total=total,
        comments=panel,
    )


# Files and folders level --------------------------------------------------------


def _side_revision(path: Path) -> Revision:
    full, short, kind, date = describe_side(path)
    return Revision(hexsha=full, short=short, subject=kind, author="", date=date)


def compare_paths(
    old: str | Path,
    new: str | Path,
    options: Options = DEFAULT_OPTIONS,
    *,
    paths: list[str] | None = None,
    include: str | None = FOLDER_FILES,
) -> Comparison:
    """Compare two files, or two folders, outside git.

    Two files are compared with each other whatever their names. Two folders
    are compared file by file, by path; a file removed from one place and
    added, identical, in another is a rename. paths restricts the comparison
    to those paths within the folders, include to the files matching its
    glob patterns, separated by "|" (by default Word, OpenDocument, Markdown,
    Typst and text files; None or "" for every file), matched against each
    file's name, or its path within the folder for a pattern with a "/",
    ignoring case. options says how the files are compared and shown
    (Options).
    """
    options = options.checked()
    old, new = Path(old), Path(new)
    entries: list[tuple[FileDiff, bytes, bytes]] = []
    if old.is_file() and new.is_file():
        a, b = old.read_bytes(), new.read_bytes()
        if a != b:
            change = "modified" if old.name == new.name else "renamed"
            entries.append((FileDiff(change, old.name, new.name), a, b))
    elif old.is_dir() and new.is_dir():
        a_files, b_files = read_side(old, include), read_side(new, include)
        gone = {p: a_files[p] for p in a_files.keys() - b_files.keys() if in_paths(p, paths)}
        came = {p: b_files[p] for p in b_files.keys() - a_files.keys() if in_paths(p, paths)}
        for p in sorted(a_files.keys() & b_files.keys()):
            if in_paths(p, paths) and a_files[p] != b_files[p]:
                entries.append((FileDiff("modified", p, p), a_files[p], b_files[p]))
        by_content = defaultdict(list)
        for p, data in came.items():
            by_content[data].append(p)
        for p, data in sorted(gone.items()):
            if by_content.get(data):
                q = by_content[data].pop(0)
                came.pop(q)
                entries.append((FileDiff("renamed", p, q), data, data))
            else:
                entries.append((FileDiff("deleted", p, None), data, b""))
        for p, data in sorted(came.items()):
            entries.append((FileDiff("added", None, p), b"", data))
    else:
        for side in (old, new):
            if not side.exists():
                raise SourceError(f"no such file or folder: {side}")
        raise SourceError("compare a file with a file, or a folder with a folder")

    panel = build_files(entries, options)
    return Comparison(
        repo_name=old.name if old.name == new.name else f"{old.name} → {new.name}",
        base=_side_revision(old),
        target=_side_revision(new),
        files=sorted((fd for fd, _, _ in entries), key=lambda f: f.path),
        comments=panel,
    )
