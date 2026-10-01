"""Render a Comparison to a self-contained HTML report, to a unified diff
(prosediff.unified), or to a document of tracked changes (prosediff.tracked)."""

import os
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from prosediff.assess import Assessment, AssessRequest, assess
from prosediff.diff import CONTEXT, Comparison, all_rows, comment_text
from prosediff.flags import flag_css, flag_html
from prosediff.hyphenate import hyphenate
from prosediff.language import file_language_note, flag_code, paragraph_language_note
from prosediff.sources import is_document
from prosediff.tracked import TRACKED_FORMATS, write_tracked
from prosediff.unified import unified


def counted(n: int, word: str) -> str:
    """A count and what it counts, plural but for one: "1,234 lines"."""
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def package_version(default: str = "") -> str:
    """prosediff's version, or default when it is not installed."""
    try:
        return version("prosediff")
    except PackageNotFoundError:
        return default


_env = Environment(
    loader=PackageLoader("prosediff", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["hyphenate"] = hyphenate
_env.filters["counted"] = lambda n, word: counted(n, word)
_env.filters["comma"] = lambda n: f"{n:,}"
_env.globals["flag"] = lambda tag, title=None: flag_html(flag_code(tag), tag, title)
_env.globals["file_language_note"] = file_language_note
_env.globals["paragraph_language_note"] = paragraph_language_note


ALIGNMENTS = ("left", "justify")
# How prose is compared (--split): paragraph by paragraph, sentence by
# sentence, or both (in the HTML report only).
SPLITS = ("paragraph", "sentence", "both")


def default_split(fmt: str) -> str:
    """The split when none is chosen: both for the HTML report, paragraph by
    paragraph for a diff, which holds one."""
    return "both" if fmt == "html" else "paragraph"


def check_split(split: str, fmt: str) -> None:
    """Refuse both splits for a diff, which holds one, and sentences for a
    document of tracked changes, whose paragraphs are paragraphs
    (ValueError)."""
    if split == "both" and fmt != "html":
        raise ValueError("both splits are for the HTML report: a diff holds one")
    if split == "sentence" and fmt in TRACKED_FORMATS:
        raise ValueError(f"a .{fmt} of tracked changes compares paragraph by paragraph")


# What prosediff writes: the HTML report, a unified diff, a word diff, or a
# Word document or an OpenDocument text of tracked changes; and their files'
# suffix.
FORMATS = {"html": ".html", "diff": ".diff", "wdiff": ".wdiff", "docx": ".docx", "odt": ".odt"}
# The suffixes each format but the HTML report is recognised by.
TEXT_SUFFIXES = {s: f for f, s in FORMATS.items() if f != "html"} | {".patch": "diff"}
HOMEPAGE = "https://github.com/raffaelemancuso/prosediff"


def format_of(path: Path | str | None) -> str:
    """The format a file name asks for: a unified diff for .diff and .patch,
    a word diff for .wdiff, a document of tracked changes for .docx and .odt,
    else the HTML report."""
    return TEXT_SUFFIXES.get(Path(path).suffix.lower(), "html") if path else "html"


def default_output(fmt: str = "html") -> Path:
    """A fresh HTML report (or diff) in the temporary folder, so no repository is
    cluttered; created empty, so HTML reports made in the same second (git
    difftool, one per file) do not overwrite each other."""
    folder = Path(tempfile.gettempdir()) / "prosediff"
    folder.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f"prosediff_{datetime.now():%Y%m%d_%H%M%S}_",
        suffix=FORMATS[fmt],
        dir=folder,
        delete=False,
    ) as f:
        return Path(f.name)


def page_flags(comparison: Comparison) -> set[str]:
    """The countries whose flags the HTML report shows: a file's language's in its
    header, or, when its paragraphs differ, each paragraph's."""
    codes = set()
    for f in comparison.files:
        if not f.language:
            continue
        codes.add(flag_code(f.language))
        if f.mixed_languages:
            for row in all_rows(f.rows):
                codes.add(flag_code(row.left_lang or f.language))
                codes.add(flag_code(row.right_lang or f.language))
    return codes


def set_apart(comparison: Comparison, prefix: str) -> None:
    """Prefix the ids of a comparison's files and rows (and the comments'
    links to them), for a report to hold it beside another."""
    for f in comparison.files:
        old = f.anchor
        f.anchor_prefix = prefix
        new = f.anchor
        for row in all_rows(f.rows):
            if row.anchor.startswith(old):
                row.anchor = new + row.anchor[len(old) :]
        for e in comparison.comments:
            if e.anchor == old or e.anchor.startswith(old + "-"):
                e.anchor = new + e.anchor[len(old) :]


@cache
def fflate() -> Markup:
    """fflate's browser build (vendor/fflate.umd.js, MIT: vendor/fflate.LICENSE),
    which reads and writes the zips of the documents the report offers."""
    return Markup((files("prosediff") / "vendor" / "fflate.umd.js").read_text(encoding="utf-8"))


def render(
    comparison: Comparison,
    paths: list[str] | None = None,
    align: str = "left",
    sentences: Comparison | None = None,
    split: str = "paragraph",
    assessment: Assessment | None = None,
    writing: Assessment | None = None,
    documents: bool = True,
) -> str:
    """The HTML report; align ("left" or "justify") sets how wrapped lines are
    aligned. split says how the comparison compared prose, "paragraph" or
    "sentence"; given sentences, the same comparison sentence by sentence,
    the report holds both (comparison then paragraph by paragraph), and a
    switch of its toolbar shows one or the other. An AI's assessment of the
    changes, given, heads the report; writing, its assessment of whether
    their new text reads as written by an AI, beside it. documents: when the
    AI marked problems in a Word document or an OpenDocument text, compared
    paragraph by paragraph, the report holds the documents made of them to
    download (prosediff.aidocs)."""
    if align not in ALIGNMENTS:
        raise ValueError(f"align must be one of {ALIGNMENTS}, not {align!r}")
    template = _env.get_template("report.html.j2")
    # the documents keep the paragraphs as they are: made from them only
    by_paragraph = sentences is not None or split == "paragraph"
    ai_documents = []
    if documents and by_paragraph and assessment is not None and assessment.annotations:
        from prosediff.aidocs import downloads  # python-docx and odfdo: only then

        ai_documents = downloads(comparison, assessment)
    if sentences is not None:
        set_apart(sentences, "s-")
    return template.render(
        c=comparison,
        alt=sentences,
        split="paragraph" if sentences is not None else split,
        paths=paths or [],
        align=align,
        assessment=assessment,
        writing=writing,
        ai_documents=ai_documents,
        fflate=fflate() if ai_documents else "",
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        version=package_version(),
        homepage=HOMEPAGE,
        flag_css=flag_css(
            page_flags(comparison) | (page_flags(sentences) if sentences is not None else set())
        ),
    )


def write_output(
    comparison: Comparison,
    path: Path,
    fmt: str = "html",
    paths: list[str] | None = None,
    align: str = "left",
    context: int | str | None = CONTEXT,
    sentences: Comparison | None = None,
    split: str = "paragraph",
    assessment: Assessment | None = None,
    writing: Assessment | None = None,
    documents: bool = True,
) -> Path:
    """Write the HTML report (fmt "html"), the unified diff ("diff"), the word
    diff ("wdiff"), LF line ends on every system, or the Word document
    ("docx") or OpenDocument text ("odt") of tracked changes; returns the
    path written, another than path when path was open and locked
    (prosediff.tracked.save). The text formats have context unchanged lines
    around each change (None: every line; "auto": git's 3); a document of
    tracked changes holds every line. sentences and split as in render; the
    other formats hold one comparison only. An AI's assessment, given, heads
    the HTML report (and, when asked for, the text sent to the AI ends it);
    the other formats have none. documents as in render."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {tuple(FORMATS)}, not {fmt!r}")
    if comparison.single and fmt != "html":
        raise ValueError("a file reviewed alone has no changes: its review is an HTML report")
    if fmt in TRACKED_FORMATS:
        return write_tracked(comparison, path, fmt)
    if fmt != "html":
        text = unified(comparison, CONTEXT if context == "auto" else context, fmt)
    else:
        text = render(
            comparison,
            paths,
            align=align,
            sentences=sentences,
            split=split,
            assessment=assessment,
            writing=writing,
            documents=documents,
        )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def open_output(path: Path) -> None:
    """Show the output: the HTML report and the diffs in the browser, a
    document in the program that opens it."""
    if not is_document(str(path)):
        webbrowser.open(path.resolve().as_uri())
    elif sys.platform == "win32":
        os.startfile(path)
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)])


def assess_comparison(
    comparison: Comparison, request: AssessRequest, kind: str = "value"
) -> Assessment:
    """The assessment of a comparison's changes by the AI request names
    (prosediff.assess): its word diff, the changed lines alone, comments
    included, and the whole new version (new_version) when the request's
    context says so, sent to the model; kind "writing" asks instead whether
    their new text reads as written by an AI. A file reviewed alone
    (Comparison.single) is sent whole, for a review (kind "review"); it has
    no new text to ask the other question of (ValueError)."""
    documents = any(f.old_data or f.new_data for f in comparison.files)
    if comparison.single:
        if kind != "value":
            raise ValueError("a file reviewed alone has no changes whose writing to assess")
        return assess(
            "",
            comparison.repo_name,
            request,
            document=new_version(comparison),
            documents=documents,
            kind="review",
        )
    return assess(
        unified(comparison, 0, "wdiff"),
        comparison.repo_name,
        request,
        document=new_version(comparison) if request.context == "document" else "",
        documents=documents,
        kind=kind,
    )


def new_version(comparison: Comparison) -> str:
    """The new version of every changed file, whole, as the word diff
    writes its lines (a document's formatting in Markdown, its comments in
    CriticMarkup), each file headed by its path when there are several."""
    parts = []
    for f in comparison.files:
        if f.binary or not f.new_lines:
            continue
        text = "\n\n".join(comment_text(line, f.comments) for line in f.new_lines)
        parts.append(f"=== {f.path} ===\n\n{text}" if len(comparison.files) > 1 else text)
    return "\n\n".join(parts)
