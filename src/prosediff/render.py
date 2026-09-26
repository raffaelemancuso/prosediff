"""Render a Comparison to a self-contained HTML page."""

import tempfile
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from prosediff.diff import Comparison
from prosediff.flags import flag_css, flag_html
from prosediff.hyphenate import hyphenate
from prosediff.language import file_language_note, flag_code, paragraph_language_note


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
HOMEPAGE = "https://github.com/raffaelemancuso/prosediff"


def default_output() -> Path:
    """A fresh page in the temporary folder, so no repository is cluttered;
    created empty, so pages made in the same second (git difftool, one per
    file) do not overwrite each other."""
    folder = Path(tempfile.gettempdir()) / "prosediff"
    folder.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f"prosediff_{datetime.now():%Y%m%d_%H%M%S}_",
        suffix=".html",
        dir=folder,
        delete=False,
    ) as f:
        return Path(f.name)


def page_flags(comparison: Comparison) -> set[str]:
    """The countries whose flags the page shows: a file's language's in its
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


def render(comparison: Comparison, paths: list[str] | None = None, align: str = "left") -> str:
    """The page; align ("left" or "justify") sets how wrapped lines are aligned."""
    if align not in ALIGNMENTS:
        raise ValueError(f"align must be one of {ALIGNMENTS}, not {align!r}")
    template = _env.get_template("report.html.j2")
    return template.render(
        c=comparison,
        paths=paths or [],
        align=align,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        version=_version(),
        homepage=HOMEPAGE,
        flag_css=flag_css(page_flags(comparison)),
    )
