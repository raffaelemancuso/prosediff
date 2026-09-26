"""Soft hyphens in the prose of the page, by its language's rules.

CSS can ask the browser to hyphenate (hyphens: auto), but a browser does so
only where it has that language's dictionary: Chrome and Edge fetch theirs
into the user's profile on demand, so a page could print hyphenated on one
machine and not on another. Soft hyphens (U+00AD) are honoured everywhere:
invisible, they become a hyphen only where a line breaks at them. They are
placed by pyphen, which carries the hyphenation patterns of LibreOffice for
some eighty languages.

A cell is HTML: the changed part of a word may sit in its own element
("regu<ins>lation</ins>"). The words are therefore found in the cell's text,
across its tags, and their soft hyphens put back in the markup, so a word is
hyphenated as a whole wherever its changes fall.
"""

import html
import re
from functools import cache

import pyphen
from markupsafe import Markup

SOFT_HYPHEN = "\u00ad"
# Shorter words are not worth a break.
MIN_WORD = 6
# A tag, an entity, or one character of text.
UNIT = re.compile(r"<[^>]*>|&(?:#\d+|#x[0-9a-fA-F]+|\w+);|.", re.S)
WORD = re.compile(rf"[^\W\d_]{{{MIN_WORD},}}")


@cache
def _dictionary(language: str) -> pyphen.Pyphen | None:
    name = pyphen.language_fallback(language.replace("-", "_"))
    # left=2, right=3: no break leaves a lone letter at a word's end.
    return pyphen.Pyphen(lang=name, left=2, right=3) if name else None


def can_hyphenate(language: str) -> bool:
    return bool(language) and _dictionary(language) is not None


@cache
def _breaks(language: str, word: str) -> tuple[int, ...]:
    dictionary = _dictionary(language)
    return tuple(dictionary.positions(word)) if dictionary else ()


def hyphenate(markup: str, language: str) -> Markup:
    """The HTML with soft hyphens where its words may break; unchanged when
    the language is unknown or has no patterns."""
    markup = Markup(markup)
    if not markup or not can_hyphenate(language):
        return markup
    units = UNIT.findall(markup)
    # The text, one character per text unit; tags stand as a character no
    # word contains, so a word does not run across <br> and the like.
    text, at = [], []
    for i, unit in enumerate(units):
        if unit.startswith("<"):
            if not re.match(r"</?(del|ins|mark|span|em|strong)\b", unit):
                text.append("\n")
                at.append(i)
            continue
        char = html.unescape(unit) if unit.startswith("&") else unit
        text.append(char if len(char) == 1 else " ")
        at.append(i)
    plain = "".join(text)
    before: set[int] = set()
    for m in WORD.finditer(plain):
        for offset in _breaks(language, m.group()):
            before.add(at[m.start() + offset])
    if not before:
        return markup
    return Markup("".join(SOFT_HYPHEN + u if i in before else u for i, u in enumerate(units)))
