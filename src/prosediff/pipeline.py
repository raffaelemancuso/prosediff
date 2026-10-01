"""What a run of prosediff does, asked for by the command line
(prosediff.cli) or the window (prosediff.gui) alike: compare two versions,
or read one file alone; have an AI assess the changes, or review the file,
and, apart, say whether the new text reads as written by an AI; write the
output. Each front end turns its own input into a Run, and reports the
Result, and the errors, its own way."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from prosediff.assess import Assessment, AssessRequest
from prosediff.diff import (
    CONTEXT,
    Comparison,
    Options,
    compare,
    compare_paths,
    compare_split,
    review_file,
)
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


def execute(
    run: Run,
    progress: Callable[[str], None] = lambda stage: None,
    approve: Callable[[Path], bool] | None = None,
) -> Result:
    """Do run, telling progress each stage as it starts ("Comparing…").
    With approve, an HTML report with an AI to assess is first written
    without the assessment, and approve, given its path, says whether the
    text goes to the AI."""
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

        comparison, sentences = compare_split(compared, run.options, run.split)
        split = run.split
    write = partial(
        write_output,
        comparison,
        run.output,
        run.fmt,
        run.paths,
        align=run.align,
        context=CONTEXT if reviewing else run.options.context,
        sentences=sentences,
        split=split,
        documents=run.documents,
    )
    # no other format has a place for the assessment
    ask = run.request is not None and run.fmt == "html"
    if ask and approve is not None:
        progress("Writing the preview…")
        write()
        if not approve(run.output):
            return Result(run.output, comparison)
    assessment = writing = None
    if ask:
        ai = run.request.spec
        progress(f"Asking {ai} to {'review the file' if reviewing else 'assess the changes'}…")
        assessment = assess_comparison(comparison, run.request)
        if run.ai_writing and not reviewing:
            progress(f"Asking {ai} whether the new text reads as written by an AI…")
            writing = assess_comparison(comparison, run.request, kind="writing")
    progress(
        "Writing the report…"
        if run.fmt == "html"
        else "Writing the document…"
        if run.fmt in ("docx", "odt")
        else "Writing the diff…"
    )
    try:
        path = write(assessment=assessment, writing=writing)
    except ValueError as e:  # a .docx or .odt of anything but two such documents
        raise OutputError(str(e)) from e
    return Result(path, comparison, assessment, writing)
