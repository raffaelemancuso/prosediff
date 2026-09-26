"""Compare two versions of a set of files, file by file, into side-by-side rows.

The sides are commits, the index (staged changes) or the working tree of a
git repository, or two files or two folders. git aligns the lines of every
changed file (histogram algorithm, one git process for all files); within a
block of replaced lines, each old line is paired with its most similar new
line and the two are compared word by word, and letter by letter within a
changed word. A removed line that reappears elsewhere in the file, as it was
or lightly edited, is shown as moved.
"""

import base64
import codecs
import difflib
import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Literal

import cchardet
import git
import psutil
from charset_normalizer import from_bytes
from markupsafe import Markup
from rapidfuzz import fuzz
from rapidfuzz.distance import Indel, Levenshtein

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
from prosediff.document import CommentMark, Document, Line, comment_markdown, sub
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
# and its tracked changes; and a heading's style (h1 ... h6).
TEXT_MARKS = frozenset(document.FORMATTING) | {"tc-ins", "tc-del"}
HEADING = re.compile(r"h[1-6]")

# Windows opens a console for a console program (git, cmd.exe) started from
# a process without one, such as the window of prosediff-gui: a terminal
# that flashes up while it runs. This flag starts it without.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# How long git may take to line up every file of a comparison, and a
# filter to rewrite one (seconds): far beyond what either takes on the
# longest manuscript, so that a hung process fails instead of waiting forever.
GIT_TIMEOUT = 300
FILTER_TIMEOUT = 300
# A comment's date as its document stamps it, to the minute.
COMMENT_DATE = re.compile(r"(\d{4}-\d\d-\d\d)T(\d\d:\d\d)")
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

    @property
    def skipped(self) -> int:
        return len(self.hidden) + self.omitted

    @property
    def changed(self) -> bool:
        return self.kind not in ("equal", "skip")


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
    def change_count(self) -> int:
        return sum(r.first_of_change for r in self.rows)

    @property
    def formatted_rows(self) -> int:
        """How many lines' formatting changed."""
        return sum(bool(row.format_changes) for r in self.rows for row in [r, *r.hidden])

    @property
    def anchor(self) -> str:
        return "file-" + re.sub(r"[^A-Za-z0-9_-]", "-", self.path)


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
    def change_count(self) -> int:
        return sum(f.change_count for f in self.files)

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
HEADING_STYLE = re.compile(r"h([1-6])")
FMT = Markup('<span class="fmt" data-fmt="{}">{}</span>')


def formatting(styles: set[str] | frozenset[str]) -> frozenset[str]:
    """The styles of a character that are formatting."""
    return frozenset(s for s in styles if s in FORMATS or HEADING_STYLE.fullmatch(s))


def describe_format(text: str, old: frozenset[str], new: frozenset[str]) -> list[str]:
    """How the formatting of a piece of text changed, in plain English."""
    q = quote(text)
    out = []
    old_heading = next((int(s[1]) for s in old if HEADING_STYLE.fullmatch(s)), 0)
    new_heading = next((int(s[1]) for s in new if HEADING_STYLE.fullmatch(s)), 0)
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
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    ao, bo = _offsets(a), _offsets(b)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [
        (op, ao[i1], ao[i2], bo[j1], bo[j2])
        for op, i1, i2, j1, j2 in merge_across_spaces(matcher.get_opcodes(), a)
    ]


def word_diff(old: str, new: str, old_styles: Styles = None, new_styles: Styles = None) -> WordDiff:
    """Both lines as HTML, the changed tokens wrapped in <del> and <ins>.

    Changes separated only by whitespace are one change. A word changed into
    a similar word ("repeat" -> "repeated") is marked class="partial", with
    only the changed letters in <mark>. The styles, if given, are the
    Markdown styles of each character of the two lines.
    """
    left, right, changes, formats = [], [], [], []
    added = removed = 0
    for op, o1, o2, n1, n2 in word_ops(old, new):
        old_part, new_part = old[o1:o2], new[n1:n2]
        old_st, new_st = _slice(old_styles, o1, o2), _slice(new_styles, n1, n2)
        if op == "equal" or comments_only(old_part, new_part):
            # Comment markers are not text: whether a comment is new, gone or
            # moved (to the next paragraph, when its own was deleted) shows in
            # its icon and in the comments panel, not as a changed word.
            if op == "equal" and (marked := format_marks(old_part, old_st, new_st)):
                left.append(marked[0])
                right.append(marked[1])
                formats += marked[2]
                continue
            left.append(styled(old_part, old_st))
            right.append(styled(new_part, new_st))
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
            left.append(PARTIAL_DEL.format(marks[0]))
            right.append(PARTIAL_INS.format(marks[1]))
            continue
        if old_part:
            left.append(DEL.format(styled(old_part, old_st)))
        if new_part:
            right.append(INS.format(styled(new_part, new_st)))
    return WordDiff(Markup("").join(left), Markup("").join(right), changes, added, removed, formats)


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


def similarity(old: str, new: str) -> float:
    """Similarity of two lines, 0 to 1; 0 below the pairing threshold."""
    return token_similarity(similarity_tokens(old), similarity_tokens(new), PAIRING_THRESHOLD)


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


def move_key(line: str) -> str | None:
    """What identifies a line as moved: its words, spacing aside."""
    key = " ".join(line.split())
    return key if len(key.replace(" ", "")) >= MIN_MOVE_CHARS else None


def _make_move(out: Row, into: Row, style: Styler) -> None:
    out.kind, into.kind = "moved-out", "moved-in"
    to_line = into.right_label or f"{into.right_no:,}"
    from_line = out.left_label or f"{out.left_no:,}"
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


def _spaced(line: str) -> str:
    return " ".join(line.split())


def _sorted_tokens(line: str) -> list[str]:
    return sorted(similarity_tokens(line))


# How alike two lines are for the moved-line matching, 0 to 1, each measure
# as (what a line becomes before it is compared, the score of two of them,
# 0 below the cutoff). All of rapidfuzz, in C.
MOVE_ALGORITHMS = {
    # words and punctuation in common, in order: 2 x longest common
    # subsequence / total length
    "tokens": (
        similarity_tokens,
        lambda a, b, cutoff: Indel.normalized_similarity(a, b, score_cutoff=cutoff),
    ),
    # characters in common, in order (spacing aside)
    "chars": (
        _spaced,
        lambda a, b, cutoff: Indel.normalized_similarity(a, b, score_cutoff=cutoff),
    ),
    # words and punctuation: 1 - edits (insertions, deletions, substitutions)
    # / length of the longer line
    "levenshtein": (
        similarity_tokens,
        lambda a, b, cutoff: Levenshtein.normalized_similarity(a, b, score_cutoff=cutoff),
    ),
    # words and punctuation in common, whatever their order
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
MOVE_TOLERANCE = 1e-9
MOVE_MARGIN = 0.01


def check_move_algorithm(name: str) -> None:
    if name not in MOVE_ALGORITHMS:
        raise ValueError(
            f"move algorithm must be one of {', '.join(MOVE_ALGORITHMS)}, not {name!r}"
        )


def mark_moves(
    rows: list[Row],
    style: Styler | None = None,
    similarity: float = MOVE_SIMILARITY,
    algorithm: str = MOVE_ALGORITHM,
) -> None:
    """Turn a removed line that reappears as an added line into a move.

    First the identical lines (spacing aside), each removed line with the
    first unmatched identical added line; then the edited ones at least
    similarity alike (1: none) by algorithm (one of MOVE_ALGORITHMS), the
    most similar pairs first.
    """
    style = style or Styler(False)
    removed: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.kind == "delete" and (key := move_key(r.text)):
            removed[key].append(r)
    for r in rows:
        if r.kind == "insert" and (key := move_key(r.text)) and removed.get(key):
            _make_move(removed[key].pop(0), r, style)

    outs = [r for r in rows if r.kind == "delete" and move_key(r.text)]
    ins = [r for r in rows if r.kind == "insert" and move_key(r.text)]
    if similarity >= 1 or not outs or not ins or len(outs) * len(ins) > MOVE_MAX_CELLS:
        return
    prepare, score = MOVE_ALGORITHMS[algorithm]
    out_items = [prepare(r.text) for r in outs]
    in_items = [prepare(r.text) for r in ins]
    # "at least similarity": rapidfuzz's cutoff can turn down a score right
    # at it, so the scores are taken from a little below and then checked
    cutoff = max(0.0, similarity - MOVE_MARGIN)
    candidates = []
    for a, ta in enumerate(out_items):
        for b, tb in enumerate(in_items):
            s = score(ta, tb, cutoff)
            if s and s >= similarity - MOVE_TOLERANCE:
                candidates.append((s, a, b))
    used_out, used_in = set(), set()
    for _, a, b in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        if a not in used_out and b not in used_in:
            used_out.add(a)
            used_in.add(b)
            _make_move(outs[a], ins[b], style)


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
    prepare, score = MOVE_ALGORITHMS[algorithm]
    cutoff = max(0.0, similarity - MOVE_MARGIN)

    def alike(a: str, b: str) -> bool:
        key_a, key_b = move_key(a), move_key(b)
        if not key_a or not key_b:
            return False
        if key_a == key_b:
            return True
        return similarity < 1 and score(prepare(a), prepare(b), cutoff) >= (
            similarity - MOVE_TOLERANCE
        )

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
) -> tuple[list[Row], int, int]:
    """Side-by-side rows for two versions of a file, with the line counts.

    context is the number of unchanged lines shown around each change; the
    others are gathered in "skip" rows, which embed at most max_hidden lines
    (None: all). None shows the whole file. opcodes is the line alignment
    (difflib's if not given), pairs the pairing made from it (line_pairs,
    made here if not given). markdown styles the lines for the HTML report's
    formatted view. The counts are the lines added and removed; moved lines
    count in neither.
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

    for r in rows:
        for row in [r, *r.hidden]:
            if row.left_no is not None:
                row.left_label = old_labels[row.left_no - 1] if old_labels else str(row.left_no)
            if row.right_no is not None:
                row.right_label = new_labels[row.right_no - 1] if new_labels else str(row.right_no)
    mark_moves(rows, style, move_similarity, move_algorithm)
    # The stops of the HTML report's next/previous navigation: a run of changed
    # lines of code, but each changed paragraph of prose (its blank lines
    # are left out, so changed paragraphs are neighbours).
    for prev, r in zip([None, *rows], rows, strict=False):
        r.first_of_change = r.changed and (markdown or not (prev and prev.changed))
    deletions = sum(r.kind in ("delete", "replace") for r in rows)
    additions = sum(r.kind in ("insert", "replace") for r in rows)
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
    for r in rows:
        for row in [r, *r.hidden]:
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


def build_files(
    entries: list[tuple[FileDiff, bytes, bytes]],
    *,
    context: Context,
    md_filter: str | None,
    ignore_whitespace: bool,
    fold: bool,
    empty_comments: bool = False,
    drop_comments: bool = False,
    max_hidden: int | None,
    docx_changes: str,
    move_similarity: float = MOVE_SIMILARITY,
    move_algorithm: str = MOVE_ALGORITHM,
    by_sentence: bool = False,
    language: str = DEFAULT,
    encoding: str = AUTO_ENCODING,
) -> list[CommentEntry]:
    """Fill in the rows of every file; returns the comments for the panel.

    Word and OpenDocument texts are read into paragraphs of styled text
    (prosediff.word, prosediff.odt, prosediff.document), compared as lines
    that carry their styles, languages and kinds; a document that cannot be
    read is listed as a binary file, with the reason.
    """
    if language == DOCUMENT:
        for fd, old_bytes, new_bytes in entries:
            if not (is_document(fd.old_path) or is_document(fd.new_path)) and not (
                is_binary(old_bytes) or is_binary(new_bytes)
            ):
                raise SourceError(
                    f"language document: {fd.path} is not a Word or OpenDocument file, "
                    "the kind that records the language of its text; give a language "
                    "code (en, it, ...) or guess"
                )
    comments = Comments()
    # to leave the comments out, they are first found, as when folding them
    fold = fold or drop_comments
    texts: list[tuple[FileDiff, list[str], list[str]]] = []
    labels: dict[int, tuple[list[str] | None, list[str] | None]] = {}
    notes: dict[int, footnotes.Footnotes] = {}
    # The languages a document marks are used, or the one given or guessed.
    marked_languages = language in (DOCUMENT, DEFAULT)

    def comment(c: CommentMark) -> str:
        """What a document's comment is in its text: a placeholder, folded;
        nothing, when it has no text to show; or the span pandoc writes."""
        if not fold:
            return comment_markdown(c)
        if not c.text and not empty_comments:
            return ""
        when = COMMENT_DATE.match(c.date)
        mark = comments.placeholder(
            c.author.replace('"', "'"), c.text, f"{when[1]} {when[2]}" if when else ""
        )
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
                    old_doc = read_document(old_bytes, fd.old_path, docx_changes)
                if new_bytes and is_document(fd.new_path):
                    new_doc = read_document(new_bytes, fd.new_path, docx_changes)
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
                    docx_changes
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
            old_text, old_encoding = decode_text(old_bytes, encoding)
        if new_doc is None:
            new_text, new_encoding = decode_text(new_bytes, encoding)
        if read_as := " and ".join(dict.fromkeys(e for e in (old_encoding, new_encoding) if e)):
            fd.note = "; ".join(filter(None, (fd.note, f"read as {read_as}")))
        # Folded before filtering, so a filter cannot cut a comment in two.
        if fold and fd.markdown:
            old_text = fold_comments(old_text, comments, empty_comments)
            new_text = fold_comments(new_text, comments, empty_comments)
        if md_filter and fd.markdown:
            if old_text:
                old_text = run_filter(md_filter, old_text, fd.path)
            if new_text:
                new_text = run_filter(md_filter, new_text, fd.path)
        old_lines = document_lines(old_doc) if old_doc is not None else split_lines(old_text)
        new_lines = document_lines(new_doc) if new_doc is not None else split_lines(new_text)
        if fd.markdown:
            # One language for both sides: the new one's, unless it is gone.
            marked = None
            if marked_languages:
                marked = next((d.language for d in (new_doc, old_doc) if d and d.language), None)
            fd.language, fd.language_source = resolve_language(
                language, "\n".join(new_lines or old_lines), marked
            )
        old_labels = new_labels = None
        if by_sentence and fd.markdown:
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
            old_lines, new_lines, notes[id(fd)] = footnotes.set_aside(
                old_lines, new_lines, footnote_similarity
            )
        labels[id(fd)] = (old_labels, new_labels)
        texts.append((fd, old_lines, new_lines))

    all_ops = git_opcodes([(old, new) for _, old, new in texts], ignore_whitespace)
    for (fd, old, new), ops in zip(texts, all_ops, strict=False):
        fn = notes.get(id(fd))
        token = footnotes.use_for_tooltips(fn)
        pairs = line_pairs(ops, old, new, move_similarity, move_algorithm)
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
            context_for(context, fd.markdown),
            ops,
            markdown=fd.markdown,
            max_hidden=max_hidden,
            move_similarity=move_similarity,
            move_algorithm=move_algorithm,
            old_labels=labels[id(fd)][0],
            new_labels=labels[id(fd)][1],
            pairs=pairs,
        )
        footnotes.reset_tooltips(token)
        fd.mixed_languages = set_row_languages(fd.rows, old, new, fd.language)
        if fn is not None and (fn.old or fn.new):
            for r in fd.rows:
                for row in [r, *r.hidden]:
                    row.left = footnotes.restore(row.left, fn.old)
                    row.right = footnotes.restore(row.right, fn.new)

    panel: list[CommentEntry] = []
    if len(comments):
        for fd, old, new in texts:
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
            for r in fd.rows:
                for row in [r, *r.hidden]:
                    row.left = show_comments(row.left, comments)
                    row.right = show_comments(row.right, comments, added)
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
        text = document.markdown([document.Text(str.__getitem__(line, slice(k, k + n)), key)])
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
    return footnotes.STAND_IN.sub(lambda m: f"[^{notes.get(m[0], '?')}]", line_markdown(line))


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
    rows = [row for r in fd.rows for row in ([r, *r.hidden])]
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


def check_move_similarity(value: float) -> None:
    if not 0 < value <= 1:
        raise ValueError(f"move similarity must be above 0 and at most 1, not {value}")


def in_paths(path: str, paths: list[str] | None) -> bool:
    if not paths:
        return True
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in paths)


def compare(
    repo_path: str | Path,
    base: str,
    target: str | None = None,
    paths: list[str] | None = None,
    context: Context = "auto",
    md_filter: str | None = None,
    *,
    cached: bool = False,
    untracked: bool = False,
    ignore_whitespace: bool = False,
    fold_comments_md: bool = True,
    empty_comments: bool = False,
    drop_comments: bool = False,
    max_hidden: int | None = MAX_HIDDEN,
    docx_changes: str = "accept",
    move_similarity: float = MOVE_SIMILARITY,
    move_algorithm: str = MOVE_ALGORITHM,
    by_sentence: bool = False,
    language: str = DEFAULT,
    encoding: str = AUTO_ENCODING,
) -> Comparison:
    """Compare two versions of the repository at repo_path.

    base and target are anything git resolves to a commit (hash, branch, tag,
    HEAD~2). Without target, base is compared with the working tree, as git
    diff <commit> does, or with the index when cached is set (git diff
    --cached <commit>). untracked adds the untracked files of the working
    tree that .gitignore does not exclude. paths restricts the comparison to
    those paths. md_filter is a shell command both versions of every
    Markdown file are piped through before they are compared.
    ignore_whitespace compares lines as git diff --ignore-all-space does.
    fold_comments_md (on by default) shows each pandoc comment span of a
    Markdown file, [note]{.comment-start ...}, as a marker whose tooltip is
    the comment, and lists the comments in a panel; off, the comment markup
    is compared as text. Comments without text are left out, unless
    empty_comments. drop_comments leaves every comment out, in every format.
    context is the number of unchanged lines shown around each change, in
    every file; "auto" (the default) is 0 in Markdown files
    and Word documents, whose lines are paragraphs, and 3 in the others;
    None shows every line. max_hidden caps
    the unchanged lines embedded per gap. docx_changes settles the tracked changes of Word
    documents: "accept", "reject" or "all" (kept as markup).
    move_similarity is how alike, from 0 to 1, an edited line must be to
    where it reappears to count as moved (1: only lines moved unchanged), by
    move_algorithm, one of MOVE_ALGORITHMS.
    by_sentence compares the prose of Markdown files (and Word documents)
    sentence by sentence instead of line by line; each sentence is labelled
    with its line and its place in it ("12.3"). language is that of their
    prose, whose rules split sentences and hyphenate lines: a code (en, it,
    ...); "document", the language Word and OpenDocument files mark their
    text with (SourceError when another text file is compared); "guess", to
    guess it from each file's text; or "default" (the default), a Word or
    OpenDocument file's own, the others guessed. encoding is that
    of the text files (Word and OpenDocument files carry their own): a
    codec's name, or "auto" (the default), UTF-8 unless the file shows it is
    not, then guessed (decode_text).
    """
    check_move_similarity(move_similarity)
    check_move_algorithm(move_algorithm)
    language = normalize_language(language)
    encoding = check_encoding(encoding)
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

    panel = build_files(
        entries,
        context=context,
        md_filter=md_filter,
        ignore_whitespace=ignore_whitespace,
        fold=fold_comments_md,
        empty_comments=empty_comments,
        drop_comments=drop_comments,
        max_hidden=max_hidden,
        docx_changes=docx_changes,
        move_similarity=move_similarity,
        move_algorithm=move_algorithm,
        by_sentence=by_sentence,
        language=language,
        encoding=encoding,
    )
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
    paths: list[str] | None = None,
    context: Context = "auto",
    md_filter: str | None = None,
    *,
    ignore_whitespace: bool = False,
    fold_comments_md: bool = True,
    empty_comments: bool = False,
    drop_comments: bool = False,
    max_hidden: int | None = MAX_HIDDEN,
    docx_changes: str = "accept",
    move_similarity: float = MOVE_SIMILARITY,
    move_algorithm: str = MOVE_ALGORITHM,
    by_sentence: bool = False,
    language: str = DEFAULT,
    encoding: str = AUTO_ENCODING,
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
    ignoring case. The other options are those of compare().
    """
    check_move_similarity(move_similarity)
    check_move_algorithm(move_algorithm)
    language = normalize_language(language)
    encoding = check_encoding(encoding)
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

    panel = build_files(
        entries,
        context=context,
        md_filter=md_filter,
        ignore_whitespace=ignore_whitespace,
        fold=fold_comments_md,
        empty_comments=empty_comments,
        drop_comments=drop_comments,
        max_hidden=max_hidden,
        docx_changes=docx_changes,
        move_similarity=move_similarity,
        move_algorithm=move_algorithm,
        by_sentence=by_sentence,
        language=language,
        encoding=encoding,
    )
    return Comparison(
        repo_name=old.name if old.name == new.name else f"{old.name} → {new.name}",
        base=_side_revision(old),
        target=_side_revision(new),
        files=sorted((fd for fd, _, _ in entries), key=lambda f: f.path),
        comments=panel,
    )
