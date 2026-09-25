"""A window to choose what to compare; it writes the page and opens it.

Two tabs: a git repository (base and target picked among its latest commits,
the working tree and the index, or typed as any ref), or two files or
folders. The options are those of the command line that matter when reading
a diff. The comparison runs in a background thread, so the window stays
responsive; the choices are remembered for the next time.
"""

import json
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import git

from sidediff.diff import MOVE_SIMILARITY, Comparison, FilterError, compare, compare_paths
from sidediff.render import ALIGNMENTS, render
from sidediff.sources import DOCX_CHANGES, SourceError

MAX_COMMITS = 200
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
    fold_comments: bool = True
    empty_comments: bool = False
    docx_changes: str = "accept"
    align: str = "justify"
    context: int = 3
    full: bool = False
    ignore_whitespace: bool = False
    move_similarity: float = MOVE_SIMILARITY
    by_sentence: bool = False
    sentence_language: str = "en"
    output: str = ""
    open_page: bool = True


PREFILLED_FILES = (".md", ".docx")


def single_file(args: list[str]) -> Path | None:
    """The one Markdown or Word file given, whose partner the window asks for."""
    if len(args) == 1:
        path = Path(args[0])
        if path.is_file() and path.suffix.lower() in PREFILLED_FILES:
            return path.resolve()
    return None


def page_beside(old: Path, new: Path) -> str:
    """Where the page comparing two files goes: next to the new one, named
    after both, so pages of different pairs do not overwrite each other."""
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
    the repository, the sides starting from their defaults; one Markdown or
    Word file fills in the files tab, its partner to be chosen when the
    window opens; two Markdown or Word files fill in the files tab. Anything
    else is ignored, and the second value says why.
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
        return s, f"Not a folder, a Markdown or a Word file: {path}"
    if len(args) == 2:
        old, new = Path(args[0]), Path(args[1])
        if all(p.is_file() and p.suffix.lower() in PREFILLED_FILES for p in (old, new)):
            s.mode = "files"
            s.old, s.new = str(old.resolve()), str(new.resolve())
            s.output = page_beside(old, new)
            return s, ""
        return s, "Two arguments must be two Markdown or Word files."
    if args:
        return s, "Give one git repository, or two Markdown or Word files."
    return s, ""


def settings_file() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "sidediff" / "gui.json"


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


def default_output() -> Path:
    """A fresh page in the temporary folder, so no repository is cluttered."""
    folder = Path(tempfile.gettempdir()) / "sidediff"
    folder.mkdir(exist_ok=True)
    return folder / f"sidediff_{datetime.now():%Y%m%d_%H%M%S}.html"


def generate(s: Settings) -> tuple[Path, Comparison]:
    """Compare as the settings say and write the page; returns its path."""
    options = dict(
        paths=s.paths or None,
        context=None if s.full else s.context,
        ignore_whitespace=s.ignore_whitespace,
        fold_comments_md=s.fold_comments,
        empty_comments=s.empty_comments,
        docx_changes=s.docx_changes,
        move_similarity=s.move_similarity,
        by_sentence=s.by_sentence,
        sentence_language=s.sentence_language or "en",
    )
    if s.mode == "files":
        if not s.old or not s.new:
            raise ValueError("choose the old and the new file or folder")
        comparison = compare_paths(s.old, s.new, **options)
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
    out = Path(s.output) if s.output else default_output()
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(comparison, s.paths, align=s.align))
    return out, comparison


class App:
    """The window."""

    def __init__(self, root: tk.Tk | tk.Toplevel, settings: Settings | None = None) -> None:
        self.root = root
        self.s = settings or load_settings()
        self.choices: dict[str, str] = {}  # label -> ref
        self.results: queue.Queue = queue.Queue()
        root.title("sidediff: compare two versions")
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
        ttk.Label(git_tab, text="optional, separated by ;", foreground="grey").grid(
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
            foreground="grey",
        ).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(files_tab, text="⇅ Swap", command=self.swap_files).grid(
            row=2, column=2, columnspan=2, sticky="ew", **pad
        )

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
        self.context = tk.IntVar(value=self.s.context)
        ttk.Spinbox(opts, from_=0, to=50, textvariable=self.context, width=6).grid(
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
            foreground="grey",
        ).grid(row=3, column=2, columnspan=2, sticky="w", **pad)
        self.by_sentence = tk.BooleanVar(value=self.s.by_sentence)
        ttk.Checkbutton(opts, text="Compare sentence by sentence", variable=self.by_sentence).grid(
            row=4, column=0, columnspan=2, sticky="w", **pad
        )
        self.empty_comments = tk.BooleanVar(value=self.s.empty_comments)
        ttk.Checkbutton(opts, text="Show comments without text", variable=self.empty_comments).grid(
            row=5, column=0, columnspan=2, sticky="w", **pad
        )
        language = ttk.Frame(opts)
        language.grid(row=4, column=2, columnspan=2, sticky="w", **pad)
        ttk.Label(language, text="Language").pack(side="left")
        self.sentence_language = tk.StringVar(value=self.s.sentence_language)
        ttk.Entry(language, textvariable=self.sentence_language, width=6).pack(side="left", padx=6)
        ttk.Label(language, text="en, it, de, fr, ...", foreground="grey").pack(side="left")

        # Output
        out = ttk.LabelFrame(root, text="Page", padding=8)
        out.pack(fill="x", padx=10, pady=4)
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save to").grid(row=0, column=0, sticky="w", **pad)
        self.output = tk.StringVar(value=self.s.output)
        ttk.Entry(out, textvariable=self.output).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(out, text="Save as…", command=self.pick_output).grid(row=0, column=2, **pad)
        ttk.Label(out, text="empty: a new page in the temporary folder", foreground="grey").grid(
            row=1, column=1, sticky="w", padx=6
        )
        self.open_page = tk.BooleanVar(value=self.s.open_page)
        ttk.Checkbutton(out, text="Open in the browser when done", variable=self.open_page).grid(
            row=2, column=1, sticky="w", **pad
        )

        # Run
        bottom = ttk.Frame(root, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        self.status = tk.StringVar(value="Choose what to compare, then Compare.")
        ttk.Label(bottom, textvariable=self.status).pack(side="left")
        self.button = ttk.Button(bottom, text="Compare", command=self.run, default="active")
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
        try:
            context = max(0, int(self.context.get()))
        except (tk.TclError, ValueError):
            context = 3
        try:
            move_similarity = min(1.0, max(0.05, float(self.move_similarity.get())))
        except (tk.TclError, ValueError):
            move_similarity = MOVE_SIMILARITY
        return Settings(
            mode="files" if self.tabs.index("current") == 1 else "git",
            repo=self.repo.get().strip(),
            base=self.ref_of(self.base.get()),
            target=self.ref_of(self.target.get()),
            untracked=self.untracked.get(),
            paths=[p.strip() for p in self.paths.get().split(";") if p.strip()],
            old=self.old.get().strip(),
            new=self.new.get().strip(),
            fold_comments=self.fold.get(),
            empty_comments=self.empty_comments.get(),
            docx_changes=self.docx.get(),
            align=self.align.get(),
            context=context,
            full=self.full.get(),
            ignore_whitespace=self.ignore_ws.get(),
            move_similarity=move_similarity,
            by_sentence=self.by_sentence.get(),
            sentence_language=self.sentence_language.get().strip().lower() or "en",
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
            messagebox.showerror("sidediff", str(value) or type(value).__name__)
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
        filetypes=[("Word and Markdown", "*.docx *.md"), ("All files", "*.*")],
    )
    return Path(chosen) if chosen else None


def main(argv: list[str] | None = None) -> None:
    """sidediff-gui [REPOSITORY | FILE | OLD NEW]: the window, prefilled from
    the arguments when they are a git repository or Markdown or Word files;
    for one file, a dialog asks for the file to compare it with."""
    args = sys.argv[1:] if argv is None else argv
    settings, note = settings_from_args(args, load_settings())
    root = tk.Tk()
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
