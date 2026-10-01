"""A window to choose what to compare; it writes the HTML report and opens it.

What is compared is chosen with a segmented button: a git repository (base
and target picked among its latest commits, the working tree and the index,
or typed as any ref), two files, or two folders; or one file alone, for an
AI to review, nothing compared. The options that change
what the comparison finds sit in one card, each explained by a tooltip; how
the report shows it, with the settings few change, under Advanced settings;
the output (an HTML report, a unified or word diff, or tracked changes) in
a card of its own. The comparison runs in a
background thread, a progress bar running meanwhile, so the window stays
responsive; a notification tells when it is done. The choices are
remembered for the next time only when asked (Save options), and Reset to
defaults puts every option back.

The widgets are ttkbootstrap's, in its Bootstrap theme: light or dark as the
system is set (Windows' app mode, macOS's appearance), the title bar too on
Windows; switches for the yes-or-no options, Bootstrap icons on the buttons.
"""

import contextlib
import ctypes
import json
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk as tk_ttk

import git
import psutil
import ttkbootstrap as ttk

from prosediff.assess import (
    CLAUDE_DEFAULT,
    CONTEXTS,
    AssessError,
    Assessment,
    AssessRequest,
    ModelInfo,
    duration,
    models_of,
    parse_backend,
    providers,
)
from prosediff.diff import (
    AUTO_ENCODING,
    COMMENT_MODES,
    MOVE_ALGORITHMS,
    Comparison,
    Context,
    FilterError,
    MovedPassageSettings,
    MoveSettings,
    Options,
    check_encoding,
    compare,
    compare_paths,
    compare_split,
    move_defaults,
    review_file,
    setting_type,
)
from prosediff.language import DEFAULT, DOCUMENT, GUESS, language_name, normalize_language
from prosediff.render import (
    ALIGNMENTS,
    FORMATS,
    SPLITS,
    TEXT_SUFFIXES,
    assess_comparison,
    check_split,
    counted,
    default_output,
    default_split,
    format_of,
    open_output,
    write_output,
)
from prosediff.sources import (
    DOCX_CHANGES,
    FOLDER_FILES,
    SourceError,
    default_page,
    review_page,
)
from prosediff.tracked import TRACKED_FORMATS, check_paths

MAX_COMMITS = 200
ENCODINGS = (AUTO_ENCODING, "utf-8", "cp1252", "latin-1", "utf-16", "cp1250", "cp1251")
# The choices, then the common codes; any code can be typed.
LANGUAGES = (DEFAULT, DOCUMENT, GUESS)
LANGUAGES += ("en", "it", "de", "fr", "es", "pt", "nl", "pl", "sv", "da", "fi", "cs", "el")
# The AI assessment: the AI (none, the two subscriptions, Ollama, then the
# other providers any-llm reaches, found in the background) and its model,
# chosen among those the AI reports (models_of), its own default first;
# any can be typed.
NO_ASSESSMENT = "none"
# What the model and effort fields say while the AI reports its models.
LOADING = "Loading…"
# The effort item of a model that says no default of its own: that default.
MODEL_DEFAULT = "default"
AIS = (NO_ASSESSMENT, "claude", "codex", "ollama")
AI_HINTS = {
    NO_ASSESSMENT: "No assessment.",
    "claude": "Claude Code, on your Claude login (prosediff[claude]).",
    "codex": "ChatGPT through Codex, on your ChatGPT login (prosediff[codex]); log in "
    "once with prosediff --login-codex.",
    "ollama": "A local Ollama model: nothing leaves this computer (prosediff[models]).",
}
# What each item of the drop-down lists means (item_hints).
ENCODING_HINTS = {
    AUTO_ENCODING: "UTF-8, unless a file cannot be read in it; then guessed.",
    "utf-8": "Unicode, the usual encoding today.",
    "cp1252": "Windows, Western European languages.",
    "latin-1": "ISO 8859-1, Western European languages.",
    "utf-16": "Unicode in two bytes a character, as some Windows programs save text.",
    "cp1250": "Windows, Central European languages.",
    "cp1251": "Windows, Cyrillic.",
}
LANGUAGE_HINTS = {
    DEFAULT: "The language Word and OpenDocument files are marked with (guessed when they "
    "mark none); guessed for the other files.",
    DOCUMENT: "The language Word and OpenDocument files are marked with.",
    GUESS: "Guessed from each file's text.",
}
# How the tracked changes are settled (DOCX_CHANGES), as the list names it.
DOCX_CHANGE_LABELS = {"accept-all": "accept all", "reject-all": "reject all", "show": "show"}
DOCX_CHANGE_VALUES = {label: value for value, label in DOCX_CHANGE_LABELS.items()}
DOCX_CHANGE_HINTS = {
    "accept-all": "Compare the documents with every tracked change accepted.",
    "reject-all": "Compare the documents with every tracked change rejected.",
    "show": "Show the tracked changes as Word does: insertions and deletions marked.",
}
COMMENT_HINTS = {
    "markers": "Only the comments added or removed, set apart: a marker and a panel in the "
    "HTML report, CriticMarkup in the diffs.",
    "text": "Compared as part of the text, as pandoc writes them.",
    "none": "Left out.",
}
ALIGNMENT_HINTS = {
    "left": "Aligned on the left, ragged on the right.",
    "justify": "Justified on both sides, hyphenated.",
}
MOVE_ALGORITHM_HINTS = {
    "token-sort": "The share of their words and punctuation in common, whatever their order.",
    "token-set": "The words both share against the rest of each, whatever their order: a "
    "line inside a longer one scores high.",
}
WORKTREE = "Working tree (uncommitted changes)"
INDEX = "Index (staged changes)"


@dataclass
class Choice:
    """One entry of the base and target lists."""

    label: str
    ref: str  # a commit hash, or "worktree" / "index"


def list_choices(repo_path: str | Path) -> tuple[list[Choice], bool]:
    """The latest commits of a repository, newest first, and whether its
    working tree has uncommitted changes (tracked files)."""
    repo = git.Repo(repo_path, search_parent_directories=True)
    commits = []
    for c in repo.iter_commits(max_count=MAX_COMMITS):
        date = c.committed_datetime.strftime("%Y-%m-%d")
        commits.append(Choice(f"{c.hexsha[:7]}  {date}  {c.author.name}  {c.summary}", c.hexsha))
    return commits, repo.is_dirty(untracked_files=False)


def default_sides(commits: list[Choice], dirty: bool) -> tuple[str, str]:
    """(base, target) refs to start from: the uncommitted changes when there
    are any, otherwise the last commit."""
    if not commits:
        return "", ""
    if dirty:
        return commits[0].ref, "worktree"
    return (commits[1].ref if len(commits) > 1 else commits[0].ref), commits[0].ref


@dataclass
class Settings:
    """Everything the window lets one choose."""

    mode: str = "git"  # one of MODES, the tab shown
    repo: str = ""
    base: str = ""  # a ref
    target: str = ""  # a ref, "worktree" or "index"
    untracked: bool = False
    paths: list[str] = field(default_factory=list)
    old: str = ""  # the two files
    new: str = ""
    old_folder: str = ""  # the two folders
    new_folder: str = ""
    single: str = ""  # the one file an AI reviews alone (the tab "review")
    # the files of two folders compared: glob patterns separated by "|"
    include: str = FOLDER_FILES
    # "markers": set apart from the text (a marker and a panel in the HTML
    # report, CriticMarkup in the diffs); "text": compared as text; "none"
    comments: str = "markers"
    empty_comments: bool = False
    docx_changes: str = "accept-all"
    align: str = "justify"
    # "auto": 0 for Markdown files and Word documents, 3 for the others; a
    # number applies to every file
    context_lines: str = "auto"
    full: bool = False
    ignore_whitespace: bool = False
    # The moved-line matching of paragraphs (lines of other files), and of
    # sentences; None: prosediff's default (diff.move_defaults), whatever it
    # is when the window runs; a value only when one was chosen
    move_similarity: float | None = None
    move_algorithm: str | None = None
    sentence_move_similarity: float | None = None
    sentence_move_algorithm: str | None = None
    # passages moved within a line or between two, followed too
    move_passages: bool = True
    # how moved passages are told from chance likeness (the advanced settings):
    # field of MovedPassageSettings -> value, only for those changed from
    # prosediff's default, which the others follow whatever it becomes
    moved_passages: dict[str, float] = field(default_factory=dict)
    # how prose is compared: one of SPLITS
    split: str = "both"
    # a language code; "document": marked in Word and OpenDocument files;
    # "guess": guessed from each file's text; "default": document, else guess
    language: str = DEFAULT
    # a codec's name, or "auto": UTF-8 unless a file shows it is not
    encoding: str = AUTO_ENCODING
    output: str = ""
    # "html": the HTML report; "diff", "wdiff": a unified or word diff
    # (prosediff.unified); "docx", "odt": a document of tracked changes
    # (prosediff.tracked)
    output_format: str = "html"
    open_page: bool = True
    # the AI that assesses the changes (prosediff.assess): "claude", "codex",
    # "PROVIDER/MODEL"; "": none; how hard it thinks ("": its default), what
    # it reads (one of CONTEXTS), the instructions (text, or a file), and
    # whether the text sent to it is saved beside the output
    assess: str = ""
    assess_effort: str = ""
    assess_context: str = "document"
    assess_instructions: str = ""
    assess_save_prompt: bool = False
    # whether the AI marks the problems in the text
    assess_annotate: bool = True
    # whether the report is first shown without the assessment, and the AI
    # asked only once that preview is approved
    assess_preview: bool = True
    # whether the AI is also asked, apart, if the new text reads as written by
    # an AI
    assess_ai_writing: bool = False
    # whether the report holds the documents made of the problems the AI
    # marked in a Word document or an OpenDocument text (prosediff.aidocs)
    assess_documents: bool = True


READY = "Choose what to compare, then Compare."
REVIEW_READY = "Choose the file, and the AI to review it, then Review."
# The tooltips: their width in pixels, and how long the pointer must rest.
HINT_WIDTH = 360
HINT_DELAY_MS = 400
# How long the notification of a finished comparison stays up.
TOAST_MS = 4000
# The tabs, in their order: the last reviews one file, nothing compared.
MODES = ("git", "files", "folders", "review")
PREFILLED_FILES = (".md", ".docx", ".odt")
# The space around the fields of the window.
PAD = {"padx": 6, "pady": 4}


def single_file(args: list[str]) -> Path | None:
    """The one Markdown, Word or OpenDocument file given, whose partner the
    window asks for."""
    if len(args) == 1:
        path = Path(args[0])
        if path.is_file() and path.suffix.lower() in PREFILLED_FILES:
            return path.resolve()
    return None


def with_second_file(s: Settings, first: Path, second: Path) -> Settings:
    """The settings comparing two files, the older (by modification time)
    on the left: the draft sent before the one returned. The HTML report goes next
    to the newer."""
    older, newer = sorted((first, second), key=lambda p: (p.stat().st_mtime, str(p)))
    return replace(s, mode="files", old=str(older.resolve()), new=str(newer.resolve()), output="")


def settings_from_args(args: list[str], base: Settings) -> tuple[Settings, str]:
    """The settings to open the window with, given its command-line arguments.

    One argument that is a git repository (or a folder inside one) fills in
    the repository, the sides starting from their defaults; one Markdown,
    Word or OpenDocument file fills in the files tab, its partner to be
    chosen when the window opens, and the One file tab, to review it alone
    instead; two such files fill in the files tab, two
    folders the folders tab. Anything else is ignored, and the second value says why.
    """
    s = replace(base)
    if len(args) == 1:
        path = Path(args[0])
        if single_file(args):
            # the other file is asked for when the window opens (main)
            s.mode = "files"
            s.old, s.new = str(path.resolve()), ""
            s.single = str(path.resolve())
            s.output = ""
            return s, ""
        if path.is_dir():
            try:
                repo = git.Repo(path, search_parent_directories=True)
            except (git.InvalidGitRepositoryError, git.NoSuchPathError):
                return s, f"Not a git repository: {path}"
            s.mode = "git"
            s.repo = str(Path(repo.working_tree_dir or path))
            s.base = s.target = ""  # start from the defaults
            s.paths = []
            return s, ""
        return s, f"Not a folder, a Markdown, Word or OpenDocument file: {path}"
    if len(args) == 2:
        old, new = Path(args[0]), Path(args[1])
        both_files = all(p.is_file() and p.suffix.lower() in PREFILLED_FILES for p in (old, new))
        if both_files:
            s.mode = "files"
            s.old, s.new = str(old.resolve()), str(new.resolve())
            s.output = ""  # next to the new one (App.follow_sides)
            return s, ""
        if old.is_dir() and new.is_dir():
            s.mode = "folders"
            s.old_folder, s.new_folder = str(old.resolve()), str(new.resolve())
            s.output = ""  # into the new one (App.follow_sides)
            return s, ""
        return s, "Two arguments must be two Markdown, Word or OpenDocument files, or two folders."
    if args:
        return (
            s,
            "Give one git repository, two Markdown, Word or OpenDocument files, or two folders.",
        )
    return s, ""


def settings_file() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "prosediff" / "gui.json"


def load_settings(path: Path | None = None) -> Settings:
    """The choices saved (Save options), or the defaults; a value that is
    no choice the window offers gives way to its default."""
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
        known = Settings.__dataclass_fields__
        s = Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()
    if s.mode not in MODES:
        s.mode = "git"
    if s.split not in SPLITS:
        s.split = "both"
    if s.comments not in COMMENT_MODES:
        s.comments = "markers"
    if s.docx_changes not in DOCX_CHANGES:
        s.docx_changes = "accept-all"
    known_passage = {f.name for f in fields(MovedPassageSettings)}
    if not isinstance(s.moved_passages, dict):
        s.moved_passages = {}
    s.moved_passages = {
        k: v
        for k, v in s.moved_passages.items()
        if k in known_passage and isinstance(v, int | float) and not isinstance(v, bool)
    }
    return s


def save_settings(s: Settings, path: Path | None = None) -> bool:
    """Write the settings, when asked (the window's Save options); whether
    they were written."""
    path = path or settings_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8", newline="\n")
    except OSError:
        return False
    return True


def context_of(s: Settings) -> Context:
    """The context for compare(), from the Context lines box."""
    if s.full:
        return None
    try:
        return max(0, int(s.context_lines))
    except ValueError:  # "auto", or anything that is not a number
        return "auto"


def generate(
    s: Settings,
    progress: Callable[[str], None] = lambda stage: None,
    approve: Callable[[Path], bool] | None = None,
) -> tuple[Path, Comparison, Assessment | None]:
    """Compare as the settings say and write the HTML report; returns its
    path, the comparison and the AI's assessment (None when none was asked,
    or the preview was not approved: a failed one holds its error, and the
    report is written all the same). progress is told each stage as it
    starts ("Comparing…"). With an AI to assess and s.assess_preview, the
    report is first written without the assessment, and approve, given its
    path, says whether the text goes to the AI (no approve: it goes)."""
    paths = s.paths or None
    options = Options(
        context=context_of(s),
        ignore_whitespace=s.ignore_whitespace,
        comments=s.comments,
        empty_comments=s.empty_comments,
        docx_changes=s.docx_changes,
        paragraph_moves=moves_of(s, False),
        sentence_moves=moves_of(s, True),
        move_passages=s.move_passages,
        moved_passage_settings=MovedPassageSettings.from_choices(s.moved_passages),
        language=s.language or DEFAULT,
        encoding=s.encoding or AUTO_ENCODING,
    )
    if s.mode == "review":
        return review(s, options, progress, approve)
    old, new = sides(s)
    fmt = s.output_format if s.output_format in FORMATS else "html"
    split = s.split if s.split in SPLITS else default_split(fmt)
    if split == "both" and fmt != "html":
        split = default_split(fmt)  # a diff holds one split
    check_split(split, fmt)
    if fmt in TRACKED_FORMATS and s.mode == "files" and old and new:
        check_paths(old, new, fmt)  # before comparing them

    def run(options: Options) -> Comparison:
        if s.mode == "files":
            if not old or not new:
                raise ValueError("choose the old and the new file")
            return compare_paths(old, new, options, paths=paths)
        if s.mode == "folders":
            if not old or not new:
                raise ValueError("choose the old and the new folder")
            return compare_paths(old, new, options, paths=paths, include=s.include)
        if not s.repo or not s.base:
            raise ValueError("choose a repository and a base")
        target = None if s.target in ("worktree", "index", "") else s.target
        return compare(
            s.repo,
            s.base,
            target,
            options,
            paths=paths,
            cached=s.target == "index",
            untracked=s.untracked and s.target in ("worktree", ""),
        )

    def staged(options: Options) -> Comparison:
        if split != "both":
            progress("Comparing…")
        else:
            unit = "sentence" if options.by_sentence else "paragraph"
            progress(f"Comparing {unit} by {unit}…")
        return run(options)

    comparison, sentences = compare_split(staged, options, split)
    out = Path(s.output) if s.output else None
    if out is None and s.mode != "git":
        out = default_page(Path(old), Path(new))
    out = Path(with_format(str(out), fmt)) if out is not None else default_output(fmt)
    assessment = writing = None
    write = partial(
        write_output,
        comparison,
        out,
        fmt,
        s.paths,
        align=s.align,
        context=context_of(s),
        sentences=sentences,
        split=split,
        documents=s.assess_documents,
    )
    if s.assess and fmt == "html" and s.assess_preview and approve is not None:
        progress("Writing the preview…")
        write()
        if not approve(out):
            return out, comparison, None
    if s.assess and fmt == "html":  # no other format has a place for it
        request = AssessRequest(
            s.assess,
            effort=s.assess_effort,
            context=s.assess_context,
            instructions=s.assess_instructions,
            save_prompt=s.assess_save_prompt,
            annotate=s.assess_annotate,
        )
        progress(f"Asking {s.assess} to assess the changes…")
        assessment = assess_comparison(comparison, request)
        if s.assess_ai_writing:
            progress(f"Asking {s.assess} whether the new text reads as written by an AI…")
            writing = assess_comparison(comparison, request, kind="writing")
    progress(
        "Writing the report…"
        if fmt == "html"
        else "Writing the document…"
        if fmt in ("docx", "odt")
        else "Writing the diff…"
    )
    out = write(assessment=assessment, writing=writing)
    return out, comparison, assessment


def review(
    s: Settings,
    options: Options,
    progress: Callable[[str], None],
    approve: Callable[[Path], bool] | None,
) -> tuple[Path, Comparison, Assessment | None]:
    """generate for one file alone (the tab "review"): the AI chosen reviews
    it whole, into an HTML report, first written without the review when
    s.assess_preview (as generate does)."""
    if not s.single:
        raise ValueError("choose the file to review")
    if not s.assess:
        raise ValueError("choose an AI to review the file")
    progress("Reading the file…")
    comparison = review_file(s.single, options)
    out = Path(s.output) if s.output else review_page(Path(s.single))
    out = Path(with_format(str(out), "html"))
    write = partial(
        write_output,
        comparison,
        out,
        align=s.align,
        split="paragraph",
        documents=s.assess_documents,
    )
    if s.assess_preview and approve is not None:
        progress("Writing the preview…")
        write()
        if not approve(out):
            return out, comparison, None
    request = AssessRequest(
        s.assess,
        effort=s.assess_effort,
        instructions=s.assess_instructions,
        save_prompt=s.assess_save_prompt,
        annotate=s.assess_annotate,
    )
    progress(f"Asking {s.assess} to review the file…")
    assessment = assess_comparison(comparison, request)
    progress("Writing the report…")
    return write(assessment=assessment), comparison, assessment


# Why a comparison can fail: the errors the window reports, others being bugs.
JOB_ERRORS = (
    git.InvalidGitRepositoryError,
    git.NoSuchPathError,
    git.BadName,
    git.GitCommandError,
    FilterError,
    RuntimeError,  # git diff failed or timed out (git_opcodes)
    SourceError,
    ValueError,
    OSError,
)


@dataclass
class JobResult:
    """What the window needs of a finished comparison: where it went, what
    changed, and the assessment."""

    path: Path
    files: int
    additions: int
    deletions: int
    assessment: Assessment | None = None
    # the name of the file reviewed alone, "" for a comparison
    reviewed: str = ""


# How long a preview waits for the window to say whether the AI assesses;
# unanswered, it does not.
PREVIEW_WAIT_S = 3600


def run_job(s: Settings, messages, replies) -> None:
    """generate, in a process of its own that the window can stop: each
    stage, then the result or the error, sent back on messages, as
    ("stage", text), ("done", JobResult) or ("error", text). A preview is
    sent as ("preview", path), and the window's answer, whether the AI
    assesses, read from replies."""
    invisible_console()  # its console programs open no window either

    def approve(path: Path) -> bool:
        messages.put(("preview", path))
        try:
            return bool(replies.get(timeout=PREVIEW_WAIT_S))
        except queue.Empty:
            return False

    try:
        path, c, assessment = generate(s, lambda stage: messages.put(("stage", stage)), approve)
    except JOB_ERRORS as e:
        messages.put(("error", str(e) or type(e).__name__))
        return
    except Exception as e:  # a bug: said rather than left unanswered
        messages.put(("error", f"{type(e).__name__}: {e}"))
        return
    counts = c.counts
    result = JobResult(
        path,
        len(c.files),
        counts.additions,
        counts.deletions,
        assessment,
        c.repo_name if c.single else "",
    )
    messages.put(("done", result))


def stop_process_tree(pid: int) -> None:
    """Stop a process and every program it started (Claude Code, Codex,
    git), at once."""
    try:
        process = psutil.Process(pid)
        family = [*process.children(recursive=True), process]
    except psutil.NoSuchProcess:
        return
    for p in family:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    psutil.wait_procs(family, timeout=5)


def with_format(path: str, fmt: str) -> str:
    """The output file named for the format chosen: .html, .diff (a .patch
    stays one) or .wdiff; any other name, or none, stays."""
    p = Path(path)
    if not path or p.suffix.lower() not in (".html", *TEXT_SUFFIXES) or format_of(p) == fmt:
        return path
    return str(p.with_suffix(FORMATS[fmt]))


def moves_of(s: Settings, sentences: bool) -> MoveSettings:
    """The moved-line settings chosen for paragraphs (or sentences); None
    for prosediff's default, and for an algorithm it does not know."""
    similarity = s.sentence_move_similarity if sentences else s.move_similarity
    algorithm = s.sentence_move_algorithm if sentences else s.move_algorithm
    return MoveSettings(similarity, algorithm if algorithm in MOVE_ALGORITHMS else None)


def sides(s: Settings) -> tuple[str, str]:
    """The old and the new file, or folder, of the tab shown."""
    return (s.old_folder, s.new_folder) if s.mode == "folders" else (s.old, s.new)


class App:
    """The window."""

    def __init__(self, root: tk.Tk | tk.Toplevel, settings: Settings | None = None) -> None:
        self.root = root
        use_theme(root)
        self.s = settings or load_settings()
        self.choices: dict[str, str] = {}  # label -> ref
        # the comparison running (a process of its own), what it sends back,
        # its settings, and the stage it is at
        self.job: multiprocessing.process.BaseProcess | None = None
        self.messages = self.replies = None
        self.job_settings: Settings | None = None
        self.stage, self.stage_started = "", 0.0
        root.title("prosediff: compare two versions")
        root.minsize(780, 0)
        page = ttk.Frame(root, padding=(14, 12, 14, 12))
        page.pack(fill="both", expand=True)

        # What is compared: a git repository, two files or two folders, one
        # at a time, chosen with a segmented button
        self.mode = tk.StringVar(value=self.s.mode if self.s.mode in MODES else "git")
        switch = ttk.Frame(page)
        switch.pack(fill="x", pady=(0, 8))
        for value, text, icon in (
            ("git", "Git repository", "git"),
            ("files", "Files", "files"),
            ("folders", "Folders", "folder2"),
            ("review", "One file", "file-earmark-text"),
        ):
            ttk.Radiobutton(
                switch,
                text=text,
                image=ttk.Icon(icon, size=16),
                compound="left",
                value=value,
                variable=self.mode,
                command=self.show_mode,
                bootstyle="primary-outline-toolbutton",
                padding=(14, 6),
            ).pack(side="left")
        source = ttk.Labelframe(page, text="Versions", padding=(10, 8))
        source.pack(fill="x")
        self.sides = {mode: ttk.Frame(source) for mode in MODES}
        for side in self.sides.values():
            side.columnconfigure(1, weight=1)
        self.build_git_side(self.sides["git"])
        self.build_path_sides(self.sides["files"], self.sides["folders"])
        self.build_review_side(self.sides["review"])

        # The options that change what the comparison finds, in one card; how
        # the report shows it goes with the advanced settings
        compared = ttk.Labelframe(page, text="Comparison", padding=(10, 8))
        compared.pack(fill="x", pady=(10, 0))
        compared.columnconfigure((0, 1), weight=1, uniform="half")
        self.build_compared_card(compared)
        self.build_advanced(page)
        self.build_output(page)
        self.build_assessment(page)
        self.build_bottom(page)

        if self.s.repo:
            self.load_repo(keep=(self.s.base, self.s.target))
        self.show_mode()
        self.update_untracked()
        self.update_empty_comments()

    def build_git_side(self, git_side: ttk.Frame) -> None:
        """The fields of a git repository: where, which versions, which paths."""
        self.repo = tk.StringVar(value=self.s.repo)
        ttk.Label(git_side, text="Repository").grid(row=0, column=0, sticky="w", **PAD)
        repo_entry = ttk.Entry(git_side, textvariable=self.repo)
        repo_entry.grid(row=0, column=1, sticky="ew", **PAD)
        repo_entry.bind("<Return>", lambda e: self.load_repo())
        repo_entry.bind("<FocusOut>", lambda e: self.load_repo())
        browse(git_side, self.pick_repo, "Choose the repository").grid(row=0, column=2, **PAD)
        self.base = tk.StringVar()
        self.target = tk.StringVar()
        ttk.Label(git_side, text="Base (older)").grid(row=1, column=0, sticky="w", **PAD)
        self.base_box = ttk.Combobox(git_side, textvariable=self.base)
        self.base_box.grid(row=1, column=1, columnspan=2, sticky="ew", **PAD)
        ttk.Label(git_side, text="Target (newer)").grid(row=2, column=0, sticky="w", **PAD)
        self.target_box = ttk.Combobox(git_side, textvariable=self.target)
        self.target_box.grid(row=2, column=1, columnspan=2, sticky="ew", **PAD)
        self.target_box.bind("<<ComboboxSelected>>", lambda e: self.update_untracked())
        hint(
            self.base_box,
            "A commit (hash, date, author, subject) or any ref git knows: HEAD~15, a tag.",
        )
        hint(self.target_box, "The working tree, the index, a commit or any ref git knows.")
        ttk.Label(git_side, text="Only these paths").grid(row=3, column=0, sticky="w", **PAD)
        self.paths = tk.StringVar(value="; ".join(self.s.paths))
        paths_entry = ttk.Entry(git_side, textvariable=self.paths)
        paths_entry.grid(row=3, column=1, columnspan=2, sticky="ew", **PAD)
        hint(paths_entry, "Optional: files or folders of the repository, separated by ;")
        self.untracked = tk.BooleanVar(value=self.s.untracked)
        self.untracked_box = toggle(git_side, "Include untracked files", self.untracked)
        self.untracked_box.grid(row=4, column=1, sticky="w", **PAD)

    def build_path_sides(self, files_side: ttk.Frame, folders_side: ttk.Frame) -> None:
        """The fields of two files, and of two folders."""
        self.old = tk.StringVar(value=self.s.old)
        self.new = tk.StringVar(value=self.s.new)
        self.old_folder = tk.StringVar(value=self.s.old_folder)
        self.new_folder = tk.StringVar(value=self.s.new_folder)
        for side, folder, old, new, what in (
            (files_side, False, self.old, self.new, "file"),
            (folders_side, True, self.old_folder, self.new_folder, "folder"),
        ):
            for row, (label, var) in enumerate((("Old", old), ("New", new))):
                ttk.Label(side, text=label).grid(row=row, column=0, sticky="w", **PAD)
                ttk.Entry(side, textvariable=var).grid(row=row, column=1, sticky="ew", **PAD)
                browse(
                    side,
                    lambda v=var, d=folder: self.pick(v, d),
                    f"Choose the {label.lower()} {what}",
                ).grid(row=row, column=2, **PAD)
            swap_button = ttk.Button(
                side,
                image=ttk.Icon("arrow-down-up", size=16),
                command=lambda a=old, b=new: swap(a, b),
                bootstyle="secondary-outline",
            )
            swap_button.grid(row=0, column=3, rowspan=2, sticky="ns", **PAD)
            hint(swap_button, f"Swap the old and the new {what}")
        ttk.Label(
            files_side,
            text="Any two files: Word, OpenDocument, Markdown, text, whatever their names.",
            bootstyle="secondary",
        ).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(folders_side, text="Only").grid(row=2, column=0, sticky="w", **PAD)
        self.include = tk.StringVar(value=self.s.include)
        include_entry = ttk.Entry(folders_side, textvariable=self.include)
        include_entry.grid(row=2, column=1, sticky="ew", **PAD)
        hint(
            include_entry,
            "The files compared: patterns separated by |, matched against each file's name "
            "(its path within the folder for a pattern with a /); empty: every file.",
        )

    def build_review_side(self, side: ttk.Frame) -> None:
        """The field of one file, reviewed alone."""
        self.single = tk.StringVar(value=self.s.single)
        ttk.Label(side, text="File").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Entry(side, textvariable=self.single).grid(row=0, column=1, sticky="ew", **PAD)
        browse(side, lambda: self.pick(self.single, False), "Choose the file to review").grid(
            row=0, column=2, **PAD
        )
        ttk.Label(
            side,
            text="One file alone, reviewed whole by the AI chosen below: nothing compared. "
            "A Word or OpenDocument file comes back with the AI's comments and fixes.",
            bootstyle="secondary",
        ).grid(row=1, column=1, sticky="w", padx=6)

    def build_compared_card(self, card: ttk.Labelframe) -> None:
        """The options that change what the comparison finds, in two columns."""
        compared = ttk.Frame(card)
        compared.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(card)
        right.grid(row=0, column=1, sticky="nw", padx=(18, 0))
        self.docx = tk.StringVar(value=DOCX_CHANGE_LABELS.get(self.s.docx_changes, "accept all"))
        field_row(
            compared,
            0,
            "Tracked changes",
            item_hints(
                ttk.Combobox(
                    compared,
                    textvariable=self.docx,
                    values=[DOCX_CHANGE_LABELS[v] for v in DOCX_CHANGES],
                    state="readonly",
                    width=12,
                ),
                lambda label: DOCX_CHANGE_HINTS.get(DOCX_CHANGE_VALUES.get(label, ""), ""),
            ),
            "Word and OpenDocument tracked changes: accept them all, reject them all, or "
            "show them, as Word does.",
        )
        self.language = tk.StringVar(value=self.s.language)
        # any code can be typed; the list holds the common ones
        field_row(
            compared,
            1,
            "Language",
            item_hints(
                ttk.Combobox(compared, textvariable=self.language, values=LANGUAGES, width=12),
                lambda code: LANGUAGE_HINTS.get(code) or language_name(code),
            ),
            "Splits sentences and hyphenates lines. default: the language Word and "
            "OpenDocument files are marked with, else guessed; or a code such as it.",
        )
        self.comments = tk.StringVar(
            value=self.s.comments if self.s.comments in COMMENT_MODES else "markers"
        )
        field_row(
            right,
            0,
            "Comments",
            item_hints(
                ttk.Combobox(
                    right,
                    textvariable=self.comments,
                    values=COMMENT_MODES,
                    state="readonly",
                    width=12,
                ),
                COMMENT_HINTS.get,
            ),
            "markers: only the comments added or removed, set apart (a marker and a panel in "
            "the HTML report, CriticMarkup in the diffs); text: compared as text; none: left "
            "out.",
        )
        self.comments.trace_add("write", lambda *_: self.update_empty_comments())
        self.split = tk.StringVar(value=self.s.split if self.s.split in SPLITS else "both")
        splits = ttk.Frame(compared)
        split_names = (("paragraph", "Paragraphs"), ("sentence", "Sentences"), ("both", "Both"))
        # what only a comparison has, greyed out reviewing one file (show_mode)
        self.comparing_only: list[tk_ttk.Widget] = []
        for value, text in split_names:
            button = ttk.Radiobutton(
                splits,
                text=text,
                value=value,
                variable=self.split,
                bootstyle="secondary-outline-toolbutton",
                padding=(8, 3),
            )
            button.pack(side="left")
            self.comparing_only.append(button)
        field_row(
            compared,
            2,
            "Compare by",
            splits,
            "How prose is compared: paragraph by paragraph, sentence by sentence (a sentence "
            "moved between paragraphs is recognised), or both, in one HTML report whose "
            "toolbar switches between the two.",
        )
        self.ignore_ws = tk.BooleanVar(value=self.s.ignore_whitespace)
        self.comparing_only.append(
            switch_row(
                right,
                1,
                "Ignore whitespace",
                self.ignore_ws,
                "Lines that differ only in spacing are the same, as git diff -w.",
            )
        )

    def build_report_card(self, card: ttk.Labelframe) -> None:
        """The options of how the report shows the comparison, in two columns."""
        compared = ttk.Frame(card)
        compared.grid(row=0, column=0, sticky="nw")
        shown = ttk.Frame(card)
        shown.grid(row=0, column=1, sticky="nw", padx=(18, 0))
        self.context = tk.StringVar(value=self.s.context_lines)
        # "auto": 0 around the changes of Markdown and Word, 3 of other files
        field_row(
            compared,
            0,
            "Context lines",
            ttk.Spinbox(compared, values=("auto", *range(51)), textvariable=self.context, width=10),
            "Unchanged lines shown around each change. auto: none in Markdown files and Word "
            "documents, whose lines are paragraphs; 3 in the others.",
        )
        self.align = tk.StringVar(value=self.s.align)
        field_row(
            compared,
            1,
            "Wrapped lines",
            item_hints(
                ttk.Combobox(
                    compared, textvariable=self.align, values=ALIGNMENTS, state="readonly", width=12
                ),
                ALIGNMENT_HINTS.get,
            ),
            "How long lines that wrap are aligned in the HTML report.",
        )
        self.move_passages = tk.BooleanVar(value=self.s.move_passages)
        switch_row(
            shown,
            0,
            "Moved passages",
            self.move_passages,
            "Also follow the passages moved within a paragraph or between two: words removed "
            "in one place and added in another, as alike as the moved paragraphs (sentences) "
            "must be, are shown as moved, not as a deletion and an unrelated insertion.",
        )
        self.full = tk.BooleanVar(value=self.s.full)
        switch_row(shown, 1, "Whole files", self.full, "Show every line of each changed file.")
        self.empty_comments = tk.BooleanVar(value=self.s.empty_comments)
        self.empty_comments_box = switch_row(
            shown,
            2,
            "Comments without text",
            self.empty_comments,
            "Show the comments that have no text too (with markers only).",
        )

    def build_advanced(self, page: ttk.Frame) -> None:
        """The advanced settings, hidden until asked for."""
        # Advanced settings, hidden until asked for: how alike moved paragraphs
        # and sentences must be, how moved passages are told from chance
        # likeness (one field for each of MovedPassageSettings), and the
        # encoding of text files
        # a window of its own, hidden until asked for: this one would grow past
        # the screen
        self.advanced_window = tk.Toplevel(self.root)
        self.advanced_window.title("prosediff: advanced settings")
        self.advanced_window.withdraw()
        self.advanced_window.resizable(False, False)
        self.advanced_window.protocol("WM_DELETE_WINDOW", self.toggle_advanced)
        self.advanced_window.bind("<Escape>", lambda e: self.toggle_advanced())
        self.advanced = ttk.Frame(self.advanced_window, padding=(14, 12, 14, 12))
        self.advanced.pack(fill="both", expand=True)
        report = ttk.Labelframe(self.advanced, text="Report", padding=(10, 8))
        report.pack(fill="x")
        report.columnconfigure((0, 1), weight=1, uniform="half")
        self.build_report_card(report)
        moves = ttk.Labelframe(
            self.advanced, text="Moved paragraphs and sentences", padding=(10, 8)
        )
        moves.pack(fill="x", pady=(8, 0))
        similarity, algorithm = moves_of(self.s, False).resolved(False)
        self.move_similarity = tk.DoubleVar(value=similarity)
        self.move_algorithm = tk.StringVar(value=algorithm)
        similarity, algorithm = moves_of(self.s, True).resolved(True)
        self.sentence_move_similarity = tk.DoubleVar(value=similarity)
        self.sentence_move_algorithm = tk.StringVar(value=algorithm)
        for row, what, similarity, algorithm, sentences in (
            (0, "paragraphs", self.move_similarity, self.move_algorithm, False),
            (1, "sentences", self.sentence_move_similarity, self.sentence_move_algorithm, True),
        ):
            default = "{:.2f} {}".format(*move_defaults(sentences))
            field_row(
                moves,
                row,
                f"Moved {what}",
                move_fields(moves, similarity, algorithm),
                f"How alike an edited {what[:-1]} must be to where it reappears to count as "
                "moved (1: only unchanged), and how that is measured. token-sort: the words "
                f"in common, whatever their order. Default: {default}"
                + (" (lines of files other than prose too)." if not sentences else "."),
            )
        passages = ttk.Labelframe(
            self.advanced,
            text="Moved passages: telling them from chance likeness",
            padding=(10, 8),
        )
        passages.pack(fill="x", pady=(8, 0))
        passages.columnconfigure((1, 3), weight=1)
        self.passage_vars: dict[str, tk.StringVar] = {}
        for k, f in enumerate(fields(MovedPassageSettings)):
            value = self.s.moved_passages.get(f.name, f.default)
            var = tk.StringVar(value=f"{value:g}" if f.metadata["share"] else f"{int(value):,}")
            self.passage_vars[f.name] = var
            spin = (
                ttk.Spinbox(passages, from_=0.01, to=1, increment=0.05, textvariable=var, width=10)
                if f.metadata["share"]
                else ttk.Spinbox(
                    passages,
                    from_=f.metadata["low"],
                    to=10**7,
                    increment=1,
                    textvariable=var,
                    width=10,
                )
            )
            row, col = k // 2, (k % 2) * 2
            label = ttk.Label(passages, text=f.metadata["label"])
            label.grid(row=row, column=col, sticky="w", padx=(0 if col == 0 else 18, 6), pady=3)
            spin.grid(row=row, column=col + 1, sticky="w", pady=3)
            what = f.metadata["help"]
            tip = f"{what[0].upper()}{what[1:]}. Default: {f.default:,}."
            hint(label, tip)
            hint(spin, tip)
        reading = ttk.Labelframe(self.advanced, text="Reading files", padding=(10, 8))
        reading.pack(fill="x", pady=(8, 0))
        self.encoding = tk.StringVar(value=self.s.encoding)
        field_row(
            reading,
            0,
            "Text encoding",
            item_hints(
                ttk.Combobox(reading, textvariable=self.encoding, values=ENCODINGS, width=12),
                ENCODING_HINTS.get,
            ),
            "Of text and Markdown files. auto: UTF-8, unless a file is not; then guessed.",
        )
        ttk.Button(
            self.advanced, text="Close", command=self.toggle_advanced, bootstyle="secondary"
        ).pack(side="bottom", anchor="e", pady=(12, 0))

    def build_output(self, page: ttk.Frame) -> None:
        """The output: its format, where it goes, whether it opens."""
        out = ttk.Labelframe(page, text="Output", padding=(10, 8))
        out.pack(fill="x", pady=(10, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Format").grid(row=0, column=0, sticky="w", **PAD)
        formats = ttk.Frame(out)
        formats.grid(row=0, column=1, columnspan=3, sticky="w", **PAD)
        self.format_buttons: dict[str, ttk.Radiobutton] = {}
        self.output_format = tk.StringVar(
            value=self.s.output_format if self.s.output_format in FORMATS else "html"
        )
        for value, text, tip in (
            ("html", "HTML report", "Side by side, in the browser: words, moves, comments."),
            ("diff", "Unified diff", "A .diff, as git diff writes it; a patch for text files."),
            ("wdiff", "Word diff", "A .wdiff: the words changed in each line, [-old-]{+new+}."),
            (
                "docx",
                "Word, tracked",
                "Two Word documents compared into a copy of the new one, everything in it "
                "kept, each change since the old one a tracked change to accept or reject "
                "in Word. Two Word documents only (greyed out for other files); paragraph "
                "by paragraph.",
            ),
            (
                "odt",
                "OpenDocument, tracked",
                "Two OpenDocument texts compared into a copy of the new one, the same way, "
                "for LibreOffice Writer. Two OpenDocument texts only (greyed out for other "
                "files).",
            ),
        ):
            button = ttk.Radiobutton(
                formats,
                text=text,
                value=value,
                variable=self.output_format,
                command=self.rename_output,
                bootstyle="secondary-outline-toolbutton",
                padding=(12, 4),
            )
            button.pack(side="left")
            hint(button, tip)
            self.format_buttons[value] = button
        ttk.Label(out, text="Save to").grid(row=1, column=0, sticky="w", **PAD)
        self.output = tk.StringVar(value=self.s.output)
        output_entry = ttk.Entry(out, textvariable=self.output)
        output_entry.grid(row=1, column=1, sticky="ew", **PAD)
        hint(
            output_entry,
            "By default next to the new file (OLD_vs_NEW.html), or into the new folder "
            "(prosediff.html), following them as they change; comparing git versions, "
            "a new file in the temporary folder. A file chosen here stays.",
        )
        # Save to, while it is the default: it follows the sides (follow_sides)
        self.auto_output = ""
        for var in (self.mode, self.old, self.new, self.old_folder, self.new_folder, self.single):
            var.trace_add("write", lambda *_: self.follow_sides())
        self.follow_sides()
        save = ttk.Button(
            out,
            image=ttk.Icon("save", size=16),
            command=self.pick_output,
            bootstyle="secondary-outline",
        )
        save.grid(row=1, column=2, **PAD)
        hint(save, "Choose where to save it")
        self.open_page = tk.BooleanVar(value=self.s.open_page)
        toggle(out, "Open when done", self.open_page).grid(
            row=1, column=3, sticky="w", padx=(12, 6), pady=6
        )
        for var in (self.mode, self.old, self.new):
            var.trace_add("write", lambda *_: self.update_tracked_formats())
        self.update_tracked_formats()

    def build_assessment(self, page: ttk.Frame) -> None:
        """The AI assessment: the AI, its model and effort, among those it
        reports; what it reads; the instructions of the person asking."""
        card = ttk.Labelframe(page, text="AI assessment", padding=(10, 8))
        card.pack(fill="x", pady=(10, 0))
        self.ai_card = card
        card.columnconfigure(1, weight=1)
        # the AI saved shown at once, checked once the AIs and its models are
        # known (add_found, update_models); none saved: "Loading…" until the
        # AIs are known, then none
        ai, _, model = self.s.assess.partition("/")
        self.assess_ai = tk.StringVar(value=ai or LOADING)
        self.assess_model = tk.StringVar(value=model or (CLAUDE_DEFAULT if ai == "claude" else ""))
        self.assess_effort = tk.StringVar(value=self.s.assess_effort)
        self.saved_model = (ai, self.assess_model.get()) if ai else None
        # the models each AI reports, once asked; the AIs being asked; what
        # the background found, for tkinter's own thread
        self.ai_models: dict[str, list[ModelInfo]] = {}
        self.asking: set[str] = set()
        self.found: queue.Queue[tuple[str, list, str]] = queue.Queue()
        # the model and effort chosen before the list shows "Loading…"
        self.pending: tuple[str, str] = ("", "")
        ttk.Label(card, text="AI").grid(row=0, column=0, sticky="w", **PAD)
        ai_row = ttk.Frame(card)
        ai_row.grid(row=0, column=1, columnspan=2, sticky="w", **PAD)
        self.ai_box = item_hints(
            ttk.Combobox(ai_row, textvariable=self.assess_ai, width=12),
            lambda value: AI_HINTS.get(
                value,
                f"{value}: a model of its API, through any-llm, "
                "its key in the environment (prosediff[models]).",
            ),
        )
        self.ai_box.pack(side="left")
        ttk.Label(ai_row, text="Model").pack(side="left", padx=(12, 6))
        self.model_box = item_hints(
            ttk.Combobox(ai_row, textvariable=self.assess_model, width=22),
            self.model_hint,
        )
        self.model_box.pack(side="left")
        ttk.Label(ai_row, text="Effort").pack(side="left", padx=(12, 6))
        self.effort_box = item_hints(
            ttk.Combobox(ai_row, textvariable=self.assess_effort, width=9),
            self.effort_hint,
        )
        self.effort_box.pack(side="left")
        for w, tip in (
            (
                self.ai_box,
                "Have an AI assess the value of the changes as a whole: a verdict, what "
                "changed, what improved and the problems to fix, at the top of the HTML "
                "report (the HTML report only). claude: Claude Code; codex: "
                "ChatGPT through Codex; ollama: a local model; or another provider "
                "any-llm reaches (openai, anthropic, gemini, ...), its API key in the "
                "environment.",
            ),
            (
                self.model_box,
                "The model that assesses, among those the AI reports, its own default "
                "first; or any name typed.",
            ),
            (
                self.effort_box,
                "How hard the model thinks, among the levels it reports it supports; "
                "empty: its own default. More effort, a closer reading, but slower and "
                "costlier.",
            ),
        ):
            hint(w, tip)
        self.assess_context = tk.StringVar(
            value=self.s.assess_context if self.s.assess_context in CONTEXTS else "document"
        )
        # what only matters when an AI assesses, greyed out while none is
        # chosen (update_ai_switches)
        self.ai_switches = []
        # what only applies to changes, greyed out reviewing one file
        self.changes_only: list[tk_ttk.Widget] = []
        reads = ttk.Frame(card)
        for value, text, tip in (
            (
                "document",
                "Changes + new version",
                "The changes (a word diff: the old wording beside the new) and the whole "
                "new version, for the model to check them against the rest of the "
                "document: citations, cross-references, terms. The old version needs no "
                "sending: its unchanged paragraphs are in the new one, the rest in the "
                "changes. More to read, a better check.",
            ),
            (
                "changes",
                "Changes only",
                "The changes alone (a word diff of the changed paragraphs): less to read, "
                "but nothing of the text around them.",
            ),
        ):
            button = ttk.Radiobutton(
                reads,
                text=text,
                value=value,
                variable=self.assess_context,
                bootstyle="secondary-outline-toolbutton",
                padding=(8, 3),
            )
            button.pack(side="left")
            hint(button, tip)
            self.ai_switches.append(button)
            self.changes_only.append(button)
        field_row(card, 1, "Reads", reads, "What the model is sent, besides the instructions.")
        self.assess_instructions = tk.StringVar(value=self.s.assess_instructions)
        ttk.Label(card, text="Instructions").grid(row=2, column=0, sticky="w", **PAD)
        entry = ttk.Entry(card, textvariable=self.assess_instructions)
        entry.grid(row=2, column=1, sticky="ew", **PAD)
        hint(
            entry,
            "Your own instructions, added to the prompt: the journal, what a co-author "
            'asked for, what to look at (e.g. "the journal is Research Policy; check that '
            'the introduction was cut by a fifth"); or a text file holding them.',
        )
        pick = browse(
            card,
            lambda: self.pick_into(self.assess_instructions, "Instructions for the AI"),
            "Choose a text file holding the instructions",
        )
        pick.grid(row=2, column=2, **PAD)
        self.ai_switches += [entry, pick]
        self.assess_annotate = tk.BooleanVar(value=self.s.assess_annotate)
        self.assess_save_prompt = tk.BooleanVar(value=self.s.assess_save_prompt)
        self.assess_preview = tk.BooleanVar(value=self.s.assess_preview)
        self.assess_ai_writing = tk.BooleanVar(value=self.s.assess_ai_writing)
        self.assess_documents = tk.BooleanVar(value=self.s.assess_documents)
        switches = ttk.Frame(card)
        switches.grid(row=3, column=1, columnspan=2, sticky="w", **PAD)
        for k, (text, var, tip) in enumerate(
            (
                (
                    "Preview before sending",
                    self.assess_preview,
                    "First write the report without the assessment and open it, then ask "
                    "whether to send the changes to the AI: to check what it will read "
                    "before it reads it. No: the report stays as it is, unassessed.",
                ),
                (
                    "Mark individual changes",
                    self.assess_annotate,
                    "Have the AI mark each problem in the text, from its first words to its "
                    "last, with what is wrong and the change it proposes: a numbered badge "
                    "before each in the HTML report, its passage highlighted when clicked, and "
                    "a card in the margin beside it.",
                ),
                (
                    "Check for AI writing",
                    self.assess_ai_writing,
                    "Also ask the AI, apart, whether the text the changes added reads as "
                    "written by an AI: a second assessment, its verdict (likely, possibly or "
                    "unlikely) in the report's top bar. An indication, not a proof: careful "
                    "writers show the same signs, and writers in a second language are often "
                    "taken for an AI wrongly.",
                ),
                (
                    "Save AI prompt",
                    self.assess_save_prompt,
                    "Also put the exact text the AI was sent (its system prompt and its "
                    "message) in the HTML report, in a closed panel at its end: to see what "
                    "it read.",
                ),
                (
                    "Documents to download",
                    self.assess_documents,
                    "Comparing a Word document with another (or an OpenDocument text with "
                    "another), put two documents in the HTML report, to download from the AI "
                    "assessment: the tracked changes with the AI's comments, and the new "
                    "version with the AI's fixes as its own tracked changes, to accept or "
                    "reject. Review mode chooses which problems they hold. They make the "
                    "report larger: about 2.7 times the document's size. Needs \"Mark "
                    'individual changes".',
                ),
            )
        ):
            switch = toggle(switches, text, var)
            # rows of two
            switch.grid(row=k // 2, column=k % 2, sticky="w", padx=(0, 24), pady=3)
            hint(switch, tip)
            self.ai_switches.append(switch)
            if var is self.assess_documents:
                self.documents_switch = switch
            if var is self.assess_ai_writing:
                self.changes_only.append(switch)
        self.assess_annotate.trace_add("write", lambda *_: self.update_ai_switches())
        self.assess_ai.trace_add("write", lambda *_: self.update_ai_switches())
        self.update_ai_switches()
        self.assess_ai.trace_add("write", lambda *_: self.update_models())
        self.assess_model.trace_add("write", lambda *_: self.update_efforts())
        self.update_models(keep=True)  # the model and effort saved stay
        self.output_format.trace_add("write", lambda *_: self.update_ai_card())
        self.update_ai_card()
        # the AIs: prosediff's own and the providers any-llm reaches
        self.ask("providers", lambda: [*AIS, *(p for p in providers() if p not in AIS)])

    def ai_chosen(self) -> str:
        """The AI chosen; "" while the AIs are being found."""
        ai = self.assess_ai.get().strip()
        return "" if ai == LOADING else ai

    def complain(self, text: str) -> None:
        """An error found by the window itself, said in a dialog and the
        status line."""
        self.status.set(text)
        messagebox.showerror("prosediff", text, parent=self.root)

    def ai_active(self) -> bool:
        """Whether an AI will assess: one is chosen, and the output is the HTML
        report, the only one with room for its assessment."""
        chosen = self.ai_chosen() not in ("", NO_ASSESSMENT)
        return chosen and self.output_format.get() == "html"

    def reviewing(self) -> bool:
        """Whether one file is reviewed alone (the tab "review"), not two
        versions compared."""
        return self.mode.get() == "review"

    def update_ai_card(self) -> None:
        """The AI assessment card, greyed out whole unless the output is the
        HTML report; its model, effort and switches then as the AI says."""
        html = self.output_format.get() == "html"
        widgets = [self.ai_card]
        while widgets:
            w = widgets.pop()
            widgets.extend(w.winfo_children())
            if isinstance(w, tk_ttk.Widget):
                w.state(["!disabled"] if html else ["disabled"])
        if self.assess_ai.get() == LOADING:
            self.ai_box.state(["disabled"])  # until the AIs are known
        self.update_ai_switches()
        on = self.ai_active() and self.assess_model.get() != LOADING
        for box in (self.model_box, self.effort_box):
            box.state(["!disabled"] if on else ["disabled"])

    def update_ai_switches(self) -> None:
        """What the AI is sent and the AI assessment's switches, greyed out
        while no AI will assess."""
        for switch in self.ai_switches:
            on = self.ai_active()
            # the documents are made of the problems marked in the text
            if switch is getattr(self, "documents_switch", None):
                on = on and self.assess_annotate.get()
            # what the model reads and the AI-writing check are of changes
            if switch in self.changes_only:
                on = on and not self.reviewing()
            switch.state(["!disabled"] if on else ["disabled"])

    def pick_into(self, var: tk.StringVar, title: str) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root, title=title, filetypes=[("Text", "*.txt *.md"), ("All", "*.*")]
        )
        if chosen:
            var.set(chosen)

    def ask(self, kind: str, find: Callable[[], list]) -> None:
        """Look for something in the background (an AI's models, the
        providers), without holding the window up; add_found puts it in its
        list once found."""
        if kind in self.asking:
            return
        self.asking.add(kind)

        def work() -> None:
            try:
                self.found.put((kind, find(), ""))
            except Exception as e:
                self.found.put((kind, [], str(e)))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, self.add_found)

    def add_found(self) -> None:
        """Put what the background found in the lists, as it comes; an AI
        that could not say its models says why in the status line."""
        while True:
            try:
                kind, found, error = self.found.get_nowait()
            except queue.Empty:
                break
            self.asking.discard(kind)
            if kind == "providers":
                self.ai_box.configure(values=found)
                ai = self.assess_ai.get().strip()
                if ai == LOADING:
                    self.assess_ai.set(NO_ASSESSMENT)
                elif ai not in found:
                    self.complain(
                        f"The AI saved, {ai}, is not available any more"
                        + (f" ({error})" if error else "")
                        + ": no AI assesses the changes until another is chosen."
                    )
                    self.assess_ai.set(NO_ASSESSMENT)
                self.update_ai_card()
                continue
            self.ai_models[kind] = found
            if error:
                self.status.set(f"The models of {kind} are not known: {error}")
            if self.ai_chosen() == kind:
                self.update_models(keep=True)
        if self.asking:
            self.root.after(200, self.add_found)

    def update_models(self, keep: bool = False) -> None:
        """The model list of the AI chosen, as the AI reports it (asked for
        in the background the first time); none for none. The model shown
        becomes the AI's own default, the first it reports, unless keep and
        one is already chosen."""
        ai = self.ai_chosen()
        self.model_box.state(["!disabled"] if self.ai_active() else ["disabled"])
        if ai in ("", NO_ASSESSMENT):
            self.model_box.configure(values=())
            self.update_efforts()
            return
        if ai not in self.ai_models and self.saved_model == (ai, self.assess_model.get()):
            # the model saved shown meanwhile, checked once they are known
            self.ask(ai, lambda: models_of(ai))
            return
        if ai not in self.ai_models:
            # "Loading…" until the AI has said its models; the model and
            # effort chosen before kept for then
            if self.assess_model.get() != LOADING:
                self.pending = (
                    (self.assess_model.get().strip(), self.assess_effort.get().strip())
                    if keep
                    else ("", "")
                )
            self.model_box.configure(values=())
            self.assess_model.set(LOADING)
            self.assess_effort.set(LOADING)
            self.model_box.state(["disabled"])
            self.effort_box.state(["disabled"])
            self.ask(ai, lambda: models_of(ai))
            return
        models = [m.name for m in self.ai_models[ai]]
        self.model_box.configure(values=models)
        if self.saved_model and self.saved_model[0] == ai:
            saved, self.saved_model = self.saved_model[1], None
            if models and self.assess_model.get() == saved and saved not in models:
                self.complain(
                    f"The model saved, {saved}, is not one {ai} offers any more: its "
                    f"default, {models[0]}, is chosen instead."
                )
                self.assess_model.set(models[0])
                return
        if self.assess_model.get() == LOADING:
            model, effort = self.pending
            self.pending = ("", "")
            self.assess_model.set(model or (models[0] if models else ""))
            if effort and effort in self.effort_box["values"]:
                self.assess_effort.set(effort)
            return
        if keep and self.assess_model.get().strip():
            self.update_efforts(keep=True)
            return
        self.assess_model.set(models[0] if models else "")

    def chosen_model(self) -> ModelInfo | None:
        """The model chosen, as its AI reports it; None when unknown."""
        name = self.assess_model.get().strip()
        found = self.ai_models.get(self.ai_chosen(), [])
        return next((m for m in found if m.name == name), None)

    def update_efforts(self, keep: bool = False) -> None:
        """The efforts the model chosen reports it supports, its own default
        chosen (none said: empty, the model's default), unless keep and one
        it supports is chosen already."""
        model = self.chosen_model()
        levels = [level for level, _ in model.efforts] if model else []
        if levels and not model.default_effort:
            levels.insert(0, MODEL_DEFAULT)  # shown, not an empty box
        self.effort_box.configure(values=levels)
        self.effort_box.state(["!disabled"] if self.ai_active() else ["disabled"])
        if keep and (self.assess_effort.get() in levels or model is None):
            return
        default = model.default_effort if model else ""
        self.assess_effort.set(default or (MODEL_DEFAULT if levels else ""))

    def model_hint(self, value: str) -> str:
        found = self.ai_models.get(self.ai_chosen(), [])
        return next((m.description for m in found if m.name == value), "")

    def effort_chosen(self) -> str:
        """The effort to ask for: "" for the model's own default; while the
        models load, the one chosen before."""
        effort = self.assess_effort.get().strip()
        if effort == LOADING:
            return self.pending[1]
        return "" if effort == MODEL_DEFAULT else effort

    def effort_hint(self, value: str) -> str:
        model = self.chosen_model()
        if value == MODEL_DEFAULT:
            return "The model's own default effort: nothing is asked for."
        said = dict(model.efforts).get(value, "") if model else ""
        default = " (the model's default)" if model and value == model.default_effort else ""
        return f"{said}{default}".strip()

    def assess_spec(self) -> str:
        """The AI assessment asked for, as --assess takes it: "claude",
        "claude/opus", "ollama/qwen3"; "" for none. Claude Code's own
        default is asked for by naming no model."""
        ai, model = self.ai_chosen(), self.assess_model.get().strip()
        if model == LOADING:
            model = self.pending[0]  # the one chosen before, or the AI's default
        if ai in ("", NO_ASSESSMENT):
            return ""
        if not model or (ai == "claude" and model == CLAUDE_DEFAULT):
            return ai
        return f"{ai}/{model}"

    def build_bottom(self, page: ttk.Frame) -> None:
        """The status line and the buttons."""
        bottom = ttk.Frame(page)
        # laid out before the cards, so a window too short for them never
        # hides the Compare button
        bottom.pack(fill="x", side="bottom", pady=(12, 0), before=page.winfo_children()[0])
        self.status = tk.StringVar(value=READY)
        ttk.Label(bottom, textvariable=self.status, bootstyle="secondary").pack(side="left")
        self.compare_icon = ttk.Icon("play-fill", size=16, color="white")
        self.cancel_icon = ttk.Icon("stop-fill", size=16, color="white")
        self.button = ttk.Button(
            bottom,
            text="Compare",
            image=self.compare_icon,
            compound="left",
            command=self.run,
            default="active",
            bootstyle="primary",
            padding=(16, 6),
        )
        self.button.pack(side="right")
        # its tooltip changes with it: Compare, or Cancel while one runs
        self.button_tip = ttk.ToolTip(
            self.button,
            text="Compare, write the output and open it (Ctrl+Enter)",
            wraplength=HINT_WIDTH,
            delay=HINT_DELAY_MS,
        )
        reset = ttk.Button(
            bottom,
            text="Reset to defaults",
            image=ttk.Icon("arrow-counterclockwise", size=16),
            compound="left",
            command=self.reset_options,
            bootstyle="secondary-outline",
        )
        reset.pack(side="right", padx=(0, 8))
        hint(
            reset,
            "Put every option back to its default: the cards and the output format; "
            "what is compared and where the output goes stay",
        )
        save = ttk.Button(
            bottom,
            text="Save options",
            image=ttk.Icon("floppy", size=16),
            compound="left",
            command=self.save_options,
            bootstyle="secondary-outline",
        )
        save.pack(side="right", padx=(0, 8))
        hint(save, f"Open the window with these choices next time ({settings_file()})")
        advanced = ttk.Button(
            bottom,
            text="Advanced settings",
            image=ttk.Icon("sliders", size=16),
            compound="left",
            command=self.toggle_advanced,
            bootstyle="secondary-outline",
        )
        advanced.pack(side="right", padx=(0, 8))
        hint(
            advanced,
            "Open the settings few need to change in a window of their own, beside this "
            "one: how the report shows the changes, moved passages, the encoding of text "
            "files. They apply to the next comparison as they are when it starts.",
        )
        self.progress = ttk.Progressbar(
            bottom, mode="indeterminate", bootstyle="striped", length=140
        )
        # the question a preview asks, in this window above the buttons: a
        # dialog of its own would open under the browser showing the preview
        self.bottom = bottom
        self.preview_bar = ttk.Labelframe(page, text="Preview", padding=(10, 8))
        self.preview_text = tk.StringVar()
        ttk.Label(
            self.preview_bar, textvariable=self.preview_text, wraplength=500, justify="left"
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            self.preview_bar,
            text="Don't send",
            command=lambda: self.answer_preview(False),
            bootstyle="secondary-outline",
        ).pack(side="right")
        self.send_button = ttk.Button(
            self.preview_bar,
            text="Send to the AI",
            command=lambda: self.answer_preview(True),
            bootstyle="primary",
        )
        self.send_button.pack(side="right", padx=(0, 8))
        self.root.bind("<Control-Return>", lambda e: self.run())
        self.root.bind("<Escape>", lambda e: self.cancel())

    def show_mode(self) -> None:
        """Show the fields of what is compared: a repository, files or
        folders; or of the one file reviewed, the options of a comparison
        greyed out, the output an HTML report."""
        if self.mode.get() != "git":
            self.status.set(REVIEW_READY if self.reviewing() else READY)
        for mode, side in self.sides.items():
            if mode == self.mode.get():
                side.pack(fill="x")
            else:
                side.pack_forget()
        for w in self.comparing_only:
            w.state(["disabled"] if self.reviewing() else ["!disabled"])
        if self.reviewing() and self.output_format.get() != "html":
            self.output_format.set("html")
            self.rename_output()
        self.update_tracked_formats()
        self.update_ai_switches()
        if self.job is None:
            self.show_cancel(False)

    # Choosing ------------------------------------------------------------------

    def pick_repo(self) -> None:
        folder = filedialog.askdirectory(title="Git repository", initialdir=self.repo.get() or None)
        if folder:
            self.repo.set(folder)
            self.load_repo()

    def pick(self, var: tk.StringVar, folder: bool) -> None:
        """Choose a file (or a folder) for var."""
        f = (
            filedialog.askdirectory(title="Folder")
            if folder
            else filedialog.askopenfilename(title="File")
        )
        if f:
            var.set(f)

    def rename_output(self) -> None:
        """Give the Save to file the extension of the format chosen."""
        default = self.output.get().strip() == self.auto_output
        self.output.set(with_format(self.output.get().strip(), self.output_format.get()))
        if default:
            self.auto_output = self.output.get().strip()

    def sides_page(self) -> str:
        """Where the output goes by default: next to the new file, into the new
        folder (default_page); "" comparing git versions, or sides not yet chosen."""
        mode = self.mode.get()
        if mode == "git":
            return ""
        if mode == "review":
            single = self.single.get().strip()
            return str(review_page(Path(single)).resolve()) if single else ""
        old, new = (self.old, self.new) if mode == "files" else (self.old_folder, self.new_folder)
        if not old.get().strip() or not new.get().strip():
            return ""
        page = default_page(Path(old.get().strip()), Path(new.get().strip()))
        return with_format(str(page.resolve()), self.output_format.get()) if page else ""

    def follow_sides(self) -> None:
        """Keep Save to on the default as the sides change: an empty one, or one
        still on the default of the sides before, is moved; one chosen stays."""
        if self.output.get().strip() not in ("", self.auto_output):
            return
        self.auto_output = self.sides_page()
        self.output.set(self.auto_output)

    def update_tracked_formats(self) -> None:
        """Word, tracked and OpenDocument, tracked greyed out when two files
        are compared that are not both of their kind; a repository or folders
        are only known once compared."""
        files = self.mode.get() == "files"
        for fmt, suffix in TRACKED_FORMATS.items():
            fits = not files or all(
                Path(v.get().strip()).suffix.lower() == suffix for v in (self.old, self.new)
            )
            self.format_buttons[fmt].state(["!disabled"] if fits else ["disabled"])
        if self.reviewing():  # a review is an HTML report: no diff, no changes
            for fmt, button in self.format_buttons.items():
                button.state(["!disabled"] if fmt == "html" else ["disabled"])

    def update_empty_comments(self) -> None:
        """Comments without text are a choice of markers only."""
        markers = self.comments.get() == "markers"
        self.empty_comments_box.state(["!disabled"] if markers else ["disabled"])

    def pick_output(self) -> None:
        fmt = self.output_format.get()
        kinds = {
            "html": ("the HTML report", [("HTML report", "*.html")]),
            "diff": ("the diff", [("Unified diff", "*.diff *.patch")]),
            "wdiff": ("the word diff", [("Word diff", "*.wdiff")]),
            "docx": ("the tracked changes", [("Word document", "*.docx")]),
            "odt": ("the tracked changes", [("OpenDocument text", "*.odt")]),
        }
        what, filetypes = kinds.get(fmt, kinds["html"])
        f = filedialog.asksaveasfilename(
            title=f"Save {what} as",
            defaultextension=FORMATS.get(fmt, ".html"),
            filetypes=filetypes,
        )
        if f:
            self.output.set(f)

    def load_repo(self, keep: tuple[str, str] | None = None) -> None:
        """Fill the base and target lists from the repository."""
        path = self.repo.get().strip()
        if not path:
            return
        try:
            commits, dirty = list_choices(path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError, ValueError):
            self.status.set(f"Not a git repository: {path}")
            self.base_box["values"] = self.target_box["values"] = ()
            return
        self.choices = {c.label: c.ref for c in commits}
        self.choices[WORKTREE] = "worktree"
        self.choices[INDEX] = "index"
        labels = [c.label for c in commits]
        self.base_box["values"] = labels
        self.target_box["values"] = [WORKTREE, INDEX, *labels]
        base, target = keep if keep and keep[0] else default_sides(commits, dirty)
        self.base.set(self.label_of(base))
        self.target.set(self.label_of(target))
        self.update_untracked()
        n = len(commits)
        self.status.set(
            f"{counted(n, 'commit')} listed" + (", uncommitted changes present." if dirty else ".")
        )

    def label_of(self, ref: str) -> str:
        for label, r in self.choices.items():
            if r == ref:
                return label
        return ref  # typed by hand: shown as it is

    def ref_of(self, text: str) -> str:
        """A list entry's ref, or what was typed (any ref git knows)."""
        text = text.strip()
        return self.choices.get(text, text)

    def update_untracked(self) -> None:
        on_worktree = self.ref_of(self.target.get()) in ("worktree", "")
        self.untracked_box.state(["!disabled"] if on_worktree else ["disabled"])

    def collect(self) -> Settings:
        """The settings the window shows."""
        context = self.context.get().strip()
        if not context.isdigit():
            context = "auto"
        moves = {}
        for sentences, similarity, algorithm in (
            (False, self.move_similarity, self.move_algorithm),
            (True, self.sentence_move_similarity, self.sentence_move_algorithm),
        ):
            # the default, while it is the one shown: it follows prosediff's
            default_similarity, default_algorithm = move_defaults(sentences)
            try:
                value = min(1.0, max(0.05, float(similarity.get())))
            except (tk.TclError, ValueError):
                value = default_similarity
            moves[sentences] = (
                None if value == default_similarity else value,
                None if algorithm.get() == default_algorithm else algorithm.get(),
            )
        try:
            language = normalize_language(self.language.get())
        except ValueError:
            language = DEFAULT
        try:
            encoding = check_encoding(self.encoding.get())
        except ValueError:
            encoding = AUTO_ENCODING
        return Settings(
            mode=self.mode.get(),
            repo=self.repo.get().strip(),
            base=self.ref_of(self.base.get()),
            target=self.ref_of(self.target.get()),
            untracked=self.untracked.get(),
            paths=[p.strip() for p in self.paths.get().split(";") if p.strip()],
            old=self.old.get().strip(),
            new=self.new.get().strip(),
            old_folder=self.old_folder.get().strip(),
            new_folder=self.new_folder.get().strip(),
            single=self.single.get().strip(),
            include=self.include.get().strip(),
            comments=self.comments.get(),
            empty_comments=self.empty_comments.get(),
            docx_changes=DOCX_CHANGE_VALUES.get(self.docx.get(), "accept-all"),
            align=self.align.get(),
            context_lines=context,
            full=self.full.get(),
            ignore_whitespace=self.ignore_ws.get(),
            move_similarity=moves[False][0],
            move_algorithm=moves[False][1],
            sentence_move_similarity=moves[True][0],
            sentence_move_algorithm=moves[True][1],
            move_passages=self.move_passages.get(),
            moved_passages=self.passage_choices(),
            split=self.split.get(),
            language=language,
            encoding=encoding,
            # the default is kept as "": it follows the sides next time
            output=(
                "" if self.output.get().strip() == self.auto_output else self.output.get().strip()
            ),
            output_format=self.output_format.get(),
            open_page=self.open_page.get(),
            assess=self.assess_spec(),
            assess_effort=self.effort_chosen(),
            assess_context=self.assess_context.get(),
            assess_instructions=self.assess_instructions.get().strip(),
            assess_save_prompt=self.assess_save_prompt.get(),
            assess_annotate=self.assess_annotate.get(),
            assess_preview=self.assess_preview.get(),
            assess_ai_writing=self.assess_ai_writing.get(),
            assess_documents=self.assess_documents.get(),
        )

    def toggle_advanced(self) -> None:
        """Open the advanced settings' window beside this one (on the side
        with room for it), or close it."""
        top = self.advanced_window
        if top.state() != "withdrawn":
            top.withdraw()
            return
        top.update_idletasks()
        width = top.winfo_reqwidth()
        # frame to frame: winfo_x and winfo_y are those of the window's frame
        frame = self.root.winfo_rootx() - self.root.winfo_x()
        x = self.root.winfo_rootx() + self.root.winfo_width() + frame + 8
        if x + width > self.root.winfo_screenwidth():
            x = max(self.root.winfo_x() - width - 2 * frame - 8, 0)
        top.geometry(f"+{x}+{self.root.winfo_y()}")
        top.deiconify()
        top.lift()
        dark_title_bar(top)

    def passage_choices(self) -> dict[str, float]:
        """The moved-passage settings shown that differ from prosediff's
        defaults; a box that holds no number keeps the default."""
        chosen: dict[str, float] = {}
        for f in fields(MovedPassageSettings):
            kind = setting_type(f)
            try:
                value = kind(float(self.passage_vars[f.name].get().replace(",", "")))
            except ValueError:
                continue
            if value != f.default:
                chosen[f.name] = value
        return chosen

    def reset_passage_settings(self) -> None:
        for f in fields(MovedPassageSettings):
            default = f"{f.default:g}" if f.metadata["share"] else f"{f.default:,}"
            self.passage_vars[f.name].set(default)

    def save_options(self) -> None:
        """Remember the choices shown, for the next time the window opens:
        only when asked, never on its own."""
        path = settings_file()
        if save_settings(self.collect(), path):
            self.status.set(f"Options saved: {path}")
        else:
            self.status.set(f"Options not saved: {path} cannot be written")

    def reset_options(self) -> None:
        """Every option to its default (what is compared and where the output
        goes stay as they are); nothing is saved until asked."""
        d = Settings()
        paragraphs, sentences = moves_of(d, False).resolved(False), moves_of(d, True).resolved(True)
        for var, value in (
            (self.comments, d.comments),
            (self.empty_comments, d.empty_comments),
            (self.docx, DOCX_CHANGE_LABELS[d.docx_changes]),
            (self.align, d.align),
            (self.context, d.context_lines),
            (self.full, d.full),
            (self.ignore_ws, d.ignore_whitespace),
            (self.move_similarity, paragraphs[0]),
            (self.move_algorithm, paragraphs[1]),
            (self.sentence_move_similarity, sentences[0]),
            (self.sentence_move_algorithm, sentences[1]),
            (self.split, d.split),
            (self.language, d.language),
            (self.encoding, d.encoding),
            (self.include, d.include),
            (self.untracked, d.untracked),
            (self.output_format, d.output_format),
            (self.open_page, d.open_page),
            (self.assess_ai, d.assess or NO_ASSESSMENT),
            (self.assess_context, d.assess_context),
            (self.assess_instructions, d.assess_instructions),
            (self.assess_save_prompt, d.assess_save_prompt),
            (self.assess_annotate, d.assess_annotate),
            (self.assess_preview, d.assess_preview),
            (self.assess_ai_writing, d.assess_ai_writing),
            (self.assess_documents, d.assess_documents),
        ):
            var.set(value)
        self.move_passages.set(d.move_passages)
        self.reset_passage_settings()
        self.rename_output()
        self.update_untracked()
        self.update_empty_comments()
        self.status.set("Options reset to their defaults (not saved).")

    # Running -------------------------------------------------------------------

    def run(self) -> None:
        if self.job is not None:
            return  # one comparison at a time
        s = self.collect()
        if s.mode == "review" and not s.assess:
            self.complain("Choose an AI, under AI assessment, to review the file.")
            return
        if s.assess and s.output_format == "html":
            try:
                parse_backend(s.assess)
            except AssessError as e:
                messagebox.showerror("prosediff", f"AI assessment: {e}")
                return
        self.set_stage("Starting…")
        self.progress.pack(side="right", padx=12)
        self.progress.start(12)
        # a process of its own, which Cancel stops with all it started
        context = multiprocessing.get_context("spawn")
        self.messages, self.replies = context.Queue(), context.Queue()
        self.job = context.Process(
            target=run_job, args=(s, self.messages, self.replies), daemon=True
        )
        self.job.start()
        self.job_settings = s
        self.show_cancel(True)
        self.root.after(100, self.poll, self.job)

    def cancel(self) -> None:
        """Stop the comparison running, and all it started."""
        if self.job is None:
            return
        stop_process_tree(self.job.pid)
        self.finish_job()
        self.status.set("Cancelled.")

    def finish_job(self) -> None:
        self.job = None
        self.preview_bar.pack_forget()
        self.progress.stop()
        self.progress.pack_forget()
        self.show_cancel(False)

    def show_cancel(self, running: bool) -> None:
        """The button Compare, or Cancel while a comparison runs."""
        if running:
            self.button.configure(
                text="Cancel", image=self.cancel_icon, command=self.cancel, bootstyle="danger"
            )
            self.button_tip.text = "Stop this comparison and the AI assessment (Esc)"
        elif self.reviewing():
            self.button.configure(
                text="Review", image=self.compare_icon, command=self.run, bootstyle="primary"
            )
            self.button_tip.text = (
                "Have the AI review the file, write the report and open it (Ctrl+Enter)"
            )
        else:
            self.button.configure(
                text="Compare", image=self.compare_icon, command=self.run, bootstyle="primary"
            )
            self.button_tip.text = "Compare, write the output and open it (Ctrl+Enter)"

    def set_stage(self, stage: str) -> None:
        self.stage, self.stage_started = stage, time.monotonic()
        self.status.set(stage)

    def poll(self, job: multiprocessing.process.BaseProcess | None) -> None:
        """Pick up what job, the comparison's process, says: each stage,
        shown with the seconds it has taken so far, then its result (tkinter
        must only be touched from its own thread). A poll of a job cancelled
        ends there, even when another has started since."""
        if job is None or job is not self.job:
            return  # cancelled
        try:
            kind, value = self.messages.get_nowait()
        except queue.Empty:
            if not self.job.is_alive():
                # its last word may still be on its way when it has ended
                try:
                    kind, value = self.messages.get(timeout=2)
                except queue.Empty:
                    code = self.job.exitcode
                    self.finish_job()
                    self.status.set("Not compared.")
                    messagebox.showerror(
                        "prosediff",
                        f"The comparison stopped without a result (exit code {code}).",
                    )
                    return
                self.handle(kind, value)
                return
            seconds = time.monotonic() - self.stage_started
            if seconds >= 1:
                self.status.set(f"{self.stage} {duration(seconds)}")
            self.root.after(100, self.poll, self.job)
            return
        self.handle(kind, value)

    def handle(self, kind: str, value) -> None:
        """One message of the comparison's process: a stage, its result, or
        its error."""
        if kind == "stage":
            self.set_stage(value)
            self.root.after(100, self.poll, self.job)
            return
        if kind == "preview":
            self.preview(value)
            return
        s = self.job_settings
        self.finish_job()
        if kind == "error":
            self.status.set("Not compared.")
            messagebox.showerror("prosediff", value)
            return
        result: JobResult = value
        path, assessment = result.path, result.assessment
        summary = (
            f"{result.reviewed} reviewed"
            if result.reviewed
            else f"{counted(result.files, 'file')} changed, "
            f"+{result.additions:,} −{result.deletions:,} lines"
        )
        if assessment is not None:
            summary += (
                "; the assessment failed"
                if assessment.error
                else f"; assessed: {assessment.verdict or 'see the report'}"
            )
        self.status.set(f"{summary}: {path.name}")
        if assessment is not None and assessment.error:
            messagebox.showwarning("prosediff", f"The AI assessment failed: {assessment.error}")
        ttk.ToastNotification(
            "prosediff",
            f"{summary}\n{path.name}",
            duration=TOAST_MS,
            bootstyle="success",
            icon="",
        ).show_toast()
        if s.open_page:
            open_output(path)

    def preview(self, path: Path) -> None:
        """The report without the assessment, open in the browser: whether
        its text goes to the AI is asked in this window (answer_preview), not
        in a dialog, which the browser opening would cover."""
        self.set_stage("Preview open in the browser: send it to the AI?")
        what, it = (
            ("the file", "it") if self.job_settings.mode == "review" else ("the changes", "them")
        )
        self.preview_text.set(
            f"The report without the AI assessment is open in the browser: {path.name}. "
            f"Send {what} to {self.job_settings.assess} for assessment? Send: the "
            f"AI assesses {it} and the report is written again with its assessment. "
            "Don't send: the report stays as it is."
        )
        self.preview_bar.pack(fill="x", side="bottom", pady=(10, 0), after=self.bottom)
        self.send_button.focus_set()
        webbrowser.open(path.resolve().as_uri())

    def answer_preview(self, send: bool) -> None:
        """Send the preview's answer to the comparison's process, which then
        goes on."""
        self.preview_bar.pack_forget()
        if self.job is None:
            return  # cancelled while the question was open
        self.replies.put(send)
        self.root.after(100, self.poll, self.job)


def hint(widget: tk.Misc, text: str) -> None:
    """What a widget does, shown when the pointer rests on it."""
    ttk.ToolTip(widget, text=text, wraplength=HINT_WIDTH, delay=HINT_DELAY_MS)


def popdown_listbox(combo: ttk.Combobox, popdown: str) -> str | None:
    """The listbox of a combobox's drop-down list, found among the widgets of
    its popdown (at .f.l on Windows and X11, elsewhere on macOS); None when
    it has none."""
    widgets = [popdown]
    while widgets:
        w = widgets.pop()
        if combo.tk.call("winfo", "class", w) == "Listbox":
            return w
        widgets += combo.tk.splitlist(combo.tk.call("winfo", "children", w))
    return None


def item_hints(combo: ttk.Combobox, tip: Callable[[str], str]) -> ttk.Combobox:
    """What each item of a drop-down list means, shown beside the item under
    the pointer while the list is open; tip gives an item's hint ("" for
    none). Tk's combobox list is a plain Tk listbox with no Python widget
    behind it (ttk::combobox::PopdownWindow), so ttkbootstrap's ToolTip
    cannot take it: a small window of our own shows the hint."""
    popdown = combo.tk.eval(f"ttk::combobox::PopdownWindow {combo}")
    listbox = popdown_listbox(combo, popdown)
    if listbox is None:
        return combo  # a Tk that builds its list otherwise: no hints, all else works
    window: list[tk.Toplevel] = []

    def hide(*_) -> None:
        while window:
            window.pop().destroy()

    def show(y: str) -> None:
        hide()
        if not int(combo.tk.call("winfo", "ismapped", popdown)):
            return  # a motion left over once the list closed
        index = int(combo.tk.call(listbox, "nearest", y))
        text = tip(str(combo.tk.call(listbox, "get", index)))
        if not text:
            return
        top = tk.Toplevel(combo)
        top.wm_overrideredirect(True)
        top.attributes("-topmost", True)
        ttk.Label(
            top, text=text, wraplength=HINT_WIDTH, padding=(8, 4), bootstyle="inverse-dark"
        ).pack()
        x0, y0 = int(combo.tk.call("winfo", "rootx", listbox)), int(y)
        width = int(combo.tk.call("winfo", "width", listbox))
        top.wm_geometry(f"+{x0 + width + 4}+{int(combo.tk.call('winfo', 'rooty', listbox)) + y0}")
        window.append(top)

    combo.tk.call("bind", listbox, "<Motion>", (combo.register(show), "%y"))
    combo.tk.call("bind", listbox, "<Leave>", combo.register(hide))
    combo.tk.call("bind", popdown, "<Unmap>", combo.register(hide))
    return combo


def browse(parent: tk.Misc, command, tip: str) -> ttk.Button:
    """A button that opens a file or folder dialog."""
    button = ttk.Button(
        parent,
        image=ttk.Icon("folder2-open", size=16),
        command=command,
        bootstyle="secondary-outline",
    )
    hint(button, tip)
    return button


def toggle(parent: tk.Misc, text: str, variable: tk.BooleanVar) -> ttk.Checkbutton:
    """A yes-or-no option, as a switch."""
    return ttk.Checkbutton(parent, text=text, variable=variable, bootstyle="round-toggle")


def field_row(parent: tk.Misc, row: int, label: str, widget: tk.Misc, tip: str) -> None:
    """A labelled field of an options card, explained by a tooltip on both
    the label and the field, and an info icon."""
    text = ttk.Label(parent, text=label)
    text.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
    widget.grid(row=row, column=1, sticky="w", pady=4)
    info = ttk.Label(parent, image=ttk.Icon("info-circle", size=14), bootstyle="secondary")
    info.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=4)
    for w in (text, widget, info):
        hint(w, tip)


def switch_row(
    parent: tk.Misc, row: int, label: str, variable: tk.BooleanVar, tip: str
) -> ttk.Checkbutton:
    """A yes-or-no option of an options card, explained by a tooltip."""
    box = toggle(parent, label, variable)
    box.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
    hint(box, tip)
    return box


def move_fields(parent: tk.Misc, similarity: tk.DoubleVar, algorithm: tk.StringVar) -> ttk.Frame:
    """A moved-line setting: its threshold and its algorithm, side by side."""
    frame = ttk.Frame(parent)
    ttk.Spinbox(
        frame,
        from_=0.05,
        to=1.0,
        increment=0.05,
        format="%.2f",
        textvariable=similarity,
        width=5,
    ).pack(side="left")
    item_hints(
        ttk.Combobox(
            frame, textvariable=algorithm, values=tuple(MOVE_ALGORITHMS), state="readonly", width=11
        ),
        MOVE_ALGORITHM_HINTS.get,
    ).pack(side="left", padx=(6, 0))
    return frame


def swap(a: tk.StringVar, b: tk.StringVar) -> None:
    """Exchange the values of two fields."""
    first = a.get()
    a.set(b.get())
    b.set(first)


def ask_second_file(root: tk.Tk, first: Path) -> Path | None:
    """The file to compare first with, chosen in a dialog opened in its folder."""
    chosen = filedialog.askopenfilename(
        parent=root,
        title=f"Compare {first.name} with…",
        initialdir=str(first.parent),
        filetypes=[("Word, OpenDocument and Markdown", "*.docx *.odt *.md"), ("All files", "*.*")],
    )
    return Path(chosen) if chosen else None


def received(args: list[str]) -> str:
    """The arguments as the program got them, one per line and quoted, so a
    path split at a space or quoted twice shows as such; a path that does
    not exist is marked."""
    lines = [f"Received {counted(len(args), 'argument')}:"]
    for i, a in enumerate(args, 1):
        missing = "" if Path(a).exists() else "  (not found)"
        lines.append(f"{i}. “{a}”{missing}")
    return "\n".join(lines)


class _AllocConsoleOptions(ctypes.Structure):
    _fields_ = (
        ("mode", ctypes.c_int),
        ("use_show_window", ctypes.c_int),
        ("show_window", ctypes.c_ushort),
    )


ALLOC_CONSOLE_MODE_NO_WINDOW = 2


def invisible_console() -> bool:
    """On Windows, a console without a window for the window program, when it
    has none: the console programs it starts (Claude Code for an AI
    assessment, Codex) share it rather than each opening a console window of
    its own, which the Claude Agent SDK, starting its program with no flags,
    would otherwise do. Needs Windows 11 24H2 (AllocConsoleWithOptions);
    earlier, nothing is done. Whether a console was made."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    # attached to a console, with a window or not (GetConsoleWindow sees
    # only a console's window)
    attached = kernel32.GetConsoleProcessList((ctypes.c_ulong * 1)(), 1) > 0
    if attached or not hasattr(kernel32, "AllocConsoleWithOptions"):
        return False  # a console already, or a Windows without the call
    options = _AllocConsoleOptions(ALLOC_CONSOLE_MODE_NO_WINDOW, 0, 0)
    result = ctypes.c_int(0)
    return kernel32.AllocConsoleWithOptions(ctypes.byref(options), ctypes.byref(result)) == 0


def own_taskbar_button() -> None:
    """On Windows, a taskbar button of prosediff's own, showing its icon,
    rather than one grouped with every other Python program under Python's.
    Called before the first window is made."""
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("prosediff.gui")


# The Bootstrap theme, in the system's light or dark.
LIGHT_THEME, DARK_THEME = "bootstrap-light", "bootstrap-dark"
# Windows' setting: its apps in light or dark ("app mode").
PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
# How long to wait for a system setting (seconds): it answers at once, or
# never.
SETTING_TIMEOUT = 2
# The desktop portal's colour scheme for dark.
PREFER_DARK = 1
# DwmSetWindowAttribute's attribute for a dark title bar (Windows 10 20H1 on).
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def system_dark() -> bool:
    """Whether the system asks for dark windows: Windows' app mode, macOS's
    appearance, Linux's colour scheme (the desktop portal's, which GNOME,
    KDE and others set); when it cannot be told, light."""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            done = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=SETTING_TIMEOUT,
            )
            return done.stdout.strip() == "Dark"
        if sys.platform.startswith("linux"):
            return portal_color_scheme() == PREFER_DARK
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def portal_color_scheme() -> int | None:
    """The colour scheme the freedesktop desktop portal reports on its
    session bus (1: prefer dark, 2: prefer light, 0: none), read with
    jeepney; None when there is no portal to ask."""
    try:
        from jeepney import DBusAddress, DBusErrorResponse, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return None
    portal = DBusAddress(
        "/org/freedesktop/portal/desktop",
        bus_name="org.freedesktop.portal.Desktop",
        interface="org.freedesktop.portal.Settings",
    )
    try:
        with open_dbus_connection(bus="SESSION", auth_timeout=SETTING_TIMEOUT) as bus:
            # ReadOne since version 2 of the portal; Read, now deprecated,
            # before it
            for method in ("ReadOne", "Read"):
                call = new_method_call(
                    portal, method, "ss", ("org.freedesktop.appearance", "color-scheme")
                )
                try:
                    reply = bus.send_and_get_reply(call, timeout=SETTING_TIMEOUT)
                except DBusErrorResponse:
                    continue
                return unwrap_variant(reply.body[0])
    except (OSError, KeyError, ValueError, TimeoutError, DBusErrorResponse):
        return None
    return None


def unwrap_variant(value) -> int | None:
    """An integer out of D-Bus variants as jeepney gives them, (signature,
    value) pairs, nested once by the portal's deprecated Read."""
    while isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        value = value[1]
    return value if isinstance(value, int) else None


def use_theme(root: tk.Tk | tk.Toplevel) -> None:
    """The Bootstrap theme on the window, light or dark as the system is;
    on Windows, a title bar to match."""
    dark = system_dark()
    ttk.Style(theme=DARK_THEME if dark else LIGHT_THEME)
    if dark:
        dark_title_bar(root)


def dark_title_bar(root: tk.Tk | tk.Toplevel) -> None:
    """On Windows, a dark title bar for a window of the dark theme."""
    if system_dark() and sys.platform == "win32":
        try:
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(on), ctypes.sizeof(on)
            )
        except (AttributeError, OSError, tk.TclError):
            pass  # an older Windows: a light title bar


def set_icon(root: tk.Tk) -> None:
    """The prosediff logo on the title bar and the taskbar, and on the
    dialogs and message boxes too: the .ico, with all its sizes, on Windows,
    the .png elsewhere."""
    here = Path(__file__).parent
    try:
        if sys.platform == "win32":
            # the window's own, then the default of the dialogs made later
            # (with default=, tkinter ignores the first argument)
            root.iconbitmap(str(here / "logo.ico"))
            root.iconbitmap(default=str(here / "logo.ico"))
        else:
            root.iconphoto(True, tk.PhotoImage(master=root, file=str(here / "logo.png")))
    except tk.TclError:  # no icon rather than no window
        pass


def main(argv: list[str] | None = None) -> None:
    """prosediff-gui [REPOSITORY | FILE | OLD NEW]: the window, prefilled from
    the arguments when they are a git repository, Markdown, Word or OpenDocument files, or
    two folders; for one file, a dialog asks for the file to compare it with.
    Arguments that are none of these are reported in an error box, with the
    arguments received, and the program exits once it is dismissed."""
    args = sys.argv[1:] if argv is None else argv
    settings, note = settings_from_args(args, load_settings())
    invisible_console()
    own_taskbar_button()
    root = tk.Tk()
    set_icon(root)
    if note:
        root.withdraw()  # the error box alone, no empty window behind it
        messagebox.showerror(
            "prosediff",
            f"{note}\n\n{received(args)}\n\nUsage: prosediff-gui [REPOSITORY | FILE | OLD NEW]",
            parent=root,
        )
        root.destroy()
        sys.exit(2)
    first = single_file(args)
    if first is not None:
        root.withdraw()  # the dialog alone, then the window
        second = ask_second_file(root, first)
        if second is not None and second.resolve() != first:
            settings = with_second_file(settings, first, second)
        else:
            note = f"Choose the file to compare {first.name} with."
        root.deiconify()
    app = App(root, settings)
    if note:
        app.status.set(note)
    root.mainloop()


if __name__ == "__main__":
    main()
