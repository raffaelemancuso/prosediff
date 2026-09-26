"""A window to choose what to compare; it writes the HTML report and opens it.

What is compared is chosen with a segmented button: a git repository (base
and target picked among its latest commits, the working tree and the index,
or typed as any ref), two files, or two folders. The options, those of the
command line that matter when reading a diff, sit in two cards (what is
compared, how it is shown), each explained by a tooltip; the output, an HTML
report or a unified or word diff, in a third. The comparison runs in a
background thread, a progress bar running meanwhile, so the window stays
responsive; a notification tells when it is done. The choices are
remembered for the next time only when asked (Save options), and Reset to
defaults puts every option back.

The widgets are ttkbootstrap's, in its Bootstrap theme: light or dark as the
system is set (Windows' app mode, macOS's appearance), the title bar too on
Windows; switches for the yes-or-no options, Bootstrap icons on the buttons.
"""

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from tkinter import filedialog, messagebox

import git
import ttkbootstrap as ttk

from prosediff.diff import (
    AUTO_ENCODING,
    MOVE_ALGORITHMS,
    Comparison,
    Context,
    FilterError,
    check_encoding,
    compare,
    compare_paths,
    move_defaults,
)
from prosediff.language import DEFAULT, DOCUMENT, GUESS, normalize_language
from prosediff.render import (
    ALIGNMENTS,
    FORMATS,
    TEXT_SUFFIXES,
    default_output,
    format_of,
    write_output,
)
from prosediff.sources import DOCX_CHANGES, FOLDER_FILES, SourceError, default_page

MAX_COMMITS = 200
ENCODINGS = (AUTO_ENCODING, "utf-8", "cp1252", "latin-1", "utf-16", "cp1250", "cp1251")
# The choices, then the common codes; any code can be typed.
LANGUAGES = (DEFAULT, DOCUMENT, GUESS)
LANGUAGES += ("en", "it", "de", "fr", "es", "pt", "nl", "pl", "sv", "da", "fi", "cs", "el")
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
    # the files of two folders compared: glob patterns separated by "|"
    include: str = FOLDER_FILES
    # "markers": set apart from the text (a marker and a panel in the HTML
    # report, CriticMarkup in the diffs); "text": compared as text; "none"
    comments: str = "markers"
    empty_comments: bool = False
    docx_changes: str = "accept"
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
    # how prose is compared: one of SPLITS
    split: str = "paragraph"
    # a language code; "document": marked in Word and OpenDocument files;
    # "guess": guessed from each file's text; "default": document, else guess
    language: str = DEFAULT
    # a codec's name, or "auto": UTF-8 unless a file shows it is not
    encoding: str = AUTO_ENCODING
    output: str = ""
    # "html": the HTML report; "diff", "wdiff": a unified or word diff (prosediff.unified)
    output_format: str = "html"
    open_page: bool = True


READY = "Choose what to compare, then Compare."
# The tooltips: their width in pixels, and how long the pointer must rest.
HINT_WIDTH = 360
HINT_DELAY_MS = 400
# How long the notification of a finished comparison stays up.
TOAST_MS = 4000
# What becomes of the comments, as --comments says.
COMMENT_MODES = ("markers", "text", "none")
# How prose is compared, as --split says: paragraph by paragraph, sentence
# by sentence, or both (in the HTML report only).
SPLITS = ("paragraph", "sentence", "both")
# The tabs, in their order.
MODES = ("git", "files", "folders")
PREFILLED_FILES = (".md", ".docx", ".odt")


def single_file(args: list[str]) -> Path | None:
    """The one Markdown, Word or OpenDocument file given, whose partner the
    window asks for."""
    if len(args) == 1:
        path = Path(args[0])
        if path.is_file() and path.suffix.lower() in PREFILLED_FILES:
            return path.resolve()
    return None


def page_beside(old: Path, new: Path) -> str:
    """Where the HTML report comparing two files goes: next to the new one, named
    after both, so HTML reports of different pairs do not overwrite each other; that
    comparing two folders, into the new one (default_page)."""
    if folder_page := default_page(old, new):
        return str(folder_page.resolve())
    return str(new.resolve().parent / f"{old.stem}_vs_{new.stem}.html")


def with_second_file(s: Settings, first: Path, second: Path) -> Settings:
    """The settings comparing two files, the older (by modification time)
    on the left: the draft sent before the one returned. The HTML report goes next
    to the newer."""
    older, newer = sorted((first, second), key=lambda p: (p.stat().st_mtime, str(p)))
    return replace(
        s,
        mode="files",
        old=str(older.resolve()),
        new=str(newer.resolve()),
        output=page_beside(older, newer),
    )


def settings_from_args(args: list[str], base: Settings) -> tuple[Settings, str]:
    """The settings to open the window with, given its command-line arguments.

    One argument that is a git repository (or a folder inside one) fills in
    the repository, the sides starting from their defaults; one Markdown,
    Word or OpenDocument file fills in the files tab, its partner to be
    chosen when the window opens; two such files fill in the files tab, two
    folders the folders tab. Anything else is ignored, and the second value says why.
    """
    s = replace(base)
    if len(args) == 1:
        path = Path(args[0])
        if single_file(args):
            # the other file is asked for when the window opens (main)
            s.mode = "files"
            s.old, s.new = str(path.resolve()), ""
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
            s.output = page_beside(old, new)
            return s, ""
        if old.is_dir() and new.is_dir():
            s.mode = "folders"
            s.old_folder, s.new_folder = str(old.resolve()), str(new.resolve())
            s.output = page_beside(old, new)
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
        s.split = "paragraph"
    if s.comments not in COMMENT_MODES:
        s.comments = "markers"
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


def generate(s: Settings) -> tuple[Path, Comparison]:
    """Compare as the settings say and write the HTML report; returns its path."""
    options = dict(
        paths=s.paths or None,
        context=context_of(s),
        ignore_whitespace=s.ignore_whitespace,
        fold_comments_md=s.comments != "text",
        drop_comments=s.comments == "none",
        empty_comments=s.empty_comments,
        docx_changes=s.docx_changes,
        # None: prosediff's defaults
        move_similarity=s.move_similarity,
        move_algorithm=s.move_algorithm if s.move_algorithm in MOVE_ALGORITHMS else None,
        sentence_move_similarity=s.sentence_move_similarity,
        sentence_move_algorithm=(
            s.sentence_move_algorithm if s.sentence_move_algorithm in MOVE_ALGORITHMS else None
        ),
        language=s.language or DEFAULT,
        encoding=s.encoding or AUTO_ENCODING,
    )
    old, new = sides(s)
    fmt = s.output_format if s.output_format in FORMATS else "html"
    split = s.split if s.split in SPLITS else "paragraph"
    if split == "both" and fmt != "html":
        raise ValueError("comparing both ways is for the HTML report: a diff holds one")

    def run(by_sentence: bool) -> Comparison:
        if s.mode == "files":
            if not old or not new:
                raise ValueError("choose the old and the new file")
            return compare_paths(old, new, by_sentence=by_sentence, **options)
        if s.mode == "folders":
            if not old or not new:
                raise ValueError("choose the old and the new folder")
            return compare_paths(old, new, include=s.include, by_sentence=by_sentence, **options)
        if not s.repo or not s.base:
            raise ValueError("choose a repository and a base")
        target = None if s.target in ("worktree", "index", "") else s.target
        return compare(
            s.repo,
            s.base,
            target,
            cached=s.target == "index",
            untracked=s.untracked and s.target in ("worktree", ""),
            by_sentence=by_sentence,
            **options,
        )

    comparison = run(split == "sentence")
    sentences = run(True) if split == "both" else None
    out = Path(s.output) if s.output else None
    if out is None and s.mode != "git":
        out = default_page(Path(old), Path(new))
    out = Path(with_format(str(out), fmt)) if out is not None else default_output(fmt)
    write_output(
        comparison,
        out,
        fmt,
        s.paths,
        align=s.align,
        context=context_of(s),
        sentences=sentences,
        split=split,
    )
    return out, comparison


def with_format(path: str, fmt: str) -> str:
    """The output file named for the format chosen: .html, .diff (a .patch
    stays one) or .wdiff; any other name, or none, stays."""
    p = Path(path)
    if not path or p.suffix.lower() not in (".html", *TEXT_SUFFIXES) or format_of(p) == fmt:
        return path
    return str(p.with_suffix(FORMATS[fmt]))


def move_similarity_of(s: Settings, sentences: bool = False) -> float:
    """The moved-line similarity chosen for paragraphs (or sentences), or
    prosediff's default for them."""
    chosen = s.sentence_move_similarity if sentences else s.move_similarity
    return move_defaults(sentences)[0] if chosen is None else chosen


def move_algorithm_of(s: Settings, sentences: bool = False) -> str:
    """The moved-line algorithm chosen for paragraphs (or sentences), or
    prosediff's default for them."""
    chosen = s.sentence_move_algorithm if sentences else s.move_algorithm
    return chosen if chosen in MOVE_ALGORITHMS else move_defaults(sentences)[1]


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
        self.results: queue.Queue = queue.Queue()
        root.title("prosediff: compare two versions")
        root.minsize(780, 0)
        pad = {"padx": 6, "pady": 4}
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
        git_side, files_side, folders_side = (ttk.Frame(source) for _ in MODES)
        self.sides = dict(zip(MODES, (git_side, files_side, folders_side), strict=True))
        for side in self.sides.values():
            side.columnconfigure(1, weight=1)

        # Git repository
        self.repo = tk.StringVar(value=self.s.repo)
        ttk.Label(git_side, text="Repository").grid(row=0, column=0, sticky="w", **pad)
        repo_entry = ttk.Entry(git_side, textvariable=self.repo)
        repo_entry.grid(row=0, column=1, sticky="ew", **pad)
        repo_entry.bind("<Return>", lambda e: self.load_repo())
        repo_entry.bind("<FocusOut>", lambda e: self.load_repo())
        browse(git_side, self.pick_repo, "Choose the repository").grid(row=0, column=2, **pad)
        self.base = tk.StringVar()
        self.target = tk.StringVar()
        ttk.Label(git_side, text="Base (older)").grid(row=1, column=0, sticky="w", **pad)
        self.base_box = ttk.Combobox(git_side, textvariable=self.base)
        self.base_box.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(git_side, text="Target (newer)").grid(row=2, column=0, sticky="w", **pad)
        self.target_box = ttk.Combobox(git_side, textvariable=self.target)
        self.target_box.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        self.target_box.bind("<<ComboboxSelected>>", lambda e: self.update_untracked())
        hint(
            self.base_box,
            "A commit (hash, date, author, subject) or any ref git knows: HEAD~15, a tag.",
        )
        hint(self.target_box, "The working tree, the index, a commit or any ref git knows.")
        ttk.Label(git_side, text="Only these paths").grid(row=3, column=0, sticky="w", **pad)
        self.paths = tk.StringVar(value="; ".join(self.s.paths))
        paths_entry = ttk.Entry(git_side, textvariable=self.paths)
        paths_entry.grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        hint(paths_entry, "Optional: files or folders of the repository, separated by ;")
        self.untracked = tk.BooleanVar(value=self.s.untracked)
        self.untracked_box = toggle(git_side, "Include untracked files", self.untracked)
        self.untracked_box.grid(row=4, column=1, sticky="w", **pad)

        # Files, and folders
        self.old = tk.StringVar(value=self.s.old)
        self.new = tk.StringVar(value=self.s.new)
        self.old_folder = tk.StringVar(value=self.s.old_folder)
        self.new_folder = tk.StringVar(value=self.s.new_folder)
        for side, pick, old, new, what in (
            (files_side, self.pick_file, self.old, self.new, "file"),
            (folders_side, self.pick_folder, self.old_folder, self.new_folder, "folder"),
        ):
            for row, (label, var) in enumerate((("Old", old), ("New", new))):
                ttk.Label(side, text=label).grid(row=row, column=0, sticky="w", **pad)
                ttk.Entry(side, textvariable=var).grid(row=row, column=1, sticky="ew", **pad)
                browse(side, lambda v=var, f=pick: f(v), f"Choose the {label.lower()} {what}").grid(
                    row=row, column=2, **pad
                )
            swap_button = ttk.Button(
                side,
                image=ttk.Icon("arrow-down-up", size=16),
                command=lambda a=old, b=new: swap(a, b),
                bootstyle="secondary-outline",
            )
            swap_button.grid(row=0, column=3, rowspan=2, sticky="ns", **pad)
            hint(swap_button, f"Swap the old and the new {what}")
        ttk.Label(
            files_side,
            text="Any two files: Word, OpenDocument, Markdown, text, whatever their names.",
            bootstyle="secondary",
        ).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(folders_side, text="Only").grid(row=2, column=0, sticky="w", **pad)
        self.include = tk.StringVar(value=self.s.include)
        include_entry = ttk.Entry(folders_side, textvariable=self.include)
        include_entry.grid(row=2, column=1, sticky="ew", **pad)
        hint(
            include_entry,
            "The files compared: patterns separated by |, matched against each file's name "
            "(its path within the folder for a pattern with a /); empty: every file.",
        )

        # Options, in two cards: what is compared, and how it is shown
        cards = ttk.Frame(page)
        cards.pack(fill="x", pady=(10, 0))
        cards.columnconfigure((0, 1), weight=1, uniform="card")
        compared = ttk.Labelframe(cards, text="What is compared", padding=(10, 8))
        compared.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        shown = ttk.Labelframe(cards, text="How it is shown", padding=(10, 8))
        shown.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.docx = tk.StringVar(value=self.s.docx_changes)
        field_row(
            compared,
            0,
            "Tracked changes",
            ttk.Combobox(
                compared, textvariable=self.docx, values=DOCX_CHANGES, state="readonly", width=12
            ),
            "Word and OpenDocument tracked changes: accept them, reject them, or show them "
            "all, as Word does.",
        )
        self.split = tk.StringVar(value=self.s.split if self.s.split in SPLITS else "paragraph")
        splits = ttk.Frame(compared)
        split_names = (("paragraph", "Paragraphs"), ("sentence", "Sentences"), ("both", "Both"))
        for value, text in split_names:
            ttk.Radiobutton(
                splits,
                text=text,
                value=value,
                variable=self.split,
                bootstyle="secondary-outline-toolbutton",
                padding=(8, 3),
            ).pack(side="left")
        field_row(
            compared,
            1,
            "Compare by",
            splits,
            "How prose is compared: paragraph by paragraph, sentence by sentence (a sentence "
            "moved between paragraphs is recognised), or both, in one HTML report whose "
            "toolbar switches between the two.",
        )
        self.move_similarity = tk.DoubleVar(value=move_similarity_of(self.s))
        self.move_algorithm = tk.StringVar(value=move_algorithm_of(self.s))
        self.sentence_move_similarity = tk.DoubleVar(value=move_similarity_of(self.s, True))
        self.sentence_move_algorithm = tk.StringVar(value=move_algorithm_of(self.s, True))
        for row, what, similarity, algorithm, sentences in (
            (2, "paragraphs", self.move_similarity, self.move_algorithm, False),
            (3, "sentences", self.sentence_move_similarity, self.sentence_move_algorithm, True),
        ):
            default = "{:.2f} {}".format(*move_defaults(sentences))
            field_row(
                compared,
                row,
                f"Moved {what}",
                move_fields(compared, similarity, algorithm),
                f"How alike an edited {what[:-1]} must be to where it reappears to count as "
                "moved (1: only unchanged), and how that is measured. token-sort: the words "
                f"in common, whatever their order. Default: {default}"
                + (" (lines of files other than prose too)." if not sentences else "."),
            )
        self.language = tk.StringVar(value=self.s.language)
        # any code can be typed; the list holds the common ones
        field_row(
            compared,
            4,
            "Language",
            ttk.Combobox(compared, textvariable=self.language, values=LANGUAGES, width=12),
            "Splits sentences and hyphenates lines. default: the language Word and "
            "OpenDocument files are marked with, else guessed; or a code such as it.",
        )
        self.encoding = tk.StringVar(value=self.s.encoding)
        field_row(
            compared,
            5,
            "Text encoding",
            ttk.Combobox(compared, textvariable=self.encoding, values=ENCODINGS, width=12),
            "Of text and Markdown files. auto: UTF-8, unless a file is not; then guessed.",
        )
        self.ignore_ws = tk.BooleanVar(value=self.s.ignore_whitespace)
        switch_row(
            compared,
            6,
            "Ignore whitespace",
            self.ignore_ws,
            "Lines that differ only in spacing are the same, as git diff -w.",
        )

        self.comments = tk.StringVar(
            value=self.s.comments if self.s.comments in COMMENT_MODES else "markers"
        )
        field_row(
            shown,
            0,
            "Comments",
            ttk.Combobox(
                shown, textvariable=self.comments, values=COMMENT_MODES, state="readonly", width=12
            ),
            "markers: only the comments added or removed, set apart (a marker and a panel in "
            "the HTML report, CriticMarkup in the diffs); text: compared as text; none: left "
            "out.",
        )
        self.comments.trace_add("write", lambda *_: self.update_empty_comments())
        self.empty_comments = tk.BooleanVar(value=self.s.empty_comments)
        self.empty_comments_box = switch_row(
            shown,
            1,
            "Comments without text",
            self.empty_comments,
            "Show the comments that have no text too (with markers only).",
        )
        self.context = tk.StringVar(value=self.s.context_lines)
        # "auto": 0 around the changes of Markdown and Word, 3 of other files
        field_row(
            shown,
            2,
            "Context lines",
            ttk.Spinbox(shown, values=("auto", *range(51)), textvariable=self.context, width=10),
            "Unchanged lines shown around each change. auto: none in Markdown files and Word "
            "documents, whose lines are paragraphs; 3 in the others.",
        )
        self.full = tk.BooleanVar(value=self.s.full)
        switch_row(shown, 3, "Whole files", self.full, "Show every line of each changed file.")
        self.align = tk.StringVar(value=self.s.align)
        field_row(
            shown,
            4,
            "Wrapped lines",
            ttk.Combobox(
                shown, textvariable=self.align, values=ALIGNMENTS, state="readonly", width=12
            ),
            "How long lines that wrap are aligned in the HTML report.",
        )

        # Output
        out = ttk.Labelframe(page, text="Output", padding=(10, 8))
        out.pack(fill="x", pady=(10, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Format").grid(row=0, column=0, sticky="w", **pad)
        formats = ttk.Frame(out)
        formats.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        self.output_format = tk.StringVar(
            value=self.s.output_format if self.s.output_format in FORMATS else "html"
        )
        for value, text, tip in (
            ("html", "HTML report", "Side by side, in the browser: words, moves, comments."),
            ("diff", "Unified diff", "A .diff, as git diff writes it; a patch for text files."),
            ("wdiff", "Word diff", "A .wdiff: the words changed in each line, [-old-]{+new+}."),
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
        ttk.Label(out, text="Save to").grid(row=1, column=0, sticky="w", **pad)
        self.output = tk.StringVar(value=self.s.output)
        output_entry = ttk.Entry(out, textvariable=self.output)
        output_entry.grid(row=1, column=1, sticky="ew", **pad)
        hint(
            output_entry,
            "Empty: comparing two folders, prosediff.html (or .diff, .wdiff) in the new one; "
            "otherwise a new file in the temporary folder.",
        )
        save = ttk.Button(
            out,
            image=ttk.Icon("save", size=16),
            command=self.pick_output,
            bootstyle="secondary-outline",
        )
        save.grid(row=1, column=2, **pad)
        hint(save, "Choose where to save it")
        self.open_page = tk.BooleanVar(value=self.s.open_page)
        toggle(out, "Open when done", self.open_page).grid(row=2, column=1, sticky="w", **pad)

        # Run
        bottom = ttk.Frame(page)
        bottom.pack(fill="x", pady=(12, 0))
        self.status = tk.StringVar(value=READY)
        ttk.Label(bottom, textvariable=self.status, bootstyle="secondary").pack(side="left")
        self.button = ttk.Button(
            bottom,
            text="Compare",
            image=ttk.Icon("play-fill", size=16, color="white"),
            compound="left",
            command=self.run,
            default="active",
            bootstyle="primary",
            padding=(16, 6),
        )
        self.button.pack(side="right")
        hint(self.button, "Compare, write the output and open it (Ctrl+Enter)")
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
        self.progress = ttk.Progressbar(
            bottom, mode="indeterminate", bootstyle="striped", length=140
        )
        root.bind("<Control-Return>", lambda e: self.run())

        if self.s.repo:
            self.load_repo(keep=(self.s.base, self.s.target))
        self.show_mode()
        self.update_untracked()
        self.update_empty_comments()

    def show_mode(self) -> None:
        """Show the fields of what is compared: a repository, files or folders."""
        if self.mode.get() != "git":
            self.status.set(READY)
        for mode, side in self.sides.items():
            if mode == self.mode.get():
                side.pack(fill="x")
            else:
                side.pack_forget()

    # Choosing ------------------------------------------------------------------

    def pick_repo(self) -> None:
        folder = filedialog.askdirectory(title="Git repository", initialdir=self.repo.get() or None)
        if folder:
            self.repo.set(folder)
            self.load_repo()

    def pick_file(self, var: tk.StringVar) -> None:
        f = filedialog.askopenfilename(title="File")
        if f:
            var.set(f)

    def pick_folder(self, var: tk.StringVar) -> None:
        f = filedialog.askdirectory(title="Folder")
        if f:
            var.set(f)

    def swap_files(self) -> None:
        """Exchange the old and the new file, or folder, of what is shown."""
        if self.mode.get() == "folders":
            swap(self.old_folder, self.new_folder)
        else:
            swap(self.old, self.new)

    def rename_output(self) -> None:
        """Give the Save to file the extension of the format chosen."""
        self.output.set(with_format(self.output.get().strip(), self.output_format.get()))

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
            f"{n:,} commit{'' if n == 1 else 's'} listed"
            + (", uncommitted changes present." if dirty else ".")
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
            include=self.include.get().strip(),
            comments=self.comments.get(),
            empty_comments=self.empty_comments.get(),
            docx_changes=self.docx.get(),
            align=self.align.get(),
            context_lines=context,
            full=self.full.get(),
            ignore_whitespace=self.ignore_ws.get(),
            move_similarity=moves[False][0],
            move_algorithm=moves[False][1],
            sentence_move_similarity=moves[True][0],
            sentence_move_algorithm=moves[True][1],
            split=self.split.get(),
            language=language,
            encoding=encoding,
            output=self.output.get().strip(),
            output_format=self.output_format.get(),
            open_page=self.open_page.get(),
        )

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
        for var, value in (
            (self.comments, d.comments),
            (self.empty_comments, d.empty_comments),
            (self.docx, d.docx_changes),
            (self.align, d.align),
            (self.context, d.context_lines),
            (self.full, d.full),
            (self.ignore_ws, d.ignore_whitespace),
            (self.move_similarity, move_similarity_of(d)),
            (self.move_algorithm, move_algorithm_of(d)),
            (self.sentence_move_similarity, move_similarity_of(d, True)),
            (self.sentence_move_algorithm, move_algorithm_of(d, True)),
            (self.split, d.split),
            (self.language, d.language),
            (self.encoding, d.encoding),
            (self.include, d.include),
            (self.untracked, d.untracked),
            (self.output_format, d.output_format),
            (self.open_page, d.open_page),
        ):
            var.set(value)
        self.rename_output()
        self.update_untracked()
        self.update_empty_comments()
        self.status.set("Options reset to their defaults (not saved).")

    # Running -------------------------------------------------------------------

    def run(self) -> None:
        s = self.collect()
        self.button.state(["disabled"])
        self.status.set("Comparing…")
        self.progress.pack(side="right", padx=12)
        self.progress.start(12)

        def work() -> None:
            try:
                self.results.put(("done", generate(s), s))
            except (
                git.InvalidGitRepositoryError,
                git.NoSuchPathError,
                git.BadName,
                git.GitCommandError,
                FilterError,
                SourceError,
                ValueError,
                OSError,
            ) as e:
                self.results.put(("error", e, s))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self.poll)

    def poll(self) -> None:
        """Pick up the result of the background comparison (tkinter must
        only be touched from its own thread)."""
        try:
            kind, value, s = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.button.state(["!disabled"])
        self.progress.stop()
        self.progress.pack_forget()
        if kind == "error":
            self.status.set("Not compared.")
            messagebox.showerror("prosediff", str(value) or type(value).__name__)
            return
        path, c = value
        n = len(c.files)
        summary = (
            f"{n:,} file{'' if n == 1 else 's'} changed, +{c.additions:,} −{c.deletions:,} lines"
        )
        self.status.set(f"{summary}: {path.name}")
        ttk.ToastNotification(
            "prosediff",
            f"{summary}\n{path.name}",
            duration=TOAST_MS,
            bootstyle="success",
            icon="",
        ).show_toast()
        if s.open_page:
            webbrowser.open(path.resolve().as_uri())


def hint(widget: tk.Misc, text: str) -> None:
    """What a widget does, shown when the pointer rests on it."""
    ttk.ToolTip(widget, text=text, wraplength=HINT_WIDTH, delay=HINT_DELAY_MS)


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
    ttk.Combobox(
        frame, textvariable=algorithm, values=tuple(MOVE_ALGORITHMS), state="readonly", width=11
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
    lines = [f"Received {len(args)} argument{'' if len(args) == 1 else 's'}:"]
    for i, a in enumerate(args, 1):
        missing = "" if Path(a).exists() else "  (not found)"
        lines.append(f"{i}. “{a}”{missing}")
    return "\n".join(lines)


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
    if dark and sys.platform == "win32":
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
