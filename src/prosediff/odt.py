"""OpenDocument text (.odt: LibreOffice, OpenOffice, Google Docs' download)
read with odfdo, into paragraphs of styled text.

odfdo opens the package and resolves the styles; the body is then walked
element by element, as prosediff.word walks a Word document, into the same
prosediff.document.Document:

- headings (text:h, and the Title style), list items and tables are blocks of
  their kind, footnotes and endnotes numbered blocks of their own; each run
  keeps the styles of its spans (bold, italic, underline, struck through,
  superscript, subscript), each link its target;
- each comment (office:annotation) is kept where it starts;
- tracked changes are settled as asked. An insertion is the text between
  text:change-start and text:change-end; a deletion is a text:change point
  whose text is kept in text:tracked-changes. Accepting keeps the inserted
  text and leaves the deleted out, rejecting does the reverse, and "all"
  keeps both, marked as insertions and deletions. A comment anchored in
  dropped text is kept. Deleted text that spanned several paragraphs comes
  back, when rejected, as one run of text.

Headers, footers, frames' text and the table of contents are left out; an
image is written [image], with its description when it has one.
"""

import re
from collections import Counter
from io import BytesIO

from odfdo import Document, Element

from prosediff.document import (
    EM,
    STRIKE,
    STRONG,
    SUB,
    SUP,
    UNDERLINE,
    Block,
    CommentMark,
    Image,
    NoteRef,
    Span,
    Text,
    comments_in,
    comments_only,
    markdown,
    strip,
    to_markdown,
)
from prosediff.document import Document as Prose
from prosediff.language import OdtLanguages, most_letters
from prosediff.word import CHANGES

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
    "office:annotation-end",
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


class OdtError(RuntimeError):
    """The file is not an OpenDocument text odfdo can read."""


# An inline of a paragraph and the tracked insertion it belongs to (or None).
Tagged = tuple[object, str | None]


class Reader:
    def __init__(
        self, document: Document, changes: str, languages: OdtLanguages | None = None
    ) -> None:
        self.document = document
        self.changes = changes
        # The languages the text is marked with, when asked for, and the
        # letters of the whole in each language.
        self.languages = languages
        self.letters: Counter[str] = Counter()
        # change id -> ("insertion" | "deletion", author, date, region element)
        self.regions: dict[str, tuple[str, str, str, Element]] = {}
        self.open: list[str] = []  # the insertions the walk is inside
        self.notes: list[Block] = []  # footnotes and endnotes, as referenced
        self.comment_count = 0
        self.styles: dict[str, frozenset[str]] = {}
        # comments of a paragraph deleted as a whole, for the next paragraph
        self.carried: list = []

    def language_of(self, paragraphs: list[Element]) -> str | None:
        if self.languages is None:
            return None
        language, counts = self.languages.of(p._xml_element for p in paragraphs)
        self.letters += counts
        return language

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
            for kind in ("insertion", "deletion"):
                change = region.get_element(f"text:{kind}")
                if change is None:
                    continue
                author = change.get_element("office:change-info/dc:creator")
                date = change.get_element("office:change-info/dc:date")
                self.regions[cid] = (
                    kind,
                    (author.text if author is not None else "") or "",
                    (date.text if date is not None else "") or "",
                    change,
                )

    def dropping(self) -> bool:
        """Whether the text being walked goes: a rejected insertion."""
        return bool(self.open) and self.changes == "reject"

    def deletion(self, cid: str | None) -> list[Tagged]:
        """What a deletion point leaves: the deleted text when rejecting, a
        deletion span when showing all, only its comments when accepting."""
        kind, author, date, change = self.regions.get(cid or "", ("", "", "", None))
        if kind != "deletion" or change is None:
            return []
        saved = self.open
        self.open = []
        inner: list = []
        for p in change.get_elements("text:p|text:h"):
            if inner:
                inner.append(Text(" "))
            inner += [i for i, _ in self.inline(p, frozenset())]
        self.open = saved
        if self.changes == "reject":
            return [(i, None) for i in inner]
        if self.changes == "all":
            if not markdown(inner).strip():
                return []
            return [(Span("deletion", inner, author=author, date=date), None)]
        return [(c, None) for c in comments_in(inner)]

    def settle(self, tagged: list[Tagged]) -> list:
        """The inlines of a paragraph, its insertions settled: kept, dropped
        (their comments kept) or wrapped in insertion spans."""
        out: list = []
        k = 0
        while k < len(tagged):
            cid = tagged[k][1]
            group = []
            while k < len(tagged) and tagged[k][1] == cid:
                group.append(tagged[k][0])
                k += 1
            if cid is None or self.changes == "accept":
                out += group
            elif self.changes == "reject":
                out += comments_in(group)
            elif markdown(group).strip():
                _, author, date, _ = self.regions.get(cid, ("", "", "", None))
                out.append(Span("insertion", group, author=author, date=date))
        return out

    # Paragraph content ---------------------------------------------------------------

    def comment(self, el: Element) -> CommentMark:
        self.comment_count += 1
        author = el.get_element("dc:creator")
        date = el.get_element("dc:date")
        texts = [p.text_recursive for p in el.get_elements("text:p")]
        return CommentMark(
            str(self.comment_count - 1),
            (author.text if author is not None else "") or "",
            " ".join(" ".join(texts).split()),
            ((date.text if date is not None else "") or "")[:19],
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
                inner = [i for i, _ in self.inline(child, styles)]
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
                    saved = self.open
                    self.open = []
                    note = self.cell(paragraphs)
                    self.open = saved
                    number = len(self.notes) + 1
                    language = self.language_of(paragraphs)
                    self.notes.append(Block("note", note, number=number, language=language))
                    out.append((NoteRef(number), where))
            elif tag == "office:annotation":
                out.append((self.comment(child), None))
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
                    out.append((Image(f"[image: {descr}]" if descr else "[image]"), where))
            elif tag not in SILENT:
                # fields (dates, page numbers, cross-references) and the like
                out += self.inline(child, styles)
            out += self.text(child.tail, styles)
        return out

    # Blocks ------------------------------------------------------------------------

    def paragraph_inlines(self, el: Element) -> list:
        return strip(self.settle(self.inline(el, frozenset())))

    def cell(self, paragraphs: list[Element]) -> list:
        """The inlines of several paragraphs, as one, a space between them."""
        out: list = []
        for p in paragraphs:
            inlines = self.paragraph_inlines(p)
            if markdown(inlines):
                if out:
                    out.append(Text(" "))
                out += inlines
        return out

    def paragraph(self, el: Element, item: bool = False) -> Block | None:
        inlines = self.paragraph_inlines(el)
        if not markdown(inlines):
            return None
        # A paragraph whose text all went keeps only its comments, for the
        # next paragraph.
        if comments_only(inlines):
            self.carried += inlines
            return None
        inlines, self.carried = self.carried + inlines, []
        language = self.language_of([el])
        if el.tag == "text:h":
            level = min(el.get_attribute_integer("text:outline-level") or 1, 6)
            return Block("heading", inlines, level=level, language=language)
        level = self.paragraph_level(el.get_attribute_string("text:style-name"))
        if level and not item:
            return Block("heading", inlines, level=level, language=language)
        return Block("item" if item else "p", inlines, language=language)

    def table(self, el: Element) -> Block:
        rows = [
            [
                self.cell(tc.get_elements(".//text:p|.//text:h"))
                for tc in tr.get_elements("table:table-cell")
            ]
            for tr in el.get_elements(
                "table:table-row|table:table-header-rows/table:table-row"
                "|table:table-rows/table:table-row"
            )
        ]
        paragraphs = el.get_elements(".//text:p|.//text:h")
        return Block("table", rows=rows, language=self.language_of(paragraphs))

    def blocks(self, container: Element, item: bool = False) -> list[Block]:
        out: list[Block] = []
        for child in container.children:
            tag = child.tag
            if tag in ("text:p", "text:h"):
                if block := self.paragraph(child, item):
                    out.append(block)
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
        out = self.blocks(body)
        if self.carried:
            if not out:
                out.append(Block("p"))
            last = out[-1]
            if last.kind == "table" and last.rows and last.rows[-1]:
                last.rows[-1][-1] += self.carried
            elif last.kind == "table":
                out.append(Block("p", self.carried))
            else:
                last.inlines += self.carried
            self.carried = []
        return out


def odt_to_markdown(data: bytes, changes: str = "accept") -> str:
    """An OpenDocument text's body as Markdown, its tracked changes settled
    ("accept", "reject") or kept as markup ("all"), its comments kept."""
    return to_markdown(read_odt(data, changes))


def read_odt(data: bytes, changes: str = "accept") -> Prose:
    """An OpenDocument text as prosediff reads it (prosediff.document), its
    tracked changes settled ("accept", "reject") or kept as markup ("all"),
    its comments kept, each paragraph with the language it is marked with."""
    if changes not in CHANGES:
        raise ValueError(f"changes must be one of {CHANGES}, not {changes!r}")
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
