"""Command line: sidediff REPO BASE [TARGET], or sidediff --files OLD NEW."""

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import git

from sidediff.diff import MAX_HIDDEN, MOVE_SIMILARITY, FilterError, compare, compare_paths
from sidediff.render import ALIGNMENTS, render
from sidediff.sources import DOCX_CHANGES, SourceError

PROG = "sidediff"


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
        epilog="Examples: sidediff . HEAD~1 HEAD; sidediff . HEAD --untracked; "
        "sidediff --files draft_v1.docx draft_v2.docx",
    )
    ap.add_argument(
        "repo",
        metavar="REPO|OLD",
        help="the repository (or any folder inside it); with --files, the old file or folder",
    )
    ap.add_argument(
        "base",
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
        default=Path("diff.html"),
        help="output file (default: diff.html)",
    )
    lines = ap.add_mutually_exclusive_group()
    lines.add_argument(
        "-U",
        "--context",
        type=int,
        default=3,
        metavar="N",
        help="unchanged lines shown around each change (default: 3)",
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
        "--staged",
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
        "--sentence-language",
        default="en",
        metavar="CODE",
        help="the language whose rules split sentences with --by-sentence "
        "(default: en; e.g. it, de, fr; others fall back to a simple rule)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    args = ap.parse_args(argv)

    if args.context < 0:
        ap.error("--context must be 0 or more")
    if args.max_hidden < 0:
        ap.error("--max-hidden must be 0 or more")
    if not 0 < args.move_similarity <= 1:
        ap.error("--move-similarity must be above 0 and at most 1")
    if args.files:
        if args.target:
            ap.error("--files compares OLD with NEW: give no TARGET")
        if args.cached or args.untracked:
            ap.error("--cached and --untracked need a git repository, not --files")
    if args.cached and args.target:
        ap.error("--cached compares BASE with the index: give no TARGET")
    if args.untracked and (args.target or args.cached):
        ap.error("--untracked needs the working tree: give no TARGET and no --cached")

    options = dict(
        paths=args.paths,
        context=None if args.full else args.context,
        md_filter=args.md_filter,
        ignore_whitespace=args.ignore_whitespace,
        fold_comments_md=args.fold_comments,
        empty_comments=args.empty_comments,
        max_hidden=args.max_hidden,
        docx_changes=args.docx_changes,
        move_similarity=args.move_similarity,
        by_sentence=args.by_sentence,
        sentence_language=args.sentence_language,
    )
    try:
        if args.files:
            comparison = compare_paths(args.repo, args.base, **options)
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
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(comparison, args.paths, align=args.align))

    c = comparison
    files = "file" if len(c.files) == 1 else "files"
    print(
        f"{PROG}: {c.base.short}..{c.target.short}: {len(c.files):,} {files}, "
        f"+{c.additions:,} -{c.deletions:,} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
