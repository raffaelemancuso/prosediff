"""Markdown prose split into sentences, for comparing sentence by sentence.

A paragraph is often one long line (a Word document's paragraphs, or
text written that way), so a line-by-line comparison pairs whole paragraphs:
a sentence moved from one paragraph to another is not recognised, and a long
paragraph is one row. Split into sentences, each is a line of its own.

Each line of the file is split on its own, so every sentence keeps the
number of the line it came from: "12" for a line of one sentence, "12.1",
"12.2", ... for the sentences of a longer one. Only prose is split: fenced
and indented code, YAML front matter, tables (pipe tables, and the tables of
dashes pandoc writes), headings, block quotes and blank lines are kept as
they are; a list item keeps its marker on its first sentence.

Sentence boundaries come from yasbd, a rule-based detector that knows
abbreviations, initials, decimals and quotations, for some forty languages.
For a language it does not know, a simple rule stands in: a full stop,
question or exclamation mark followed by a space and a capital letter.
"""

import re
from functools import cache

from yasbd import BoundaryDetector
from yasbd.exceptions import UnsupportedLanguageError

from prosediff.document import BULLET, Line, concat

# The paragraphs of a document that are not prose to split: headings and
# table rows.
UNSPLIT = {"heading", "row"}
FENCE = re.compile(r"^\s*(```|~~~)")
BLANK = re.compile(r"^\s*$")
HEADING = re.compile(r"^\s{0,3}#")
TABLE = re.compile(r"^\s*\|")
# Pandoc writes Word tables as simple or multiline tables, held in place by
# rules of dashes; such a rule also underlines a setext heading.
RULE = re.compile(r"^\s*([-=])\1{2,}[-=\s]*$")
QUOTE = re.compile(r"^\s*>")
INDENTED_CODE = re.compile(r"^ {4,}\S")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)")
FOOTNOTE_REF = re.compile(r"\[\^[^\]]*\]")
# A footnote reference or a closing quote or bracket rides along with the
# sentence it follows: "...the library.[^4] We..." breaks after the marker.
ATTACHED = re.compile(r"[\"'”’)\]]*(?:\[\^[^\]]*\])?[\"'”’)\]]*")
GAP = re.compile(r"[ \t]+")
# The stand-in rule, for languages yasbd does not know.
SIMPLE_BOUNDARY = re.compile(r"[.!?]+(?=\s+[\"'“‘(\[]?[A-ZÀ-ɏ])")


class SimpleDetector:
    """Boundaries after ., ! or ? followed by a space and a capital."""

    def detect(self, text: str):
        for m in SIMPLE_BOUNDARY.finditer(text):
            yield m.end()


@cache
def detector(language: str):
    """yasbd's detector for the language (or, for a regional variant such
    as pt-br, for its language), or the stand-in rule."""
    for lang in dict.fromkeys((language, language.split("-")[0])):
        try:
            return BoundaryDetector(lang=lang, preserve_quote_and_paren=False)
        except UnsupportedLanguageError:
            pass
    return SimpleDetector()


def is_supported(language: str) -> bool:
    return not isinstance(detector(language), SimpleDetector)


def _without_footnotes(text: str) -> tuple[str, list[int]]:
    """The text without footnote markers, and a map back to its offsets.

    A marker glued to the full stop ("...the library.[^4] We...") would hide
    the boundary from the detector, which would see the bracket, not the
    space.
    """
    kept, index_map, pos = [], [], 0
    for marker in FOOTNOTE_REF.finditer(text):
        kept.append(text[pos : marker.start()])
        index_map.extend(range(pos, marker.start()))
        pos = marker.end()
    kept.append(text[pos:])
    index_map.extend(range(pos, len(text)))
    index_map.append(len(text))
    return "".join(kept), index_map


def sentence_spans(text: str, language: str = "en") -> list[tuple[int, int]]:
    """Where each sentence of one line of prose starts and ends.

    A break is only taken where whitespace follows (a footnote marker or a
    closing quote may come first), so nothing is cut inside a word or a link.
    """
    detected, index_map = _without_footnotes(text)
    out, start = [], 0
    for offset in detector(language).detect(detected):
        offset = index_map[offset]
        end = offset + len(ATTACHED.match(text, offset).group(0))
        gap = GAP.match(text, end)
        if gap is None or end <= start:
            continue
        out.append((start, end))
        start = gap.end()
    if start < len(text):
        out.append((start, len(text)))
    return [(a, b) for a, b in out if text[a:b].strip()] or [(0, len(text))]


def sentences(text: str, language: str = "en") -> list[str]:
    """The sentences of one line of prose (sentence_spans)."""
    return [text[a:b] for a, b in sentence_spans(text, language)]


def _is_special(line: str) -> bool:
    return bool(
        BLANK.match(line)
        or FENCE.match(line)
        or HEADING.match(line)
        or TABLE.match(line)
        or RULE.match(line)
        or QUOTE.match(line)
        or INDENTED_CODE.match(line)
    )


def _rule_block_end(lines: list[str], start: int) -> int:
    """Index just past the dash-ruled table that opens at start (a lone rule,
    a setext underline or a thematic break, ends at itself)."""
    last_rule, blanks, i = start, 0, start
    while i < len(lines):
        if RULE.match(lines[i]):
            last_rule, blanks = i, 0
        elif not lines[i].strip():
            blanks += 1
            if blanks >= 2:
                break
        else:
            blanks = 0
            if i - last_rule > 40:
                break
        i += 1
    return last_rule + 1


def split_sentences(
    lines: list[str], language: str = "en", line_languages: list[str | None] | None = None
) -> tuple[list[str], list[str]]:
    """The lines with their prose split into sentences, and each new line's
    label: the number of the line it came from, plus its place among the
    sentences of that line when there are several ("12.3"). Each line is
    split by the rules of its language in line_languages, when it has one,
    else by those of language.

    A line of a Word or OpenDocument text (a Line) is split by the rules of
    its paragraph's language, when marked, and its styles are split with
    it; being no Markdown, it is never taken for code, a table or a heading
    because of how it starts: its kind says what it is.
    """
    out: list[str] = []
    labels: list[str] = []

    def keep(i: int) -> None:
        out.append(lines[i])
        labels.append(str(i + 1))

    i, n, in_fence = 0, len(lines), False
    # A YAML metadata block is kept as it is, delimiters included.
    if lines and lines[0].strip() == "---":
        keep(0)
        i = 1
        while i < n and lines[i].strip() not in ("---", "..."):
            keep(i)
            i += 1
        if i < n:
            keep(i)
            i += 1
    while i < n:
        line = lines[i]
        if isinstance(line, Line):
            if line.kind in UNSPLIT:
                keep(i)
            else:
                prefix = BULLET if line.kind == "item" else ""
                parts = sentence_spans(str(line)[len(prefix) :], line.lang or language)
                if len(parts) == 1:
                    keep(i)
                else:
                    for k, (a, b) in enumerate(parts):
                        lead = line.cut(0, len(prefix)) if k == 0 else " " * len(prefix)
                        out.append(concat(lead, line.cut(len(prefix) + a, len(prefix) + b)))
                        labels.append(f"{i + 1}.{k + 1}")
            i += 1
        elif FENCE.match(line):
            in_fence = not in_fence
            keep(i)
            i += 1
        elif not in_fence and RULE.match(line):
            for k in range(i, _rule_block_end(lines, i)):
                keep(k)
            i = _rule_block_end(lines, i)
        elif in_fence or _is_special(line):
            keep(i)
            i += 1
        else:
            item = LIST_ITEM.match(line)
            prefix = item.group(0) if item else ""
            indent = " " * len(prefix)
            rules = (line_languages[i] if line_languages else None) or language
            parts = sentences(line[len(prefix) :], rules)
            if len(parts) == 1:
                keep(i)
            else:
                for k, part in enumerate(parts):
                    out.append((prefix if k == 0 else indent) + part)
                    labels.append(f"{i + 1}.{k + 1}")
            i += 1
    return out, labels
