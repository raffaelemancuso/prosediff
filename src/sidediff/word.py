"""Word documents read into Markdown, with python-docx.

python-docx opens the document and resolves what the XML alone does not say:
paragraph and run styles, bold and italic, link targets, and the comments
(author, date, text). The paragraphs are then walked element by element, so
that everything lands where it sits in the text:

- headings (Title, Heading 1-6) become #, list paragraphs "- ", tables pipe
  tables, footnotes and endnotes [^n] with their text at the end, links
  [text](url), bold and italic ** and *;
- each comment becomes, where it starts, the span pandoc writes for it,
  [note]{.comment-start id=... author="..." date="..."}, which the rest of
  sidediff folds into a marker and lists in the comments panel;
- tracked changes are settled as asked: accepting keeps the inserted runs
  (w:ins, w:moveTo) and drops the deleted ones (w:del, w:moveFrom),
  rejecting does the reverse, and "all" keeps both, as [text]{.insertion
  ...} and [text]{.deletion ...} spans. The spaces at the edges of a change
  stay with it, and a comment anchored in dropped text is kept.

Headers, footers, text boxes' layout and page breaks are not text of the
body and are left out; an image is written [image], with its description
when it has one, and an equation as its text.
"""

from dataclasses import dataclass, field
from io import BytesIO

import docx
from docx.document import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

CHANGES = ("accept", "reject", "all")
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
HEADING_STYLES = {"title": 1, **{f"heading {n}": n for n in range(1, 7)}}
LIST_STYLES = ("list", "bullet", "number")
INSERTED = {qn("w:ins"), qn("w:moveTo")}
DELETED = {qn("w:del"), qn("w:moveFrom")}
# Wrappers whose content is part of the paragraph's text.
TRANSPARENT = {
    qn("w:smartTag"),
    qn("w:customXml"),
    qn("w:fldSimple"),
    qn("w:sdtContent"),
    qn("w:dir"),
    qn("w:bdo"),
}


class WordError(RuntimeError):
    """The file is not a Word document python-docx can read."""


@dataclass
class Piece:
    """A piece of a paragraph: text in a style, or Markdown written as is."""

    text: str
    bold: bool = False
    italic: bool = False
    raw: bool = False


class _Story:
    """What a python-docx paragraph needs of its container: the part it
    belongs to, to resolve styles and link targets."""

    def __init__(self, part) -> None:
        self.part = part


@dataclass
class Reader:
    document: Document
    changes: str
    comments: dict = field(default_factory=dict)  # id -> python-docx Comment
    notes: dict = field(default_factory=dict)  # ("footnote"/"endnote", id) -> element
    note_order: list = field(default_factory=list)  # [(kind, id)] as referenced
    shown_comments: set = field(default_factory=set)

    # Paragraph content ----------------------------------------------------------

    def comment(self, cid: str) -> list[Piece]:
        """The comment-start span of a comment, once."""
        c = self.comments.get(cid)
        if c is None or cid in self.shown_comments:
            return []
        self.shown_comments.add(cid)
        note = " ".join(c.text.split()).replace("\\", "\\\\")
        note = note.replace("[", "\\[").replace("]", "\\]")
        author = (c.author or "").replace('"', "'")
        date = c.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if c.timestamp else ""
        span = f'[{note}]{{.comment-start id="{cid}" author="{author}" date="{date}"}}'
        return [Piece(span, raw=True)]

    def note_ref(self, kind: str, nid: str) -> list[Piece]:
        key = (kind, nid)
        if key not in self.note_order:
            self.note_order.append(key)
        return [Piece(f"[^{self.note_order.index(key) + 1}]", raw=True)]

    def run(self, el, paragraph, deleted: bool, dropping: bool = False) -> list[Piece]:
        """The pieces of a w:r: its text, with the run's bold and italic.

        dropping: the run is in text that goes (a change settled away); only
        its comments are kept, so its footnote references are not counted.
        """
        r = Run(el, paragraph)
        style = (r.style.name or "").lower() if r.style is not None else ""
        bold = bool(r.bold) or style == "strong"
        italic = bool(r.italic) or style == "emphasis"
        pieces: list[Piece] = []
        for child in el:
            tag = child.tag
            if tag == qn("w:t") or (tag == qn("w:delText") and deleted):
                pieces.append(Piece(child.text or "", bold, italic))
            elif tag in (qn("w:tab"), qn("w:ptab"), qn("w:br"), qn("w:cr")):
                pieces.append(Piece(" ", bold, italic))
            elif tag == qn("w:noBreakHyphen"):
                pieces.append(Piece("-", bold, italic))
            elif tag == qn("w:footnoteReference") and not dropping:
                pieces += self.note_ref("footnote", child.get(qn("w:id")))
            elif tag == qn("w:endnoteReference") and not dropping:
                pieces += self.note_ref("endnote", child.get(qn("w:id")))
            elif tag == qn("w:commentReference"):
                pieces += self.comment(child.get(qn("w:id")))
            elif tag in (qn("w:drawing"), qn("w:pict"), qn("w:object")):
                descr = next((d.get("descr") for d in child.iter() if d.get("descr")), "")
                pieces.append(Piece(f"[image: {descr}]" if descr else "[image]", raw=True))
        return pieces

    def children(self, el, paragraph, deleted: bool = False, dropping: bool = False) -> list[Piece]:
        """The pieces of an element's children, tracked changes settled."""
        pieces: list[Piece] = []
        for child in el:
            tag = child.tag
            if tag == qn("w:r"):
                pieces += self.run(child, paragraph, deleted, dropping)
            elif tag == qn("w:commentRangeStart"):
                pieces += self.comment(child.get(qn("w:id")))
            elif tag in INSERTED or tag in DELETED:
                is_deletion = tag in DELETED
                keep = self.changes == "all" or (self.changes == "accept") != is_deletion
                inner = self.children(
                    child, paragraph, deleted=is_deletion, dropping=dropping or not keep
                )
                if not keep:
                    # the text goes, the comments anchored in it stay
                    pieces += [p for p in inner if p.raw and ".comment-start" in p.text]
                elif self.changes == "all":
                    kind = "deletion" if is_deletion else "insertion"
                    author = (child.get(qn("w:author")) or "").replace('"', "'")
                    date = child.get(qn("w:date")) or ""
                    text = render_pieces(inner)
                    if text:
                        span = f'[{text}]{{.{kind} author="{author}" date="{date}"}}'
                        pieces.append(Piece(span, raw=True))
                else:
                    pieces += inner
            elif tag == qn("w:hyperlink"):
                inner = self.children(child, paragraph, deleted, dropping)
                rid = child.get(qn("r:id"))
                target = ""
                if rid and rid in paragraph.part.rels:
                    target = paragraph.part.rels[rid].target_ref
                text = render_pieces(inner)
                pieces.append(Piece(f"[{text}]({target})" if target else text, raw=True))
            elif tag == qn("w:sdt"):
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    pieces += self.children(content, paragraph, deleted, dropping)
            elif tag in TRANSPARENT:
                pieces += self.children(child, paragraph, deleted, dropping)
            elif tag in (f"{{{M_NS}}}oMath", f"{{{M_NS}}}oMathPara"):
                pieces.append(Piece(omml_text(child)))
        return pieces

    # Blocks -----------------------------------------------------------------------

    def paragraph_text(self, el) -> str:
        p = Paragraph(el, _Story(self.document.part))
        return render_pieces(self.children(el, p)).strip()

    def paragraph(self, el) -> str:
        text = self.paragraph_text(el)
        if not text:
            return ""
        p = Paragraph(el, _Story(self.document.part))
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style in HEADING_STYLES:
            return "#" * HEADING_STYLES[style] + " " + text
        numbered = el.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is not None
        if numbered or style.startswith(LIST_STYLES):
            return "- " + text
        return text

    def table(self, el) -> list[str]:
        rows = []
        for tr in el.iter(qn("w:tr")):
            cells = []
            for tc in tr.findall(qn("w:tc")):
                texts = [self.paragraph_text(p) for p in tc.iter(qn("w:p"))]
                cells.append(" ".join(t for t in texts if t).replace("|", "\\|"))
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            width = rows[0].count(" | ") + 1
            rows.insert(1, "|" + "---|" * width)
        return rows

    def blocks(self, container) -> list[str]:
        out: list[str] = []
        for child in container:
            if child.tag == qn("w:p"):
                line = self.paragraph(child)
                if line:
                    out.append(line)
            elif child.tag == qn("w:tbl"):
                out.append("\n".join(self.table(child)))
            elif child.tag == qn("w:sdt"):
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    out += self.blocks(content)
            elif child.tag in INSERTED | DELETED or child.tag == qn("w:customXml"):
                out += self.blocks(child)
        return out

    def note_lines(self) -> list[str]:
        """The footnotes and endnotes, in the order they are referenced (a note
        may reference another, so the list can grow while it is written)."""
        out = []
        k = 0
        while k < len(self.note_order):
            el = self.notes.get(self.note_order[k])
            k += 1
            if el is None:
                continue
            texts = [self.paragraph_text(p) for p in el.iter(qn("w:p"))]
            out.append(f"[^{k}]: " + " ".join(t for t in texts if t))
        return out


def _m(name: str) -> str:
    return f"{{{M_NS}}}{name}"


def _group(text: str) -> str:
    return f"({text})"


def _prop(el, prop: str, name: str, default: str) -> str:
    """The m:val of a property of an equation element, e.g. a bracket."""
    found = el.find(f"{_m(prop)}/{_m(name)}")
    return default if found is None else found.get(_m("val"), default)


def omml_text(el) -> str:
    """An equation (Office Math) as linear text, as pandoc's plain output
    writes it: DV_(it) = α_(i) + β ⋅ X, (a)/(b), √(x), ∑_(i)^(n) x."""
    tag = el.tag.split("}")[-1]

    def part(name: str) -> str:
        found = el.find(_m(name))
        return "" if found is None else omml_text(found)

    if tag == "t":
        return el.text or ""
    if tag.endswith("Pr"):  # properties: not text
        return ""
    if tag == "sSub":
        return part("e") + "_" + _group(part("sub"))
    if tag == "sSup":
        return part("e") + "^" + _group(part("sup"))
    if tag == "sSubSup":
        return part("e") + "_" + _group(part("sub")) + "^" + _group(part("sup"))
    if tag == "f":
        return _group(part("num")) + "/" + _group(part("den"))
    if tag == "rad":
        return "√" + _group(part("e"))
    if tag == "d":
        begin = _prop(el, "dPr", "begChr", "(")
        end = _prop(el, "dPr", "endChr", ")")
        sep = _prop(el, "dPr", "sepChr", "|")
        return begin + sep.join(omml_text(e) for e in el.findall(_m("e"))) + end
    if tag == "nary":
        op = _prop(el, "naryPr", "chr", "∫")
        sub, sup = part("sub"), part("sup")
        return (
            op
            + (f"_{_group(sub)}" if sub else "")
            + (f"^{_group(sup)}" if sup else "")
            + " "
            + part("e")
        )
    text = "".join(omml_text(c) for c in el)
    if tag == "oMath":
        # spaces around relations and operators, which Word does not store
        for op in "=+×⋅<>≤≥≈−":
            text = text.replace(op, f" {op} ")
        text = " ".join(text.split())
    return text


def render_pieces(pieces: list[Piece]) -> str:
    """Pieces as Markdown: runs of the same style wrapped in ** or *, the
    blanks at the edges of a run kept outside the markers."""
    out = []
    k = 0
    while k < len(pieces):
        p = pieces[k]
        if p.raw:
            out.append(p.text)
            k += 1
            continue
        text = ""
        while (
            k < len(pieces)
            and not pieces[k].raw
            and (pieces[k].bold, pieces[k].italic)
            == (
                p.bold,
                p.italic,
            )
        ):
            text += pieces[k].text
            k += 1
        marker = ("**" if p.bold else "") + ("*" if p.italic else "")
        core = text.strip()
        if marker and core:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()) :]
            text = f"{lead}{marker}{core}{marker[::-1]}{trail}"
        out.append(text)
    return "".join(out)


def _notes(document: Document) -> dict:
    """The footnote and endnote elements, by (kind, id), separators left out."""
    notes = {}
    for part in document.part.package.iter_parts():
        name = str(part.partname)
        kind = {"/word/footnotes.xml": "footnote", "/word/endnotes.xml": "endnote"}.get(name)
        if kind is None:
            continue
        root = parse_xml(part.blob)
        for note in root.findall(qn(f"w:{kind}")):
            if note.get(qn("w:type")) in (None, "normal"):
                notes[(kind, note.get(qn("w:id")))] = note
    return notes


def docx_to_markdown(data: bytes, changes: str = "accept") -> str:
    """A Word document's body as Markdown, its tracked changes settled
    ("accept", "reject") or kept as markup ("all"), its comments kept."""
    if changes not in CHANGES:
        raise ValueError(f"changes must be one of {CHANGES}, not {changes!r}")
    try:
        document = docx.Document(BytesIO(data))
    except (PackageNotFoundError, KeyError, ValueError) as e:
        raise WordError(str(e) or type(e).__name__) from None
    except Exception as e:  # a zip that is not a Word package, broken XML
        raise WordError(f"{type(e).__name__}: {e}") from None
    reader = Reader(document, changes)
    try:
        reader.comments = {str(c.comment_id): c for c in document.comments}
    except (KeyError, ValueError):
        reader.comments = {}
    reader.notes = _notes(document)
    lines = reader.blocks(document.element.body)
    notes = reader.note_lines()
    text = "\n\n".join(lines)
    if notes:
        text += "\n\n" + "\n\n".join(notes)
    return text + "\n"
