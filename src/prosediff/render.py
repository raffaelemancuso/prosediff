"""Render a Comparison to a self-contained HTML page."""

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from jinja2 import Environment, PackageLoader, select_autoescape

from prosediff.diff import Comparison


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


ALIGNMENTS = ("left", "justify")


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
    )
