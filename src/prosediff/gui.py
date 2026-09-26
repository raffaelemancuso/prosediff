"""A window to choose what to compare; it writes the page and opens it.

Two tabs: a git repository (base and target picked among its latest commits,
the working tree and the index, or typed as any ref), or two files or
folders. The options are those of the command line that matter when reading
a diff. The comparison runs in a background thread, so the window stays
responsive; the choices are remembered for the next time.

The widgets are ttkbootstrap's, in its Bootstrap theme: light or dark as the
system is set (Windows' app mode, macOS's appearance), the title bar too on
Windows.
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
    MOVE_SIMILARITY,
    Comparison,
    Context,
    FilterError,
    check_encoding,
    compare,
    compare_paths,
)
from prosediff.language import DEFAULT, DOCUMENT, GUESS, normalize_language
from prosediff.render import ALIGNMENTS, default_output, render
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

    mode: str = "git"  # "git" or "files"
    repo: str = ""
    base: str = ""  # a ref
    target: str = ""  # a ref, "worktree" or "index"
    untracked: bool = False
    paths: list[str] = field(default_factory=list)
    old: str = ""
    new: str = ""
    # the files of two folders compared: glob patterns separated by "|"
    include: str = FOLDER_FILES
    fold_comments: bool = True
    empty_comments: bool = False
    docx_changes: str = "accept"
    align: str = "justify"
    # "auto": 0 for Markdown files and Word documents, 3 for the others; a
    # number applies to every file
    context_lines: str = "auto"
    full: bool = False
    ignore_whitespace: bool = False
    move_similarity: float = MOVE_SIMILARITY
    by_sentence: bool = False
    # a language code; "document": marked in Word and OpenDocument files;
    # "guess": guessed from each file's text; "default": document, else guess
    language: str = DEFAULT
    # a codec's name, or "auto": UTF-8 unless a file shows it is not
    encoding: str = AUTO_ENCODING
    output: str = ""
    open_page: bool = True


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
    """Where the page comparing two files goes: next to the new one, named
    after both, so pages of different pairs do not overwrite each other; that
    comparing two folders, into the new one (default_page)."""
    if folder_page := default_page(old, new):
        return str(folder_page.resolve())
    return str(new.resolve().parent / f"{old.stem}_vs_{new.stem}.html")


def with_second_file(s: Settings, first: Path, second: Path) -> Settings:
    """The settings comparing two files, the older (by modification time)
    on the left: the draft sent before the one returned. The page goes next
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
    chosen when the window opens; two such files, or two folders, fill in
    the files tab. Anything else is ignored, and the second value says why.
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
        if both_files or (old.is_dir() and new.is_dir()):
            s.mode = "files"
            s.old, s.new = str(old.resolve()), str(new.resolve())
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
    """The choices of the last run, or the defaults."""
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
        known = Settings.__dataclass_fields__
        return Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or settings_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    except OSError:
        pass  # remembering is a convenience


def context_of(s: Settings) -> Context:
    """The context for compare(), from the Context lines box."""
    if s.full:
        return None
    try:
        return max(0, int(s.context_lines))
    except ValueError:  # "auto", or anything that is not a number
        return "auto"


def generate(s: Settings) -> tuple[Path, Comparison]:
    """Compare as the settings say and write the page; returns its path."""
    options = dict(
        paths=s.paths or None,
        context=context_of(s),
        ignore_whitespace=s.ignore_whitespace,
        fold_comments_md=s.fold_comments,
        empty_comments=s.empty_comments,
        docx_changes=s.docx_changes,
        move_similarity=s.move_similarity,
        by_sentence=s.by_sentence,
        language=s.language or DEFAULT,
        encoding=s.encoding or AUTO_ENCODING,
    )
    if s.mode == "files":
        if not s.old or not s.new:
            raise ValueError("choose the old and the new file or folder")
        comparison = compare_paths(s.old, s.new, include=s.include, **options)
    else:
        if not s.repo or not s.base:
            raise ValueError("choose a repository and a base")
        target = None if s.target in ("worktree", "index", "") else s.target
        comparison = compare(
            s.repo,
            s.base,
            target,
            cached=s.target == "index",
            untracked=s.untracked and s.target in ("worktree", ""),
            **options,
        )
    out = Path(s.output) if s.output else None
    if out is None and s.mode == "files":
        out = default_page(Path(s.old), Path(s.new))
    out = out or default_output()
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(comparison, s.paths, align=s.align))
    return out, comparison


class App:
    """The window."""

    def __init__(self, root: tk.Tk | tk.Toplevel, settings: Settings | None = None) -> None:
        self.root = root
        use_theme(root)
        self.s = settings or load_settings()
        self.choices: dict[str, str] = {}  # label -> ref
        self.results: queue.Queue = queue.Queue()
        root.title("prosediff: compare two versions")
        root.minsize(720, 0)
        pad = {"padx": 6, "pady": 3}

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="x", padx=10, pady=(10, 4))
        git_tab, files_tab = ttk.Frame(self.tabs, padding=8), ttk.Frame(self.tabs, padding=8)
        self.tabs.add(git_tab, text="Git repository")
        self.tabs.add(files_tab, text="Files or folders")
        git_tab.columnconfigure(1, weight=1)
        files_tab.columnconfigure(1, weight=1)

        # Git repository
        self.repo = tk.StringVar(value=self.s.repo)
        ttk.Label(git_tab, text="Repository").grid(row=0, column=0, sticky="w", **pad)
        repo_entry = ttk.Entry(git_tab, textvariable=self.repo)
        repo_entry.grid(row=0, column=1, sticky="ew", **pad)
        repo_entry.bind("<Return>", lambda e: self.load_repo())
        repo_entry.bind("<FocusOut>", lambda e: self.load_repo())
        ttk.Button(git_tab, text="Browse…", command=self.pick_repo).grid(row=0, column=2, **pad)
        self.base = tk.StringVar()
        self.target = tk.StringVar()
        ttk.Label(git_tab, text="Base (older)").grid(row=1, column=0, sticky="w", **pad)
        self.base_box = ttk.Combobox(git_tab, textvariable=self.base)
        self.base_box.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(git_tab, text="Target (newer)").grid(row=2, column=0, sticky="w", **pad)
        self.target_box = ttk.Combobox(git_tab, textvariable=self.target)
        self.target_box.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        self.target_box.bind("<<ComboboxSelected>>", lambda e: self.update_untracked())
        self.untracked = tk.BooleanVar(value=self.s.untracked)
        self.untracked_box = ttk.Checkbutton(
            git_tab, text="Include untracked files", variable=self.untracked
        )
        self.untracked_box.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(git_tab, text="Only these paths").grid(row=4, column=0, sticky="w", **pad)
        self.paths = tk.StringVar(value="; ".join(self.s.paths))
        ttk.Entry(git_tab, textvariable=self.paths).grid(
            row=4, column=1, columnspan=2, sticky="ew", **pad
        )
        ttk.Label(git_tab, text="optional, separated by ;", bootstyle="secondary").grid(
            row=5, column=1, sticky="w", padx=6
        )

        # Files or folders
        self.old = tk.StringVar(value=self.s.old)
        self.new = tk.StringVar(value=self.s.new)
        for row, (label, var) in enumerate((("Old", self.old), ("New", self.new))):
            ttk.Label(files_tab, text=label).grid(row=row, column=0, sticky="w", **pad)
            ttk.Entry(files_tab, textvariable=var).grid(row=row, column=1, sticky="ew", **pad)
            ttk.Button(files_tab, text="File…", command=lambda v=var: self.pick_file(v)).grid(
                row=row, column=2, **pad
            )
            ttk.Button(files_tab, text="Folder…", command=lambda v=var: self.pick_folder(v)).grid(
                row=row, column=3, **pad
            )
        ttk.Label(
            files_tab,
            text="Two files (whatever their names, Word documents included) or two folders.",
            bootstyle="secondary",
        ).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(files_tab, text="⇅ Swap", command=self.swap_files).grid(
            row=2, column=2, columnspan=2, sticky="ew", **pad
        )
        ttk.Label(files_tab, text="Folders: only").grid(row=3, column=0, sticky="w", **pad)
        self.include = tk.StringVar(value=self.s.include)
        ttk.Entry(files_tab, textvariable=self.include).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Label(
            files_tab,
            text="patterns separated by |; empty: every file",
            bootstyle="secondary",
        ).grid(row=4, column=1, sticky="w", padx=6)

        # Options
        opts = ttk.LabelFrame(root, text="Options", padding=8)
        opts.pack(fill="x", padx=10, pady=4)
        self.fold = tk.BooleanVar(value=self.s.fold_comments)
        ttk.Checkbutton(
            opts, text="Comments as markers, with a comments panel", variable=self.fold
        ).grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        self.ignore_ws = tk.BooleanVar(value=self.s.ignore_whitespace)
        ttk.Checkbutton(opts, text="Ignore whitespace", variable=self.ignore_ws).grid(
            row=0, column=2, columnspan=2, sticky="w", **pad
        )
        ttk.Label(opts, text="Word tracked changes").grid(row=1, column=0, sticky="w", **pad)
        self.docx = tk.StringVar(value=self.s.docx_changes)
        ttk.Combobox(
            opts, textvariable=self.docx, values=DOCX_CHANGES, state="readonly", width=10
        ).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(opts, text="Wrapped lines").grid(row=1, column=2, sticky="w", **pad)
        self.align = tk.StringVar(value=self.s.align)
        ttk.Combobox(
            opts, textvariable=self.align, values=ALIGNMENTS, state="readonly", width=10
        ).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(opts, text="Context lines").grid(row=2, column=0, sticky="w", **pad)
        # "auto": 0 around the changes of Markdown and Word, 3 of other files
        self.context = tk.StringVar(value=self.s.context_lines)
        ttk.Spinbox(opts, values=("auto", *range(51)), textvariable=self.context, width=6).grid(
            row=2, column=1, sticky="w", **pad
        )
        self.full = tk.BooleanVar(value=self.s.full)
        ttk.Checkbutton(opts, text="Show whole files", variable=self.full).grid(
            row=2, column=2, columnspan=2, sticky="w", **pad
        )
        ttk.Label(opts, text="Moved-line similarity").grid(row=3, column=0, sticky="w", **pad)
        self.move_similarity = tk.DoubleVar(value=self.s.move_similarity)
        ttk.Spinbox(
            opts,
            from_=0.05,
            to=1.0,
            increment=0.05,
            format="%.2f",
            textvariable=self.move_similarity,
            width=6,
        ).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(
            opts,
            text="how alike an edited line must be to count as moved (1: only unchanged)",
            bootstyle="secondary",
        ).grid(row=3, column=2, columnspan=2, sticky="w", **pad)
        self.by_sentence = tk.BooleanVar(value=self.s.by_sentence)
        ttk.Checkbutton(opts, text="Compare sentence by sentence", variable=self.by_sentence).grid(
            row=4, column=0, columnspan=2, sticky="w", **pad
        )
        self.empty_comments = tk.BooleanVar(value=self.s.empty_comments)
        ttk.Checkbutton(opts, text="Show comments without text", variable=self.empty_comments).grid(
            row=5, column=0, columnspan=2, sticky="w", **pad
        )
        ttk.Label(opts, text="Document language").grid(row=6, column=0, sticky="w", **pad)
        self.language = tk.StringVar(value=self.s.language)
        # Any code can be typed; the list holds the common ones.
        ttk.Combobox(opts, textvariable=self.language, values=LANGUAGES, width=10).grid(
            row=6, column=1, sticky="w", **pad
        )
        ttk.Label(
            opts,
            text="splits sentences and hyphenates lines; default: marked in Word and "
            "OpenDocument files, else guessed",
            bootstyle="secondary",
        ).grid(row=6, column=2, columnspan=2, sticky="w", **pad)
        ttk.Label(opts, text="Text encoding").grid(row=7, column=0, sticky="w", **pad)
        self.encoding = tk.StringVar(value=self.s.encoding)
        ttk.Combobox(opts, textvariable=self.encoding, values=ENCODINGS, width=10).grid(
            row=7, column=1, sticky="w", **pad
        )
        ttk.Label(
            opts,
            text="of text and Markdown files; auto: UTF-8 unless a file is not, then guessed",
            bootstyle="secondary",
        ).grid(row=7, column=2, columnspan=2, sticky="w", **pad)

        # Output
        out = ttk.LabelFrame(root, text="Page", padding=8)
        out.pack(fill="x", padx=10, pady=4)
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save to").grid(row=0, column=0, sticky="w", **pad)
        self.output = tk.StringVar(value=self.s.output)
        ttk.Entry(out, textvariable=self.output).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(out, text="Save as…", command=self.pick_output).grid(row=0, column=2, **pad)
        ttk.Label(
            out,
            text="empty: two folders, prosediff.html in the new one; "
            "otherwise a new page in the temporary folder",
            bootstyle="secondary",
        ).grid(row=1, column=1, sticky="w", padx=6)
        self.open_page = tk.BooleanVar(value=self.s.open_page)
        ttk.Checkbutton(out, text="Open in the browser when done", variable=self.open_page).grid(
            row=2, column=1, sticky="w", **pad
        )

        # Run
        bottom = ttk.Frame(root, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        self.status = tk.StringVar(value="Choose what to compare, then Compare.")
        ttk.Label(bottom, textvariable=self.status).pack(side="left")
        self.button = ttk.Button(
            bottom, text="Compare", command=self.run, default="active", bootstyle="primary"
        )
        self.button.pack(side="right")
        root.bind("<Control-Return>", lambda e: self.run())

        self.tabs.select(1 if self.s.mode == "files" else 0)
        if self.s.repo:
            self.load_repo(keep=(self.s.base, self.s.target))
        self.update_untracked()

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
        """Exchange the old and the new file or folder."""
        old, new = self.old.get(), self.new.get()
        self.old.set(new)
        self.new.set(old)

    def pick_output(self) -> None:
        f = filedialog.asksaveasfilename(
            title="Save the page as",
            defaultextension=".html",
            filetypes=[("Web page", "*.html")],
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
        try:
            move_similarity = min(1.0, max(0.05, float(self.move_similarity.get())))
        except (tk.TclError, ValueError):
            move_similarity = MOVE_SIMILARITY
        try:
            language = normalize_language(self.language.get())
        except ValueError:
            language = DEFAULT
        try:
            encoding = check_encoding(self.encoding.get())
        except ValueError:
            encoding = AUTO_ENCODING
        return Settings(
            mode="files" if self.tabs.index("current") == 1 else "git",
            repo=self.repo.get().strip(),
            base=self.ref_of(self.base.get()),
            target=self.ref_of(self.target.get()),
            untracked=self.untracked.get(),
            paths=[p.strip() for p in self.paths.get().split(";") if p.strip()],
            old=self.old.get().strip(),
            new=self.new.get().strip(),
            include=self.include.get().strip(),
            fold_comments=self.fold.get(),
            empty_comments=self.empty_comments.get(),
            docx_changes=self.docx.get(),
            align=self.align.get(),
            context_lines=context,
            full=self.full.get(),
            ignore_whitespace=self.ignore_ws.get(),
            move_similarity=move_similarity,
            by_sentence=self.by_sentence.get(),
            language=language,
            encoding=encoding,
            output=self.output.get().strip(),
            open_page=self.open_page.get(),
        )

    # Running -------------------------------------------------------------------

    def run(self) -> None:
        s = self.collect()
        save_settings(s)
        self.button.state(["disabled"])
        self.status.set("Comparing…")

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
        if kind == "error":
            self.status.set("Not compared.")
            messagebox.showerror("prosediff", str(value) or type(value).__name__)
            return
        path, c = value
        self.status.set(
            f"{len(c.files):,} files changed, +{c.additions:,} −{c.deletions:,} lines: {path.name}"
        )
        if s.open_page:
            webbrowser.open(path.resolve().as_uri())


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
