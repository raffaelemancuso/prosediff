"""OpenDocument text (.odt: LibreOffice, OpenOffice, Google Docs' download)
read with odfdo, into paragraphs of styled text.

odfdo opens the package and resolves the styles; the body is then walked
element by element, as prosediff.word walks a Word document, into the same
prosediff.document.Document:

- headings (text:h, and the Title style), list items and tables are blocks of
  their kind, footnotes and endnotes numbered blocks of their own; each run
  keeps the styles of its spans (bold, italic, underline, struck through,
  superscript, subscript), each link its target;
- each comment (office:annotation) is kept where it starts, and where the
  text it is anchored to ends (office:annotation-end);
- tracked changes are settled as asked. An insertion is the text between
  text:change-start and text:change-end; a deletion is a text:change point
  whose text is kept in text:tracked-changes. Accepting keeps the inserted
  text and leaves the deleted out, rejecting does the reverse, and "show"
  keeps both, marked as insertions and deletions. A deletion of inserted
  text (LibreOffice's stacked changes: a region holding a deletion, then
  the insertion it deletes from) keeps its text only when both stay, so
  rejecting it all takes it out. A comment anchored in
  dropped text is kept. Deleted text that spanned several paragraphs comes
  back, when rejected, as those paragraphs, each of its own kind (a heading,
  a list item); shown ("show"), as one run of text. A table row
  LibreOffice tracks whole (loext:text-changes-only "false" in its style)
  goes when its cells are left empty: deleted and accepted, or inserted
  and rejected.

Headers, footers, frames' text and the table of contents are left out; an
image is written [image], with its description when it has one.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from itertools import groupby
from operator import itemgetter

from odfdo import Document, Element

from prosediff.document import (
    EM,
    STRIKE,
    STRONG,
    SUB,
    SUP,
    UNDERLINE,
    Block,
    CommentEnd,
    CommentMark,
    DocumentReader,
    Image,
    Kind,
    NoteRef,
    Span,
    Text,
    check_changes,
    comment_runs,
    comments_in,
    markdown,
    spaced,
    strip,
)
from prosediff.document import Document as Prose
from prosediff.language import OdtLanguages, most_letters

# Whitespace in ODF text collapses to one space; text:s stands for the rest.
BLANKS = re.compile(r"[ \t\r\n]+")
HEADING_STYLE = re.compile(r"Heading_20_([1-6])")
# Blocks that are not text of the body.
SKIPPED_BLOCKS = {
    "text:tracked-changes",
    "text:sequence-decls",
    "text:variable-decls",
    "text:user-field-decls",
    "office:forms",
    "text:table-of-content",
    "text:alphabetical-index",
    "text:illustration-index",
    "text:table-index",
    "text:object-index",
    "text:user-index",
    "text:bibliography",
}
# Inline elements with nothing to say.
SILENT = {
    "text:soft-page-break",
    "text:bookmark",
    "text:bookmark-start",
    "text:bookmark-end",
    "text:reference-mark",
    "text:reference-mark-start",
    "text:reference-mark-end",
    "text:alphabetical-index-mark",
    "text:toc-mark",
}


def position(value: str) -> set[str]:
    """Superscript or subscript, from style:text-position: "super", "sub",
    or a raise in percent ("33%", "-33%"), then the size."""
    first = value.split()[0] if value.split() else ""
    if first == "super" or (first.rstrip("%").isdigit() and first.rstrip("%") != "0"):
        return {SUP}
    if first == "sub" or first.startswith("-"):
        return {SUB}
    return set()


# A table cell's own paragraphs: not those of a comment or a note in it, which
# are read as the comment's (the note's) text.
_OWN = "[not(ancestor::office:annotation) and not(ancestor::text:note)]"
CELL_PARAGRAPHS = f".//text:p{_OWN}|.//text:h{_OWN}"


class OdtError(RuntimeError):
    """The file is not an OpenDocument text odfdo can read."""


# An inline of a paragraph and the tracked insertion it belongs to (or None).
Tagged = tuple[object, str | None]


@dataclass
class Break:
    """A paragraph break inside a rejected deletion, among the inlines of
    the paragraph it is in: the kind of the deleted paragraph it ends and of
    the one it starts (None for an empty one, which takes the kind of the
    paragraph around it, as it merged into it)."""

    before: Kind | None
    after: Kind | None


def source_of(el: Element) -> tuple[str, str]:
    """Where a paragraph is (document.Source): content.xml, its XPath."""
    x = el._Element__element  # odfdo's lxml element
    return ("content.xml", x.getroottree().getpath(x))


def _text_of(el: Element, path: str) -> str:
    """The text of the element at path under el, "" when there is none."""
    found = el.get_element(path)
    return (found.text if found is not None else "") or ""


class Reader(DocumentReader):
    def __init__(self, document: Document, changes: str, languages: OdtLanguages) -> None:
        super().__init__(changes=changes, languages=languages)
        self.document = document
        # change id -> ("insertion" | "deletion", author, date, region element)
        self.regions: dict[str, tuple[str, str, str, Element]] = {}
        # the deletions of inserted text: a region holding a deletion and,
        # after it, the insertion it deletes from (LibreOffice's stacked
        # changes, OASIS OFFICE-4174)
        self.stacked: set[str] = set()
        self.open: list[str] = []  # the insertions the walk is inside
        self.notes: list[Block] = []  # footnotes and endnotes, as referenced
        self.comment_count = 0
        # an annotation's office:name -> the id of its CommentMark
        self.comment_names: dict[str, str] = {}
        self.styles: dict[str, frozenset[str]] = {}

    def language_of(self, paragraphs: list[Element]) -> str | None:
        return super().language_of(p._xml_element for p in paragraphs)

    # Styles ------------------------------------------------------------------------

    def text_style(self, name: str | None) -> frozenset[str]:
        """The styles of a text style (bold, italic, underline, struck
        through, superscript, subscript), following its parents."""
        if not name:
            return frozenset()
        if name not in self.styles:
            self.styles[name] = frozenset()  # a loop of parents ends here
            style = self.document.get_style("text", name)
            styles = set()
            if style is not None:
                props = style.get_properties(area="text") or {}
                styles = set(self.text_style(style.parent_style))
                for key, on, cls in (
                    ("fo:font-weight", lambda v: v not in ("normal", "400"), STRONG),
                    ("fo:font-style", lambda v: v in ("italic", "oblique"), EM),
                    ("style:text-underline-style", lambda v: v != "none", UNDERLINE),
                    ("style:text-line-through-style", lambda v: v != "none", STRIKE),
                ):
                    if key in props:
                        (styles.add if on(props[key]) else styles.discard)(cls)
                if "style:text-position" in props:
                    styles -= {SUP, SUB}
                    styles |= position(props["style:text-position"])
            self.styles[name] = frozenset(styles)
        return self.styles[name]

    def paragraph_level(self, name: str | None) -> int:
        """The heading level a paragraph style stands for (Title: 1), else 0."""
        seen = set()
        while name and name not in seen:
            seen.add(name)
            if name == "Title":
                return 1
            if m := HEADING_STYLE.fullmatch(name):
                return int(m[1])
            style = self.document.get_style("paragraph", name)
            name = style.parent_style if style is not None else None
        return 0

    # Tracked changes ---------------------------------------------------------------

    def read_regions(self, body: Element) -> None:
        for region in body.get_elements("text:tracked-changes/text:changed-region"):
            cid = region.get_attribute_string("text:id") or ""
            if all(region.get_element(f"text:{k}") is not None for k in ("deletion", "insertion")):
                self.stacked.add(cid)
            for kind in ("insertion", "deletion"):
                change = region.get_element(f"text:{kind}")
                if change is None:
                    continue
                self.regions[cid] = (
                    kind,
                    _text_of(change, "office:change-info/dc:creator"),
                    _text_of(change, "office:change-info/dc:date"),
                    change,
                )

    def dropping(self) -> bool:
        """Whether the text being walked goes: a rejected insertion."""
        return bool(self.open) and self.changes == "reject-all"

    @contextmanager
    def outside_insertions(self) -> Iterator[None]:
        """Read what the walk meets outside the insertions it is inside (a
        deletion's text, a comment's, a note's)."""
        saved, self.open = self.open, []
        try:
            yield
        finally:
            self.open = saved

    def deletion(self, cid: str | None) -> list[Tagged]:
        """What a deletion point leaves: the deleted text when rejecting, a
        deletion span when showing all, only its comments when accepting."""
        kind, author, date, change = self.regions.get(cid or "", ("", "", "", None))
        if kind != "deletion" or change is None:
            return []
        with self.outside_insertions():
            paragraphs = [
                ([i for i, _ in self.inline(p, frozenset())], kind)
                for p, kind in self.deleted_paragraphs(change)
            ]
        if cid in self.stacked and not self.keeps("insertion"):
            # the insertion beneath rejected too: the words go
            return [(c, None) for inlines, _ in paragraphs for c in comments_in(inlines)]
        if self.changes == "reject-all" and len(paragraphs) > 1:
            # the paragraphs back, a break between each two
            out: list[Tagged] = []
            for k, (inlines, kind) in enumerate(paragraphs):
                if k:
                    before = paragraphs[k - 1]
                    out.append(
                        (
                            Break(
                                before[1] if markdown(before[0]).strip() else None,
                                kind if markdown(inlines).strip() else None,
                            ),
                            None,
                        )
                    )
                out += [(i, None) for i in inlines]
            return out
        inner: list = []
        for inlines, _ in paragraphs:
            if inner:
                inner.append(Text(" "))
            inner += inlines
        return [(i, None) for i in self.settle_change("deletion", inner, author, date)]

    def deleted_paragraphs(self, el: Element, item: bool = False):
        """The paragraphs a deletion holds, each with its kind: those in a
        list too, as list items."""
        for child in el.children:
            if child.tag in ("text:p", "text:h"):
                yield child, self.kind_of(child, item)
            elif child.tag == "text:list":
                for entry in child.get_elements("text:list-item|text:list-header"):
                    yield from self.deleted_paragraphs(entry, True)

    def settle(self, tagged: list[Tagged]) -> list:
        """The inlines of a paragraph, its insertions settled: kept, dropped
        (their comments kept) or wrapped in insertion spans."""
        out: list = []
        for cid, run in groupby(tagged, key=itemgetter(1)):
            group = [i for i, _ in run]
            if cid is None:
                out += group
            else:
                _, author, date, _ = self.regions.get(cid, ("", "", "", None))
                out += self.settle_change("insertion", group, author, date)
        return out

    # Paragraph content ---------------------------------------------------------------

    def comment(self, el: Element) -> CommentMark:
        self.comment_count += 1
        paragraphs = el.get_elements("text:p")
        texts = [p.text_recursive for p in paragraphs]
        cid = str(self.comment_count - 1)
        if name := el.get_attribute_string("office:name"):
            self.comment_names[name] = cid
        # its paragraphs read as the body's are, in their styles, outside
        # any insertion the annotation sits in
        with self.outside_insertions():
            rich = comment_runs([i for i, _ in self.inline(p, frozenset())] for p in paragraphs)
        return CommentMark(
            cid,
            _text_of(el, "dc:creator"),
            spaced(" ".join(texts)),
            _text_of(el, "dc:date")[:19],
            rich,
        )

    def text(self, text: str | None, styles: frozenset[str]) -> list[Tagged]:
        if not text:
            return []
        return [(Text(BLANKS.sub(" ", text), styles), self.open[-1] if self.open else None)]

    def inline(self, el: Element, styles: frozenset[str]) -> list[Tagged]:
        """The inlines of a paragraph, span or link, each with the insertion
        it belongs to."""
        out = self.text(el.text, styles)
        for child in el.children:
            tag = child.tag
            where = self.open[-1] if self.open else None
            if tag == "text:span":
                own = self.text_style(child.get_attribute_string("text:style-name"))
                out += self.inline(child, styles | own)
            elif tag == "text:a":
                # its words' insertions settled within it, as a paragraph's
                inner = self.settle(self.inline(child, styles))
                target = child.get_attribute_string("xlink:href") or ""
                out.append((Span("link", inner, target=target), where))
            elif tag == "text:s":
                count = child.get_attribute_integer("text:c") or 1
                out.append((Text(" " * count, styles), where))
            elif tag in ("text:tab", "text:line-break"):
                out.append((Text(" ", styles), where))
            elif tag == "text:note":
                if not self.dropping():
                    body = child.get_element("text:note-body")
                    paragraphs = body.get_elements("text:p|text:h") if body is not None else []
                    with self.outside_insertions():
                        note = self.cell(paragraphs)
                    number = len(self.notes) + 1
                    self.notes.append(self.note_block(paragraphs, number, note, source_of))
                    out.append((NoteRef(number), where))
            elif tag == "office:annotation":
                out.append((self.comment(child), None))
            elif tag == "office:annotation-end":
                cid = self.comment_names.pop(child.get_attribute_string("office:name") or "", None)
                if cid is not None:
                    out.append((CommentEnd(cid), None))
            elif tag == "text:change-start":
                cid = child.get_attribute_string("text:change-id")
                if self.regions.get(cid or "", ("",))[0] == "insertion":
                    self.open.append(cid)
            elif tag == "text:change-end":
                cid = child.get_attribute_string("text:change-id")
                if cid in self.open:
                    self.open.remove(cid)
            elif tag == "text:change":
                out += self.deletion(child.get_attribute_string("text:change-id"))
            elif tag in ("draw:frame", "draw:a"):
                if child.get_element(".//draw:image") is not None:
                    descr = next(
                        (
                            d.text_recursive.strip()
                            for d in child.get_elements(".//svg:desc|.//svg:title")
                            if d.text_recursive.strip()
                        ),
                        "",
                    )
                    out.append((Image.described(descr), where))
            elif tag not in SILENT:
                # fields (dates, page numbers, cross-references) and the like
                out += self.inline(child, styles)
            out += self.text(child.tail, styles)
        return out

    # Blocks ------------------------------------------------------------------------

    def paragraph_inlines(self, el: Element) -> list:
        """A paragraph's inlines, as one: the paragraphs a rejected
        deletion brings back into it (in a table's cell, a footnote) joined
        by a space."""
        inlines = self.settle(self.inline(el, frozenset()))
        return strip([Text(" ") if isinstance(i, Break) else i for i in inlines])

    def kind_of(self, el: Element, item: bool = False) -> Kind:
        """A paragraph's kind: a heading (text:h, or of a heading's style),
        a list item, or a paragraph."""
        if el.tag == "text:h":
            return ("heading", min(el.get_attribute_integer("text:outline-level") or 1, 6))
        level = self.paragraph_level(el.get_attribute_string("text:style-name"))
        if level and not item:
            return ("heading", level)
        return ("item",) if item else ("p",)

    def paragraphs(self, el: Element, item: bool = False) -> list[Block]:
        """The blocks of a paragraph: one, or, when a rejected deletion
        brings paragraphs back into it (Break), one for each, a paragraph
        brought back empty taking the kind of the one around it."""
        inlines = self.settle(self.inline(el, frozenset()))
        own = self.kind_of(el, item)
        pieces: list[list] = [[]]
        breaks: list[Break] = []
        for i in inlines:
            if isinstance(i, Break):
                breaks.append(i)
                pieces.append([])
            else:
                pieces[-1].append(i)
        language = self.language_of([el])
        source = (source_of(el),)
        out = []
        for k, piece in enumerate(pieces):
            before = breaks[k - 1].after if k else None
            after = breaks[k].before if k < len(breaks) else None
            kind = before or after or own
            if block := self.block(strip(piece), kind, language, source):
                out.append(block)
        return out

    def tracked_whole(self, tr: Element) -> bool:
        """Whether a table row is tracked as a whole: its style says
        loext:text-changes-only "false", as LibreOffice writes a row
        inserted or deleted with its changes tracked (each cell's words a
        change of their own)."""
        name = tr.get_attribute_string("table:style-name")
        style = self.document.get_style("table-row", name) if name else None
        if style is None:
            return False
        props = style.get_element("style:table-row-properties")
        return props is not None and props.attributes.get("loext:text-changes-only") == "false"

    def table(self, el: Element) -> Block:
        rows, row_sources = [], []
        for tr in el.get_elements(
            "table:table-row|table:table-header-rows/table:table-row"
            "|table:table-rows/table:table-row"
        ):
            cells = [
                self.cell(tc.get_elements(CELL_PARAGRAPHS))
                for tc in tr.get_elements("table:table-cell")
            ]
            # a row deleted whole, accepted (inserted whole, rejected): gone
            if self.tracked_whole(tr) and not any(markdown(c).strip() for c in cells):
                continue
            rows.append(cells)
            row_sources.append(tuple(source_of(p) for p in tr.get_elements(CELL_PARAGRAPHS)))
        paragraphs = el.get_elements(".//text:p|.//text:h")
        return Block(
            "table", rows=rows, language=self.language_of(paragraphs), row_sources=row_sources
        )

    def blocks(self, container: Element, item: bool = False) -> list[Block]:
        out: list[Block] = []
        for child in container.children:
            tag = child.tag
            if tag in ("text:p", "text:h"):
                out += self.paragraphs(child, item)
            elif tag == "text:list":
                for entry in child.get_elements("text:list-item|text:list-header"):
                    out += self.blocks(entry, True)
            elif tag == "table:table":
                out.append(self.table(child))
            elif tag not in SKIPPED_BLOCKS:
                out += self.blocks(child, item)  # sections and the like
        return out

    def body(self) -> list[Block]:
        """The paragraphs and tables of the document; comments carried past
        the last paragraph join it."""
        body = self.document.body
        self.read_regions(body)
        return self.finish(self.blocks(body))


def read_odt(data: bytes, changes: str = "accept-all") -> Prose:
    """An OpenDocument text as prosediff reads it (prosediff.document), its
    tracked changes settled ("accept-all", "reject-all") or kept as markup ("show"),
    its comments kept, each paragraph with the language it is marked with."""
    check_changes(changes)
    try:
        document = Document(BytesIO(data))
        if document.get_type() not in ("text", "text-template"):
            raise OdtError(f"an OpenDocument {document.get_type()}, not a text")
        reader = Reader(document, changes, OdtLanguages(data))
        blocks = reader.body()
    except OdtError:
        raise
    except Exception as e:  # not a zip, not an OpenDocument, broken XML
        raise OdtError(f"{type(e).__name__}: {e}") from None
    return Prose(blocks, reader.notes, most_letters(reader.letters))
