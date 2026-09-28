"""A word-processor document as prosediff reads it: paragraphs of styled text.

prosediff.word and prosediff.odt read a Word document or an OpenDocument
text into a Document: its blocks (paragraphs, headings, list items, tables)
and its footnotes, each made of inlines: runs of text, each with its styles
(bold, italic, underline, ...), links and tracked changes around them,
comments, footnote references and images.

From it, prosediff builds the lines it compares (lines()): the text a reader
sees, with the styles of each character, the language and the kind of the
paragraph beside it, and no Markdown syntax in it; and the Markdown git's
own commands show (to_markdown()), for --to-markdown and git diff.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# The styles a run of text can have, as classes of the HTML report: s-strong, ...
STRONG, EM, UNDERLINE, STRIKE, SUP, SUB = "strong", "em", "u", "strike", "sup", "sub"
# A date, and its time to the minute if it has one (short_date); and the
# date="..." attribute of a pandoc span, a comment or a tracked change.
SHORT_DATE = re.compile(r"(\d{4}-\d\d-\d\d)(?:T(\d\d:\d\d))?")
DATE_ATTRIBUTE = re.compile(r'\bdate="([^"]*)"')
# What a list item starts with in the HTML report.
BULLET = "\u2022 "


@dataclass
class Text:
    """A run of text in one style."""

    text: str
    styles: frozenset[str] = frozenset()


@dataclass
class Span:
    """Inlines around which a link or a tracked change kept as markup sits:
    kind is "link", "insertion" or "deletion"."""

    kind: str
    children: list = field(default_factory=list)
    target: str = ""  # a link's address
    author: str = ""  # a tracked change's
    date: str = ""


@dataclass
class CommentMark:
    """Where a comment starts."""

    id: str
    author: str
    text: str
    date: str = ""


@dataclass
class CommentEnd:
    """Where the text a comment is anchored to ends: the id of its
    CommentMark, in the same document."""

    id: str


@dataclass
class NoteRef:
    """A reference to a footnote or endnote: its number among them."""

    number: int


@dataclass
class Image:
    """An image, as it is written: [image] or [image: its description]."""

    text: str

    @classmethod
    def described(cls, description: str | None) -> "Image":
        return cls(f"[image: {description}]" if description else "[image]")


Inline = Text | Span | CommentMark | CommentEnd | NoteRef | Image


@dataclass
class Block:
    """A paragraph ("p"), a heading ("heading", with its level), a list item
    ("item"), a table ("table": rows of cells of inlines) or a footnote
    ("note", with its number)."""

    kind: str
    inlines: list = field(default_factory=list)
    level: int = 0
    rows: list[list[list]] = field(default_factory=list)
    number: int = 0
    language: str | None = None


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    notes: list[Block] = field(default_factory=list)
    # The language most of its letters are marked with.
    language: str | None = None


# Inlines ------------------------------------------------------------------------


def is_blank(inlines: Iterable) -> bool:
    """Whether inlines hold no text (comments alone count as none)."""
    return not any(
        (isinstance(i, Text) and i.text.strip())
        or isinstance(i, (NoteRef, Image))
        or (isinstance(i, Span) and not is_blank(i.children))
        for i in inlines
    )


def comments_only(inlines: list) -> bool:
    """Whether inlines are comments and nothing else: a paragraph deleted as
    a whole keeps them, for the next paragraph."""
    return any(isinstance(i, CommentMark | CommentEnd) for i in inlines) and is_blank(inlines)


def comments_in(inlines: Iterable) -> list[CommentMark | CommentEnd]:
    """The comments of inlines, where they start and where their text ends."""
    out = []
    for i in inlines:
        if isinstance(i, CommentMark | CommentEnd):
            out.append(i)
        elif isinstance(i, Span):
            out += comments_in(i.children)
    return out


def strip(inlines: list) -> list:
    """Inlines without the blanks at their edges (runs of text only, as the
    Markdown of a paragraph is stripped)."""
    out = list(inlines)
    while out and isinstance(out[0], Text) and not out[0].text.strip():
        out.pop(0)
    while out and isinstance(out[-1], Text) and not out[-1].text.strip():
        out.pop()
    if out and isinstance(out[0], Text):
        out[0] = Text(out[0].text.lstrip(), out[0].styles)
    if out and isinstance(out[-1], Text):
        out[-1] = Text(out[-1].text.rstrip(), out[-1].styles)
    return out


def join_paragraphs(paragraphs: Iterable[list]) -> list:
    """The inlines of several paragraphs (a table cell's), as one, a space
    between them; paragraphs without text left out."""
    out: list = []
    for inlines in paragraphs:
        if markdown(inlines):
            if out:
                out.append(Text(" "))
            out += inlines
    return out


def attach_carried(blocks: list[Block], carried: list) -> None:
    """Join to the last block the comments carried past the last paragraph
    (those of paragraphs deleted as a whole): to its last table cell, or
    after a table without cells, in a paragraph of their own."""
    if not carried:
        return
    if not blocks:
        blocks.append(Block("p"))
    last = blocks[-1]
    if last.kind == "table" and last.rows and last.rows[-1]:
        last.rows[-1][-1] += carried
    elif last.kind == "table":
        blocks.append(Block("p", carried))
    else:
        last.inlines += carried


@dataclass(kw_only=True)
class DocumentReader:
    """What the Word and OpenDocument readers share: their tracked changes
    settled as changes says ("accept", "reject", or "all", kept as markup);
    the comments of a paragraph deleted as a whole carried to the next; and
    the languages the paragraphs are marked with, when asked for (languages,
    with an of(paragraphs) giving the language most of their letters are in
    and the letters in each), and the letters of the whole in each."""

    changes: str
    languages: object = None
    letters: Counter = field(default_factory=Counter)
    # comments of a paragraph deleted as a whole, for the next paragraph
    carried: list = field(default_factory=list)

    def language_of(self, paragraphs) -> str | None:
        """The language the paragraphs (elements of the XML) are in."""
        if self.languages is None:
            return None
        language, counts = self.languages.of(paragraphs)
        self.letters += counts
        return language

    def keeps(self, kind: str) -> bool:
        """Whether the text of a tracked change ("insertion", "deletion")
        stays: accepted insertions, rejected deletions, all shown."""
        return self.changes == "all" or (self.changes == "accept") == (kind == "insertion")

    def settle_change(self, kind: str, inner: list, author: str = "", date: str = "") -> list:
        """What a tracked change of the inlines inner leaves: them, a span
        marking them (with changes "all"; nothing when they hold no text),
        or, when its text goes, only the comments anchored in it."""
        if not self.keeps(kind):
            return comments_in(inner)
        if self.changes == "all":
            return [Span(kind, inner, author=author, date=date)] if markdown(inner).strip() else []
        return inner

    def carry(self, inlines: list) -> list | None:
        """A paragraph's inlines, the comments carried to it first; None for
        a paragraph of comments alone (its text deleted as a whole), whose
        comments go on to the next paragraph, as Word merges it into it."""
        if comments_only(inlines):
            self.carried += inlines
            return None
        inlines, self.carried = self.carried + inlines, []
        return inlines

    def finish(self, blocks: list[Block]) -> list[Block]:
        """The blocks of the body, the comments carried past the last
        paragraph joined to it."""
        attach_carried(blocks, self.carried)
        self.carried = []
        return blocks


# Markdown -----------------------------------------------------------------------


def spaced(text: str) -> str:
    """The text with each run of whitespace one space, none at its edges."""
    return " ".join(text.split())


def quoted_author(author: str) -> str:
    """An author's name as an attribute's value in double quotes."""
    return author.replace('"', "'")


def change_marks(kind: str, author: str = "", date: str = "") -> set[str]:
    """The styles of text in a tracked change ("insertion", "deletion"):
    tc-ins or tc-del, and who made it and when (@author=, @date=)."""
    marks = {"tc-ins" if kind == "insertion" else "tc-del"}
    if author:
        marks.add(f"@author={author}")
    if when := short_date(date):
        marks.add(f"@date={when}")
    return marks


def comment_markdown(c: CommentMark) -> str:
    """A comment as pandoc writes it: [text]{.comment-start ...}."""
    note = c.text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    author = quoted_author(c.author)
    return f'[{note}]{{.comment-start id="{c.id}" author="{author}" date="{c.date}"}}'


def comment_end_markdown(c: CommentEnd) -> str:
    """The end of a comment's text as pandoc writes it: []{.comment-end ...}."""
    return f'[]{{.comment-end id="{c.id}"}}'


FORMATTING = (STRONG, EM, UNDERLINE, STRIKE, SUP, SUB)


def _wrap(core: str, styles: frozenset[str]) -> str:
    """A run's text in pandoc's Markdown for its styles: [text]{.underline},
    ^superscript^, ~subscript~, ~~struck through~~, *italic*, **bold**."""
    if UNDERLINE in styles:
        core = f"[{core}]{{.underline}}"
    if SUP in styles:
        core = f"^{core}^"
    elif SUB in styles:
        core = f"~{core}~"
    if STRIKE in styles:
        core = f"~~{core}~~"
    marker = ("**" if STRONG in styles else "") + ("*" if EM in styles else "")
    return f"{marker}{core}{marker[::-1]}"


def markdown(inlines: list) -> str:
    """Inlines as Markdown: runs of the same styles wrapped in their markers,
    the blanks at the edges of a run kept outside them."""
    out = []
    k = 0
    while k < len(inlines):
        i = inlines[k]
        if not isinstance(i, Text):
            out.append(_markdown_one(i))
            k += 1
            continue
        key = frozenset(s for s in i.styles if s in FORMATTING)
        text = ""
        while k < len(inlines) and isinstance(inlines[k], Text):
            if frozenset(s for s in inlines[k].styles if s in FORMATTING) != key:
                break
            text += inlines[k].text
            k += 1
        core = text.strip()
        if key and core:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()) :]
            text = f"{lead}{_wrap(core, key)}{trail}"
        out.append(text)
    return "".join(out)


def _markdown_one(i) -> str:
    if isinstance(i, CommentMark):
        return comment_markdown(i)
    if isinstance(i, CommentEnd):
        return comment_end_markdown(i)
    if isinstance(i, NoteRef):
        return f"[^{i.number}]"
    if isinstance(i, Image):
        return i.text
    text = markdown(i.children)
    if i.kind == "link":
        # a link that shows its own address is written once
        return f"[{text}]({i.target})" if i.target and text != i.target else text
    return f'[{text}]{{.{i.kind} author="{quoted_author(i.author)}" date="{i.date}"}}'


def block_markdown(block: Block) -> str:
    if block.kind == "table":
        rows = [
            "| " + " | ".join(markdown(cell).replace("|", "\\|") for cell in row) + " |"
            for row in block.rows
        ]
        if rows:
            rows.insert(1, "|" + "---|" * len(block.rows[0]))
        return "\n".join(rows)
    text = markdown(block.inlines)
    if block.kind == "heading":
        return "#" * block.level + " " + text
    if block.kind == "item":
        return "- " + text
    if block.kind == "note":
        return f"[^{block.number}]: " + text
    return text


def to_markdown(doc: Document) -> str:
    """The document as Markdown: what git diff shows of it, once set up."""
    return "\n\n".join(block_markdown(b) for b in doc.blocks + doc.notes) + "\n"


# Lines ----------------------------------------------------------------------------


class Line(str):
    """A line of a document as prosediff compares it: its text (a str: it is
    compared as one), with the styles of each character, the language and
    the kind of the paragraph it comes from."""

    def __new__(
        cls,
        text: str,
        styles: list[frozenset[str]] | None = None,
        lang: str = "",
        kind: str = "p",
    ):
        line = super().__new__(cls, text)
        line.styles = list(styles) if styles is not None else [frozenset()] * len(text)
        if len(line.styles) != len(text):
            raise ValueError("a style for each character")
        line.lang = lang
        line.kind = kind
        return line

    def cut(self, start: int, end: int) -> "Line":
        """The line from start to end, with its styles."""
        return Line(self[start:end], self.styles[start:end], self.lang, self.kind)

    def replaced(self, text: str, styles: list[frozenset[str]]) -> "Line":
        """Another text of the same paragraph."""
        return Line(text, styles, self.lang, self.kind)


def sub(pattern: re.Pattern, repl: Callable[[re.Match], str], line: str, count: int = 0) -> str:
    """pattern.sub(repl, line), for a Line too: the text put in takes the
    styles of the first character it replaces (none when it replaces none)."""
    if not isinstance(line, Line):
        return pattern.sub(repl, line, count=count)
    text, styles, pos = [], [], 0
    for n, m in enumerate(pattern.finditer(line)):
        if count and n == count:
            break
        text.append(line[pos : m.start()])
        styles += line.styles[pos : m.start()]
        new = repl(m)
        style = line.styles[m.start()] if m.end() > m.start() else frozenset()
        text.append(new)
        styles += [style] * len(new)
        pos = m.end()
    text.append(line[pos:])
    styles += line.styles[pos:]
    return line.replaced("".join(text), styles)


def concat(*parts: str) -> str:
    """Pieces of text and lines as one: a Line when any is one (the plain
    text unstyled, the paragraph that of the first line)."""
    first = next((p for p in parts if isinstance(p, Line)), None)
    if first is None:
        return "".join(parts)
    styles: list[frozenset[str]] = []
    for p in parts:
        styles += p.styles if isinstance(p, Line) else [frozenset()] * len(p)
    return first.replaced("".join(parts), styles)


class Builder:
    """Text and styles, run by run."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.styles: list[frozenset[str]] = []

    def add(self, text: str, styles: frozenset[str] = frozenset()) -> None:
        self.text.append(text)
        self.styles += [styles] * len(text)

    def line(self, lang: str, kind: str) -> Line:
        return Line("".join(self.text), self.styles, lang, kind)


def short_date(date: str) -> str:
    """A date and time as the HTML report shows them, to the minute: an ISO
    2026-01-01T10:15:30Z as 2026-01-01 10:15, a date alone as it is; "" when
    it is none."""
    m = SHORT_DATE.match(date)
    return " ".join(filter(None, m.groups())) if m else ""


def _add(
    out: Builder,
    inlines: Iterable,
    styles: frozenset[str],
    comment: Callable[[CommentMark | CommentEnd], str],
) -> None:
    for i in inlines:
        if isinstance(i, Text):
            out.add(i.text, styles | i.styles)
        elif isinstance(i, Image):
            out.add(i.text, styles)
        elif isinstance(i, NoteRef):
            out.add(f"[^{i.number}]")
        elif isinstance(i, CommentMark | CommentEnd):
            out.add(comment(i))
        elif i.kind == "link":
            _add(out, i.children, styles | {"link"}, comment)
        else:
            _add(out, i.children, styles | change_marks(i.kind, i.author, i.date), comment)


def lines(doc: Document, comment: Callable[[CommentMark | CommentEnd], str]) -> list[Line]:
    """The lines prosediff compares: one per paragraph, heading, list item,
    table row and footnote. comment is what a comment, or the end of its
    text, becomes in the text: a placeholder, the Markdown pandoc writes for
    it, or nothing."""
    out = []
    for block in doc.blocks + doc.notes:
        lang = block.language or ""
        if block.kind == "table":
            for row in block.rows:
                b = Builder()
                for k, cell in enumerate(row):
                    if k:
                        b.add(" | ")
                    _add(b, cell, frozenset(), comment)
                out.append(b.line(lang, "row"))
            continue
        b = Builder()
        if block.kind == "item":
            b.add(BULLET)
        elif block.kind == "note":
            b.add(f"[^{block.number}]: ")
        styles = frozenset({f"h{min(block.level, 6)}"}) if block.kind == "heading" else frozenset()
        _add(b, block.inlines, styles, comment)
        out.append(b.line(lang, block.kind))
    return out
