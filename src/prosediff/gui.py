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

import ctypes
import functools
import json
import multiprocessing
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk as tk_ttk

import git
import ttkbootstrap as ttk

from prosediff.assess import (
    ASSESS_TIMEOUT,
    CLAUDE_DEFAULT,
    CONTEXTS,
    AssessError,
    Assessment,
    ModelInfo,
    default_system,
    duration,
    instructions_file,
    models_of,
    parse_backend,
    providers,
)
from prosediff.diff import (
    AUTO_ENCODING,
    COMMENT_MODES,
    MAX_HIDDEN,
    MOVE_ALGORITHMS,
    Context,
    MovedPassageSettings,
    MoveSettings,
    check_encoding,
    move_defaults,
    stop_process_tree,
)
from prosediff.document import CHANGES as DOCX_CHANGES
from prosediff.history import config_dir
from prosediff.language import DEFAULT, DOCUMENT, GUESS, normalize_language
from prosediff.pipeline import Run, execute, options_of, request_of
from prosediff.render import (
    ALIGNMENTS,
    FORMATS,
    SPLITS,
    TEXT_SUFFIXES,
    check_split,
    counted,
    default_output,
    default_split,
    format_of,
    open_output,
)
from prosediff.sources import FOLDER_FILES, page_of, suffix_of
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
# What the AI field says while the AIs are being found.
LOADING = "Loading…"
# The effort item of a model that says no default of its own: that default.
MODEL_DEFAULT = "default"
AIS = (NO_ASSESSMENT, "claude", "codex", "ollama")
# How the tracked changes are settled (DOCX_CHANGES), as the list names it.
DOCX_CHANGE_LABELS = {"accept-all": "accept all", "reject-all": "reject all", "show": "show"}
DOCX_CHANGE_VALUES = {label: value for value, label in DOCX_CHANGE_LABELS.items()}
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
    # the AI's answers kept beside a report, to make it again (the tab "rebuild")
    rebuild_file: str = ""
    # the files of two folders compared: glob patterns separated by "|"
    include: str = FOLDER_FILES
    # "markers": set apart from the text (a marker and a panel in the HTML
    # report, CriticMarkup in the diffs); "text": compared as text; "none"
    comments: str = "markers"
    empty_comments: bool = False
    skip_resolved: bool = True
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
    # a shell command both versions of every Markdown file are piped through
    md_filter: str = ""
    # the unchanged lines embedded per gap, for the HTML report to reveal
    max_hidden: int = MAX_HIDDEN
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
    # other files sent to the AI as context, separated by ";", while the
    # switch is on
    assess_send_files: bool = False
    assess_files: str = ""
    # the prompts in place of prosediff's own (text, or a file; "":
    # prosediff's): for two versions compared, for one file reviewed, and for
    # whether the new text reads as written by an AI
    assess_prompt: str = ""
    assess_review_prompt: str = ""
    assess_writing_prompt: str = ""
    assess_review_writing_prompt: str = ""
    # who the AI's comments in the documents are by; "": the AI and its model
    assess_author: str = ""
    assess_save_prompt: bool = False
    # how long the AI may take, in seconds
    assess_timeout: float = ASSESS_TIMEOUT
    # whether the AI marks the problems in the text
    assess_annotate: bool = True
    # whether the AI may edit the text (fixes as rewordings)
    assess_edits: bool = True
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
REBUILD_READY = "Choose the AI's answers saved beside a report (.ai.json), then Rebuild."
# The tooltips: their width in pixels, and how long the pointer must rest.
HINT_WIDTH = 360
HINT_DELAY_MS = 400
# How long the notification of a finished comparison stays up.
TOAST_MS = 4000
# The tabs, in their order: the last reviews one file, nothing compared.
MODES = ("git", "files", "folders", "review", "rebuild")
PREFILLED_FILES = (".md", ".docx", ".odt")
# The space around the fields of the window.
PAD = {"padx": 6, "pady": 4}
# What is compared, which Reset to defaults leaves as it is.
COMPARED = ("mode", "repo", "old", "new", "old_folder", "new_folder", "single")
# The settings of a box of a number, as its error names them.
NUMBER_NAMES = {"max_hidden": "Hidden lines", "assess_timeout": "The AI's timeout"}


def prefillable(path: Path) -> bool:
    """Whether path is a Markdown, Word or OpenDocument file, which the
    window's tabs are filled in with."""
    return path.is_file() and suffix_of(path) in PREFILLED_FILES


def settings_from_args(args: list[str], base: Settings) -> tuple[Settings, str]:
    """The settings to open the window with, given its command-line arguments.

    One argument that is a git repository (or a folder inside one) fills in
    the repository, the sides starting from their defaults; one Markdown,
    Word or OpenDocument file fills in the One file tab, to review it alone;
    two such files fill in the files tab, two
    folders the folders tab. Anything else is ignored, and the second value says why.
    """
    s = replace(base)
    if len(args) == 1:
        path = Path(args[0])
        if prefillable(path):
            s.mode = "review"
            s.single = str(path.resolve())
            s.output = ""  # next to the file (App.follow_sides)
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
        if prefillable(old) and prefillable(new):
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
    return config_dir() / "gui.json"


# The settings that are one of a list of choices: a value saved that is no
# longer among them (prosediff upgraded) gives way to the default.
CHOICES = {
    "mode": MODES,
    "split": SPLITS,
    "comments": COMMENT_MODES,
    "docx_changes": DOCX_CHANGES,
    "output_format": tuple(FORMATS),
    "assess_context": CONTEXTS,
}


def load_settings(path: Path | None = None) -> Settings:
    """The choices saved (Save options), or the defaults; a value that is
    no choice the window offers any more gives way to its default."""
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
        known = Settings.__dataclass_fields__
        s = Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()
    d = Settings()
    return replace(
        s, **{k: getattr(d, k) for k, choices in CHOICES.items() if getattr(s, k) not in choices}
    )


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


def check_numbers(s: Settings) -> None:
    """Refuse what the command line refuses too (ValueError): a negative
    number of context lines (context_of) or of hidden lines, a timeout of
    none."""
    context_of(s)
    if s.max_hidden < 0:
        raise ValueError("Hidden lines must be 0 or more.")
    if s.assess_timeout <= 0:
        raise ValueError("The AI's timeout must be above 0 seconds.")


def context_of(s: Settings) -> Context:
    """The context for compare(), from the Context lines box; ValueError
    for a negative number of lines, refused as the command line refuses it."""
    if s.full:
        return None
    try:
        lines = int(s.context_lines)
    except ValueError:  # "auto", or anything that is not a number
        return "auto"
    if lines < 0:
        raise ValueError("Context lines must be 0 or more (or auto).")
    return lines


def run_of(s: Settings) -> Run:
    """The run the settings ask for (pipeline.Run); ValueError for settings
    that ask for none: sides or an AI to review with not chosen, a split or
    a document of tracked changes the output cannot hold."""
    options = options_of(
        s,
        context=context_of(s),
        paragraph_moves=moves_of(s, False),
        sentence_moves=moves_of(s, True),
        moved_passage_settings=MovedPassageSettings.from_choices(s.moved_passages),
    )
    request = request_of(s)
    if request and s.mode == "review":
        request = replace(
            request, system=s.assess_review_prompt, writing_system=s.assess_review_writing_prompt
        )
    common = {
        "options": options,
        "align": s.align,
        "request": request,
        "documents": s.assess_documents,
    }
    if s.mode == "review":
        if not s.single:
            raise ValueError("choose the file to review")
        if not s.assess:
            raise ValueError("choose an AI to review the file")
        out = Path(with_format(s.output, "html")) if s.output else page_of("review", s.single, "")
        return Run("review", s.single, out, ai_writing=s.assess_ai_writing, **common)
    old, new = sides(s)
    fmt = s.output_format if s.output_format in FORMATS else "html"
    split = s.split if s.split in SPLITS else default_split(fmt)
    if split == "both" and fmt != "html":
        split = default_split(fmt)  # a diff holds one split
    check_split(split, fmt)
    if fmt in TRACKED_FORMATS and s.mode == "files" and old and new:
        check_paths(old, new, fmt)  # before comparing them
    if s.mode == "git":
        if not s.repo or not s.base:
            raise ValueError("choose a repository and a base")
    elif not old or not new:
        raise ValueError(f"choose the old and the new {'file' if s.mode == 'files' else 'folder'}")
    if s.output:
        out = Path(with_format(s.output, fmt))
    else:
        out = page_of(s.mode, old, new, FORMATS[fmt]) or default_output(fmt)
    run = Run(
        s.mode,
        old,
        out,
        new=new,
        paths=s.paths or None,
        fmt=fmt,
        split=split,
        ai_writing=s.assess_ai_writing,
        **common,
    )
    if s.mode == "git":
        run.old, run.new = s.repo, s.base
        run.target = None if s.target in ("worktree", "index", "") else s.target
        run.cached = s.target == "index"
        run.untracked = s.untracked and s.target in ("worktree", "")
    elif s.mode == "folders":
        run.include = s.include
    return run


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
    # why the AI-writing assessment failed ("": it did not, or was not asked)
    writing_error: str = ""


# How long a preview waits for the window to say whether the AI assesses;
# unanswered, it does not.
PREVIEW_WAIT_S = 3600

# Why a comparison can fail: the errors the window reports, others being bugs.
JOB_ERRORS = (
    git.InvalidGitRepositoryError,
    git.NoSuchPathError,
    git.BadName,
    git.GitCommandError,
    RuntimeError,  # git diff failed or timed out, FilterError, SourceError
    ValueError,
    OSError,
)


def rebuild_run(s: Settings, messages):
    """The run kept with the answers s names, and the answers (prosediff.saved),
    its warnings sent as stages; written where Save to says, else over the
    report they were saved beside."""
    from prosediff.saved import load

    run, assessment, writing, warnings = load(s.rebuild_file.strip())
    for warning in warnings:
        messages.put(("stage", f"Warning: {warning}"))
    if s.output.strip():
        run = replace(run, output=Path(with_format(s.output.strip(), "html")))
    return run, (assessment, writing)


def run_job(s: Settings, messages, replies) -> None:
    """Compare as the settings say and write the output (pipeline.execute),
    in a process of its own that the window can stop: each
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
        run, saved = rebuild_run(s, messages) if s.mode == "rebuild" else (run_of(s), None)
        done = execute(
            run,
            lambda stage: messages.put(("stage", stage)),
            approve if s.assess_preview else None,
            live=lambda detail: messages.put(("live", detail)),
            saved=saved,
        )
    except JOB_ERRORS as e:
        messages.put(("error", str(e) or type(e).__name__))
        return
    except Exception as e:  # a bug: said rather than left unanswered
        messages.put(("error", f"{type(e).__name__}: {e}"))
        return
    c, counts = done.comparison, done.comparison.counts
    result = JobResult(
        done.path,
        len(c.files),
        counts.additions,
        counts.deletions,
        done.assessment,
        c.repo_name if c.single else "",
        (done.writing.error or "") if done.writing is not None else "",
    )
    messages.put(("done", result))


def with_format(path: str, fmt: str) -> str:
    """The output file named for the format chosen: .html, .diff (a .patch
    stays one) or .wdiff; any other name, or none, stays."""
    if suffix_of(path) not in (".html", *TEXT_SUFFIXES) or format_of(path) == fmt:
        return path
    return str(Path(path).with_suffix(FORMATS[fmt]))


def moves_of(s: Settings, sentences: bool) -> MoveSettings:
    """The moved-line settings chosen for paragraphs (or sentences); None
    for prosediff's default, and for an algorithm it does not know."""
    similarity = s.sentence_move_similarity if sentences else s.move_similarity
    algorithm = s.sentence_move_algorithm if sentences else s.move_algorithm
    return MoveSettings(similarity, algorithm if algorithm in MOVE_ALGORITHMS else None)


def passes(check: Callable[..., object], *args: object) -> bool:
    """Whether check(*args) passes: raises no ValueError."""
    try:
        check(*args)
    except ValueError:
        return False
    return True


def sides(s: Settings) -> tuple[str, str]:
    """The old and the new file, or folder, of the tab shown."""
    return (s.old_folder, s.new_folder) if s.mode == "folders" else (s.old, s.new)


class App:
    """The window."""

    def __init__(self, root: tk.Tk | tk.Toplevel, settings: Settings | None = None) -> None:
        self.root = root
        use_theme(root)
        self.s = settings or load_settings()
        # the widget variable of each setting shown as it is, by its name in
        # Settings (setting): collect reads them, reset_options resets them
        self.vars: dict[str, tk.Variable] = {}
        self.choices: dict[str, str] = {}  # label -> ref
        # the comparison running (a process of its own), what it sends back,
        # its settings, and the stage it is at
        self.job: multiprocessing.process.BaseProcess | None = None
        self.messages = self.replies = None
        self.job_settings: Settings | None = None
        self.stage, self.stage_started = "", 0.0
        # what the model is doing, while it is asked (pipeline.execute's live)
        self.live = ""
        root.title("prosediff: compare two versions")
        root.minsize(780, 0)
        # The window takes the size its content asks for, but never shrinks
        # back: a status line that gets shorter (6 minutes 59 seconds, then 7
        # minutes) must not make it jump. Each size it grows to becomes its
        # minimum.
        root.bind("<Configure>", self.keep_largest_size, add="+")
        page = ttk.Frame(root, padding=(14, 12, 14, 12))
        page.pack(fill="both", expand=True)

        # What is compared: a git repository, two files or two folders, one
        # at a time, chosen with a segmented button
        self.mode = self.setting("mode")
        switch = ttk.Frame(page)
        switch.pack(fill="x", pady=(0, 8))
        for value, text, icon in (
            ("git", "Git repository", "git"),
            ("files", "Files", "files"),
            ("folders", "Folders", "folder2"),
            ("review", "One file", "file-earmark-text"),
            ("rebuild", "Rebuild", "arrow-repeat"),
        ):
            segment(
                switch,
                self.mode,
                value,
                text,
                padding=(14, 6),
                command=self.show_mode,
                bootstyle="primary-outline-toolbutton",
                image=ttk.Icon(icon, size=16),
                compound="left",
            )
        source = self.source_card = ttk.Labelframe(page, text="Versions", padding=(10, 8))
        source.pack(fill="x")
        self.sides = {mode: ttk.Frame(source) for mode in MODES}
        for side in self.sides.values():
            side.columnconfigure(1, weight=1)
        self.build_git_side(self.sides["git"])
        self.build_path_sides(self.sides["files"], self.sides["folders"])
        self.build_review_side(self.sides["review"])
        self.build_rebuild_side(self.sides["rebuild"])

        # The options that change what the comparison finds, in one card; how
        # the report shows it goes with the advanced settings
        compared = self.compared_card = ttk.Labelframe(page, text="Comparison", padding=(10, 8))
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
        self.repo = self.setting("repo")
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
        self.untracked = self.setting("untracked")
        self.untracked_box = toggle(git_side, "Include untracked files", self.untracked)
        self.untracked_box.grid(row=4, column=1, sticky="w", **PAD)

    def build_path_sides(self, files_side: ttk.Frame, folders_side: ttk.Frame) -> None:
        """The fields of two files, and of two folders."""
        self.old = self.setting("old")
        self.new = self.setting("new")
        self.old_folder = self.setting("old_folder")
        self.new_folder = self.setting("new_folder")
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
        self.include = self.setting("include")
        include_entry = ttk.Entry(folders_side, textvariable=self.include)
        include_entry.grid(row=2, column=1, sticky="ew", **PAD)
        hint(
            include_entry,
            "The files compared: patterns separated by |, matched against each file's name "
            "(its path within the folder for a pattern with a /); empty: every file.",
        )

    def build_review_side(self, side: ttk.Frame) -> None:
        """The field of one file, reviewed alone."""
        self.single = self.setting("single")
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

    def build_rebuild_side(self, side: ttk.Frame) -> None:
        """The field of the AI's answers kept beside a report, to make it again."""
        self.rebuild_file = self.setting("rebuild_file")
        ttk.Label(side, text="Saved answers").grid(row=0, column=0, sticky="w", **PAD)
        entry = ttk.Entry(side, textvariable=self.rebuild_file)
        entry.grid(row=0, column=1, sticky="ew", **PAD)
        hint(entry, "The NAME.ai.json prosediff saves beside every report the AI assessed.")
        browse(side, self.pick_saved, "Choose the AI's saved answers (.ai.json)").grid(
            row=0, column=2, **PAD
        )
        ttk.Label(
            side,
            text="The report made again as this prosediff writes it, from the AI's answers "
            "kept beside it: the files compared again as then, the AI not asked again.",
            bootstyle="secondary",
        ).grid(row=1, column=1, sticky="w", padx=6)

    def pick_saved(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="The AI's saved answers",
            filetypes=[("Saved answers", "*.ai.json"), ("All", "*.*")],
        )
        if chosen:
            self.rebuild_file.set(chosen)

    def rebuilding(self) -> bool:
        """Whether a report is made again from saved answers (the tab
        "rebuild"): no comparison options, no AI to ask."""
        return self.mode.get() == "rebuild"

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
            choice_box(compared, self.docx, [DOCX_CHANGE_LABELS[v] for v in DOCX_CHANGES]),
            "Word and OpenDocument tracked changes: accept them all, reject them all, or "
            "show them, as Word does.",
        )
        self.language = self.setting("language")
        # any code can be typed; the list holds the common ones
        field_row(
            compared,
            1,
            "Language",
            ttk.Combobox(compared, textvariable=self.language, values=LANGUAGES, width=12),
            "Splits sentences and hyphenates lines. default: the language Word and "
            "OpenDocument files are marked with, else guessed; document: only that; guess: "
            "guessed from each file's text; or a code such as it.",
        )
        self.comments = self.setting("comments")
        self.comments_box = choice_box(right, self.comments, COMMENT_MODES)
        self.comments_tips = field_row(right, 0, "Comments", self.comments_box, COMMENTS_TIP)
        # "text", given up for "markers" reviewing one file, to come back after
        self.comments_given_up = False
        self.comments.trace_add("write", lambda *_: self.update_empty_comments())
        self.split = self.setting("split")
        splits = ttk.Frame(compared)
        split_names = (("paragraph", "Paragraphs"), ("sentence", "Sentences"), ("both", "Both"))
        # what only a comparison has, greyed out reviewing one file (show_mode)
        self.comparing_only: list[tk_ttk.Widget] = []
        # the grid cells only a comparison has, left out of the One file tab
        # (show_mode)
        self.review_hidden: list[tk.Misc] = []
        # each split's button, greyed out for an output that cannot hold it
        # (update_splits); the split given up for the output's default, to
        # come back with an output that can
        self.split_buttons: dict[str, ttk.Radiobutton] = {}
        self.split_given_up: tuple[str, str] | None = None
        for value, text in split_names:
            self.split_buttons[value] = segment(splits, self.split, value, text)
            self.comparing_only.append(self.split_buttons[value])
        field_row(
            compared,
            2,
            "Compare by",
            splits,
            "How prose is compared: paragraph by paragraph, sentence by sentence (a sentence "
            "moved between paragraphs is recognised), or both, in one HTML report whose "
            "toolbar switches between the two.",
        )
        self.review_hidden += compared.grid_slaves(row=2)
        self.ignore_ws = self.setting("ignore_whitespace")
        ignore = switch_row(
            right,
            1,
            "Ignore whitespace",
            self.ignore_ws,
            "Lines that differ only in spacing are the same, as git diff -w.",
        )
        self.comparing_only.append(ignore)
        self.review_hidden.append(ignore)
        self.skip_resolved = self.setting("skip_resolved")
        switch_row(
            right,
            2,
            "Skip resolved comments",
            self.skip_resolved,
            "Leave out the comments of Word and OpenDocument files marked resolved, and the "
            "replies to them: not shown in the report, and not sent to the AI.",
        )

    def build_report_card(self, card: ttk.Labelframe) -> None:
        """The options of how the report shows the comparison, in two columns."""
        compared = ttk.Frame(card)
        compared.grid(row=0, column=0, sticky="nw")
        shown = ttk.Frame(card)
        shown.grid(row=0, column=1, sticky="nw", padx=(18, 0))
        self.context = self.setting("context_lines")
        # "auto": 0 around the changes of Markdown and Word, 3 of other files
        field_row(
            compared,
            0,
            "Context lines",
            ttk.Spinbox(compared, values=("auto", *range(51)), textvariable=self.context, width=10),
            "Unchanged lines shown around each change. auto: none in Markdown files and Word "
            "documents, whose lines are paragraphs; 3 in the others.",
        )
        self.align = self.setting("align")
        field_row(
            compared,
            1,
            "Wrapped lines",
            choice_box(compared, self.align, ALIGNMENTS),
            "How long lines that wrap are aligned in the HTML report. left: ragged on the "
            "right; justify: on both sides, hyphenated.",
        )
        self.max_hidden = self.setting("max_hidden")
        field_row(
            compared,
            2,
            "Hidden lines",
            ttk.Spinbox(
                compared,
                from_=0,
                to=1_000_000,
                increment=100,
                textvariable=self.max_hidden,
                width=10,
            ),
            "The unchanged lines embedded in the HTML report per gap, for it to reveal; "
            f"longer gaps are left out. Default: {MAX_HIDDEN:,}.",
        )
        self.move_passages = self.setting("move_passages")
        switch_row(
            shown,
            0,
            "Moved passages",
            self.move_passages,
            "Also follow the passages moved within a paragraph or between two: words removed "
            "in one place and added in another, as alike as the moved paragraphs (sentences) "
            "must be, are shown as moved, not as a deletion and an unrelated insertion.",
        )
        self.full = self.setting("full")
        switch_row(shown, 1, "Whole files", self.full, "Show every line of each changed file.")
        self.empty_comments = self.setting("empty_comments")
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
        # the similarity and the algorithm of paragraphs (False) and sentences
        self.moves: dict[bool, tuple[tk.DoubleVar, tk.StringVar]] = {}
        for sentences in (False, True):
            similarity, algorithm = moves_of(self.s, sentences).resolved(sentences)
            self.moves[sentences] = (tk.DoubleVar(value=similarity), tk.StringVar(value=algorithm))
        self.move_similarity, self.move_algorithm = self.moves[False]
        self.sentence_move_similarity, self.sentence_move_algorithm = self.moves[True]
        for row, (what, sentences) in enumerate((("paragraphs", False), ("sentences", True))):
            similarity, algorithm = self.moves[sentences]
            default = "{:.2f} {}".format(*move_defaults(sentences))
            field_row(
                moves,
                row,
                f"Moved {what}",
                move_fields(moves, similarity, algorithm),
                f"How alike an edited {what[:-1]} must be to where it reappears to count as "
                "moved (1: only unchanged), and how that is measured. token-sort: the words "
                "in common, whatever their order; token-set: the words both share against "
                f"the rest of each (one inside a longer one scores high). Default: {default}"
                + (" (lines of files other than prose too)." if not sentences else "."),
            )
        passages = ttk.Labelframe(
            self.advanced,
            text="Moved passages: telling them from chance likeness",
            padding=(10, 8),
        )
        passages.pack(fill="x", pady=(8, 0))
        passages.columnconfigure((1, 3), weight=1)
        self.passage_vars: dict[str, tk.Variable] = {}
        for k, f in enumerate(fields(MovedPassageSettings)):
            share = f.metadata["share"]
            var = (tk.DoubleVar if share else tk.IntVar)(
                value=self.s.moved_passages.get(f.name, f.default)
            )
            self.passage_vars[f.name] = var
            spin = ttk.Spinbox(
                passages,
                from_=0.01 if share else f.metadata["low"],
                to=1 if share else 10**7,
                increment=0.05 if share else 1,
                textvariable=var,
                width=10,
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
        self.encoding = self.setting("encoding")
        field_row(
            reading,
            0,
            "Text encoding",
            ttk.Combobox(reading, textvariable=self.encoding, values=ENCODINGS, width=12),
            "Of text and Markdown files. auto: UTF-8, unless a file is not; then guessed; or a "
            "codec such as cp1252 (Windows, Western European).",
        )
        self.md_filter = self.setting("md_filter")
        field_row(
            reading,
            1,
            "Markdown filter",
            ttk.Entry(reading, textvariable=self.md_filter, width=30),
            "A shell command both versions of every Markdown file are piped through "
            "before they are compared (not Word or OpenDocument files). Empty: none.",
        )
        timing = ttk.Labelframe(self.advanced, text="AI assessment", padding=(10, 8))
        timing.pack(fill="x", pady=(8, 0))
        self.assess_timeout = self.setting("assess_timeout")
        field_row(
            timing,
            0,
            "Timeout (seconds)",
            ttk.Spinbox(
                timing, from_=1, to=86_400, increment=60, textvariable=self.assess_timeout, width=10
            ),
            f"Give up on the AI's assessment after this long. Default: {ASSESS_TIMEOUT:,}.",
        )
        ttk.Button(
            self.advanced, text="Close", command=self.toggle_advanced, bootstyle="secondary"
        ).pack(side="bottom", anchor="e", pady=(12, 0))

    def build_output(self, page: ttk.Frame) -> None:
        """The output: its format, where it goes, whether it opens."""
        out = self.output_card = ttk.Labelframe(page, text="Output", padding=(10, 8))
        out.pack(fill="x", pady=(10, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Format").grid(row=0, column=0, sticky="w", **PAD)
        formats = ttk.Frame(out)
        formats.grid(row=0, column=1, columnspan=3, sticky="w", **PAD)
        # a review is an HTML report: no format to choose
        self.review_hidden += out.grid_slaves(row=0)
        self.format_buttons: dict[str, ttk.Radiobutton] = {}
        self.output_format = self.setting("output_format")
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
            self.format_buttons[value] = segment(
                formats,
                self.output_format,
                value,
                text,
                tip,
                padding=(12, 4),
                command=self.on_format,
            )
        ttk.Label(out, text="Save to").grid(row=1, column=0, sticky="w", **PAD)
        self.output = self.setting("output")
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
        for var in (
            self.mode,
            self.old,
            self.new,
            self.old_folder,
            self.new_folder,
            self.single,
            self.rebuild_file,
        ):
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
        self.open_page = self.setting("open_page")
        toggle(out, "Open when done", self.open_page).grid(
            row=1, column=3, sticky="w", padx=(12, 6), pady=6
        )
        for var in (self.mode, self.old, self.new):
            var.trace_add("write", lambda *_: self.update_tracked_formats())
        self.update_tracked_formats()

    def build_assessment(self, page: ttk.Frame) -> None:
        """The AI assessment: the AI, its model and effort, among those it
        reports; what it reads; the instructions of the person asking."""
        card = self.ai_card = ttk.Labelframe(page, text="AI assessment", padding=(10, 8))
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
        # the models each AI reports, once asked; the AIs being asked; what
        # the background found, for tkinter's own thread
        self.ai_models: dict[str, list[ModelInfo]] = {}
        self.asking: set[str] = set()
        self.found: queue.Queue[tuple[str, list, str]] = queue.Queue()
        ttk.Label(card, text="AI").grid(row=0, column=0, sticky="w", **PAD)
        ai_row = ttk.Frame(card)
        ai_row.grid(row=0, column=1, columnspan=2, sticky="w", **PAD)
        self.ai_box = ttk.Combobox(ai_row, textvariable=self.assess_ai, width=12)
        self.ai_box.pack(side="left")
        ttk.Label(ai_row, text="Model").pack(side="left", padx=(12, 6))
        self.model_box = ttk.Combobox(ai_row, textvariable=self.assess_model, width=22)
        self.model_box.pack(side="left")
        ttk.Label(ai_row, text="Effort").pack(side="left", padx=(12, 6))
        self.effort_box = ttk.Combobox(ai_row, textvariable=self.assess_effort, width=9)
        self.effort_box.pack(side="left")
        for w, tip in (
            (
                self.ai_box,
                "Have an AI assess the value of the changes as a whole: a verdict, what "
                "changed, what improved and the problems to fix, at the top of the HTML "
                "report (the HTML report only). claude: Claude Code, on your Claude login; "
                "codex: ChatGPT through Codex, on your ChatGPT login (log in once with "
                "prosediff --login-codex); ollama: a local model, nothing leaving this "
                "computer; or another provider any-llm reaches (openai, anthropic, gemini, "
                "...), its API key in the environment.",
            ),
            (
                self.model_box,
                "The model that assesses, among those the AI reports, its own default "
                "first; or any name typed.",
            ),
            (
                self.effort_box,
                "How hard the model thinks, among the levels it reports it supports; "
                "default, or empty: its own. More effort, a closer reading, but slower and "
                "costlier.",
            ),
        ):
            hint(w, tip)
        self.assess_context = self.setting("assess_context")
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
            button = segment(reads, self.assess_context, value, text, tip)
            self.ai_switches.append(button)
            self.changes_only.append(button)
        # PAD, as the rows around it, not field_row: aligned with them
        reads_label = ttk.Label(card, text="Reads")
        reads_label.grid(row=1, column=0, sticky="w", **PAD)
        reads.grid(row=1, column=1, columnspan=2, sticky="w", **PAD)
        hint(reads_label, "What the model is sent, besides the instructions.")
        self.review_hidden += [reads_label, reads]
        self.assess_instructions = self.setting("assess_instructions")
        self.assess_prompt = self.setting("assess_prompt")
        self.assess_review_prompt = self.setting("assess_review_prompt")
        self.assess_writing_prompt = self.setting("assess_writing_prompt")
        self.assess_review_writing_prompt = self.setting("assess_review_writing_prompt")
        ttk.Label(card, text="Instructions").grid(row=2, column=0, sticky="w", **PAD)
        entry = ttk.Entry(card, textvariable=self.assess_instructions)
        entry.grid(row=2, column=1, sticky="ew", **PAD)
        hint(
            entry,
            "Your own instructions, added to the prompt: the journal, what a co-author "
            'asked for, what to look at (e.g. "the journal is Research Policy; check that '
            'the introduction was cut by a fifth"); or a text file holding them.',
        )
        buttons = ttk.Frame(card)
        buttons.grid(row=2, column=2, **PAD)
        write = ttk.Button(
            buttons,
            image=ttk.Icon("pencil-square", size=16),
            command=self.edit_instructions,
            bootstyle="secondary-outline",
        )
        write.pack(side="left", padx=(0, 6))
        hint(
            write,
            "Write the instructions in a large box, in a window of its own, and see or "
            "edit prosediff's own prompt",
        )
        pick = browse(
            buttons,
            lambda: self.pick_into(self.assess_instructions, "Instructions for the AI"),
            "Choose a text file holding the instructions",
        )
        pick.pack(side="left")
        self.assess_author = self.setting("assess_author")
        author_label = ttk.Label(card, text="Author")
        author_label.grid(row=3, column=0, sticky="w", **PAD)
        author = ttk.Entry(card, textvariable=self.assess_author, width=30)
        author.grid(row=3, column=1, sticky="w", **PAD)
        for w in (author_label, author):
            hint(
                w,
                "The author of what the AI adds to the Word and OpenDocument documents to "
                "download: its comments and its tracked changes, the name Word and "
                "LibreOffice show beside each (Review, Track Changes). Not the author of "
                'the document. Empty: the AI and its model, e.g. "Claude Code '
                '(claude-opus-5-5)".',
            )
        self.assess_send_files = self.setting("assess_send_files")
        self.assess_files = self.setting("assess_files")
        sending = toggle(card, "Other files", self.assess_send_files)
        sending.grid(row=4, column=0, sticky="w", **PAD)
        hint(
            sending,
            "Also send the AI other files as context, to draw on, not to assess: a "
            "journal's guidelines, a reviewer's report, a cited paper (PDF, Word, "
            "OpenDocument, Markdown or text).",
        )
        self.files_entry = ttk.Entry(card, textvariable=self.assess_files)
        self.files_entry.grid(row=4, column=1, sticky="ew", **PAD)
        hint(self.files_entry, 'The files to send, separated by ";".')
        self.files_pick = browse(card, self.pick_files, "Add files to send")
        self.files_pick.grid(row=4, column=2, sticky="w", **PAD)
        self.assess_send_files.trace_add("write", lambda *_: self.update_ai_switches())
        self.ai_switches += [entry, write, pick, author, sending]
        self.assess_annotate = self.setting("assess_annotate")
        self.assess_save_prompt = self.setting("assess_save_prompt")
        self.assess_preview = self.setting("assess_preview")
        self.assess_ai_writing = self.setting("assess_ai_writing")
        self.assess_edits = self.setting("assess_edits")
        self.assess_documents = self.setting("assess_documents")
        switches = ttk.Frame(card)
        switches.grid(row=5, column=1, columnspan=2, sticky="w", **PAD)
        self.switch_cells: list[tk.Misc] = []
        for text, var, tip in (
            (
                "Preview before sending",
                self.assess_preview,
                "First write the report without the assessment and open it, then ask "
                "whether to send the changes to the AI: to check what it will read "
                "before it reads it. No: the report stays as it is, unassessed.",
            ),
            (
                "Mark problems in the text",
                self.assess_annotate,
                "Have the AI mark each problem in the text, from its first words to its "
                "last, with what is wrong and the change it proposes: a numbered badge "
                "before each in the HTML report, its passage highlighted when clicked, and "
                "a card in the margin beside it.",
            ),
            (
                "Check for AI writing",
                self.assess_ai_writing,
                "Also ask the AI, apart, whether the text the changes added (one file: "
                "the file) reads as written by an AI: a second assessment, its verdict "
                "(likely, possibly or unlikely) in the report's top bar. An indication, "
                "not a proof: careful writers show the same signs, and writers in a "
                "second language are often taken for an AI wrongly. One file has no "
                "earlier version to weigh the text against: a weaker judgement still.",
            ),
            (
                "Allow text edits",
                self.assess_edits,
                "Let the AI edit the text: each fix it proposes is a rewording of the "
                "passage, a tracked change in the documents to download and, reviewing "
                "one file, the report a diff of the file and the file with the fixes. "
                "Off: it only marks the problems and says what to do; the text is left "
                'as it is. Needs "Mark problems in the text".',
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
                "another), put in the HTML report the documents to download from the AI "
                "assessment: the new version with the AI's fixes as tracked changes, to "
                "accept or reject, its own (the co-authors') kept; and, when it has none, "
                "the tracked changes with the AI's comments. Review mode chooses which "
                "problems they hold. They make the "
                "report larger: about 2.7 times the document's size. Needs \"Mark "
                'problems in the text".',
            ),
        ):
            switch = toggle(switches, text, var)
            hint(switch, tip)
            # rows of two, laid out by layout_switches
            self.switch_cells.append(switch)
            self.ai_switches.append(switch)
            if var is self.assess_documents:
                self.documents_switch = switch
            # a review sends the file as it is, and writes the report once
            if var is self.assess_preview:
                self.changes_only.append(switch)
                self.preview_switch = switch
        self.layout_switches()
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
                enable(w, html)
        if self.assess_ai.get() == LOADING:
            self.ai_box.state(["disabled"])  # until the AIs are known
        self.update_ai_switches()
        on = self.ai_active() and self.ai_chosen() in self.ai_models
        for box in (self.model_box, self.effort_box):
            enable(box, on)

    def layout_switches(self) -> None:
        """The AI card's switches in rows of two; reviewing one file, the
        preview left out, the others closing up."""
        shown = [
            s for s in self.switch_cells if not (self.reviewing() and s is self.preview_switch)
        ]
        for s in self.switch_cells:
            s.grid_remove()
        for k, s in enumerate(shown):
            s.grid(row=k // 2, column=k % 2, sticky="w", padx=(0, 24), pady=3)

    def update_ai_switches(self) -> None:
        """What the AI is sent and the AI assessment's switches, greyed out
        while no AI will assess."""
        active = self.ai_active()
        for switch in self.ai_switches:
            on = active
            # the documents are made of the problems marked in the text
            if switch is getattr(self, "documents_switch", None):
                on = on and self.assess_annotate.get()
            # what the model reads and the AI-writing check are of changes
            if switch in self.changes_only:
                on = on and not self.reviewing()
            enable(switch, on)
        # the files to send, while sending them
        if hasattr(self, "files_entry"):
            for w in (self.files_entry, self.files_pick):
                enable(w, active and self.assess_send_files.get())

    def pick_files(self) -> None:
        """Add files to those sent to the AI as context."""
        chosen = filedialog.askopenfilenames(
            parent=self.root,
            title="Files to send the AI",
            filetypes=[
                ("Documents", "*.pdf *.docx *.odt *.md *.txt"),
                ("All", "*.*"),
            ],
        )
        if chosen:
            have = [f for f in self.assess_files.get().split(";") if f.strip()]
            self.assess_files.set(";".join([*have, *(f for f in chosen if f not in have)]))
            self.assess_send_files.set(True)

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
        in the background the first time, the model and effort greyed out
        meanwhile); none for none. The model shown becomes the AI's own
        default, the first it reports, unless keep and one is already
        chosen: the one saved, said in an error and replaced by the default
        when the AI no longer offers it."""
        ai = self.ai_chosen()
        if not keep:  # another AI: its own default, once known
            self.assess_model.set("")
        enable(self.model_box, self.ai_active())
        if ai in ("", NO_ASSESSMENT):
            self.model_box.configure(values=())
            self.update_efforts()
            return
        if ai not in self.ai_models:
            self.model_box.configure(values=())
            self.model_box.state(["disabled"])
            self.effort_box.state(["disabled"])
            self.ask(ai, lambda: models_of(ai))
            return
        models = [m.name for m in self.ai_models[ai]]
        self.model_box.configure(values=models)
        saved = self.assess_model.get().strip()
        if saved and models and saved not in models:
            self.complain(
                f"The model saved, {saved}, is not one {ai} offers any more: its "
                f"default, {models[0]}, is chosen instead."
            )
        elif saved:
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
        enable(self.effort_box, self.ai_active())
        if keep and (self.assess_effort.get() in levels or model is None):
            return
        default = model.default_effort if model else ""
        self.assess_effort.set(default or (MODEL_DEFAULT if levels else ""))

    def effort_chosen(self) -> str:
        """The effort to ask for: "" for the model's own default."""
        effort = self.assess_effort.get().strip()
        return "" if effort == MODEL_DEFAULT else effort

    def assess_spec(self) -> str:
        """The AI assessment asked for, as --assess takes it: "claude",
        "claude/opus", "ollama/qwen3"; "" for none. Claude Code's own
        default is asked for by naming no model."""
        ai, model = self.ai_chosen(), self.assess_model.get().strip()
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
        rebuild = self.rebuilding()
        if self.mode.get() != "git":
            self.status.set(
                REBUILD_READY if rebuild else REVIEW_READY if self.reviewing() else READY
            )
        # remaking a report needs neither the comparison's options nor an AI
        for card, after in (
            (self.compared_card, self.source_card),
            (self.ai_card, self.output_card),
        ):
            if rebuild:
                card.pack_forget()
            elif not card.winfo_manager():
                card.pack(fill="x", pady=(10, 0), after=after)
        # one file is reviewed, not compared
        what = (
            "rebuild a report"
            if rebuild
            else "review one file"
            if self.reviewing()
            else "compare two versions"
        )
        self.root.title(f"prosediff: {what}")
        self.source_card.configure(
            text="Saved report" if rebuild else "File" if self.reviewing() else "Versions"
        )
        for mode, side in self.sides.items():
            if mode == self.mode.get():
                side.pack(fill="x")
            else:
                side.pack_forget()
        for w in self.comparing_only:
            w.state(["disabled"] if self.reviewing() else ["!disabled"])
        for w in self.review_hidden:
            if self.reviewing() or rebuild:
                w.grid_remove()
            else:
                w.grid()
        self.layout_switches()
        if (self.reviewing() or rebuild) and self.output_format.get() != "html":
            self.output_format.set("html")
            self.rename_output()
        self.update_splits()
        self.update_comments()
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

    def on_format(self) -> None:
        """A format chosen: the Save to file renamed, the splits it cannot
        hold greyed out."""
        self.rename_output()
        self.update_splits()

    def update_splits(self) -> None:
        """Compare by offers the splits the output can hold (check_split):
        both for the HTML report only, which switches between them;
        sentences not for a document of tracked changes, whose paragraphs are
        paragraphs. One it cannot hold is greyed out and, chosen, given up for
        the output's default (default_split), to come back with an output that
        can hold it."""
        fmt = self.output_format.get()

        def fits(split: str) -> bool:
            return passes(check_split, split, fmt)

        for value, button in self.split_buttons.items():
            enable(button, fits(value) and not self.reviewing())
        if self.split_given_up is not None:
            given_up, put = self.split_given_up
            if self.split.get() != put:  # chosen since: it stays
                self.split_given_up = None
            elif fits(given_up):
                self.split.set(given_up)
                self.split_given_up = None
        if not fits(self.split.get()):
            put = default_split(fmt)
            self.split_given_up = (self.split.get(), put)
            self.split.set(put)

    def rename_output(self) -> None:
        """Give the Save to file the extension of the format chosen."""
        default = self.output.get().strip() == self.auto_output
        self.output.set(with_format(self.output.get().strip(), self.output_format.get()))
        if default:
            self.auto_output = self.output.get().strip()

    def sides_page(self) -> str:
        """Where the output goes by default: next to the new file, into the new
        folder, the file reviewed (page_of); "" comparing git versions, or sides
        not yet chosen."""
        mode = self.mode.get()
        if mode == "rebuild":  # over the report the answers were saved beside
            saved = self.rebuild_file.get().strip()
            return (
                str(Path(saved[: -len(".ai.json")] + ".html").resolve())
                if saved.endswith(".ai.json")
                else ""
            )
        old, new = {
            "review": (self.single, self.single),
            "folders": (self.old_folder, self.new_folder),
        }.get(mode, (self.old, self.new))
        page = page_of(
            mode, old.get().strip(), new.get().strip(), FORMATS[self.output_format.get()]
        )
        return str(page.resolve()) if page else ""

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
        old, new = self.old.get().strip(), self.new.get().strip()
        for fmt in TRACKED_FORMATS:
            enable(self.format_buttons[fmt], not files or passes(check_paths, old, new, fmt))
        if self.reviewing():  # a review is an HTML report: no diff, no changes
            for fmt, button in self.format_buttons.items():
                enable(button, fmt == "html")

    def update_comments(self) -> None:
        """Comments offers, reviewing one file, markers or none (the AI is
        sent the file's comments, or not), and says so; text, chosen, is
        given up for markers, to come back with two versions compared."""
        review = self.reviewing()
        self.comments_box.configure(
            values=[m for m in COMMENT_MODES if not (review and m == "text")]
        )
        for tip in self.comments_tips:
            tip.configure(text=REVIEW_COMMENTS_TIP if review else COMMENTS_TIP)
        if review and self.comments.get() == "text":
            self.comments.set("markers")
            self.comments_given_up = True
        elif not review and self.comments_given_up:
            if self.comments.get() == "markers":  # not chosen since
                self.comments.set("text")
            self.comments_given_up = False

    def update_empty_comments(self) -> None:
        """Comments without text are a choice of markers only."""
        markers = self.comments.get() == "markers"
        enable(self.empty_comments_box, markers)

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
        enable(self.untracked_box, on_worktree)

    def setting(self, name: str) -> tk.Variable:
        """The widget variable of a setting, holding its value, of its type
        in Settings (a string for any other); collect reads it and
        reset_options resets it by name (self.vars)."""
        kind = Settings.__dataclass_fields__[name].type
        var = {bool: tk.BooleanVar, int: tk.IntVar, float: tk.DoubleVar}.get(kind, tk.StringVar)
        self.vars[name] = var(value=getattr(self.s, name))
        return self.vars[name]

    def collect(self) -> Settings:
        """The settings the window shows; ValueError for a box of a number
        holding none."""
        values = {}
        for name, var in self.vars.items():
            try:
                value = var.get()
            except tk.TclError:
                raise ValueError(f"{NUMBER_NAMES[name]} must be a number.") from None
            values[name] = value.strip() if isinstance(value, str) else value
        context = values["context_lines"]
        # a number as typed, negative too, for run to refuse (context_of)
        if not context.lstrip("-").isdigit():
            context = "auto"
        moves = {}
        for sentences, (similarity, algorithm) in self.moves.items():
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
            language = normalize_language(values["language"])
        except ValueError:
            language = DEFAULT
        try:
            encoding = check_encoding(values["encoding"])
        except ValueError:
            encoding = AUTO_ENCODING
        output = self.output.get().strip()
        return Settings(
            **values
            | {
                "context_lines": context,
                "language": language,
                "encoding": encoding,
                "base": self.ref_of(self.base.get()),
                "target": self.ref_of(self.target.get()),
                "paths": [p.strip() for p in self.paths.get().split(";") if p.strip()],
                "docx_changes": DOCX_CHANGE_VALUES.get(self.docx.get(), "accept-all"),
                "move_similarity": moves[False][0],
                "move_algorithm": moves[False][1],
                "sentence_move_similarity": moves[True][0],
                "sentence_move_algorithm": moves[True][1],
                "moved_passages": self.passage_choices(),
                # the default is kept as "": it follows the sides next time
                "output": "" if output == self.auto_output else output,
                "assess": self.assess_spec(),
                "assess_effort": self.effort_chosen(),
            }
        )

    def edit_instructions(self) -> None:
        """prosediff's prompts and the instructions in large boxes, in a
        window of their own: OK keeps them (a prompt left as prosediff's is
        kept as "", to follow prosediff's), Cancel or Escape leaves them as
        they were. The prompts are those of the tab shown: the review of one
        file; or the assessment of two versions, and whether their new text
        reads as written by an AI. One a file holds is shown read-only, the
        file kept: it is read again at every run."""
        review = self.reviewing()
        if review:
            prompts = [
                ("prosediff's prompt, for reviewing one file", self.assess_review_prompt, "review"),
                (
                    "prosediff's prompt, for whether the file reads as AI-written",
                    self.assess_review_writing_prompt,
                    "writing",
                ),
            ]
        else:
            prompts = [
                ("prosediff's prompt, for assessing the changes", self.assess_prompt, "value"),
                (
                    "prosediff's prompt, for whether the new text reads as AI-written",
                    self.assess_writing_prompt,
                    "writing",
                ),
            ]
        # the instructions last, with no default to restore
        boxes = [(label, var, default_system(kind, review)) for label, var, kind in prompts]
        boxes.append(("Your instructions, added to the prompt", self.assess_instructions, ""))
        top = tk.Toplevel(self.root)
        top.title("prosediff: instructions for the AI")
        top.transient(self.root)
        frame = ttk.Frame(top, padding=(14, 12, 14, 12))
        frame.pack(fill="both", expand=True)
        editable = []
        for n, (label, var, default) in enumerate(boxes):
            path = instructions_file(var.get())
            head = ttk.Frame(frame)
            head.pack(fill="x", pady=(10 if n else 0, 4))
            if path:
                label += f": from {path.name} (edit that file, or clear the field to write here)"
            ttk.Label(head, text=label).pack(side="left")
            box = ttk.ScrolledText(
                frame, width=80, height=10 if default else 5, wrap="word", auto_hide=True
            )
            box.pack(fill="both", expand=True)
            if path:
                box.text.insert("1.0", path.read_text(encoding="utf-8").strip())
                box.text.configure(state="disabled")
                continue
            box.text.insert("1.0", var.get().strip() or default)
            editable.append((box.text, var, default))
            if not default:
                continue

            def restore(text=box.text, default=default) -> None:
                text.delete("1.0", "end")
                text.insert("1.0", default)

            reset = ttk.Button(
                head, text="Restore default", command=restore, bootstyle="secondary-link"
            )
            reset.pack(side="right")
            hint(
                reset,
                "Keep its ## Verdict section: the report reads it. prosediff still adds, "
                "after it, the rules for Word documents and for marking problems in the text.",
            )
        if editable:
            editable[-1][0].focus_set()

        def done(keep: bool) -> None:
            if keep:
                for text, var, default in editable:
                    written = text.get("1.0", "end-1c").strip()
                    var.set("" if written == default.strip() else written)
            top.destroy()

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="OK", command=lambda: done(True)).pack(side="right")
        ttk.Button(
            row, text="Cancel", command=lambda: done(False), bootstyle="secondary-outline"
        ).pack(side="right", padx=(0, 8))
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        top.bind("<Escape>", lambda e: done(False))
        dark_title_bar(top)

    def keep_largest_size(self, event: tk.Event) -> None:
        """The window's minimum raised to the largest size its content has
        asked for (not to a size dragged by hand, which may shrink again)."""
        if event.widget is not self.root:
            return
        width, height = self.root.minsize()
        wanted = (
            max(width, self.root.winfo_reqwidth()),
            max(height, self.root.winfo_reqheight()),
        )
        if wanted != (width, height):
            self.root.minsize(*wanted)

    def toggle_advanced(self) -> None:
        """Open the advanced settings' window, or close it."""
        top = self.advanced_window
        if top.state() != "withdrawn":
            top.withdraw()
            return
        top.deiconify()
        top.lift()
        dark_title_bar(top)

    def passage_choices(self) -> dict[str, float]:
        """The moved-passage settings shown that differ from prosediff's
        defaults; ValueError for a box holding no number."""
        chosen: dict[str, float] = {}
        for f in fields(MovedPassageSettings):
            try:
                value = self.passage_vars[f.name].get()
            except tk.TclError:
                raise ValueError(f"{f.metadata['label']} must be a number.") from None
            if value != f.default:
                chosen[f.name] = value
        return chosen

    def save_options(self) -> None:
        """Remember the choices shown, for the next time the window opens:
        only when asked, never on its own."""
        path = settings_file()
        try:
            s = self.collect()
        except ValueError as e:
            self.complain(str(e))
            return
        if save_settings(s, path):
            self.status.set(f"Options saved: {path}")
        else:
            self.status.set(f"Options not saved: {path} cannot be written")

    def reset_options(self) -> None:
        """Every option to its default (what is compared and where the output
        goes stay as they are); nothing is saved until asked."""
        d = Settings()
        for name, var in self.vars.items():
            if name not in COMPARED:
                var.set(getattr(d, name))
        self.docx.set(DOCX_CHANGE_LABELS[d.docx_changes])
        for sentences, (similarity, algorithm) in self.moves.items():
            value, name = moves_of(d, sentences).resolved(sentences)
            similarity.set(value)
            algorithm.set(name)
        self.assess_ai.set(d.assess or NO_ASSESSMENT)
        for f in fields(MovedPassageSettings):
            self.passage_vars[f.name].set(f.default)
        self.split_given_up = None
        self.comments_given_up = False
        self.on_format()
        self.update_untracked()
        self.update_empty_comments()
        self.status.set("Options reset to their defaults (not saved).")

    # Running -------------------------------------------------------------------

    def run(self) -> None:
        if self.job is not None:
            return  # one comparison at a time
        try:
            s = self.collect()
            check_numbers(s)
        except ValueError as e:
            self.complain(str(e))
            return
        if s.mode == "rebuild" and not s.rebuild_file.strip():
            self.complain("Choose the AI's saved answers (.ai.json).")
            return
        if s.mode == "review" and not s.assess:
            self.complain("Choose an AI, under AI assessment, to review the file.")
            return
        if s.assess and s.output_format == "html" and s.mode != "rebuild":
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
        elif self.rebuilding():
            self.button.configure(
                text="Rebuild", image=self.compare_icon, command=self.run, bootstyle="primary"
            )
            self.button_tip.text = (
                "Make the report again from the AI's saved answers, and open it (Ctrl+Enter)"
            )
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
        self.stage, self.stage_started, self.live = stage, time.monotonic(), ""
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
                # what the model is doing, when it says
                live = f" · {self.live}" if self.live else ""
                self.status.set(f"{self.stage} {duration(seconds)}{live}")
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
        if kind == "live":
            self.live = value
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
        if result.writing_error:
            messagebox.showwarning(
                "prosediff", f"The AI-writing assessment failed: {result.writing_error}"
            )
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


COMMENTS_TIP = (
    "markers: only the comments added or removed, set apart (a marker and a panel in the "
    "HTML report, CriticMarkup in the diffs); text: compared as text; none: left out."
)
REVIEW_COMMENTS_TIP = (
    "markers: the file's comments shown as markers and in a panel, and sent to the AI, "
    "which checks whether the text answers them; none: left out, and not sent to the AI."
)


def hint(widget: tk.Misc, text: str) -> ttk.ToolTip:
    """What a widget does, shown when the pointer rests on it."""
    return ttk.ToolTip(widget, text=text, wraplength=HINT_WIDTH, delay=HINT_DELAY_MS)


def segment(
    parent,
    variable: tk.StringVar,
    value: str,
    text: str,
    tip: str = "",
    padding: tuple[int, int] = (8, 3),
    bootstyle: str = "secondary-outline-toolbutton",
    **options,
) -> ttk.Radiobutton:
    """One button of a segmented choice, packed after the others in
    parent, with its tooltip (tip, if any)."""
    button = ttk.Radiobutton(
        parent,
        text=text,
        value=value,
        variable=variable,
        bootstyle=bootstyle,
        padding=padding,
        **options,
    )
    button.pack(side="left")
    if tip:
        hint(button, tip)
    return button


def enable(widget, on: bool) -> None:
    """Enable a widget, or grey it out."""
    widget.state(["!disabled"] if on else ["disabled"])


def choice_box(parent, variable: tk.StringVar, values, width: int = 12) -> ttk.Combobox:
    """A drop-down list to choose one of values from."""
    return ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=width)


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


def field_row(
    parent: tk.Misc, row: int, label: str, widget: tk.Misc, tip: str
) -> list[ttk.ToolTip]:
    """A labelled field of an options card, explained by a tooltip on both
    the label and the field, and an info icon; the tooltips, to change."""
    text = ttk.Label(parent, text=label)
    text.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
    widget.grid(row=row, column=1, sticky="w", pady=4)
    info = ttk.Label(parent, image=ttk.Icon("info-circle", size=14), bootstyle="secondary")
    info.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=4)
    return [hint(w, tip) for w in (text, widget, info)]


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
    choice_box(frame, algorithm, tuple(MOVE_ALGORITHMS), width=11).pack(side="left", padx=(6, 0))
    return frame


def swap(a: tk.StringVar, b: tk.StringVar) -> None:
    """Exchange the values of two fields."""
    first = a.get()
    a.set(b.get())
    b.set(first)


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


@functools.cache
def system_dark() -> bool:
    """Whether the system asks for dark windows: Windows' app mode, macOS's
    appearance, Linux's colour scheme (the desktop portal's, which GNOME,
    KDE and others set); when it cannot be told, light. Asked once."""
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
    two folders; one file fills in the One file tab, to review it alone.
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
    App(root, settings)
    root.mainloop()


if __name__ == "__main__":
    main()
