"""What a run of prosediff does, asked for by the command line
(prosediff.cli) or the window (prosediff.gui) alike: compare two versions,
or read one file alone; have an AI assess the changes, or review the file,
and, apart, say whether the new text reads as written by an AI; write the
output. Each front end turns its own input into a Run, and reports the
Result, and the errors, its own way."""

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path

from prosediff.assess import Assessment, AssessRequest
from prosediff.diff import (
    AUTO_ENCODING,
    CONTEXT,
    Comparison,
    Options,
    compare,
    compare_paths,
    review_diff,
    review_file,
)
from prosediff.history import estimate, record, usually
from prosediff.language import DEFAULT
from prosediff.render import assess_comparison, write_output
from prosediff.sources import FOLDER_FILES


class OutputError(ValueError):
    """The output cannot be written as asked (a document of tracked changes
    of anything but two such documents), told apart from the comparison's
    errors."""


@dataclass
class Run:
    """One run of prosediff.

    mode: "git" (old: the repository, new: the base, target: a ref, None
    for the working tree; cached: the index instead; untracked: new files
    too), "files" or "folders" (old and new; include: the folders' files),
    or "review" (old: the file request's AI reviews alone). paths limit what
    is compared. The output goes to output, as fmt (one of render.FORMATS),
    the prose compared as split says. request: the AI that assesses (None:
    none); ai_writing: asked apart, too, whether the new text reads as
    written by an AI; documents: the AI's Word or OpenDocument documents put
    in the HTML report.
    """

    mode: str
    old: str
    output: Path
    new: str = ""
    target: str | None = None
    cached: bool = False
    untracked: bool = False
    paths: list[str] | None = None
    include: str | None = FOLDER_FILES
    options: Options = field(default_factory=Options)
    fmt: str = "html"
    split: str = "both"
    align: str = "left"
    request: AssessRequest | None = None
    ai_writing: bool = False
    documents: bool = True


def request_of(src: object) -> AssessRequest | None:
    """The AI src.assess names (none: None), asked as src's assess_*
    fields say: the command line's arguments and the window's settings name
    them alike."""
    if not src.assess:
        return None
    return AssessRequest(
        src.assess,
        effort=src.assess_effort or "",
        context=src.assess_context,
        instructions=src.assess_instructions or "",
        timeout=src.assess_timeout,
        save_prompt=src.assess_save_prompt,
        annotate=src.assess_annotate,
        author=src.assess_author or "",
        system=src.assess_prompt or "",
        writing_system=src.assess_writing_prompt or "",
        edits=src.assess_edits,
        files=files_of(src),
    )


def files_of(src: object) -> tuple[str, ...]:
    """The other files src sends the AI as context: the command line's
    --assess-file, each given; the window's, separated by ";", only while
    its switch is on."""
    files = src.assess_files or ()
    if isinstance(files, str):
        files = files.split(";")
    if not getattr(src, "assess_send_files", True):
        return ()
    return tuple(f.strip() for f in files if f.strip())


def options_of(src: object, comparing: bool = True, **more) -> Options:
    """The Options src's fields say, the command line's arguments and the
    window's settings naming them alike: those that read the files and,
    comparing, those of a comparison named the same; more, the others (the
    context, the moved lines and passages), each front end's own way. A
    field left empty takes its default."""
    options = {
        "md_filter": src.md_filter or None,
        "comments": src.comments or "markers",
        "empty_comments": src.empty_comments,
        "skip_resolved": src.skip_resolved,
        "docx_changes": src.docx_changes,
        "language": src.language or DEFAULT,
        "encoding": src.encoding or AUTO_ENCODING,
    }
    if comparing:
        options |= {
            "ignore_whitespace": src.ignore_whitespace,
            "max_hidden": src.max_hidden,
            "move_passages": src.move_passages,
        }
    return Options(**options, **more)


@dataclass
class Result:
    """What a run made: the output's path (another when it was locked),
    the comparison, and the AI's assessment and AI-writing assessment (None
    when not asked, or the preview not approved; a failed one holds its
    error)."""

    path: Path
    comparison: Comparison
    assessment: Assessment | None = None
    writing: Assessment | None = None
    # where the AI's answers were kept (prosediff.saved); None when not
    saved: Path | None = None


def fixes_shown(run: Run, assessment: Assessment) -> tuple[Comparison, Assessment] | None:
    """A review's report as a diff of the file and the file as the AI's
    fixes leave it (review_diff), and the assessment to show with it: the
    problems fixed marked on the file's side, the others on the fixed one's.
    None when no fix applies."""
    from prosediff.aidocs import with_fixes

    fixed: set[int] = set()

    def edit(lines: list[str]) -> list[str]:
        out, done = with_fixes(lines, assessment.annotations)
        fixed.update(done)
        return out

    comparison = review_diff(run.old, edit, run.options)
    if not fixed:
        return None
    marked = [
        replace(n, side="old") if k in fixed else n for k, n in enumerate(assessment.annotations)
    ]
    return comparison, replace(assessment, annotations=marked)


def execute(
    run: Run,
    progress: Callable[[str], None] = lambda stage: None,
    approve: Callable[[Path], bool] | None = None,
    live: Callable[[str], None] | None = None,
    saved: tuple[Assessment, Assessment | None] | None = None,
) -> Result:
    """Do run, telling progress each stage as it starts ("Comparing…"):
    asking the AI, with how long it usually takes (prosediff.history), and
    telling live, given, what the model is doing meanwhile ("thinking, about
    3,481 tokens written"). With approve, an HTML report with an AI to
    assess the changes is first written without the assessment, and
    approve, given its path, says whether the text goes to the AI; a file
    reviewed alone has no preview. The AI's answers are kept beside the HTML
    report (prosediff.saved); saved, answers kept so before, are used in
    place of asking the AI again."""
    reviewing = run.mode == "review"
    if reviewing:
        progress("Reading the file…")
        comparison, sentences, split = review_file(run.old, run.options), None, "paragraph"
    else:

        def compared(options: Options) -> Comparison:
            if run.split != "both":
                progress("Comparing…")
            else:
                unit = "sentence" if options.by_sentence else "paragraph"
                progress(f"Comparing {unit} by {unit}…")
            if run.mode == "git":
                return compare(
                    run.old,
                    run.new,
                    run.target,
                    options,
                    paths=run.paths,
                    cached=run.cached,
                    untracked=run.untracked,
                )
            return compare_paths(run.old, run.new, options, paths=run.paths, include=run.include)

        # paragraph by paragraph, sentence by sentence, or both (the paragraphs first)
        comparison = compared(replace(run.options, by_sentence=run.split == "sentence"))
        sentences = (
            compared(replace(run.options, by_sentence=True)) if run.split == "both" else None
        )
        split = run.split
    writer = partial(
        write_output,
        path=run.output,
        fmt=run.fmt,
        paths=run.paths,
        align=run.align,
        context=CONTEXT if reviewing else run.options.context,
        sentences=sentences,
        split=split,
        documents=run.documents,
    )
    write = partial(writer, comparison)
    # no other format has a place for the assessment
    ask = run.request is not None and run.fmt == "html" and saved is None
    if ask and approve is not None and not reviewing:
        progress("Writing the preview…")
        write()
        if not approve(run.output):
            return Result(run.output, comparison)
    assessment, writing = saved or (None, None)
    if ask:
        ai = run.request.spec
        report = (lambda tracked: live(tracked.describe())) if live is not None else None

        def asked(what: str, kind: str) -> Assessment:
            # what kind of assessment it makes, as history.record notes it
            noted = "review" if reviewing and kind == "value" else kind
            span = usually(estimate(ai, run.request.effort, noted))
            progress(f"Asking {ai} {what}{f' ({span})' if span else ''}…")
            made = assess_comparison(comparison, run.request, kind=kind, report=report)
            record(made, len(made.system) + len(made.prompt))
            return made

        assessment = asked("to review the file" if reviewing else "to assess the changes", "value")
        if run.ai_writing:
            what = "the file" if reviewing else "the new text"
            writing = asked(f"whether {what} reads as written by an AI", "writing")
    assessment_shown = assessment
    if (
        reviewing
        and assessment is not None
        and assessment.annotations
        and (shown := fixes_shown(run, assessment))
    ):
        # the documents to download made of the file and the assessment as they are
        write = partial(writer, shown[0], documents_of=(comparison, assessment))
        assessment_shown = shown[1]
    progress(
        "Writing the report…"
        if run.fmt == "html"
        else "Writing the document…"
        if run.fmt in ("docx", "odt")
        else "Writing the diff…"
    )
    try:
        path = write(assessment=assessment_shown, writing=writing)
    except ValueError as e:  # a .docx or .odt of anything but two such documents
        raise OutputError(str(e)) from e
    kept = None
    if assessment is not None and run.fmt == "html" and saved is None:
        from prosediff.saved import save  # it reads Run from here

        with contextlib.suppress(OSError):  # the report stands without it
            kept = save(run, path, assessment, writing)
    return Result(path, comparison, assessment, writing, kept)
