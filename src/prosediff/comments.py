"""Pandoc comment spans: folded into markers, and listed in a panel.

pandoc --track-changes=all writes a Word comment as
[note]{.comment-start id=... author="A" date=...}anchor[]{.comment-end id=...}.
Folding replaces each comment-start span with one character of the Unicode
private use area standing for (author, note), and drops comment-end: the
comment is then compared like a word, the same comment matches on both sides
even when a new conversion renumbered its id, and a filter cannot cut it in
two. The characters become markers when the page is built.
"""

import re
from dataclasses import dataclass

from markupsafe import Markup

PUA_FIRST, PUA_LAST = 0xE000, 0xF8FF
PLACEHOLDER = re.compile(f"[{chr(PUA_FIRST)}-{chr(PUA_LAST)}]")
COMMENT_MARK = "\N{SPEECH BALLOON}"
# A comment only the new side has: added since the base.
NEW_COMMENT_MARK = "\N{SQUARED NEW}"

COMMENT_CLASS = re.compile(r"^\{\s*\.(comment-start|comment-end)\b")
AUTHOR = re.compile(r'\bauthor="((?:[^"\\]|\\.)*)"')
DATE = re.compile(r'\bdate="(\d{4}-\d\d-\d\d)T(\d\d:\d\d)')
ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|'\"<>~^$])")


@dataclass
class Comment:
    author: str
    text: str
    date: str = ""  # "YYYY-MM-DD HH:MM", as Word stamped it

    @property
    def label(self) -> str:
        """The comment in one line, for screen readers."""
        who = f"comment by {self.author}" if self.author else "comment"
        when = f", {self.date}" if self.date else ""
        return f"{who}{when}: {self.text or '(no text)'}"


@dataclass
class CommentEntry:
    """One comment of one file, for the comments panel."""

    author: str
    text: str
    status: str  # "new", "removed" or "unchanged"
    path: str
    anchor: str  # id of the row it sits in
    line: int | None
    date: str = ""
    label: str = ""  # the line as the gutter shows it ("12.3")

    @property
    def icon(self) -> str:
        return NEW_COMMENT_MARK if self.status == "new" else COMMENT_MARK


class Comments:
    """The comments folded out of a comparison, each behind one placeholder.

    A comment is identified by its author and text, not by its id: the ids
    are renumbered each time a document is converted, and the same comment
    must compare equal on both sides.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], str] = {}
        self._items: list[Comment] = []

    def placeholder(self, author: str, text: str, date: str = "") -> str | None:
        key = (author, text)
        if key not in self._by_key:
            if PUA_FIRST + len(self._items) > PUA_LAST:
                return None  # out of placeholders: leave the span as it is
            self._by_key[key] = chr(PUA_FIRST + len(self._items))
            self._items.append(Comment(author, text, date))
        return self._by_key[key]

    def get(self, placeholder: str) -> Comment:
        return self._items[ord(placeholder) - PUA_FIRST]

    def __len__(self) -> int:
        return len(self._items)


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
    """Replace the pandoc comment spans of a Markdown text with placeholders.

    A comment without text is left out, unless keep_empty.
    """
    out = []
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
                        i = end + 1
                        continue
                    author = AUTHOR.search(attrs)
                    date = DATE.search(attrs)
                    # Markdown escapes (\[ \* \_ ...) are not part of the comment
                    note = ESCAPE.sub(r"\1", " ".join(text[i + 1 : close].split()))
                    if not note and not keep_empty:
                        # a comment with no text says nothing: left out
                        i = end + 1
                        continue
                    mark = comments.placeholder(
                        author[1] if author else "",
                        note,
                        f"{date[1]} {date[2]}" if date else "",
                    )
                    if mark is not None:
                        out.append(mark)
                        i = end + 1
                        continue
        out.append(c)
        i += 1
    return "".join(out)


def plain(text: str) -> str:
    """Text for a tooltip: each folded comment shown as a speech balloon."""
    return PLACEHOLDER.sub(COMMENT_MARK, text)


MARKER = Markup(
    '<span class="comment{}" tabindex="0" role="note" data-author="{}" data-date="{}" '
    'data-text="{}" aria-label="{}">{}</span>'
)


def show_comments(markup: Markup, comments: Comments, new: frozenset[str] = frozenset()) -> Markup:
    """Replace the placeholders of a cell with comment markers.

    The page shows the author, the comment and its date when a marker is
    hovered or focused. The comments in new (placeholders) were added since
    the base and get their own icon. Tooltips never hold placeholders (see
    plain), so every one left in the markup is in the text of the line.
    """

    def marker(m: re.Match) -> str:
        c = comments.get(m[0])
        is_new = m[0] in new
        label = ("new " if is_new else "") + c.label
        icon = NEW_COMMENT_MARK if is_new else COMMENT_MARK
        return str(MARKER.format(" new" if is_new else "", c.author, c.date, c.text, label, icon))

    return Markup(PLACEHOLDER.sub(marker, str(markup)))


def placeholders_in(markup: Markup | str) -> list[str]:
    return PLACEHOLDER.findall(str(markup))
