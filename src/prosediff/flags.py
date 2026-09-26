"""The flags the HTML report shows languages by.

Browsers draw a flag emoji only where the system has the glyphs: Windows has
none, and shows two letters instead. So the HTML report draws each flag from an SVG
of flag-icons (MIT, https://github.com/lipis/flag-icons), kept in flags.zip
(docs/make_flags.py remakes it): each flag the HTML report shows is embedded once,
as the background of a CSS class, however many paragraphs show it.
"""

import base64
import zipfile
from functools import cache
from importlib.resources import files
from io import BytesIO

from markupsafe import Markup, escape

from prosediff.language import language_name

# No country to show a language by: a globe, which every system draws.
GLOBE = "\U0001f310"


@cache
def _flags() -> dict[str, bytes]:
    data = files("prosediff").joinpath("flags.zip").read_bytes()
    with zipfile.ZipFile(BytesIO(data)) as z:
        return {n.removesuffix(".svg"): z.read(n) for n in z.namelist() if n.endswith(".svg")}


def flag_css(codes: set[str]) -> Markup:
    """The CSS classes of the flags of these countries (two-letter codes,
    in lower case), flag-it and the like; unknown codes are left out."""
    rules = []
    for code in sorted(codes):
        if svg := _flags().get(code):
            uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
            rules.append(f'.flag-{code} {{ background-image: url("{uri}"); }}')
    return Markup("\n".join(rules))


def flag_html(code: str, tag: str, title: str | None = None) -> Markup:
    """A language's flag: that of the country (code) as an image named
    after the language (tag), or a globe when there is none; title is its
    tooltip."""
    tip = Markup(' title="{}"').format(title) if title else ""
    label = escape(language_name(tag)) if tag else ""
    if code in _flags():
        return Markup('<span class="flag flag-{}" role="img" aria-label="{}"{}></span>').format(
            code, label, tip
        )
    return Markup('<span class="flag globe" role="img" aria-label="{}"{}>{}</span>').format(
        label, tip, GLOBE
    )
