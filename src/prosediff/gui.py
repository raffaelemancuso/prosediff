"""prosediff's window: what to compare chosen on its Open screen, each
report it makes shown in a window of its own.

The windows are web views (pywebview: Edge WebView2 on Windows, WebKit on
macOS and Linux). The Open screen is a page (templates/window.html.j2) that
shows what App.view() says and sends back each field changed (App.set) and
each button pressed (WindowApi); App holds what the fields hold and every
rule between them, with no toolkit of its own.

What is compared is chosen with a segmented button: a git repository (base
and target picked among its latest commits, the working tree and the index,
or typed as any ref), two files, or two folders; or one file alone, for an
AI to review, nothing compared; or a report made again from the AI's
answers saved beside it. The options that change what the comparison finds
sit in one card, each explained by a tooltip; how the report shows it, with
the settings few change, under Advanced settings; the output (an HTML
report, a unified or word diff, or tracked changes) in a card of its own.
The comparison runs in a process of its own, which Cancel stops, its stages
in the progress log. The choices are remembered for the next time only when
asked (Save options), and Reset to defaults puts every option back.

A report opens in a window of its own (ReportApi): the choices made there of
the problems the AI marked (which are in the documents to download, which
resolved, which fixes undone) are kept in the project open, written into it
as they are made, and the documents to download are saved where asked.
"""

import contextlib
import ctypes
import functools
import json
import multiprocessing
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Protocol

import git

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
from prosediff.pipeline import Run, analysed, execute, options_of, request_of
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
    # only the files at the same path in both folders
    common_only: bool = False
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
    # whether that assessment also marks the passages that read so (comparing,
    # only new text): off while it is not asked
    assess_mark_ai_writing: bool = False
    # whether the report holds the documents made of the problems the AI
    # marked in a Word document or an OpenDocument text (prosediff.aidocs)
    assess_documents: bool = True


READY = "Choose what to compare, then Compare."
REVIEW_READY = "Choose the file, and the AI to review it, then Review."
REBUILD_READY = (
    "Choose the AI's answers saved beside a report (.ai.json), or a project, then Rebuild."
)
# The tabs, in their order: the last reviews one file, nothing compared.
MODES = ("git", "files", "folders", "review", "rebuild")
PREFILLED_FILES = (".md", ".docx", ".odt")
# What is compared, which Reset to defaults leaves as it is.
COMPARED = ("mode", "repo", "old", "new", "old_folder", "new_folder", "single")
# The settings of a box of a number, as its error names them.
NUMBER_NAMES = {"max_hidden": "Hidden lines", "assess_timeout": "The AI's timeout"}
# The columns of the list of files sent to the AI as context, and their titles.
FILE_COLUMNS = {"name": "File", "folder": "Folder"}


def natural(text: str) -> list:
    """A key that sorts text as people do: case aside, the numbers in it by
    their value (table_A_2 before table_A_10)."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text.lower())]


def prefillable(path: Path) -> bool:
    """Whether path is a Markdown, Word or OpenDocument file, which the
    window's tabs are filled in with."""
    return path.is_file() and suffix_of(path) in PREFILLED_FILES


def settings_from_args(args: list[str], base: Settings) -> tuple[Settings, str]:
    """The settings to open the window with, given its command-line arguments.

    One argument that is a git repository (or a folder inside one) fills in
    the repository, the sides starting from their defaults; one Markdown,
    Word or OpenDocument file fills in the One file tab, to review it alone;
    one JSON file (the NAME.ai.json saved beside a report) the Rebuild tab;
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
        if path.is_file() and path.suffix.lower() == ".json":
            s.mode = "rebuild"
            s.rebuild_file = str(path.resolve())
            s.output = ""  # over the report it was saved beside (App.follow_sides)
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
        return s, f"Not a folder, a Markdown, Word, OpenDocument or JSON file: {path}"
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
    """The choices saved (Save options), or the defaults."""
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return settings_of(data)


def settings_of(data: dict) -> Settings:
    """Settings from their saved values (Save options, or a project): the
    defaults for those missing (saved by another prosediff) or not of a
    Settings; a value that is no choice the window offers any more gives
    way to its default."""
    try:
        known = Settings.__dataclass_fields__
        s = Settings(**{k: v for k, v in data.items() if k in known})
    except (TypeError, AttributeError):
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
        run.common_only = s.common_only
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
    # where the AI's answers were kept (NAME.ai.json), for a project; "": none
    saved: str = ""


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


def project_settings(path: Path) -> tuple[Settings, dict | None]:
    """A project's settings, and the AI's answers it holds (None: none); its
    Rebuild tab set to it when it holds them. SavedError for a file that is
    not a project (prosediff.saved)."""
    from prosediff.saved import load_project

    values, report = load_project(path)
    s = settings_of(values)
    if report:
        s = replace(s, rebuild_file=str(Path(path).resolve()))
    return s, report


def rebuild_run(s: Settings, messages):
    """The run kept with the answers s names, and the answers (prosediff.saved:
    SavedError when the files compared changed since); written where Save to
    says, else over the report they were saved beside."""
    from prosediff.saved import load

    run, assessment, writing = load(s.rebuild_file.strip())
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
            live=lambda detail, news: messages.put(("live", (detail, news))),
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
        str(done.saved) if done.saved else "",
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


# The Open screen ---------------------------------------------------------------

# A moved-passage setting's field: this, then the name of its
# MovedPassageSettings field.
PASSAGE = "passage."
# The fields that hold a number as typed: read (collect) only when run.
NUMBER_FIELDS = (
    "max_hidden",
    "assess_timeout",
    "move_similarity",
    "sentence_move_similarity",
)
# The fields of the sides of what is compared, which Save to follows.
SIDES = ("old", "new", "old_folder", "new_folder", "single", "rebuild_file")
# What only matters when an AI assesses, greyed out while none will
# (App.view): each a field or a button of the AI card.
AI_SWITCHES = (
    "assess_context",
    "assess_instructions",
    "write_instructions",
    "pick_instructions",
    "assess_author",
    "assess_send_files",
    "assess_preview",
    "assess_annotate",
    "assess_ai_writing",
    "assess_mark_ai_writing",
    "assess_edits",
    "assess_save_prompt",
    "assess_documents",
)
# Of those, what only applies to changes, greyed out reviewing one file.
CHANGES_ONLY = ("assess_context", "assess_preview")
# What a review and a report made again leave out (App.view).
REVIEW_HIDDEN = ("row:split", "ignore_whitespace", "row:format", "row:reads")


def number_text(value: float) -> str:
    """A number as a field shows it: 1800, not 1800.0."""
    return str(int(value)) if float(value).is_integer() else str(value)


def values_of(s: Settings) -> dict:
    """The Open screen's fields holding s: each setting as it is, but the
    paths (a line, separated by ;), the AI (its name and model apart), the
    numbers as typed, the moved-line settings with prosediff's defaults
    shown, and one field for each moved-passage setting."""
    v: dict[str, object] = {
        f.name: getattr(s, f.name)
        for f in fields(Settings)
        if f.name not in ("assess", "paths", "moved_passages")
    }
    v["paths"] = "; ".join(s.paths)
    ai, _, model = s.assess.partition("/")
    v["assess_ai"] = ai or LOADING
    v["assess_model"] = model or (CLAUDE_DEFAULT if ai == "claude" else "")
    v["max_hidden"] = str(s.max_hidden)
    v["assess_timeout"] = number_text(s.assess_timeout)
    for sentences, prefix in ((False, ""), (True, "sentence_")):
        similarity, algorithm = moves_of(s, sentences).resolved(sentences)
        v[f"{prefix}move_similarity"] = f"{similarity:.2f}"
        v[f"{prefix}move_algorithm"] = algorithm
    for f in fields(MovedPassageSettings):
        v[PASSAGE + f.name] = number_text(s.moved_passages.get(f.name, f.default))
    return v


class Ui(Protocol):
    """What App asks of the screen it is shown on (WebUi; in the tests, one
    that records): to show it again, to say something, to ask for a file."""

    def render(self, view: dict) -> None: ...
    def log(self, line: str) -> None: ...
    def clear_log(self) -> None: ...
    def alert(self, kind: str, text: str) -> None: ...  # "error", "warning"
    def toast(self, text: str) -> None: ...
    def open_report(self, path: Path, serial: int | None) -> None: ...
    def open_file(self, path: Path) -> None: ...
    def ask_open(self, title: str, types: list[str], many: bool = False) -> list[str]: ...
    def ask_folder(self, title: str, start: str = "") -> str: ...
    def ask_save(self, title: str, types: list[str], start: str = "", name: str = "") -> str: ...


def locked(method):
    """A method of App run holding its lock: the page's calls, the
    comparison's messages and what the background finds come on threads of
    their own."""

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return run


class App:
    """The Open screen: what its fields hold (values, by the names
    values_of gives them), and every rule between them; what it shows
    (view), from them."""

    def __init__(
        self,
        ui: Ui,
        settings: Settings | None = None,
        project: tuple[Path, dict | None, dict | None] | None = None,
    ) -> None:
        self.ui = ui
        self.lock = threading.RLock()
        # what the background is looking for (an AI's models, the
        # providers), and the threads looking, for the tests to wait for
        self.asking: set[str] = set()
        self.threads: list[threading.Thread] = []
        # the models each AI reports, once asked; the AIs found (None: not yet)
        self.ai_models: dict[str, list[ModelInfo]] = {}
        self.ais: list[str] | None = None
        # the comparison running (a process of its own), what it sends back,
        # its settings, and the stage it is at; the question its preview asks
        self.job: multiprocessing.process.BaseProcess | None = None
        self.messages = self.replies = None
        self.job_settings: Settings | None = None
        self.stage, self.stage_started, self.live = "", 0.0, ""
        self.preview_text = ""
        self.preview_path: Path | None = None
        self.log_lines: list[str] = []
        # the reports opened, numbered: the last one's choices are kept (keep_choices)
        self.report_serial = 0
        self.load(settings or load_settings(), project)

    @locked
    def load(
        self, s: Settings, project: tuple[Path, dict | None, dict | None] | None = None
    ) -> None:
        """The fields set to s, and the project open (NAME.prosediff), with
        the AI's answers of the last report it holds and the choices made in
        that report, for Save project to keep (prosediff.saved)."""
        self.s = s
        self.project_path: Path | None = project[0] if project else None
        self.project_report: dict | None = project[1] if project else None
        self.project_choices: dict | None = project[2] if project else None
        self.values = values_of(s)
        self.choices: dict[str, str] = {}  # a list entry of base and target -> its ref
        self.lists: dict[str, list[str]] = {"base": [], "target": [], "assess_model": []}
        self.efforts: list[str] = []
        self.status = READY
        # Save to, while it is the default: it follows the sides (follow_sides)
        self.auto_output = ""
        # the split given up for the output's default, to come back with an
        # output that can hold it (update_splits); "text", given up for
        # "markers" reviewing one file, to come back after (update_comments)
        self.split_given_up: tuple[str, str] | None = None
        self.comments_given_up = False
        # how the list of context files is sorted: (column, descending), None as added
        self.files_sort: tuple[str, bool] | None = None
        self.follow_sides()
        self.update_models(keep=True)  # the model and effort saved stay
        if self.ais is None:
            # the AIs: prosediff's own and the providers any-llm reaches
            self.ask("providers", lambda: [*AIS, *(p for p in providers() if p not in AIS)])
        else:
            self.use_providers(self.ais, "")
        if s.repo:
            self.load_repo(keep=(s.base, s.target))
        self.show_mode()

    # What the page shows -------------------------------------------------------

    def render(self) -> None:
        """The page shown again, after a change it did not ask for."""
        self.ui.render(self.view())

    @locked
    def view(self) -> dict:
        """What the page shows: each field's value, the lists to choose
        from, what is greyed out and what is left out (by field, a field
        and its value for a segmented button, or a row's or a button's id),
        and the texts that change."""
        v = self.values
        mode = v["mode"]
        review, rebuild = mode == "review", mode == "rebuild"
        html = v["output_format"] == "html"
        active = self.ai_active()
        disabled: set[str] = set()
        hidden: set[str] = {f"side:{m}" for m in MODES if m != mode}
        # Word, tracked and OpenDocument, tracked only for two files of their
        # kind (a repository or folders are only known once compared); a
        # review is an HTML report
        old, new = str(v["old"]).strip(), str(v["new"]).strip()
        for fmt in FORMATS:
            other = fmt in TRACKED_FORMATS and mode == "files"
            if (review and fmt != "html") or (other and not passes(check_paths, old, new, fmt)):
                disabled.add(f"output_format:{fmt}")
        for split in SPLITS:
            if review or not passes(check_split, split, v["output_format"]):
                disabled.add(f"split:{split}")
        if review:
            disabled.add("ignore_whitespace")
        if review or rebuild:
            hidden.update(REVIEW_HIDDEN)
        if rebuild:  # remaking a report needs neither the comparison's options nor an AI
            hidden.update(("card:compared", "card:ai"))
        if self.ref_of(str(v["target"])) not in ("worktree", ""):
            disabled.add("untracked")
        if v["comments"] != "markers":
            disabled.add("empty_comments")
        # the AI card, greyed out whole unless the output is the HTML report
        if not html or v["assess_ai"] == LOADING:
            disabled.add("assess_ai")
        if not (active and self.ai_chosen() in self.ai_models):
            disabled.update(("assess_model", "assess_effort"))
        for name in AI_SWITCHES:
            on = active
            if name == "assess_documents":  # made of the problems marked in the text
                on = on and v["assess_annotate"]
            if name == "assess_mark_ai_writing":  # marked as that assessment is asked
                on = on and v["assess_ai_writing"]
            if name in CHANGES_ONLY:
                on = on and not review
            if not on:
                disabled.add(name)
        if not (active and v["assess_send_files"]):
            disabled.add("edit_files")
        if review:  # a review sends the file as it is, and writes the report once
            hidden.add("assess_preview")
        running = self.job is not None
        if rebuild:
            button = ("Rebuild", "Make the report again from the AI's saved answers, and open it")
        elif review:
            button = ("Review", "Have the AI review the file, write the report and open it")
        else:
            button = ("Compare", "Compare, write the output and open it")
        files = self.files_chosen()
        names = ", ".join(Path(f).name for f in files[:3]) + (", …" if len(files) > 3 else "")
        return {
            "values": dict(v),
            "lists": {
                **self.lists,
                "assess_ai": self.ais or [],
                "assess_effort": self.efforts,
                "comments": [m for m in COMMENT_MODES if not (review and m == "text")],
            },
            "disabled": sorted(disabled),
            "hidden": sorted(hidden),
            "text": {
                "title": self.title(),
                "status": self.status,
                "source": "Saved report" if rebuild else "File" if review else "Versions",
                "run": "Cancel" if running else button[0],
                "run_tip": (
                    "Stop this comparison and the AI assessment (Esc)"
                    if running
                    else f"{button[1]} (Ctrl+Enter)"
                ),
                "comments_tip": REVIEW_COMMENTS_TIP if review else COMMENTS_TIP,
                "files_summary": f"{counted(len(files), 'file')}: {names}"
                if files
                else "None chosen",
                "preview": self.preview_text,
            },
            "running": running,
            "files": self.files_rows(),
            "log": len(self.log_lines),
        }

    def title(self) -> str:
        """The window's title: what it does, and the project open."""
        mode = self.values["mode"]
        what = (
            "rebuild a report"
            if mode == "rebuild"
            else "review one file"
            if mode == "review"
            else "compare two versions"
        )
        project = f" · {self.project_path.name}" if self.project_path else ""
        return f"prosediff: {what}{project}"

    # Changing the fields -------------------------------------------------------

    @locked
    def set(self, name: str, value) -> None:
        """A field changed on the page, and what follows from it: Save to
        following the sides, the splits and the comments a mode or a format
        offers, the models of the AI chosen and the efforts of its model."""
        if name not in self.values:
            raise KeyError(name)
        old = self.values[name]
        value = bool(value) if isinstance(old, bool) else "" if value is None else str(value)
        if value == old and name != "repo":
            return
        self.values[name] = value
        if name == "mode" and old == "review" and value == "files":
            self.carry_single()
        if name in ("mode", *SIDES):
            self.follow_sides()
        if name == "mode":
            self.show_mode()
        elif name == "repo":
            self.load_repo()
        elif name == "output_format":
            self.rename_output()
            self.update_splits()
        elif name == "assess_ai":
            self.update_models()
        elif name == "assess_model":
            self.update_efforts()

    def carry_single(self) -> None:
        """From One file to Files: the file reviewed becomes the old version,
        for its revision to be compared with it; not when it is the new one."""
        single = str(self.values["single"]).strip()
        if single and single != str(self.values["new"]).strip():
            self.values["old"] = single

    def show_mode(self) -> None:
        """What a mode asks for: the status it starts from, an HTML report
        to review one file or make one again, the splits and the comments
        it offers."""
        mode = self.values["mode"]
        if mode != "git":
            self.status = (
                REBUILD_READY if mode == "rebuild" else REVIEW_READY if mode == "review" else READY
            )
        if mode in ("review", "rebuild") and self.values["output_format"] != "html":
            self.values["output_format"] = "html"
            self.rename_output()
        self.update_splits()
        self.update_comments()

    @locked
    def swap(self, what: str) -> None:
        """Exchange the old and the new file ("files") or folder ("folders")."""
        a, b = ("old", "new") if what == "files" else ("old_folder", "new_folder")
        first = self.values[a]
        self.set(a, self.values[b])
        self.set(b, first)

    def update_splits(self) -> None:
        """Compare by offers the splits the output can hold (check_split):
        both for the HTML report only, which switches between them;
        sentences not for a document of tracked changes, whose paragraphs are
        paragraphs. One it cannot hold, chosen, is given up for the output's
        default (default_split), to come back with an output that can hold
        it."""
        fmt = self.values["output_format"]

        def fits(split: str) -> bool:
            return passes(check_split, split, fmt)

        if self.split_given_up is not None:
            given_up, put = self.split_given_up
            if self.values["split"] != put:  # chosen since: it stays
                self.split_given_up = None
            elif fits(given_up):
                self.values["split"] = given_up
                self.split_given_up = None
        if not fits(self.values["split"]):
            put = default_split(fmt)
            self.split_given_up = (self.values["split"], put)
            self.values["split"] = put

    def update_comments(self) -> None:
        """Comments offers, reviewing one file, markers or none (the AI is
        sent the file's comments, or not); text, chosen, is given up for
        markers, to come back with two versions compared."""
        review = self.values["mode"] == "review"
        if review and self.values["comments"] == "text":
            self.values["comments"] = "markers"
            self.comments_given_up = True
        elif not review and self.comments_given_up:
            if self.values["comments"] == "markers":  # not chosen since
                self.values["comments"] = "text"
            self.comments_given_up = False

    def rename_output(self) -> None:
        """Give the Save to file the extension of the format chosen."""
        output = str(self.values["output"]).strip()
        default = output == self.auto_output
        self.values["output"] = with_format(output, self.values["output_format"])
        if default:
            self.auto_output = self.values["output"]

    def sides_page(self) -> str:
        """Where the output goes by default: next to the new file, into the new
        folder, the file reviewed (page_of); "" comparing git versions, or sides
        not yet chosen."""
        v = self.values
        mode = v["mode"]
        if mode == "rebuild":  # over the report the answers were saved beside
            saved = str(v["rebuild_file"]).strip()
            if saved.endswith(".ai.json"):
                return str(Path(saved[: -len(".ai.json")] + ".html").resolve())
            # a project: over the report its answers were saved with
            if saved.endswith(".prosediff"):
                from prosediff.saved import SavedError, load_project

                try:
                    report = load_project(saved)[1] or {}
                except SavedError:
                    return ""
                return str(report.get("run", {}).get("output") or "")
            return ""
        old, new = {
            "review": ("single", "single"),
            "folders": ("old_folder", "new_folder"),
        }.get(mode, ("old", "new"))
        page = page_of(mode, str(v[old]).strip(), str(v[new]).strip(), FORMATS[v["output_format"]])
        return str(page.resolve()) if page else ""

    def follow_sides(self) -> None:
        """Keep Save to on the default as the sides change: an empty one, or one
        still on the default of the sides before, is moved; one chosen stays."""
        if str(self.values["output"]).strip() not in ("", self.auto_output):
            return
        self.auto_output = self.sides_page()
        self.values["output"] = self.auto_output

    @locked
    def load_repo(self, keep: tuple[str, str] | None = None) -> None:
        """Fill the base and target lists from the repository."""
        path = str(self.values["repo"]).strip()
        if not path:
            return
        try:
            commits, dirty = list_choices(path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError, ValueError):
            self.status = f"Not a git repository: {path}"
            self.lists["base"] = self.lists["target"] = []
            return
        self.choices = {c.label: c.ref for c in commits}
        self.choices[WORKTREE] = "worktree"
        self.choices[INDEX] = "index"
        labels = [c.label for c in commits]
        self.lists["base"] = labels
        self.lists["target"] = [WORKTREE, INDEX, *labels]
        base, target = keep if keep and keep[0] else default_sides(commits, dirty)
        self.values["base"] = self.label_of(base)
        self.values["target"] = self.label_of(target)
        n = len(commits)
        self.status = f"{counted(n, 'commit')} listed" + (
            ", uncommitted changes present." if dirty else "."
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

    # The AI --------------------------------------------------------------------

    def ai_chosen(self) -> str:
        """The AI chosen; "" while the AIs are being found."""
        ai = str(self.values["assess_ai"]).strip()
        return "" if ai == LOADING else ai

    def ai_active(self) -> bool:
        """Whether an AI will assess: one is chosen, and the output is the HTML
        report, the only one with room for its assessment."""
        chosen = self.ai_chosen() not in ("", NO_ASSESSMENT)
        return chosen and self.values["output_format"] == "html"

    def ask(self, kind: str, find: Callable[[], list]) -> None:
        """Look for something in the background (an AI's models, the
        providers), without holding the page up; found, it goes in its list
        (found) and the page is shown again."""
        if kind in self.asking:
            return
        self.asking.add(kind)

        def work() -> None:
            try:
                found, error = find(), ""
            except Exception as e:
                found, error = [], str(e)
            self.found(kind, found, error)
            self.render()

        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()

    def settle(self, timeout: float = 10) -> None:
        """Wait for the background to find what it is looking for."""
        while self.threads:
            self.threads.pop().join(timeout)

    @locked
    def found(self, kind: str, found: list, error: str) -> None:
        """What the background found, put in its list; an AI that could not
        say its models says why in the status line."""
        self.asking.discard(kind)
        if kind == "providers":
            self.ais = list(found)
            self.use_providers(self.ais, error)
            return
        self.ai_models[kind] = found
        if error:
            self.status = f"The models of {kind} are not known: {error}"
        if self.ai_chosen() == kind:
            self.update_models(keep=True)

    def use_providers(self, found: list[str], error: str) -> None:
        """The AIs found: none chosen while they were looked for, none; one
        saved that is not among them, an error, and none."""
        ai = str(self.values["assess_ai"]).strip()
        if ai == LOADING:
            self.values["assess_ai"] = NO_ASSESSMENT
        elif ai not in found:
            self.complain(
                f"The AI saved, {ai}, is not available any more"
                + (f" ({error})" if error else "")
                + ": no AI assesses the changes until another is chosen."
            )
            self.values["assess_ai"] = NO_ASSESSMENT
        self.update_models(keep=True)

    def update_models(self, keep: bool = False) -> None:
        """The model list of the AI chosen, as the AI reports it (asked for
        in the background the first time, the model and effort greyed out
        meanwhile); none for none. The model shown becomes the AI's own
        default, the first it reports, unless keep and one is already
        chosen: the one saved, said in an error and replaced by the default
        when the AI no longer offers it."""
        ai = self.ai_chosen()
        if not keep:  # another AI: its own default, once known
            self.values["assess_model"] = ""
        if ai in ("", NO_ASSESSMENT) or ai not in self.ai_models:
            self.lists["assess_model"] = []
            if ai not in ("", NO_ASSESSMENT):
                self.ask(ai, lambda: models_of(ai))
            # another AI's effort goes with its model
            self.update_efforts(keep=keep)
            return
        models = [m.name for m in self.ai_models[ai]]
        self.lists["assess_model"] = models
        saved = str(self.values["assess_model"]).strip()
        if saved and models and saved not in models:
            self.complain(
                f"The model saved, {saved}, is not one {ai} offers any more: its "
                f"default, {models[0]}, is chosen instead."
            )
        elif saved:
            self.update_efforts(keep=True)
            return
        self.values["assess_model"] = models[0] if models else ""
        self.update_efforts()

    def chosen_model(self) -> ModelInfo | None:
        """The model chosen, as its AI reports it; None when unknown."""
        name = str(self.values["assess_model"]).strip()
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
        self.efforts = levels
        if keep and (self.values["assess_effort"] in levels or model is None):
            return
        default = model.default_effort if model else ""
        self.values["assess_effort"] = default or (MODEL_DEFAULT if levels else "")

    def effort_chosen(self) -> str:
        """The effort to ask for: "" for the model's own default."""
        effort = str(self.values["assess_effort"]).strip()
        return "" if effort == MODEL_DEFAULT else effort

    def assess_spec(self) -> str:
        """The AI assessment asked for, as --assess takes it: "claude",
        "claude/opus", "ollama/qwen3"; "" for none. Claude Code's own
        default is asked for by naming no model."""
        ai, model = self.ai_chosen(), str(self.values["assess_model"]).strip()
        if ai in ("", NO_ASSESSMENT):
            return ""
        if not model or (ai == "claude" and model == CLAUDE_DEFAULT):
            return ai
        return f"{ai}/{model}"

    # The files sent to the AI as context ---------------------------------------

    def files_chosen(self) -> list[str]:
        """The files to send, as kept: assess_files, separated by ";"."""
        return [f.strip() for f in str(self.values["assess_files"]).split(";") if f.strip()]

    def files_rows(self) -> dict:
        """The files to send, as their list shows them: sorted as their
        columns' headings were clicked (sent in the order added), a row
        each, its path, name and folder."""
        files = self.files_chosen()
        column, descending = self.files_sort or (None, False)
        if column is not None:
            key = {
                "name": lambda f: natural(Path(f).name),
                "folder": lambda f: (natural(str(Path(f).parent)), natural(Path(f).name)),
            }[column]
            files = sorted(files, key=key, reverse=descending)
        return {
            "rows": [[f, Path(f).name, str(Path(f).parent)] for f in files],
            "sort": [column, descending] if column else None,
        }

    def analysed(self) -> Callable[[str | Path], bool]:
        """Whether a file is one of those the mode shown analyses
        (pipeline.analysed)."""
        mode, v = self.values["mode"], self.values
        old, new = {
            "files": (v["old"], v["new"]),
            "review": (v["single"], ""),
            "folders": (v["old_folder"], v["new_folder"]),
            "git": (v["repo"], ""),
        }.get(mode, ("", ""))
        return analysed(mode, str(old).strip(), str(new).strip())

    @locked
    def add_files(self, chosen: list[str]) -> None:
        """Files added to those sent to the AI as context: not those analysed,
        which the AI would be sent twice."""
        within = self.analysed()
        refused = [f for f in chosen if within(f)]
        chosen = [f for f in chosen if not within(f)]
        if chosen:
            have = self.files_chosen()
            self.values["assess_files"] = ";".join([*have, *(f for f in chosen if f not in have)])
            self.values["assess_send_files"] = True
        if refused:
            names = ", ".join(Path(f).name for f in refused)
            self.complain(f"{names}: analysed, so not sent to the AI as a context file too.")

    def pick_files(self) -> None:
        """Choose files to add to those sent to the AI as context."""
        chosen = self.ui.ask_open(
            "Files to send the AI",
            ["Documents (*.pdf;*.docx;*.odt;*.md;*.txt)", "All files (*.*)"],
            many=True,
        )
        if chosen:
            self.add_files(chosen)

    @locked
    def remove_files(self, gone: list[str]) -> None:
        """Take files off the list of those sent to the AI."""
        if gone:
            self.values["assess_files"] = ";".join(f for f in self.files_chosen() if f not in gone)

    @locked
    def sort_files(self, column: str) -> None:
        """The list of files sorted by column, the other way when it already
        is."""
        if column not in FILE_COLUMNS:
            return
        current, descending = self.files_sort or (None, False)
        self.files_sort = (column, not descending if column == current else False)

    # The instructions and prosediff's prompts ----------------------------------

    @locked
    def instructions(self) -> list[dict]:
        """prosediff's prompts and the instructions, for large boxes of their
        own: the prompts of the mode shown (the review of one file; or the
        assessment of two versions, and whether their new text reads as
        written by an AI), then the instructions. Each its field, its label,
        its text (a prompt left as prosediff's, prosediff's), its default
        (none for the instructions); one a file holds read-only, the file
        kept: it is read again at every run."""
        review = self.values["mode"] == "review"
        if review:
            prompts = [
                ("prosediff's prompt, for reviewing one file", "assess_review_prompt", "review"),
                (
                    "prosediff's prompt, for whether the file reads as AI-written",
                    "assess_review_writing_prompt",
                    "writing",
                ),
            ]
        else:
            prompts = [
                ("prosediff's prompt, for assessing the changes", "assess_prompt", "value"),
                (
                    "prosediff's prompt, for whether the new text reads as AI-written",
                    "assess_writing_prompt",
                    "writing",
                ),
            ]
        # the instructions last, with no default to restore
        boxes = [(label, name, default_system(kind, review)) for label, name, kind in prompts]
        boxes.append(("Your instructions, added to the prompt", "assess_instructions", ""))
        shown = []
        for label, name, default in boxes:
            value = str(self.values[name])
            path = instructions_file(value)
            if path:
                shown.append(
                    {
                        "field": name,
                        "label": f"{label}: from {path.name} (edit that file, or clear the "
                        "field to write here)",
                        "text": path.read_text(encoding="utf-8").strip(),
                        "default": default,
                        "readonly": True,
                    }
                )
            else:
                shown.append(
                    {
                        "field": name,
                        "label": label,
                        "text": value.strip() or default,
                        "default": default,
                        "readonly": False,
                    }
                )
        return shown

    @locked
    def set_instructions(self, texts: dict[str, str]) -> None:
        """The boxes' texts kept (OK): a prompt left as prosediff's kept as
        "", to follow prosediff's; one read from a file is not changed."""
        for box in self.instructions():
            if box["readonly"] or box["field"] not in texts:
                continue
            written = str(texts[box["field"]]).strip()
            self.values[box["field"]] = "" if written == box["default"].strip() else written

    # Files and folders chosen --------------------------------------------------

    def pick(self, name: str) -> None:
        """Choose a file, or a folder, for a field: the repository, a side,
        the saved answers, the instructions."""
        title, folder, types = {
            "repo": ("Git repository", True, []),
            "old": ("The old file", False, []),
            "new": ("The new file", False, []),
            "old_folder": ("The old folder", True, []),
            "new_folder": ("The new folder", True, []),
            "single": ("The file to review", False, []),
            "rebuild_file": (
                "The AI's saved answers",
                False,
                ["Saved answers or project (*.ai.json;*.prosediff)", "All files (*.*)"],
            ),
            "assess_instructions": (
                "Instructions for the AI",
                False,
                ["Text (*.txt;*.md)", "All files (*.*)"],
            ),
        }[name]
        start = str(self.values[name]).strip()
        if folder:
            chosen = self.ui.ask_folder(title, start)
        else:
            chosen = next(iter(self.ui.ask_open(title, types)), "")
        if chosen:
            self.set(name, chosen)

    def pick_output(self) -> None:
        """Choose where the output goes."""
        fmt = self.values["output_format"]
        kinds = {
            "html": ("the HTML report", "HTML report (*.html)"),
            "diff": ("the diff", "Unified diff (*.diff;*.patch)"),
            "wdiff": ("the word diff", "Word diff (*.wdiff)"),
            "docx": ("the tracked changes", "Word document (*.docx)"),
            "odt": ("the tracked changes", "OpenDocument text (*.odt)"),
        }
        what, types = kinds.get(fmt, kinds["html"])
        output = Path(str(self.values["output"]).strip() or "output")
        chosen = self.ui.ask_save(
            f"Save {what} as",
            [types],
            str(output.parent) if output.parent != Path() else "",
            with_format(output.name, fmt),
        )
        if chosen:
            self.set("output", chosen)

    # Options and projects ------------------------------------------------------

    @locked
    def complain(self, text: str) -> None:
        """An error found by the window itself, said in a dialog and the
        status line."""
        self.status = text
        self.ui.alert("error", text)

    @locked
    def collect(self) -> Settings:
        """The settings the page shows; ValueError for a field of a number
        holding none."""
        v = self.values

        def number(name: str, kind: type) -> float:
            try:
                return kind(str(v[name]).strip())
            except ValueError:
                raise ValueError(f"{NUMBER_NAMES[name]} must be a number.") from None

        direct = {
            f.name: (v[f.name].strip() if isinstance(v[f.name], str) else v[f.name])
            for f in fields(Settings)
            if f.name in v
        }
        context = str(v["context_lines"]).strip()
        # a number as typed, negative too, for run to refuse (context_of)
        if not context.lstrip("-").isdigit():
            context = "auto"
        moves = {}
        for sentences, prefix in ((False, ""), (True, "sentence_")):
            # the default, while it is the one shown: it follows prosediff's
            default_similarity, default_algorithm = move_defaults(sentences)
            try:
                value = min(1.0, max(0.05, float(str(v[f"{prefix}move_similarity"]))))
            except ValueError:
                value = default_similarity
            algorithm = v[f"{prefix}move_algorithm"]
            moves[sentences] = (
                None if value == default_similarity else value,
                None if algorithm == default_algorithm else algorithm,
            )
        try:
            language = normalize_language(str(v["language"]))
        except ValueError:
            language = DEFAULT
        try:
            encoding = check_encoding(str(v["encoding"]))
        except ValueError:
            encoding = AUTO_ENCODING
        output = str(v["output"]).strip()
        docx_changes = v["docx_changes"] if v["docx_changes"] in DOCX_CHANGES else "accept-all"
        return Settings(
            **direct
            | {
                "max_hidden": number("max_hidden", int),
                "assess_timeout": number("assess_timeout", float),
                "context_lines": context,
                "language": language,
                "encoding": encoding,
                "base": self.ref_of(str(v["base"])),
                "target": self.ref_of(str(v["target"])),
                "paths": [p.strip() for p in str(v["paths"]).split(";") if p.strip()],
                "docx_changes": docx_changes,
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

    def passage_choices(self) -> dict[str, float]:
        """The moved-passage settings shown that differ from prosediff's
        defaults; ValueError for a field holding no number."""
        chosen: dict[str, float] = {}
        for f in fields(MovedPassageSettings):
            kind = float if f.metadata["share"] else int
            try:
                value = kind(str(self.values[PASSAGE + f.name]).strip())
            except ValueError:
                raise ValueError(f"{f.metadata['label']} must be a number.") from None
            if value != f.default:
                chosen[f.name] = value
        return chosen

    @locked
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
            self.status = f"Options saved: {path}"
        else:
            self.status = f"Options not saved: {path} cannot be written"

    @locked
    def reset_options(self) -> None:
        """Every option to its default (what is compared and where the output
        goes stay as they are); nothing is saved until asked."""
        defaults = values_of(Settings())
        stay = (*COMPARED, "output", "rebuild_file", "base", "target", "paths")
        for name, value in defaults.items():
            if name not in stay:
                self.values[name] = value
        self.values["assess_ai"] = NO_ASSESSMENT if self.ais is not None else LOADING
        self.split_given_up = None
        self.comments_given_up = False
        self.update_models()
        self.rename_output()
        self.update_splits()
        self.update_comments()
        self.status = "Options reset to their defaults (not saved)."

    # Projects ------------------------------------------------------------------
    # A project (NAME.prosediff, prosediff.saved) keeps every setting, what is
    # compared and the context files sent to the AI included, the AI's
    # answers of the last report made with them, and the choices made in that
    # report: opened, the window shows them all again, and the Rebuild tab
    # makes that report again, as long as the files it read are unchanged,
    # with those choices.

    def save_project(self, ask: bool = False) -> None:
        """Save the settings shown, the AI's answers of the last report and
        the choices made in it, in the project open, or (ask, or none open)
        in a file chosen."""
        from prosediff.saved import PROJECT_SUFFIX, save_project

        try:
            s = self.collect()
        except ValueError as e:
            self.complain(str(e))
            return
        path = self.project_path
        if ask or path is None:
            sides = [s.single, s.new, s.old, s.new_folder, s.repo]
            near = next((Path(p) for p in sides if p.strip()), None)
            chosen = self.ui.ask_save(
                "Save project",
                [f"prosediff project (*{PROJECT_SUFFIX})", "All files (*.*)"],
                str(near.parent if near and near.is_file() else near or Path.home()),
                (near.stem if near and near.is_file() else "project") + PROJECT_SUFFIX,
            )
            if not chosen:
                return
            path = Path(chosen)
            if not path.suffix:
                path = path.with_suffix(PROJECT_SUFFIX)
        with self.lock:
            try:
                save_project(path, asdict(s), self.project_report, self.project_choices)
            except OSError as e:
                self.complain(f"The project was not saved: {e}")
                return
            self.project_path = path.resolve()
            kept = " with the last report's AI answers" if self.project_report else ""
            self.status = f"Project saved{kept}: {path}"

    def open_project(self, path: str | Path | None = None) -> None:
        """Open a project: the window set to its settings, its Rebuild tab
        set to it when it holds a report."""
        from prosediff.saved import PROJECT_SUFFIX, SavedError, project_choices

        if self.job is not None:
            self.complain("Wait for the run to end, or cancel it, before opening a project.")
            return
        if path is None:
            path = next(
                iter(
                    self.ui.ask_open(
                        "Open project",
                        [f"prosediff project (*{PROJECT_SUFFIX})", "All files (*.*)"],
                    )
                ),
                "",
            )
            if not path:
                return
        try:
            s, report = project_settings(Path(path))
            choices = project_choices(path)
        except SavedError as e:
            self.complain(str(e))
            return
        self.load(s, (Path(path).resolve(), report, choices))

    def keep_answers(self, s: Settings, result: JobResult) -> None:
        """The AI's answers of the report just made, for Save project: those
        it kept (NAME.ai.json); a report made again, those it was made from
        (a project's own stay). The choices made in the report before go
        with answers of another."""
        from prosediff.saved import SavedError, answers

        source = result.saved or (s.rebuild_file.strip() if s.mode == "rebuild" else "")
        if not source:
            return
        # a project made again from is no NAME.ai.json: its answers are those kept
        with contextlib.suppress(SavedError):
            kept = answers(source)
            if kept != self.project_report:
                self.project_report, self.project_choices = kept, None

    # The choices made in a report ----------------------------------------------

    @locked
    def choices_of(self, serial: int | None) -> dict | None:
        """The choices made in the report opened as serial: those kept, when
        it is the last report, whose answers the project holds."""
        if serial is None or serial != self.report_serial or self.project_report is None:
            return None
        return self.project_choices

    @locked
    def keep_choices(self, serial: int | None, made: dict) -> None:
        """The choices made in the report opened as serial, kept for the
        project (the last report's only): written into the project open at
        once, when it holds that report's answers; otherwise kept for Save
        project."""
        from prosediff.saved import SavedError, load_project, save_choices

        if serial is None or serial != self.report_serial or self.project_report is None:
            return
        self.project_choices = made
        if self.project_path is None:
            self.status = "Choices made in the report: Save project keeps them."
            self.render()
            return
        try:
            if load_project(self.project_path)[1] != self.project_report:
                self.status = (
                    "Choices made in a report the project does not hold yet: "
                    "Save project keeps them with it."
                )
                self.render()
                return
            save_choices(self.project_path, made)
        except (SavedError, OSError) as e:
            self.status = f"The choices were not saved in the project: {e}"
            self.render()

    # Running -------------------------------------------------------------------

    def run(self) -> None:
        """Compare (review, rebuild) as the fields say, in a process of its
        own that Cancel stops; its stages in the progress log."""
        with self.lock:
            if self.job is not None:
                return  # one comparison at a time
            try:
                s = self.collect()
                check_numbers(s)
            except ValueError as e:
                self.complain(str(e))
                return
            if s.mode == "rebuild" and not s.rebuild_file.strip():
                self.complain(
                    "Choose the AI's saved answers (.ai.json), or a project (.prosediff)."
                )
                return
            if s.mode == "review" and not s.assess:
                self.complain("Choose an AI, under AI assessment, to review the file.")
                return
            if s.assess and s.output_format == "html" and s.mode != "rebuild":
                try:
                    parse_backend(s.assess)
                except AssessError as e:
                    self.complain(f"AI assessment: {e}")
                    return
            self.log_lines = []  # the last run's lines gone
            self.ui.clear_log()
            self.set_stage("Starting…")
            # a process of its own, which Cancel stops with all it started
            context = multiprocessing.get_context("spawn")
            self.messages, self.replies = context.Queue(), context.Queue()
            self.job = context.Process(
                target=run_job, args=(s, self.messages, self.replies), daemon=True
            )
            self.job.start()
            self.job_settings = s
            job = self.job
        watcher = threading.Thread(target=self.watch, args=(job,), daemon=True)
        self.threads.append(watcher)
        watcher.start()

    @locked
    def cancel(self) -> None:
        """Stop the comparison running, and all it started."""
        if self.job is None:
            return
        stop_process_tree(self.job.pid)
        self.finish_job()
        self.status = "Cancelled."

    def finish_job(self) -> None:
        self.job = None
        self.preview_text = ""

    def set_stage(self, stage: str) -> None:
        self.stage, self.stage_started, self.live = stage, time.monotonic(), ""
        self.status = stage
        self.log(stage)

    def log(self, line: str) -> None:
        """A line at the end of the progress log, with the time."""
        line = f"{time.strftime('%H:%M:%S')}  {line}"
        self.log_lines.append(line)
        self.ui.log(line)

    def watch(self, job: multiprocessing.process.BaseProcess) -> None:
        """Pick up what job, the comparison's process, says: each stage,
        shown with the seconds it has taken so far, then its result. Ends
        when the job does, or is cancelled (another may have started since)."""
        last = ""
        while True:
            try:
                kind, value = self.messages.get(timeout=0.1)
            except (queue.Empty, OSError, ValueError):
                with self.lock:
                    if job is not self.job:
                        return  # cancelled
                    if job.is_alive():
                        seconds = time.monotonic() - self.stage_started
                        if seconds >= 1 and not self.preview_text:
                            # what the model is doing, when it says
                            live = f" · {self.live}" if self.live else ""
                            self.status = f"{self.stage} {duration(seconds)}{live}"
                        if self.status != last:
                            last = self.status
                            self.render()
                        continue
                # its last word may still be on its way when it has ended
                try:
                    kind, value = self.messages.get(timeout=2)
                except queue.Empty:
                    with self.lock:
                        if job is not self.job:
                            return
                        code = job.exitcode
                        self.finish_job()
                        self.status = "Not compared."
                    self.ui.alert(
                        "error", f"The comparison stopped without a result (exit code {code})."
                    )
                    self.render()
                    return
            with self.lock:
                if job is not self.job:
                    return
                more = self.handle(kind, value)
            self.render()
            if not more:
                return

    def handle(self, kind: str, value) -> bool:
        """One message of the comparison's process: a stage, what the model
        is doing, a preview, its result, or its error; whether more are to
        come."""
        if kind == "stage":
            self.set_stage(value)
            return True
        if kind == "live":
            self.live, news = value
            for line in news:
                self.log(line)
            return True
        if kind == "preview":
            self.preview(value)
            return True
        s = self.job_settings
        self.finish_job()
        if kind == "error":
            self.status = "Not compared."
            self.ui.alert("error", value)
            return False
        result: JobResult = value
        path, assessment = result.path, result.assessment
        self.keep_answers(s, result)
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
        self.status = f"{summary}: {path.name}"
        if assessment is not None and assessment.error:
            self.ui.alert("warning", f"The AI assessment failed: {assessment.error}")
        if result.writing_error:
            self.ui.alert("warning", f"The AI-writing assessment failed: {result.writing_error}")
        self.ui.toast(f"{summary}\n{path.name}")
        if s.open_page:
            if path.suffix.lower() == ".html":
                self.report_serial += 1
                self.ui.open_report(path, self.report_serial)
            else:
                self.ui.open_file(path)
        return False

    def preview(self, path: Path) -> None:
        """The report without the assessment, open in a window of its own:
        whether its text goes to the AI is asked on the Open screen
        (answer_preview)."""
        self.set_stage("Preview open: send it to the AI?")
        what, it = (
            ("the file", "it") if self.job_settings.mode == "review" else ("the changes", "them")
        )
        self.preview_text = (
            f"The report without the AI assessment is open: {path.name}. "
            f"Send {what} to {self.job_settings.assess} for assessment? Send: the "
            f"AI assesses {it} and the report is written again with its assessment. "
            "Don't send: the report stays as it is."
        )
        self.preview_path = path
        self.ui.open_report(path, None)

    @locked
    def show_preview(self) -> None:
        """The preview open again, its window closed or out of sight."""
        if self.preview_text and self.preview_path is not None:
            self.ui.open_report(self.preview_path, None)

    @locked
    def answer_preview(self, send: bool) -> None:
        """Send the preview's answer to the comparison's process, which then
        goes on."""
        self.preview_text = ""
        if self.job is None:
            return  # cancelled while the question was open
        self.replies.put(bool(send))


COMMENTS_TIP = (
    "markers: only the comments added or removed, set apart (a marker and a panel in the "
    "HTML report, CriticMarkup in the diffs); text: compared as text; none: left out."
)
REVIEW_COMMENTS_TIP = (
    "markers: the file's comments shown as markers and in a panel, and sent to the AI, "
    "which checks whether the text answers them; none: left out, and not sent to the AI."
)


# The windows -------------------------------------------------------------------
# pywebview's, imported only when the window opens: a comparison's process,
# and the tests of App, never need it.

# The Open screen's size, and the least it may be made.
WINDOW_SIZE = (940, 900)
WINDOW_MIN = (760, 560)
REPORT_SIZE = (1400, 900)
# The least and the most a report's window may be zoomed (its page steps
# between them as a browser does).
ZOOM_RANGE = (0.25, 5.0)


def page_html() -> str:
    """The Open screen (templates/window.html.j2), with the choices the
    fields offer and their explanations."""
    from prosediff.render import _env

    passages = [
        {
            "name": PASSAGE + f.name,
            "label": f.metadata["label"],
            "tip": f"{f.metadata['help'][0].upper()}{f.metadata['help'][1:]}. "
            f"Default: {f.default:,}.",
            "share": f.metadata["share"],
            "low": 0.01 if f.metadata["share"] else f.metadata["low"],
        }
        for f in fields(MovedPassageSettings)
    ]
    moves = [
        {
            "what": what,
            "prefix": prefix,
            "default": "{:.2f} {}".format(*move_defaults(sentences)),
            "sentences": sentences,
        }
        for what, prefix, sentences in (("paragraphs", "", False), ("sentences", "sentence_", True))
    ]
    return _env.get_template("window.html.j2").render(
        languages=LANGUAGES,
        encodings=ENCODINGS,
        docx_changes=[(v, DOCX_CHANGE_LABELS[v]) for v in DOCX_CHANGES],
        alignments=ALIGNMENTS,
        algorithms=tuple(MOVE_ALGORITHMS),
        passages=passages,
        moves=moves,
        max_hidden=MAX_HIDDEN,
        assess_timeout=ASSESS_TIMEOUT,
        comments_tip=COMMENTS_TIP,
    )


class WebUi:
    """App's screen: the Open screen's page in its window (window), each
    report in a window of its own."""

    def __init__(self) -> None:
        self.window = None
        self.app: App | None = None
        self.report = None  # the report's window, while open, and its address
        self.report_url = ""
        self.report_api: ReportApi | None = None
        self.title = ""

    def js(self, call: str, *args) -> None:
        """Call one of the page's functions (window.prosediff.NAME)."""
        if self.window is None:
            return
        text = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        with contextlib.suppress(Exception):  # a page not loaded yet asks itself (view)
            self.window.evaluate_js(f"window.prosediff && prosediff.{call}({text})")

    def render(self, view: dict) -> None:
        self.js("render", view)
        self.retitle(view["text"]["title"])

    def retitle(self, title: str) -> None:
        if self.window is not None and title != self.title:
            self.title = title
            self.window.set_title(title)

    def log(self, line: str) -> None:
        self.js("log", line)

    def clear_log(self) -> None:
        self.js("clearLog")

    def alert(self, kind: str, text: str) -> None:
        self.js("alert", kind, text)

    def toast(self, text: str) -> None:
        self.js("toast", text)

    def open_report(self, path: Path, serial: int | None) -> None:
        """The report in its window (the one open, or a new one), its
        choices kept as serial's."""
        import webview

        url = path.resolve().as_uri()
        if self.report is not None:
            self.report_api._serial, self.report_api._path = serial, path
            # the same file written again (a preview, then its report): the
            # web view would not load an address it shows, it reloads it
            if url == self.report_url:
                self.report.evaluate_js("location.reload()")
            else:
                self.report.load_url(url)
            self.report_url = url
            self.report.set_title(f"{path.name} · prosediff")
            self.report.show()
            return
        self.report_url = url
        self.report_api = ReportApi(self.app, serial, path, self)
        self.report = webview.create_window(
            f"{path.name} · prosediff",
            url=url,
            js_api=self.report_api,
            width=REPORT_SIZE[0],
            height=REPORT_SIZE[1],
            min_size=WINDOW_MIN,
        )

        def closed() -> None:
            self.report = None

        self.report.events.closed += closed

    def zoom_report(self, factor: float) -> bool:
        """The report's window zoomed to factor (1: as made), as a browser
        zooms a page: the web view's own zoom, so the page lays itself out
        again at the new size, its margin cards still beside their passages.
        Whether the window could (WebView2 on Windows, Qt on Linux; not on
        macOS, where the page leaves the zoom to the system)."""
        native = getattr(self.report, "native", None)
        view = getattr(native, "webview", None)
        if view is None:
            return False
        try:
            # WebView2 known by its class's name: reading any of its
            # properties off the form's thread waits for that thread
            if type(view).__name__ == "WebView2":
                from System import Func, Type  # pythonnet, with pywebview on Windows

                def set_zoom() -> None:
                    view.ZoomFactor = factor

                # set on the form's thread, not waited for: asked from the
                # page as it loads, Invoke would wait on the form's thread
                # while that waits on pywebview giving the page its functions
                native.BeginInvoke(Func[Type](set_zoom))
                return True
            if hasattr(view, "setZoomFactor"):  # Qt, on its own thread
                from qtpy import QtCore

                QtCore.QTimer.singleShot(0, view, lambda: view.setZoomFactor(factor))
                return True
        except Exception:
            return False
        return False

    def open_file(self, path: Path) -> None:
        open_output(path)

    def dialog(
        self,
        kind: str,
        title: str,
        types=(),
        start: str = "",
        name: str = "",
        many=False,
        window=None,
    ) -> list[str] | None:
        """A file dialog of the system's, over window (the Open screen's):
        the files or the folder chosen, None for none."""
        import webview

        window = window or self.window
        kinds = {
            "open": webview.FileDialog.OPEN,
            "folder": webview.FileDialog.FOLDER,
            "save": webview.FileDialog.SAVE,
        }
        try:
            chosen = window.create_file_dialog(
                kinds[kind],
                directory=start,
                allow_multiple=many,
                save_filename=name,
                file_types=tuple(types),
            )
        except Exception as e:
            self.alert("error", f"{title}: {e}")
            return None
        if chosen is None:
            return None
        return [chosen] if isinstance(chosen, str) else list(chosen)

    def ask_open(self, title: str, types: list[str], many: bool = False) -> list[str]:
        return self.dialog("open", title, types, many=many) or []

    def ask_folder(self, title: str, start: str = "") -> str:
        start = start if start and Path(start).is_dir() else ""
        return next(iter(self.dialog("folder", title, start=start) or []), "")

    def ask_save(
        self, title: str, types: list[str], start: str = "", name: str = "", window=None
    ) -> str:
        start = start if start and Path(start).is_dir() else ""
        return next(iter(self.dialog("save", title, types, start, name, window=window) or []), "")


class WindowApi:
    """The Open screen's calls into Python (window.pywebview.api): each
    answered with what the page now shows. Its attributes are private:
    pywebview would offer the page any others."""

    def __init__(self, app: App, ui: WebUi) -> None:
        self._app = app
        self._ui = ui
        self._actions: dict[str, Callable] = {
            "run": lambda: app.cancel() if app.job is not None else app.run(),
            "cancel": app.cancel,
            "preview": app.answer_preview,
            "show_preview": app.show_preview,
            "swap": app.swap,
            "pick": app.pick,
            "pick_output": app.pick_output,
            "pick_files": app.pick_files,
            "add_files": app.add_files,
            "remove_files": app.remove_files,
            "sort_files": app.sort_files,
            "save_options": app.save_options,
            "reset_options": app.reset_options,
            "save_project": app.save_project,
            "open_project": app.open_project,
            "set_instructions": app.set_instructions,
        }

    def view(self) -> dict:
        view = self._app.view()
        self._ui.retitle(view["text"]["title"])
        return view

    def log(self) -> list[str]:
        """The progress log's lines so far (a page loaded again)."""
        return list(self._app.log_lines)

    def set(self, name: str, value) -> dict:
        self._app.set(name, value)
        return self.view()

    def act(self, action: str, args: list | None = None) -> dict:
        self._actions[action](*(args or []))
        return self.view()

    def instructions(self) -> list[dict]:
        return self._app.instructions()


class ReportApi:
    """A report's calls into Python, in its window: the choices made in it,
    kept for the project, the documents to download saved, and the window
    zoomed."""

    def __init__(self, app: App, serial: int | None, path: Path, ui: WebUi) -> None:
        self._app = app
        self._ui = ui
        self._serial = serial
        self._path = path

    def choices(self) -> dict | None:
        return self._app.choices_of(self._serial)

    def save_choices(self, made: dict) -> None:
        if isinstance(made, dict):
            self._app.keep_choices(self._serial, made)

    def zoom(self, factor) -> bool:
        """The window zoomed to factor, kept between ZOOM_RANGE's ends:
        whether it could be (the page hides its zoom when not)."""
        try:
            factor = min(max(float(factor), ZOOM_RANGE[0]), ZOOM_RANGE[1])
        except (TypeError, ValueError):
            return False
        return self._ui.zoom_report(factor)

    def save_document(self, name: str, data: str) -> str:
        """A document to download saved where asked, beside the report at
        first; where it went ("": not saved)."""
        import base64

        suffix = Path(name).suffix.lower()
        kind = {".docx": "Word document", ".odt": "OpenDocument text"}.get(suffix, "Document")
        chosen = self._ui.ask_save(
            f"Save {name}", [f"{kind} (*{suffix})"], str(self._path.parent), name, self._ui.report
        )
        if not chosen:
            return ""
        try:
            Path(chosen).write_bytes(base64.b64decode(data))
        except (OSError, ValueError) as e:
            self._ui.alert("error", f"{Path(chosen).name} was not saved: {e}")
            return ""
        with self._app.lock:
            self._app.status = f"Saved: {chosen}"
        self._app.render()
        return chosen


def menu(app: App, ui: WebUi) -> list:
    """The Open screen's menus: File, to open and save projects (Ctrl+O,
    Ctrl+S on the page); Options, to remember the options for next time or
    put them back to their defaults."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    def then(do: Callable) -> Callable:
        def run() -> None:
            do()
            app.render()

        return run

    return [
        Menu(
            "File",
            [
                MenuAction("Open project…", then(app.open_project)),
                MenuAction("Save project", then(app.save_project)),
                MenuAction("Save project as…", then(lambda: app.save_project(ask=True))),
                MenuSeparator(),
                MenuAction("Quit", lambda: ui.window.destroy()),
            ],
        ),
        Menu(
            "Options",
            [
                # the labels say what the tooltips would: a menu has none
                MenuAction(
                    "Save options (the window opens with them next time)", then(app.save_options)
                ),
                MenuAction(
                    "Reset to defaults (what is compared and the output's place stay)",
                    then(app.reset_options),
                ),
            ],
        ),
    ]


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


def icon_path() -> Path:
    """The prosediff logo, for the windows: the .ico, with all its sizes, on
    Windows, the .png elsewhere."""
    here = Path(__file__).parent
    return here / ("logo.ico" if sys.platform == "win32" else "logo.png")


def show_error(text: str) -> None:
    """An error in a window of its own, before any other: the program's
    arguments refused."""
    import html

    import webview

    page = (
        "<!doctype html><meta charset=utf-8><style>body{font:14px/1.5 system-ui,sans-serif;"
        "margin:20px;white-space:pre-wrap}@media(prefers-color-scheme:dark){body{background:"
        "#0d1117;color:#e6edf3}}button{margin-top:12px;padding:4px 16px}</style>"
        f"<div>{html.escape(text)}</div><button onclick='pywebview.api.close()' autofocus>"
        "OK</button>"
    )

    class Close:
        def close(self) -> None:
            window.destroy()

    window = webview.create_window("prosediff", html=page, js_api=Close(), width=640, height=360)
    webview.start(icon=str(icon_path()))


def main(argv: list[str] | None = None) -> None:
    """prosediff-gui [REPOSITORY | FILE | OLD NEW | PROJECT]: the window,
    prefilled from the arguments when they are a git repository, Markdown,
    Word or OpenDocument files, or two folders; one file fills in the One
    file tab, to review it alone, one JSON file (NAME.ai.json) the Rebuild
    tab, and a project (NAME.prosediff) opens it.
    Arguments that are none of these are reported in an error window, with
    the arguments received, and the program exits once it is closed."""
    from prosediff.saved import PROJECT_SUFFIX, SavedError, project_choices

    args = sys.argv[1:] if argv is None else argv
    project = None
    if len(args) == 1 and Path(args[0]).suffix.lower() == PROJECT_SUFFIX:
        try:
            settings, report = project_settings(Path(args[0]))
            project = (Path(args[0]).resolve(), report, project_choices(args[0]))
            note = ""
        except SavedError as e:
            settings, note = load_settings(), str(e)
    else:
        settings, note = settings_from_args(args, load_settings())
    invisible_console()
    own_taskbar_button()
    if note:
        show_error(
            f"{note}\n\n{received(args)}\n\n"
            "Usage: prosediff-gui [REPOSITORY | FILE | OLD NEW | PROJECT.prosediff]"
        )
        sys.exit(2)
    import webview

    ui = WebUi()
    app = App(ui, settings, project)
    ui.app = app
    window = webview.create_window(
        app.title(),
        html=page_html(),
        js_api=WindowApi(app, ui),
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN,
        menu=menu(app, ui),
    )
    ui.window = window
    ui.title = app.title()

    def closing() -> None:
        # the comparison stopped with the window, and the report's window closed
        app.cancel()
        if ui.report is not None:
            with contextlib.suppress(Exception):
                ui.report.destroy()

    window.events.closed += closing
    storage = config_dir() / "window"
    webview.start(private_mode=False, storage_path=str(storage), icon=str(icon_path()))


if __name__ == "__main__":
    main()
