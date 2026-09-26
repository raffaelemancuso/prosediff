"""Render a Comparison to a self-contained HTML report, or to a unified diff
(prosediff.unified)."""

import tempfile
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from prosediff.diff import CONTEXT, Comparison
from prosediff.flags import flag_css, flag_html
from prosediff.hyphenate import hyphenate
from prosediff.language import file_language_note, flag_code, paragraph_language_note
from prosediff.unified import unified


def _version() -> str:
    try:
        return version("prosediff")
    except PackageNotFoundError:
        return ""


_env = Environment(
    loader=PackageLoader("prosediff", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["hyphenate"] = hyphenate
_env.globals["flag"] = lambda tag, title=None: flag_html(flag_code(tag), tag, title)
_env.globals["file_language_note"] = file_language_note
_env.globals["paragraph_language_note"] = paragraph_language_note


ALIGNMENTS = ("left", "justify")
# What prosediff writes: the HTML report, a unified diff or a word diff; and their
# files' suffix.
FORMATS = {"html": ".html", "diff": ".diff", "wdiff": ".wdiff"}
# The suffixes each text format is recognised by.
TEXT_SUFFIXES = {".diff": "diff", ".patch": "diff", ".wdiff": "wdiff"}
HOMEPAGE = "https://github.com/raffaelemancuso/prosediff"


def format_of(path: Path | str | None) -> str:
    """The format a file name asks for: a unified diff for .diff and .patch,
    a word diff for .wdiff, else the HTML report."""
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
            for r in f.rows:
                for row in [r, *r.hidden]:
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
        for r in f.rows:
            for row in [r, *r.hidden]:
                if row.anchor.startswith(old):
                    row.anchor = new + row.anchor[len(old) :]
        for e in comparison.comments:
            if e.anchor == old or e.anchor.startswith(old + "-"):
                e.anchor = new + e.anchor[len(old) :]


def render(
    comparison: Comparison,
    paths: list[str] | None = None,
    align: str = "left",
    sentences: Comparison | None = None,
    split: str = "paragraph",
) -> str:
    """The HTML report; align ("left" or "justify") sets how wrapped lines are
    aligned. split says how the comparison compared prose, "paragraph" or
    "sentence"; given sentences, the same comparison sentence by sentence,
    the report holds both (comparison then paragraph by paragraph), and a
    switch of its toolbar shows one or the other."""
    if align not in ALIGNMENTS:
        raise ValueError(f"align must be one of {ALIGNMENTS}, not {align!r}")
    template = _env.get_template("report.html.j2")
    if sentences is not None:
        set_apart(sentences, "s-")
    return template.render(
        c=comparison,
        alt=sentences,
        split="paragraph" if sentences is not None else split,
        paths=paths or [],
        align=align,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        version=_version(),
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
) -> None:
    """Write the HTML report (fmt "html"), the unified diff ("diff") or the word
    diff ("wdiff"), LF line ends on every system. The text formats have
    context unchanged lines around each change (None: every line; "auto":
    git's 3). sentences and split as in render; a text format holds one
    comparison only."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {tuple(FORMATS)}, not {fmt!r}")
    if fmt != "html":
        text = unified(comparison, CONTEXT if context == "auto" else context, fmt)
    else:
        text = render(comparison, paths, align=align, sentences=sentences, split=split)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
