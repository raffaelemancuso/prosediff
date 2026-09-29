"""Pandoc comment spans: folded into markers, and listed in a panel.

pandoc --track-changes=all writes a Word comment as
[note]{.comment-start id=... author="A" date=...}anchor[]{.comment-end id=...}.
Folding replaces each comment-start span with one character of the Unicode
private use area standing for (author, note), and each comment-end with
another, of the supplementary private use area B (so no pattern for the
first, nor the footnotes' stand-ins, finds it), standing for the same comment: the comment is then
compared like a word, the same comment matches on both sides even when a
new conversion renumbered its id, and a filter cannot cut it in two. The
characters become markers, and the end an empty span the HTML report
highlights the anchor up to, when the HTML report is built.
"""

import json
import re
from dataclasses import dataclass

from markupsafe import Markup

from prosediff.document import DATE_ATTRIBUTE, Rich, short_date, spaced

PUA_FIRST, PUA_LAST = 0xE000, 0xF8FF
PLACEHOLDER = re.compile(f"[{chr(PUA_FIRST)}-{chr(PUA_LAST)}]")
# Where the text a comment is anchored to ends: the comment's placeholder
# moved to the supplementary private use area B, the same distance in (area
# A holds the footnotes' stand-ins).
END_FIRST = 0x100000
END_PLACEHOLDER = re.compile(f"[{chr(END_FIRST)}-{chr(END_FIRST + PUA_LAST - PUA_FIRST)}]")
# Either: what is a comment's, not text.
ANY_PLACEHOLDER = re.compile(f"{PLACEHOLDER.pattern}|{END_PLACEHOLDER.pattern}")
# A comment's marker.
COMMENT_MARK = "\N{SPEECH BALLOON}"


def balloon(kind: str, sign: str) -> Markup:
    """A comment's balloon holding a sign, one glyph (no character draws it;
    a squared NEW is unreadable at the size of the text)."""
    return Markup(
        f'<svg class="{kind}-comment" viewBox="0 0 16 16" aria-hidden="true">'
        '<path d="M3 1.5h10A1.5 1.5 0 0 1 14.5 3v7a1.5 1.5 0 0 1-1.5 1.5H7.2L3.5 '
        '14.5v-3H3A1.5 1.5 0 0 1 1.5 10V3A1.5 1.5 0 0 1 3 1.5Z"/>'
        f'<path class="sign" d="{sign}"/></svg>'
    )


# One only the new side has, added since the base: a green balloon with a +
# in it; one only the old side has, removed since the base: a red one with a -.
NEW_COMMENT_MARK = balloon("new", "M8 3.8v5.4M5.3 6.5h5.4")
REMOVED_COMMENT_MARK = balloon("removed", "M5.3 6.5h5.4")
ICONS = {"new": NEW_COMMENT_MARK, "removed": REMOVED_COMMENT_MARK}

COMMENT_CLASS = re.compile(r"^\{\s*\.(comment-start|comment-end)\b")
AUTHOR = re.compile(r'\bauthor="((?:[^"\\]|\\.)*)"')
ID = re.compile(r'\bid="([^"]*)"')
ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|'\"<>~^$])")


def rich_json(rich: Rich) -> str:
    """A comment's paragraphs for the HTML report's script: a list of
    paragraphs, each of [text, "styles"] runs; "" when it has none."""
    return json.dumps([[[t, " ".join(s)] for t, s in p] for p in rich], ensure_ascii=False)


@dataclass
class Comment:
    author: str
    text: str
    date: str = ""  # "YYYY-MM-DD HH:MM", as Word stamped it
    # its paragraphs as written (document.comment_runs), to be shown
    rich: Rich = ()

    @property
    def label(self) -> str:
        """The comment in one line, for screen readers."""
        who = f"comment by {self.author}" if self.author else "comment"
        when = f", {self.date}" if self.date else ""
        return f"{who}{when}: {self.text or '(no text)'}"


@dataclass
class CommentEntry:
    """One comment of one file, added, removed or kept, and where it is."""

    author: str
    text: str
    status: str  # "new", "removed" or "unchanged"
    path: str
    anchor: str  # id of the row it sits in
    line: int | None
    date: str = ""
    label: str = ""  # the line as the gutter shows it ("12.3")


class Comments:
    """The comments folded out of a comparison, each behind one placeholder.

    A comment is identified by its author and text, not by its id: the ids
    are renumbered each time a document is saved or read, and the same comment
    must compare equal on both sides.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], str] = {}
        self._items: list[Comment] = []

    def placeholder(self, author: str, text: str, date: str = "", rich: Rich = ()) -> str | None:
        key = (author, text)
        if key not in self._by_key:
            if PUA_FIRST + len(self._items) > PUA_LAST:
                return None  # out of placeholders: leave the span as it is
            self._by_key[key] = chr(PUA_FIRST + len(self._items))
            self._items.append(Comment(author, text, date, rich))
        return self._by_key[key]

    def get(self, placeholder: str) -> Comment:
        return self._items[ord(placeholder) - PUA_FIRST]

    def __len__(self) -> int:
        return len(self._items)


def end_of(placeholder: str) -> str:
    """The character marking where the text of the comment behind
    placeholder ends."""
    return chr(END_FIRST + ord(placeholder) - PUA_FIRST)


def number_of(mark: str) -> int:
    """The comment a placeholder, or the end of its text, stands for: its
    number among the comments."""
    n = ord(mark)
    return n - END_FIRST if n >= END_FIRST else n - PUA_FIRST


def match_bracket(s: str, i: int) -> int:
    """Index of the ']' closing the '[' at s[i], or -1."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def match_attrs(s: str, i: int) -> int:
    """Index of the '}' closing the attribute block opened at s[i], or -1.

    The block may span lines, but not a blank line.
    """
    in_quote = False
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif c == "}" and not in_quote:
            return j
        elif c == "\n" and not in_quote and s[j + 1 : j + 2] == "\n":
            return -1
        j += 1
    return -1


def fold_comments(text: str, comments: Comments, keep_empty: bool = False) -> str:
    """Replace the pandoc comment spans of a Markdown text with placeholders,
    and the ends of their text with theirs (end_of).

    A comment without text is left out, unless keep_empty, and the end of
    its text with it.
    """
    out = []
    # a comment-start's id -> its placeholder, for its comment-end
    started: dict[str, str] = {}
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            out.append(text[i : i + 2])
            i += 2
            continue
        if c == "[":
            close = match_bracket(text, i)
            if close != -1 and text[close + 1 : close + 2] == "{":
                end = match_attrs(text, close + 1)
                attrs = text[close + 1 : end + 1] if end != -1 else ""
                m = COMMENT_CLASS.match(attrs)
                if m:
                    if m[1] == "comment-end":
                        cid = ID.search(attrs)
                        mark = started.pop(cid[1], None) if cid else None
                        if mark is not None:
                            out.append(end_of(mark))
                        i = end + 1
                        continue
                    author = AUTHOR.search(attrs)
                    date = DATE_ATTRIBUTE.search(attrs)
                    # Markdown escapes (\[ \* \_ ...) are not part of the comment
                    note = ESCAPE.sub(r"\1", spaced(text[i + 1 : close]))
                    if not note and not keep_empty:
                        # a comment with no text says nothing: left out
                        i = end + 1
                        continue
                    mark = comments.placeholder(
                        author[1] if author else "",
                        note,
                        short_date(date[1]) if date else "",
                    )
                    if mark is not None:
                        if cid := ID.search(attrs):
                            started[cid[1]] = mark
                        out.append(mark)
                        i = end + 1
                        continue
        out.append(c)
        i += 1
    return "".join(out)


def plain(text: str) -> str:
    """Text for a tooltip: each folded comment shown as a speech balloon, the
    ends of their text left out."""
    return PLACEHOLDER.sub(COMMENT_MARK, END_PLACEHOLDER.sub("", text))


MARKER = Markup(
    '<span class="comment{}" tabindex="0" role="note" data-c="{}" data-author="{}" '
    'data-date="{}" data-text="{}"{} aria-label="{}">{}</span>'
)
# A comment's paragraphs as written, for the tooltip (rich_json).
RICH_ATTRIBUTE = Markup(' data-rich="{}"')
# Where the text a comment is anchored to ends: nothing to see, a place the
# HTML report highlights the text up to.
END_MARKER = Markup('<span class="comment-end" data-c="{}"></span>')


def show_comments(
    markup: Markup,
    comments: Comments,
    new: frozenset[str] = frozenset(),
    removed: frozenset[str] = frozenset(),
) -> Markup:
    """Replace the placeholders of a cell with comment markers.

    The HTML report shows the author, the comment and its date when a marker is
    hovered or focused. The comments in new (placeholders) were added since
    the base, those in removed were removed since, and each gets its own
    icon. The end of a comment's text becomes an empty span, of the same
    number (data-c) as its marker. Tooltips never hold placeholders (see
    plain), so every one left in the markup is in the text of the line.
    """

    def marker(m: re.Match) -> str:
        c = comments.get(m[0])
        status = "new" if m[0] in new else "removed" if m[0] in removed else ""
        icon = ICONS.get(status, COMMENT_MARK)
        label = f"{status} {c.label}" if status else c.label
        return str(
            MARKER.format(
                f" {status}" if status else "",
                number_of(m[0]),
                c.author,
                c.date,
                c.text,
                RICH_ATTRIBUTE.format(rich_json(c.rich)) if c.rich else "",
                label,
                icon,
            )
        )

    def end(m: re.Match) -> str:
        return str(END_MARKER.format(number_of(m[0])))

    return Markup(END_PLACEHOLDER.sub(end, PLACEHOLDER.sub(marker, str(markup))))


def placeholders_of(lines: list[str]) -> set[str]:
    """The comment placeholders of lines."""
    return set(placeholders_in("\n".join(lines)))


def placeholders_in(markup: Markup | str) -> list[str]:
    return PLACEHOLDER.findall(str(markup))
