"""Inline Markdown styles, character by character, for the formatted view.

The HTML report shows a Markdown line raw by default; the formatted view hides its
syntax (the ** of **bold**, the (url) of a link, the # of a heading) and
styles what it marks. Change markup (<del>, <ins>) and formatting cannot be
nested into one another in general (a change may start inside a bold run and
end outside it), so instead each character gets a set of style classes and
every piece of text the diff emits is cut into runs of equal style, each in a
<span>. The formatting is therefore only ever applied within the pieces.

No Markdown parser reports where in the source each inline element starts
and ends, so the common inline syntax is recognised with regular
expressions: headings, block quotes, code spans, strong and emphasis, links,
images, citations and pandoc bracketed spans with attributes.

A Word or OpenDocument text read with its tracked changes kept ("show") has
them as [text]{.insertion author=... date=...} and [text]{.deletion ...}
spans: their text is styled as Word shows it, and carries the change's
author and date for the HTML report's tooltip. A style starting with "@" is such
a value, name=value, written as a data- attribute instead of a class.
"""

import re

from markupsafe import Markup, escape

from prosediff.document import DATE_ATTRIBUTE, change_marks, runs

HEADING = re.compile(r"^(#{1,6})([ \t]+)")
QUOTE = re.compile(r"^((?:>[ \t]?)+)")
CODE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
EMPH = re.compile(r"(?<![*\w])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![*\w])")
IMAGE_OR_LINK = re.compile(r"(!?\[)((?:[^\[\]]|\[[^\]]*\])*)(\]\([^)\s]*(?:\s+\"[^\"]*\")?\))")
ATTR_SPAN = re.compile(r"(\[)((?:[^\[\]]|\[[^\]]*\])*)(\]\{[^}]*\})")
TRACKED = re.compile(r"\{\.(insertion|deletion)\b")
AUTHOR = re.compile(r'\bauthor="([^"]*)"')
CITATION = re.compile(r"\[-?@[^\]]+\]|(?<![\w\[])-?@[\w:.#$%&+?<>~/-]+")


def md_styles(line: str) -> list[set[str]]:
    """The style classes of every character of a Markdown line."""
    styles: list[set[str]] = [set() for _ in line]
    protected = [False] * len(line)  # inside code: no further styling

    def mark(start: int, end: int, cls: str) -> None:
        for k in range(start, end):
            if not protected[k]:
                styles[k].add(cls)

    if m := HEADING.match(line):
        mark(0, m.end(), "syn")
        mark(m.end(), len(line), f"h{len(m[1])}")
    elif m := QUOTE.match(line):
        mark(0, m.end(), "syn")
        mark(m.end(), len(line), "quote")

    for m in CODE.finditer(line):
        mark(m.start(1), m.end(1), "syn")
        mark(m.start(2), m.end(2), "code")
        mark(m.start(0) + len(m[1]) + len(m[2]), m.end(0), "syn")
        for k in range(m.start(0), m.end(0)):
            protected[k] = True

    for m in IMAGE_OR_LINK.finditer(line):
        mark(m.start(1), m.end(1), "syn")
        mark(m.start(2), m.end(2), "link")
        mark(m.start(3), m.end(3), "syn")
    for m in ATTR_SPAN.finditer(line):
        if any("syn" in styles[k] for k in range(m.start(), m.end())):
            continue  # already a link
        mark(m.start(1), m.end(1), "syn")
        mark(m.start(3), m.end(3), "syn")
        if tracked := TRACKED.match(m[3], 1):
            author, date = AUTHOR.search(m[3]), DATE_ATTRIBUTE.search(m[3])
            for style in change_marks(
                tracked[1], author[1] if author else "", date[1] if date else ""
            ):
                mark(m.start(2), m.end(2), style)
    for m in CITATION.finditer(line):
        mark(m.start(), m.end(), "cite")
    for pattern, cls in ((STRONG, "strong"), (EMPH, "em")):
        for m in pattern.finditer(line):
            if protected[m.start()]:
                continue
            mark(m.start(1), m.end(1), "syn")
            mark(m.start(2), m.end(2), cls)
            mark(m.end(2), m.end(0), "syn")
    return styles


def styled(text: str, styles: list[set[str]] | None) -> Markup:
    """text, escaped, cut into runs of equal style, each in a <span>: its
    classes, and its values ("@name=value") as data- attributes."""
    if not styles:
        return escape(text)
    out = []
    for start, end in runs(styles[: len(text)]):
        piece = escape(text[start:end])
        if styles[start]:
            cls = " ".join(f"s-{c}" for c in sorted(styles[start]) if c[0] != "@")
            data = Markup("").join(
                Markup(' data-{}="{}"').format(*c[1:].split("=", 1))
                for c in sorted(styles[start])
                if c[0] == "@"
            )
            out.append(Markup('<span class="{}"{}>{}</span>').format(cls, data, piece))
        else:
            out.append(piece)
    return Markup("").join(out)
