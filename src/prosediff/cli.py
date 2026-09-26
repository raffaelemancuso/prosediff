"""Command line: prosediff REPO BASE [TARGET], or prosediff --files OLD NEW;
prosediff --setup-git and --to-markdown for git's own commands."""

import argparse
import sys
import webbrowser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import git

from prosediff.diff import (
    AUTO_ENCODING,
    CONTEXT,
    MAX_HIDDEN,
    MOVE_SIMILARITY,
    PROSE_CONTEXT,
    FilterError,
    check_encoding,
    compare,
    compare_paths,
)
from prosediff.gitsetup import SetupError, document_name, setup_git
from prosediff.language import DEFAULT, normalize_language
from prosediff.render import ALIGNMENTS, default_output, render
from prosediff.sources import DOCX_CHANGES, FOLDER_FILES, SourceError, document_to_markdown

PROG = "prosediff"


def package_version() -> str:
    try:
        return version(PROG)
    except PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Write an HTML page showing the differences between two "
        "commits of a git repository (or a commit and the working tree or the "
        "index), or between two files or two folders, side by side.",
        epilog="Examples: prosediff . HEAD~1 HEAD; prosediff . HEAD --untracked; "
        "prosediff --files draft_v1.docx draft_v2.docx --open; prosediff --setup-git",
    )
    ap.add_argument(
        "repo",
        nargs="?",
        metavar="REPO|OLD",
        help="the repository (or any folder inside it); with --files, the old file or folder",
    )
    ap.add_argument(
        "base",
        nargs="?",
        metavar="BASE|NEW",
        help="the older commit: hash, branch, tag, HEAD~2, ...; with "
        "--files, the new file or folder",
    )
    ap.add_argument(
        "target",
        nargs="?",
        metavar="TARGET",
        help="the newer commit; without it, BASE is compared with the "
        "working tree (tracked files only), as git diff BASE does",
    )
    ap.add_argument(
        "--files", action="store_true", help="compare two files or two folders, outside git"
    )
    ap.add_argument(
        "--include",
        metavar="PATTERNS",
        help="with --files and two folders, compare only the files matching these glob "
        'patterns, separated by | (quote them), e.g. "*.docx|*.md", matched against '
        "each file's name (its path within the folder for a pattern with a /), ignoring "
        f'case; "" for every file (default: "{FOLDER_FILES}")',
    )
    ap.add_argument(
        "-p",
        "--path",
        action="append",
        dest="paths",
        default=[],
        metavar="PATH",
        help="restrict the diff to this path (repeatable)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: diff.html; with --open, a new page in the temporary folder)",
    )
    ap.add_argument("--open", action="store_true", help="open the page in the browser once written")
    lines = ap.add_mutually_exclusive_group()
    lines.add_argument(
        "-U",
        "--context",
        type=int,
        default=None,
        metavar="N",
        help="unchanged lines shown around each change, in every file (default: "
        f"{PROSE_CONTEXT} in Markdown files and Word documents, whose lines are "
        f"paragraphs, {CONTEXT} in the others); the page can reveal the rest",
    )
    lines.add_argument("--full", action="store_true", help="show every line of the changed files")
    ap.add_argument(
        "--max-hidden",
        type=int,
        default=MAX_HIDDEN,
        metavar="N",
        help="unchanged lines embedded per gap, for the page to reveal; "
        f"longer gaps are left out (default: {MAX_HIDDEN:,})",
    )
    ap.add_argument(
        "--align",
        choices=ALIGNMENTS,
        default="left",
        help="alignment of wrapped lines (default: left)",
    )
    ap.add_argument(
        "--md-filter",
        metavar="COMMAND",
        help="shell command both versions of every Markdown file are "
        "piped through before comparing",
    )
    ap.add_argument(
        "--cached",
        action="store_true",
        help="compare BASE with the index (the staged changes) instead of the working tree",
    )
    ap.add_argument(
        "--untracked",
        action="store_true",
        help="include untracked files not excluded by .gitignore (working tree only)",
    )
    ap.add_argument(
        "-w",
        "--ignore-whitespace",
        action="store_true",
        help="ignore whitespace when comparing lines",
    )
    ap.add_argument(
        "--fold-comments",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="in Markdown files and Word documents, show each comment "
        "as a marker whose tooltip is the comment, and list them in a panel "
        "(default: on; --no-fold-comments compares the comment markup as text)",
    )
    ap.add_argument(
        "--empty-comments",
        action="store_true",
        help="show the comments that have no text too (left out by default)",
    )
    ap.add_argument(
        "--docx-changes",
        choices=DOCX_CHANGES,
        default="accept",
        help="the tracked changes of Word documents: accept them, reject them, "
        "or show them as markup (default: accept)",
    )
    ap.add_argument(
        "--move-similarity",
        type=float,
        default=MOVE_SIMILARITY,
        metavar="X",
        help="how alike, above 0 and at most 1, an edited line must be to where it "
        "reappears to count as moved: the share of its words and punctuation in "
        f"common, in order (default: {MOVE_SIMILARITY}; 1: only lines moved unchanged)",
    )
    ap.add_argument(
        "--by-sentence",
        action="store_true",
        help="compare the prose of Markdown files and Word documents sentence by "
        "sentence instead of paragraph by paragraph: moved sentences are recognised, "
        "lines are labelled 12.1, 12.2, ...",
    )
    ap.add_argument(
        "--language",
        default=DEFAULT,
        metavar="CODE",
        help="the language of the documents' prose: its rules split sentences with "
        "--by-sentence and hyphenate wrapped lines. A code (e.g. en, it, de, fr); "
        "document: the language Word and OpenDocument files mark their text with "
        "(an error for Markdown and text files); guess: guessed from each file's text. "
        "Default: document for Word and OpenDocument files (guess when they mark "
        "none), guess for the others",
    )
    ap.add_argument(
        "--encoding",
        default=AUTO_ENCODING,
        metavar="NAME",
        help="the encoding of text and Markdown files, e.g. utf-8, cp1252, latin-1 "
        "(default: auto, UTF-8 unless a file cannot be read in it or reads with "
        "control characters, then guessed with charset-normalizer)",
    )
    git_group = ap.add_argument_group("git's own commands")
    git_group.add_argument(
        "--setup-git",
        action="store_true",
        help="make git diff, git log -p and git show show Word and OpenDocument files as "
        "text, and add a difftool: git difftool -t prosediff (-d: one page for all files); "
        "for the repository REPO (default: the current folder) or, with --global, for all",
    )
    git_group.add_argument(
        "--global",
        dest="global_",
        action="store_true",
        help="with --setup-git, set git up for every repository of the user",
    )
    git_group.add_argument(
        "--to-markdown",
        type=Path,
        metavar="FILE",
        help="print a Word document or OpenDocument text as the Markdown prosediff "
        "compares, tracked changes as --docx-changes says (git's textconv command)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    args = ap.parse_args(argv)

    if args.global_ and not args.setup_git:
        ap.error("--global goes with --setup-git")
    if args.setup_git:
        if args.base or (args.global_ and args.repo):
            ap.error("--setup-git takes one REPO, or --global and none")
        return _setup_git(None if args.global_ else Path(args.repo or "."))
    if args.to_markdown:
        if args.repo:
            ap.error("--to-markdown takes one FILE and no other argument")
        return _to_markdown(args.to_markdown, args.docx_changes)
    if not args.base:
        ap.error("give REPO and BASE, or --files OLD NEW")

    if args.context is not None and args.context < 0:
        ap.error("--context must be 0 or more")
    if args.max_hidden < 0:
        ap.error("--max-hidden must be 0 or more")
    if not 0 < args.move_similarity <= 1:
        ap.error("--move-similarity must be above 0 and at most 1")
    try:
        args.language = normalize_language(args.language)
    except ValueError as e:
        ap.error(f"--language: {e}")
    try:
        args.encoding = check_encoding(args.encoding)
    except ValueError as e:
        ap.error(f"--encoding: {e}")
    if args.files:
        if args.target:
            ap.error("--files compares OLD with NEW: give no TARGET")
        if args.cached or args.untracked:
            ap.error("--cached and --untracked need a git repository, not --files")
    elif args.include is not None:
        ap.error("--include picks the files of two folders: it goes with --files")
    if args.cached and args.target:
        ap.error("--cached compares BASE with the index: give no TARGET")
    if args.untracked and (args.target or args.cached):
        ap.error("--untracked needs the working tree: give no TARGET and no --cached")

    options = dict(
        paths=args.paths,
        context=None if args.full else ("auto" if args.context is None else args.context),
        md_filter=args.md_filter,
        ignore_whitespace=args.ignore_whitespace,
        fold_comments_md=args.fold_comments,
        empty_comments=args.empty_comments,
        max_hidden=args.max_hidden,
        docx_changes=args.docx_changes,
        move_similarity=args.move_similarity,
        by_sentence=args.by_sentence,
        language=args.language,
        encoding=args.encoding,
    )
    try:
        if args.files:
            include = FOLDER_FILES if args.include is None else args.include
            comparison = compare_paths(args.repo, args.base, include=include, **options)
        else:
            comparison = compare(
                Path(args.repo),
                args.base,
                args.target,
                cached=args.cached,
                untracked=args.untracked,
                **options,
            )
    except git.InvalidGitRepositoryError:
        print(
            f"{PROG}: not a git repository: {args.repo} (use --files to compare files or folders)",
            file=sys.stderr,
        )
        return 1
    except git.NoSuchPathError:
        print(f"{PROG}: no such folder: {args.repo}", file=sys.stderr)
        return 1
    except (git.BadName, ValueError) as e:
        print(f"{PROG}: not a commit: {e}", file=sys.stderr)
        return 1
    except (FilterError, SourceError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1

    # newline="\n" keeps the page LF on Windows too.
    output = args.output or (default_output() if args.open else Path("diff.html"))
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(comparison, args.paths, align=args.align))

    c = comparison
    files = "file" if len(c.files) == 1 else "files"
    print(
        f"{PROG}: {c.base.short}..{c.target.short}: {len(c.files):,} {files}, "
        f"+{c.additions:,} -{c.deletions:,} -> {output}"
    )
    if args.open:
        webbrowser.open(output.resolve().as_uri())
    return 0


def _setup_git(repo: Path | None) -> int:
    try:
        done = setup_git(repo)
    except SetupError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    print("\n".join(done))
    print(
        "git diff now shows Word and OpenDocument files as text; "
        "git difftool -d -t prosediff opens a prosediff page."
    )
    return 0


def _to_markdown(path: Path, changes: str) -> int:
    """The Markdown of a document on stdout, as bytes: UTF-8 whatever the
    console's encoding, for git to read."""
    try:
        data = path.read_bytes()
        text = document_to_markdown(data, document_name(data, path.name), changes)
    except (OSError, SourceError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    sys.stdout.flush()
    sys.stdout.buffer.write(text)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
