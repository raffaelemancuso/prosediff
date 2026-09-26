"""Footnote numbers set aside when comparing.

Markdown numbers footnotes, [^1], [^2], ...: in the text, where one is
referenced, and at the start of its definition, [^1]: text. Delete a
footnote and every later one is renumbered, so compared as written, each of
them, and each reference to it, would show as changed although only its
number is.

So before comparing, the footnotes of the two versions are matched by their
text (identical texts first, then the most similar pairs, above a
threshold), and every number is replaced by a stand-in: one character of the
Unicode supplementary private use area, the same on both sides for a
matched pair, its own for a footnote the other side lacks. A renumbered
footnote then compares equal; a reference to a footnote added or deleted
still differs. When the page is built, each side gets its own numbers back.
"""

import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from markupsafe import Markup, escape

from prosediff.document import sub

# Supplementary Private Use Area-A: apart from the comment placeholders,
# which use the Private Use Area of the Basic Multilingual Plane.
FIRST = 0xF0000
LAST = 0xFFFFD
STAND_IN = re.compile(f"[{chr(FIRST)}-{chr(LAST)}]")
DEFINITION = re.compile(r"^\[\^([^\]\s]+)\]:")
REFERENCE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
# Two footnotes whose texts are at least this similar are the same footnote.
FOOTNOTE_SIMILARITY = 0.5

# The labels of the comparison under way, for the tooltips: a change is
# described with the footnote's number rather than its stand-in.
_labels: ContextVar[dict[str, str] | None] = ContextVar("footnote_labels", default=None)


@dataclass
class Footnotes:
    """What each stand-in stands for, on each side."""

    old: dict[str, str] = field(default_factory=dict)  # stand-in -> label
    new: dict[str, str] = field(default_factory=dict)

    def label(self, stand_in: str) -> str:
        """The label a tooltip shows: the new number, or the old one."""
        return self.new.get(stand_in) or self.old.get(stand_in) or "?"


def _definitions(lines: list[str]) -> dict[str, str]:
    """label -> text of each footnote defined in the lines."""
    found = {}
    for line in lines:
        if m := DEFINITION.match(line):
            found.setdefault(m[1], " ".join(line[m.end() :].split()))
    return found


def _labels_in(lines: list[str]) -> list[str]:
    """Every footnote label of the lines, defined or referenced, in order."""
    seen: dict[str, None] = {}
    for line in lines:
        if m := DEFINITION.match(line):
            seen.setdefault(m[1])
        for m in REFERENCE.finditer(line):
            seen.setdefault(m[1])
    return list(seen)


def match_footnotes(
    old: dict[str, str], new: dict[str, str], similarity: Callable[[str, str], float]
) -> dict[str, str]:
    """old label -> new label of the footnotes that are the same footnote:
    identical texts first, then the most similar pairs above the threshold."""
    matched: dict[str, str] = {}
    by_text: dict[str, list[str]] = {}
    for label, text in new.items():
        by_text.setdefault(text, []).append(label)
    for label, text in old.items():
        if by_text.get(text):
            matched[label] = by_text[text].pop(0)
    rest_old = [k for k in old if k not in matched]
    taken = set(matched.values())
    rest_new = [k for k in new if k not in taken]
    candidates = []
    for a in rest_old:
        for b in rest_new:
            s = similarity(old[a], new[b])
            if s >= FOOTNOTE_SIMILARITY:
                candidates.append((s, a, b))
    for _, a, b in sorted(candidates, key=lambda c: (-c[0], rest_old.index(c[1]), c[2])):
        if a not in matched and b not in taken:
            matched[a] = b
            taken.add(b)
    return matched


def set_aside(
    old: list[str], new: list[str], similarity: Callable[[str, str], float]
) -> tuple[list[str], list[str], Footnotes]:
    """Both versions with their footnote numbers replaced by stand-ins, and
    what the stand-ins stand for. Files without footnotes come back as they
    are."""
    old_labels, new_labels = _labels_in(old), _labels_in(new)
    if not old_labels and not new_labels:
        return old, new, Footnotes()
    old_defs, new_defs = _definitions(old), _definitions(new)
    matched = match_footnotes(old_defs, new_defs, similarity)
    # A footnote defined on neither side (a reference alone) keeps its label.
    taken = set(matched.values())
    for label in old_labels:
        if (
            label not in matched
            and label in new_labels
            and label not in taken
            and label not in old_defs
            and label not in new_defs
        ):
            matched[label] = label
            taken.add(label)
    notes = Footnotes()
    old_map: dict[str, str] = {}
    new_map: dict[str, str] = {}
    code = FIRST

    def next_stand_in() -> str:
        nonlocal code
        if code > LAST:
            raise OverflowError("too many footnotes")
        code += 1
        return chr(code - 1)

    for label in old_labels:
        s = next_stand_in()
        old_map[label] = s
        notes.old[s] = label
        if label in matched:
            new_map[matched[label]] = s
            notes.new[s] = matched[label]
    for label in new_labels:
        if label not in new_map:
            s = next_stand_in()
            new_map[label] = s
            notes.new[s] = label

    def replace(lines: list[str], mapping: dict[str, str]) -> list[str]:
        out = []
        for line in lines:
            line = sub(DEFINITION, lambda m: mapping.get(m[1], m[0][:-1]) + ":", line, count=1)
            out.append(sub(REFERENCE, lambda m: mapping.get(m[1], m[0]), line))
        return out

    return replace(old, old_map), replace(new, new_map), notes


def restore(markup: Markup, labels: dict[str, str]) -> Markup:
    """A side's markup with its own footnote numbers back."""
    if not STAND_IN.search(str(markup)):
        return markup
    return Markup(STAND_IN.sub(lambda m: str(escape(f"[^{labels.get(m[0], '?')}]")), str(markup)))


def use_for_tooltips(notes: Footnotes | None):
    """Name footnotes by their numbers in the change descriptions to come;
    returns a token for reset_tooltips."""
    return _labels.set(None if notes is None else {**notes.old, **notes.new})


def reset_tooltips(token) -> None:
    _labels.reset(token)


def plain(text: str) -> str:
    """Text for a tooltip: each stand-in shown as its footnote's number."""
    labels = _labels.get()
    if not labels or not STAND_IN.search(text):
        return text
    return STAND_IN.sub(lambda m: f"[^{labels.get(m[0], '?')}]", text)
