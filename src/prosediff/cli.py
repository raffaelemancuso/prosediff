"""Command line: prosediff --git REPO BASE [TARGET], prosediff --files OLD NEW,
or prosediff --folders OLD NEW; prosediff --review FILE, for an AI to review
one file alone; prosediff --setup-git and --to-markdown for git's own
commands."""

import argparse
import sys
from dataclasses import fields
from pathlib import Path

import git

from prosediff.assess import (
    ASSESS_TIMEOUT,
    CONTEXTS,
    AssessError,
    AssessRequest,
    login_codex,
    models_of,
    parse_backend,
)
from prosediff.diff import (
    AUTO_ENCODING,
    COMMENT_MODES,
    CONTEXT,
    MAX_HIDDEN,
    MOVE_ALGORITHM,
    MOVE_ALGORITHMS,
    MOVE_SIMILARITY,
    PROSE_CONTEXT,
    SENTENCE_MOVE_ALGORITHM,
    SENTENCE_MOVE_SIMILARITY,
    FilterError,
    MovedPassageSettings,
    MoveSettings,
    Options,
    SettingError,
    check_encoding,
    check_move_similarity,
    compare,
    compare_paths,
    compare_split,
    review_file,
    setting_type,
)
from prosediff.gitsetup import SetupError, document_name, setup_git
from prosediff.language import DEFAULT, normalize_language
from prosediff.render import (
    ALIGNMENTS,
    FORMATS,
    SPLITS,
    assess_comparison,
    check_split,
    counted,
    default_output,
    default_split,
    format_of,
    open_output,
    package_version,
    write_output,
)
from prosediff.sources import (
    DOCX_CHANGES,
    FOLDER_FILES,
    SourceError,
    default_page,
    document_to_markdown,
    review_page,
)
from prosediff.tracked import TRACKED_FORMATS, check_paths

PROG = "prosediff"


def passage_option(name: str) -> str:
    """The command-line option of a field of MovedPassageSettings."""
    return "--passage-" + name.replace("_", "-")


def build_parser() -> argparse.ArgumentParser:
    """The command line's options."""
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Write an HTML report showing the differences between two "
        "commits of a git repository (or a commit and the working tree or the "
        "index), or between two files or two folders, side by side; or a "
        "unified diff of them (--format diff).",
        epilog="Examples: prosediff --git . HEAD~1 HEAD; prosediff --git . HEAD --untracked; "
        "prosediff --files draft_v1.docx draft_v2.docx --open; "
        "prosediff --folders submitted revised; prosediff --review paper.docx --assess claude; "
        "prosediff --setup-git",
    )
    ap.add_argument(
        "repo",
        nargs="?",
        metavar="REPO|OLD",
        help="with --git, the repository (or any folder inside it); with --files or "
        "--folders, the old file or folder; with --review, the file",
    )
    ap.add_argument(
        "base",
        nargs="?",
        metavar="BASE|NEW",
        help="with --git, the older commit: hash, branch, tag, HEAD~2, ...; with "
        "--files or --folders, the new file or folder",
    )
    ap.add_argument(
        "target",
        nargs="?",
        metavar="TARGET",
        help="with --git, the newer commit; without it, BASE is compared with the "
        "working tree (tracked files only), as git diff BASE does",
    )
    what = ap.add_argument_group("what to compare: one of")
    modes = what.add_mutually_exclusive_group()
    modes.add_argument(
        "--git",
        action="store_true",
        help="commits of the git repository REPO: BASE with TARGET, or with the "
        "working tree (the index with --cached)",
    )
    modes.add_argument(
        "--files",
        action="store_true",
        help="two files, OLD and NEW, whatever their names, outside git",
    )
    modes.add_argument(
        "--folders",
        action="store_true",
        help="two folders, OLD and NEW, file by file, outside git",
    )
    modes.add_argument(
        "--review",
        action="store_true",
        help="no comparison: one FILE alone, reviewed whole by the AI --assess names, in "
        "an HTML report of its assessment, the problems it marked in the text and, for a "
        "Word or OpenDocument file, the file with its comments and fixes to download",
    )
    ap.add_argument(
        "--include",
        metavar="PATTERNS",
        help="with --folders, compare only the files matching these glob "
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
        help="with --git or --folders, restrict the diff to this path (repeatable)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: with --open, a new HTML report in the temporary folder; "
        "otherwise, for two folders, prosediff.html in the new one; for two files, "
        "OLD_vs_NEW.html next to the new one; else diff.html; "
        ".diff, .wdiff, .docx or .odt for --format diff, wdiff, docx or odt)",
    )
    ap.add_argument(
        "--format",
        choices=tuple(FORMATS),
        default=None,
        help="html: the HTML report; diff: a unified diff, its lines paired as the HTML "
        "report pairs them (prose one line per paragraph, formatting in Markdown, comments "
        "added or "
        "removed in CriticMarkup); wdiff: the same as git diff --word-diff writes it, "
        "[-removed-]{+added+} within each line. With -U lines of context (default: 3) "
        "or --full. docx: two Word documents compared into a copy of the new one, "
        "everything in it kept, each change since the old one a tracked change to "
        "accept or reject; odt: the same for two OpenDocument texts; paragraph by "
        "paragraph (default: diff when OUTPUT ends in .diff or .patch, wdiff for "
        ".wdiff, docx for .docx, odt for .odt, else html)",
    )
    ap.add_argument(
        "--open",
        action="store_true",
        help="open the output once written: the HTML report and the diffs in the browser, "
        "a .docx or .odt in the program that opens it",
    )
    lines = ap.add_mutually_exclusive_group()
    lines.add_argument(
        "-U",
        "--context",
        type=int,
        default=None,
        metavar="N",
        help="unchanged lines shown around each change, in every file (default: "
        f"{PROSE_CONTEXT} in Markdown files and Word documents, whose lines are "
        f"paragraphs, {CONTEXT} in the others); the HTML report can reveal the rest",
    )
    lines.add_argument("--full", action="store_true", help="show every line of the changed files")
    ap.add_argument(
        "--max-hidden",
        type=int,
        default=MAX_HIDDEN,
        metavar="N",
        help="unchanged lines embedded per gap, for the HTML report to reveal; "
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
        "piped through before comparing (not Word or OpenDocument files)",
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
        "--comments",
        choices=COMMENT_MODES,
        default=None,
        help="the comments of Markdown files and Word and OpenDocument documents, in every "
        "format: markers (default), set apart from the text, only those added or removed "
        "shown (in the HTML report a marker with the comment on hover, and a panel; in "
        "the diffs, CriticMarkup); text, compared as part of the text, as pandoc writes "
        "them; none, left out altogether",
    )
    ap.add_argument(
        "--empty-comments",
        action="store_true",
        help="show the comments that have no text too (left out by default)",
    )
    ap.add_argument(
        "--docx-changes",
        choices=DOCX_CHANGES,
        default="accept-all",
        help="the tracked changes of Word and OpenDocument documents: accept-all, "
        "reject-all, or show them as markup (default: accept-all)",
    )
    ap.add_argument(
        "--move-similarity",
        type=float,
        default=None,
        metavar="X",
        help="how alike, above 0 and at most 1, an edited paragraph (a line of other "
        "files) must be to where it reappears to count as moved, by --move-algorithm "
        f"(default: {MOVE_SIMILARITY}; 1: only lines moved unchanged)",
    )
    ap.add_argument(
        "--move-algorithm",
        choices=tuple(MOVE_ALGORITHMS),
        default=None,
        help="how --move-similarity measures two lines: token-sort, the share of their "
        "words and punctuation in common whatever their order; token-set, the words both "
        f"share against the rest of each (default: {MOVE_ALGORITHM})",
    )
    ap.add_argument(
        "--sentence-move-similarity",
        type=float,
        default=None,
        metavar="X",
        help="the same for sentences, when prose is compared sentence by sentence "
        f"(--split sentence or both; default: {SENTENCE_MOVE_SIMILARITY})",
    )
    ap.add_argument(
        "--sentence-move-algorithm",
        choices=tuple(MOVE_ALGORITHMS),
        default=None,
        help="how --sentence-move-similarity measures two sentences "
        f"(default: {SENTENCE_MOVE_ALGORITHM})",
    )
    ap.add_argument(
        "--move-passages",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also follow the passages moved within a paragraph (a line) or between two: "
        "a run of words removed in one place and added in another, holes of a word or "
        "two allowed, as alike as the --move-similarity (--sentence-move-similarity) "
        "of the lines says, is shown as moved rather than as a deletion and an unrelated "
        "insertion (default: on; --no-move-passages turns it off)",
    )
    # How moved passages are told from chance likeness: one option for each
    # field of MovedPassageSettings, the same the GUI's advanced settings show.
    passage_group = ap.add_argument_group(
        "moved passages (advanced)",
        "how --move-passages tells a moved passage from chance likeness; the defaults "
        "were chosen on simulated revisions of books (docs/passage_benchmark.py)",
    )
    for f in fields(MovedPassageSettings):
        passage_group.add_argument(
            passage_option(f.name),
            dest=f"passage_{f.name}",
            type=setting_type(f),
            default=None,
            metavar="X" if f.metadata["share"] else "N",
            help=f"{f.metadata['help']} (default: {f.default:,})",
        )
    ap.add_argument(
        "--split",
        choices=SPLITS,
        default=None,
        help="how the prose of Markdown files and Word and OpenDocument documents is "
        "compared: paragraph by paragraph, sentence by sentence (moved sentences are "
        "recognised, lines are labelled 12.1, 12.2, ...), or both, in one HTML report "
        "whose toolbar switches between the two (default: both for the HTML report, "
        "paragraph for a diff)",
    )
    ap.add_argument(
        "--language",
        default=DEFAULT,
        metavar="CODE",
        help="the language of the documents' prose: its rules split sentences with "
        "--split sentence and hyphenate wrapped lines. A code (e.g. en, it, de, fr); "
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
        "control characters, then guessed with cchardet, or charset-normalizer)",
    )
    ai_group = ap.add_argument_group("AI assessment")
    ai_group.add_argument(
        "--assess",
        metavar="AI",
        help="have an AI assess the value of the changes as a whole (a verdict, what "
        "changed, what improved, the problems to fix), at the top of the HTML report "
        "(not in a .diff or .wdiff): claude (Claude Code, on its login; "
        "prosediff[claude]), codex (ChatGPT through Codex, on its login; "
        "prosediff[codex]), or PROVIDER/MODEL through any-llm (prosediff[models]), "
        "e.g. ollama/qwen3 for a local model or openai/gpt-5 with OPENAI_API_KEY set; "
        "claude/MODEL and codex/MODEL choose their model among those they report "
        "(--list-models), else the login's default",
    )
    ai_group.add_argument(
        "--assess-effort",
        metavar="LEVEL",
        help="how hard the model thinks, one of the levels it reports it supports "
        "(--list-models: e.g. low, medium, high, xhigh, max); default: the model's own",
    )
    ai_group.add_argument(
        "--assess-context",
        choices=CONTEXTS,
        default="document",
        help="what the model reads: document, the changes and the whole new version, to "
        "check them against the rest of it (default; the old version is in them); changes, "
        "the changes only",
    )
    ai_group.add_argument(
        "--assess-instructions",
        metavar="TEXT",
        help="your own instructions, added to the prompt (e.g. 'the journal is Research "
        "Policy; Laura asked to cut the introduction by a fifth'), or a file holding them",
    )
    ai_group.add_argument(
        "--assess-annotate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="have the AI mark each problem in the text, from its first words to its "
        "last, with the problem and the change it proposes: a badge before each in "
        "the HTML report, its passage highlighted when clicked, and a card in the "
        "margin beside it (default: on)",
    )
    ai_group.add_argument(
        "--assess-documents",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="when the AI marked problems in a Word document (or an OpenDocument text) "
        "compared with another, put in the HTML report the documents to download: the new "
        "version with the AI's fixes as its tracked changes, its own (the co-authors') kept, "
        "and, when it has none, the tracked changes with the AI's comments; review mode "
        "chooses which problems they hold. They make "
        "the report as large again as the document, and more (default: on)",
    )
    ai_group.add_argument(
        "--assess-ai-writing",
        action="store_true",
        help="also ask the AI, apart, whether the text the changes added reads as written "
        "by an AI: a second assessment, its verdict (likely, possibly or unlikely) in the "
        "HTML report's top bar; an indication, not a proof",
    )
    ai_group.add_argument(
        "--assess-save-prompt",
        action="store_true",
        help="also put the exact text sent to the model (its system prompt and its "
        "message) in the HTML report, in a closed panel at its end",
    )
    ai_group.add_argument(
        "--assess-timeout",
        type=float,
        default=ASSESS_TIMEOUT,
        metavar="SECONDS",
        help=f"give up on the assessment after this long (default: {ASSESS_TIMEOUT:,})",
    )
    ai_group.add_argument(
        "--list-models",
        metavar="AI",
        help="list the models an AI reports it offers (claude, codex, ollama, or any "
        "provider any-llm reaches), its default first, and the efforts each supports",
    )
    ai_group.add_argument(
        "--login-codex",
        action="store_true",
        help="log in to ChatGPT for --assess codex, in the browser, once",
    )
    git_group = ap.add_argument_group("git's own commands")
    git_group.add_argument(
        "--setup-git",
        action="store_true",
        help="make git diff, git log -p and git show show Word and OpenDocument files as "
        "text, and add a difftool: git difftool -t prosediff (-d: one HTML report for all files); "
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
        help="print a Word document or OpenDocument text as Markdown (pandoc's), "
        "tracked changes as --docx-changes says (git's textconv command)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {package_version('unknown')}")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    mode = next((m for m in ("git", "files", "folders", "review") if getattr(args, m)), None)
    if args.list_models:
        if mode or args.repo:
            ap.error("--list-models takes no other argument")
        return _list_models(args.list_models)
    if args.login_codex:
        if mode or args.repo:
            ap.error("--login-codex takes no other argument")
        return _login_codex()
    if args.global_ and not args.setup_git:
        ap.error("--global goes with --setup-git")
    if mode and (args.setup_git or args.to_markdown):
        ap.error(f"--{mode} compares; --setup-git and --to-markdown do not")
    if args.setup_git:
        if args.base or (args.global_ and args.repo):
            ap.error("--setup-git takes one REPO, or --global and none")
        return _setup_git(None if args.global_ else Path(args.repo or "."))
    if args.to_markdown:
        if args.repo:
            ap.error("--to-markdown takes one FILE and no other argument")
        return _to_markdown(args.to_markdown, args.docx_changes)
    if mode is None:
        ap.error(
            "say what to compare: --git REPO BASE [TARGET], --files OLD NEW or --folders OLD NEW "
            "(or --review FILE, to review one file)"
        )
    if mode == "review":
        return _review(ap, args)
    if not args.base:
        ap.error("--git takes REPO and BASE" if args.git else f"--{mode} takes OLD and NEW")

    _check_compare_args(ap, args, mode)
    if args.assess is not None:
        _check_assess(ap, args)
    elif (
        args.assess_effort
        or args.assess_instructions
        or args.assess_save_prompt
        or args.assess_ai_writing
    ):
        ap.error(
            "--assess-effort, --assess-instructions, --assess-save-prompt and "
            "--assess-ai-writing go with --assess"
        )

    fmt = args.format or format_of(args.output)
    if args.assess is not None and fmt != "html":
        ap.error(f"--assess goes in the HTML report: a .{fmt} has no place for it")
    split = args.split or default_split(fmt)
    try:
        check_split(split, fmt)
    except ValueError as e:
        ap.error(f"--split: {e}")
    if fmt in TRACKED_FORMATS and args.files and args.base:
        try:
            check_paths(args.repo, args.base, fmt)
        except ValueError as e:
            ap.error(f"--format {fmt}: {e}")
    for name in ("move_similarity", "sentence_move_similarity"):
        try:
            check_move_similarity(getattr(args, name))
        except ValueError as e:
            ap.error(f"--{name.replace('_', '-')}: {e}")
    try:
        passage_settings = MovedPassageSettings.from_choices(
            {f.name: getattr(args, f"passage_{f.name}") for f in fields(MovedPassageSettings)}
        )
    except SettingError as e:
        ap.error(f"{passage_option(e.name)}: {e}")
    options = Options(
        context=None if args.full else ("auto" if args.context is None else args.context),
        md_filter=args.md_filter,
        ignore_whitespace=args.ignore_whitespace,
        comments=args.comments or "markers",
        empty_comments=args.empty_comments,
        max_hidden=args.max_hidden,
        docx_changes=args.docx_changes,
        paragraph_moves=MoveSettings(args.move_similarity, args.move_algorithm),
        sentence_moves=MoveSettings(args.sentence_move_similarity, args.sentence_move_algorithm),
        move_passages=args.move_passages,
        moved_passage_settings=passage_settings,
        language=args.language,
        encoding=args.encoding,
    )

    def run(options: Options):
        if not args.git:
            include = FOLDER_FILES if args.include is None else args.include
            return compare_paths(args.repo, args.base, options, paths=args.paths, include=include)
        return compare(
            Path(args.repo),
            args.base,
            args.target,
            options,
            paths=args.paths,
            cached=args.cached,
            untracked=args.untracked,
        )

    try:
        comparison, sentences = compare_split(run, options, split)
    except git.InvalidGitRepositoryError:
        print(
            f"{PROG}: not a git repository: {args.repo} "
            "(--files or --folders compare files or folders outside git)",
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

    assessment = None
    if args.assess is not None:
        print(f"{PROG}: asking {args.assess} to assess the changes...", file=sys.stderr)
        request = AssessRequest(
            args.assess,
            effort=args.assess_effort or "",
            context=args.assess_context,
            instructions=args.assess_instructions or "",
            timeout=args.assess_timeout,
            save_prompt=args.assess_save_prompt,
            annotate=args.assess_annotate,
        )
        assessment = assess_comparison(comparison, request)
        if assessment.error:
            print(f"{PROG}: the assessment failed: {assessment.error}", file=sys.stderr)
    writing = None
    if args.assess is not None and args.assess_ai_writing:
        print(
            f"{PROG}: asking {args.assess} whether the new text reads as written by an AI...",
            file=sys.stderr,
        )
        writing = assess_comparison(comparison, request, kind="writing")
        if writing.error:
            print(f"{PROG}: the AI-writing assessment failed: {writing.error}", file=sys.stderr)

    output = _output_path(args, fmt)
    try:
        output = write_output(
            comparison,
            output,
            fmt,
            args.paths,
            align=args.align,
            context=options.context,
            sentences=sentences,
            split=split,
            assessment=assessment,
            writing=writing,
            documents=args.assess_documents,
        )
    except ValueError as e:  # a .docx or .odt of anything but two such documents
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1

    c = comparison
    print(
        f"{PROG}: {c.base.short}..{c.target.short}: {counted(len(c.files), 'file')}, "
        f"+{c.counts.additions:,} -{c.counts.deletions:,} -> {output}"
    )
    if assessment is not None:
        verdict = f" ({assessment.verdict})" if assessment.verdict else ""
        print(f"{PROG}: assessment{verdict}, at the top of the HTML report")
    if writing is not None and not writing.error:
        verdict = f" ({writing.verdict})" if writing.verdict else ""
        print(f"{PROG}: AI-writing assessment{verdict}, beside it")
    if args.open:
        open_output(output)
    return 0


def _review(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """--review FILE: one file alone, reviewed whole by the AI --assess
    names, into an HTML report."""
    if not args.repo or args.base or args.target:
        ap.error("--review takes one FILE")
    if args.assess is None:
        ap.error("--review has an AI review the file: say which with --assess")
    _check_assess(ap, args)
    if args.assess_ai_writing:
        ap.error("--assess-ai-writing asks about the text changes added: --review compares none")
    if (args.format or format_of(args.output)) != "html":
        ap.error("--review writes an HTML report: its output is a .html")
    if args.split not in (None, "paragraph"):
        ap.error("--review shows the file paragraph by paragraph: --split takes two versions")
    for option, given in (
        ("--path", args.paths),
        ("--include", args.include is not None),
        ("--cached", args.cached),
        ("--untracked", args.untracked),
    ):
        if given:
            ap.error(f"{option} picks what to compare: --review takes one file")
    _check_reading(ap, args)
    path = Path(args.repo)
    options = Options(
        md_filter=args.md_filter,
        comments=args.comments or "markers",
        empty_comments=args.empty_comments,
        docx_changes=args.docx_changes,
        language=args.language,
        encoding=args.encoding,
    )
    try:
        comparison = review_file(path, options)
    except (FilterError, SourceError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    print(f"{PROG}: asking {args.assess} to review {path.name}...", file=sys.stderr)
    request = AssessRequest(
        args.assess,
        effort=args.assess_effort or "",
        instructions=args.assess_instructions or "",
        timeout=args.assess_timeout,
        save_prompt=args.assess_save_prompt,
        annotate=args.assess_annotate,
    )
    assessment = assess_comparison(comparison, request)
    if assessment.error:
        print(f"{PROG}: the review failed: {assessment.error}", file=sys.stderr)
    output = args.output or (default_output() if args.open else review_page(path))
    output = write_output(
        comparison,
        output,
        align=args.align,
        split="paragraph",
        assessment=assessment,
        documents=args.assess_documents,
    )
    verdict = f" ({assessment.verdict})" if assessment.verdict else ""
    marked = counted(len(assessment.annotations), "problem")
    print(f"{PROG}: {path.name} reviewed{verdict}, {marked} marked -> {output}")
    if args.open:
        open_output(output)
    return 0


def _list_models(ai: str) -> int:
    try:
        found = models_of(ai.strip().lower())
    except AssessError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    if not found:
        print(f"{PROG}: {ai} reports no models", file=sys.stderr)
        return 1
    width = max(len(m.name) for m in found)
    for m in found:
        print(f"{m.name:<{width}}  {m.description}".rstrip())
        if m.efforts:
            levels = [f"{e}*" if e == m.default_effort else e for e, _ in m.efforts]
            print(f"{'':<{width}}  effort: {', '.join(levels)}")
    if any(m.default_effort for m in found):
        print("(*: the model's default effort)")
    return 0


def _login_codex() -> int:
    try:
        ok = login_codex()
    except AssessError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    print(f"{PROG}: {'logged in to ChatGPT' if ok else 'the login did not succeed'}")
    return 0 if ok else 1


def _check_assess(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse an AI --assess cannot name, or a timeout of none (ap.error)."""
    try:
        parse_backend(args.assess)
    except AssessError as e:
        ap.error(f"--assess: {e}")
    if args.assess_timeout <= 0:
        ap.error("--assess-timeout must be above 0")


def _check_reading(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Normalize the language and the encoding, refusing those not known
    (ap.error)."""
    try:
        args.language = normalize_language(args.language)
    except ValueError as e:
        ap.error(f"--language: {e}")
    try:
        args.encoding = check_encoding(args.encoding)
    except ValueError as e:
        ap.error(f"--encoding: {e}")


def _check_compare_args(ap: argparse.ArgumentParser, args: argparse.Namespace, mode: str) -> None:
    """Refuse the options of a comparison that make no sense (ap.error);
    normalize the language and the encoding."""
    if args.context is not None and args.context < 0:
        ap.error("--context must be 0 or more")
    if args.max_hidden < 0:
        ap.error("--max-hidden must be 0 or more")
    _check_reading(ap, args)
    if not args.git:
        if args.target:
            ap.error(f"--{mode} compares OLD with NEW: give no TARGET")
        if args.cached or args.untracked:
            ap.error("--cached and --untracked need a git repository: they go with --git")
        old, new = Path(args.repo), Path(args.base)
        if args.files and old.is_dir() and new.is_dir():
            ap.error(
                "OLD and NEW are folders: use --folders (from git difftool -d, set up by "
                "an earlier prosediff: run prosediff --setup-git again)"
            )
        if args.folders and old.is_file() and new.is_file():
            ap.error("OLD and NEW are files: use --files")
    if args.include is not None and not args.folders:
        ap.error("--include picks the files of two folders: it goes with --folders")
    if args.paths and args.files:
        ap.error("--path picks files of a repository or of two folders, not of --files")
    if args.cached and args.target:
        ap.error("--cached compares BASE with the index: give no TARGET")
    if args.untracked and (args.target or args.cached):
        ap.error("--untracked needs the working tree: give no TARGET and no --cached")


def _output_path(args: argparse.Namespace, fmt: str) -> Path:
    """Where the output goes: -o; with --open, a file in the temporary
    folder (git difftool -d gives two temporary folders, gone once prosediff
    returns); comparing two folders or two files, into the new folder or next
    to the new file (default_page); else diff.html (.diff, .wdiff) here."""
    output = args.output
    if output is None and args.open:
        output = default_output(fmt)
    if output is None and (args.folders or args.files):
        output = default_page(Path(args.repo), Path(args.base), FORMATS[fmt])
    if output is None:
        output = Path("diff").with_suffix(FORMATS[fmt])
    return output


def _setup_git(repo: Path | None) -> int:
    try:
        done = setup_git(repo)
    except SetupError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    print("\n".join(done))
    print(
        "git diff now shows Word and OpenDocument files as text; "
        "git difftool -d -t prosediff opens a prosediff HTML report."
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
