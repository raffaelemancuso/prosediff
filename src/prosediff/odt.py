"""OpenDocument text (.odt: LibreOffice, OpenOffice, Google Docs' download)
read into Markdown, with odfdo.

odfdo opens the package and resolves the styles; the body is then walked
element by element, as prosediff.word walks a Word document, into the same
Markdown:

- headings (text:h, and the Title style) become #, list items "- ", tables
  pipe tables, footnotes and endnotes [^n] with their text at the end, links
  [text](url), bold and italic (from the styles of the spans) ** and *;
- each comment (office:annotation) becomes, where it starts, the span pandoc
  writes for it, [note]{.comment-start id=... author="..." date="..."};
- tracked changes are settled as asked. An insertion is the text between
  text:change-start and text:change-end; a deletion is a text:change point
  whose text is kept in text:tracked-changes. Accepting keeps the inserted
  text and leaves the deleted out, rejecting does the reverse, and "all"
  keeps both, as [text]{.insertion ...} and [text]{.deletion ...} spans. A
  comment anchored in dropped text is kept. Deleted text that spanned
  several paragraphs comes back, when rejected, as one run of text.

Headers, footers, frames' text and the table of contents are left out; an
image is written [image], with its description when it has one.
"""

import re
from io import BytesIO

from odfdo import Document, Element

from prosediff.word import CHANGES, COMMENTS_ONLY, Piece, render_pieces

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


class OdtError(RuntimeError):
    """The file is not an OpenDocument text odfdo can read."""


# A piece of a paragraph and the tracked insertion it belongs to (or None).
Tagged = tuple[Piece, str | None]


class Reader:
    def __init__(self, document: Document, changes: str) -> None:
        self.document = document
        self.changes = changes
        # change id -> ("insertion" | "deletion", author, date, region element)
        self.regions: dict[str, tuple[str, str, str, Element]] = {}
        self.open: list[str] = []  # the insertions the walk is inside
        self.notes: list[str] = []  # footnote and endnote texts, as referenced
        self.comment_count = 0
        self.styles: dict[str, tuple[bool, bool]] = {}
        # comments of a paragraph deleted as a whole, for the next paragraph
        self.carried = ""

    # Styles ------------------------------------------------------------------------

    def text_style(self, name: str | None) -> tuple[bool, bool]:
        """(bold, italic) of a text style, following its parents."""
        if not name:
            return False, False
        if name not in self.styles:
            self.styles[name] = (False, False)  # a loop of parents ends here
            style = self.document.get_style("text", name)
            bold = italic = False
            if style is not None:
                props = style.get_properties(area="text") or {}
                bold, italic = self.text_style(style.parent_style)
                if "fo:font-weight" in props:
                    bold = props["fo:font-weight"] not in ("normal", "400")
                if "fo:font-style" in props:
                    italic = props["fo:font-style"] in ("italic", "oblique")
            self.styles[name] = (bold, italic)
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
        inner = []
        for p in change.get_elements("text:p|text:h"):
            if inner:
                inner.append((Piece(" "), None))
            inner += self.inline(p, False, False)
        self.open = saved
        pieces = [p for p, _ in inner]
        if self.changes == "reject":
            return [(p, None) for p in pieces]
        if self.changes == "all":
            text = render_pieces(pieces)
            if not text.strip():
                return []
            author = author.replace('"', "'")
            span = f'[{text}]{{.deletion author="{author}" date="{date}"}}'
            return [(Piece(span, raw=True), None)]
        return [(p, None) for p in pieces if p.raw and ".comment-start" in p.text]

    def settle(self, tagged: list[Tagged]) -> list[Piece]:
        """The pieces of a paragraph, its insertions settled: kept, dropped
        (their comments kept) or wrapped in insertion spans."""
        out: list[Piece] = []
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
                out += [p for p in group if p.raw and ".comment-start" in p.text]
            else:
                text = render_pieces(group)
                if text.strip():
                    _, author, date, _ = self.regions.get(cid, ("", "", "", None))
                    author = author.replace('"', "'")
                    out.append(
                        Piece(f'[{text}]{{.insertion author="{author}" date="{date}"}}', raw=True)
                    )
        return out

    # Paragraph content ---------------------------------------------------------------

    def comment(self, el: Element) -> Piece:
        self.comment_count += 1
        author = el.get_element("dc:creator")
        date = el.get_element("dc:date")
        texts = [p.text_recursive for p in el.get_elements("text:p")]
        note = " ".join(" ".join(texts).split()).replace("\\", "\\\\")
        note = note.replace("[", "\\[").replace("]", "\\]")
        author = ((author.text if author is not None else "") or "").replace('"', "'")
        date = ((date.text if date is not None else "") or "")[:19]
        cid = self.comment_count - 1
        span = f'[{note}]{{.comment-start id="{cid}" author="{author}" date="{date}"}}'
        return Piece(span, raw=True)

    def text(self, text: str | None, bold: bool, italic: bool) -> list[Tagged]:
        if not text:
            return []
        return [(Piece(BLANKS.sub(" ", text), bold, italic), self.open[-1] if self.open else None)]

    def inline(self, el: Element, bold: bool, italic: bool) -> list[Tagged]:
        """The pieces of a paragraph, span or link, each with the insertion
        it belongs to."""
        out = self.text(el.text, bold, italic)
        for child in el.children:
            tag = child.tag
            where = self.open[-1] if self.open else None
            if tag == "text:span":
                b, i = self.text_style(child.get_attribute_string("text:style-name"))
                out += self.inline(child, bold or b, italic or i)
            elif tag == "text:a":
                inner = [p for p, _ in self.inline(child, bold, italic)]
                text = render_pieces(inner)
                target = child.get_attribute_string("xlink:href") or ""
                # a link that shows its own address is written once
                link = f"[{text}]({target})" if target and text != target else text
                out.append((Piece(link, raw=True), where))
            elif tag == "text:s":
                count = child.get_attribute_integer("text:c") or 1
                out.append((Piece(" " * count, bold, italic), where))
            elif tag in ("text:tab", "text:line-break"):
                out.append((Piece(" ", bold, italic), where))
            elif tag == "text:note":
                if not self.dropping():
                    body = child.get_element("text:note-body")
                    paragraphs = body.get_elements("text:p|text:h") if body is not None else []
                    saved = self.open
                    self.open = []
                    texts = [
                        render_pieces(self.settle(self.inline(p, False, False))).strip()
                        for p in paragraphs
                    ]
                    self.open = saved
                    self.notes.append(" ".join(t for t in texts if t))
                    out.append((Piece(f"[^{len(self.notes)}]", raw=True), where))
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
                    image = f"[image: {descr}]" if descr else "[image]"
                    out.append((Piece(image, raw=True), where))
            elif tag not in SILENT:
                # fields (dates, page numbers, cross-references) and the like
                out += self.inline(child, bold, italic)
            out += self.text(child.tail, bold, italic)
        return out

    # Blocks ------------------------------------------------------------------------

    def paragraph_text(self, el: Element) -> str:
        return render_pieces(self.settle(self.inline(el, False, False))).strip()

    def paragraph(self, el: Element, prefix: str = "") -> str:
        text = self.paragraph_text(el)
        if not text:
            return ""
        # A paragraph whose text all went keeps only its comments, for the
        # next paragraph.
        if COMMENTS_ONLY.fullmatch(text):
            self.carried += text
            return ""
        text, self.carried = self.carried + text, ""
        if el.tag == "text:h":
            level = el.get_attribute_integer("text:outline-level") or 1
            return "#" * min(level, 6) + " " + text
        level = self.paragraph_level(el.get_attribute_string("text:style-name"))
        if level and not prefix:
            return "#" * level + " " + text
        return prefix + text

    def table(self, el: Element) -> str:
        rows = []
        for tr in el.get_elements(
            "table:table-row|table:table-header-rows/table:table-row"
            "|table:table-rows/table:table-row"
        ):
            cells = []
            for tc in tr.get_elements("table:table-cell"):
                texts = [self.paragraph_text(p) for p in tc.get_elements(".//text:p|.//text:h")]
                cells.append(" ".join(t for t in texts if t).replace("|", "\\|"))
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            width = rows[0].count(" | ") + 1
            rows.insert(1, "|" + "---|" * width)
        return "\n".join(rows)

    def blocks(self, container: Element, prefix: str = "") -> list[str]:
        out: list[str] = []
        for child in container.children:
            tag = child.tag
            if tag in ("text:p", "text:h"):
                line = self.paragraph(child, prefix)
                if line:
                    out.append(line)
            elif tag == "text:list":
                for item in child.get_elements("text:list-item|text:list-header"):
                    out += self.blocks(item, "- ")
            elif tag == "table:table":
                out.append(self.table(child))
            elif tag not in SKIPPED_BLOCKS:
                out += self.blocks(child, prefix)  # sections and the like
        return out

    def body(self) -> list[str]:
        """The paragraphs and tables of the document; comments carried past
        the last paragraph join it."""
        body = self.document.body
        self.read_regions(body)
        out = self.blocks(body)
        if self.carried:
            if out:
                out[-1] += self.carried
            else:
                out.append(self.carried)
            self.carried = ""
        return out


def odt_to_markdown(data: bytes, changes: str = "accept") -> str:
    """An OpenDocument text's body as Markdown, its tracked changes settled
    ("accept", "reject") or kept as markup ("all"), its comments kept."""
    if changes not in CHANGES:
        raise ValueError(f"changes must be one of {CHANGES}, not {changes!r}")
    try:
        document = Document(BytesIO(data))
        if document.get_type() not in ("text", "text-template"):
            raise OdtError(f"an OpenDocument {document.get_type()}, not a text")
        reader = Reader(document, changes)
        lines = reader.body()
    except OdtError:
        raise
    except Exception as e:  # not a zip, not an OpenDocument, broken XML
        raise OdtError(f"{type(e).__name__}: {e}") from None
    text = "\n\n".join(lines)
    if reader.notes:
        text += "\n\n" + "\n\n".join(f"[^{k}]: {t}" for k, t in enumerate(reader.notes, 1))
    return text + "\n"
