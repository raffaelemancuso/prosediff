"""A Comparison as a document of tracked changes: a Word document (.docx) or
an OpenDocument text (.odt) holding the new version, each change since the
old one marked as the word processor's own tracked change (a revision),
which Word or LibreOffice then accepts or rejects one by one.

It is made from the pairing every format shows (FileDiff.pairs) and the word
pairing of the HTML report and the word diff (diff.word_ops): a paragraph
added or removed whole is a paragraph inserted or deleted, its mark
included, so that accepting or rejecting it leaves no empty paragraph
behind; an edited one holds its words removed and added where the HTML
report shows them. Only the text is kept, with its bold, italic, underline,
strikethrough, superscript and subscript, its headings and its list items;
not the layout, tables (a row becomes a paragraph, its cells apart by |),
images or page setup of the documents compared. A comment added since the
base becomes a comment of the document, over the words it was anchored to;
one removed since goes with the words deleted around it, or is left out.
In a Word document, words whose formatting alone changed are marked as a
formatting change too; an OpenDocument text shows them in their new
formatting only (LibreOffice reads no formatting change back).
"""

import datetime as dt
import re
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

import docx
import odfdo
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx_plus.revisions import RevisionIdRegistry, mark_deletion, mark_insertion

from prosediff.comments import END_FIRST, END_PLACEHOLDER, PLACEHOLDER, PUA_FIRST, Comments
from prosediff.diff import Comparison, FileDiff, word_ops
from prosediff.document import BULLET, EM, FORMATTING, STRIKE, STRONG, SUB, SUP, UNDERLINE

# The documents of tracked changes, by the name --format gives them.
TRACKED_FORMATS = ("docx", "odt")
# Who the changes are by, when the new side names no author.
AUTHOR = "prosediff"
HEADING = re.compile(r"h([1-6])")
MARKS = re.compile(f"{PLACEHOLDER.pattern}|{END_PLACEHOLDER.pattern}")
STYLES = frozenset(FORMATTING)


@dataclass
class Piece:
    """A run of text in one style and one state: "" (unchanged), "ins"
    (inserted) or "del" (deleted). old_styles: an unchanged run's styles in
    the old version, when its formatting alone changed."""

    text: str
    styles: frozenset[str] = frozenset()
    change: str = ""
    old_styles: frozenset[str] | None = None


@dataclass
class Mark:
    """Where the text of a comment starts ("start") or ends ("end"),
    placeholder naming the comment."""

    kind: str
    placeholder: str


@dataclass
class Para:
    """A paragraph of the document: its kind ("p", "heading", "item"), a
    heading's level, its pieces and marks, and whether it was inserted or
    deleted whole ("ins", "del"; "" when it is in both versions)."""

    kind: str = "p"
    level: int = 0
    pieces: list[Piece | Mark] = field(default_factory=list)
    change: str = ""


def styles_of(line: str) -> list[frozenset[str]]:
    """The styles of each character of a line: a document's own (a
    document.Line), none for any other line."""
    return getattr(line, "styles", None) or [frozenset()] * len(line)


def kind_of(line: str) -> tuple[str, int]:
    """The kind of the paragraph a line comes from, and a heading's level."""
    kind = getattr(line, "kind", "p")
    if kind == "heading":
        styles = styles_of(line)
        level = next((int(m[1]) for st in styles[:1] for s in st if (m := HEADING.fullmatch(s))), 1)
        return "heading", level
    return ("item", 0) if kind == "item" and line.startswith(BULLET) else ("p", 0)


def pieces(
    line: str, start: int, end: int, change: str, old: str | None = None, old_start: int = 0
) -> Iterator[Piece | Mark]:
    """The characters start to end of a line as pieces in change's state, and
    the comment marks among them (none in deleted text: its comments go with
    it). old: the same words in the old version, from old_start, whose
    formatting an unchanged run may have had otherwise."""
    styles = styles_of(line)
    old_styles = styles_of(old) if old is not None else None

    def key(k: int):
        if MARKS.fullmatch(line[k]):
            return ("mark", k)
        new = styles[k] & STYLES
        was = old_styles[old_start + k - start] & STYLES if old_styles is not None else new
        return ("text", new, was)

    k = start
    for group, run in groupby(range(start, end), key):
        n = len(list(run))
        if group[0] == "mark":
            ch = line[k]
            if change != "del":
                if PLACEHOLDER.fullmatch(ch):
                    yield Mark("start", ch)
                else:
                    yield Mark("end", chr(PUA_FIRST + ord(ch) - END_FIRST))
        else:
            _, new, was = group
            yield Piece(line[k : k + n], new, change, was if was != new else None)
        k += n


def paragraphs(f: FileDiff) -> list[Para]:
    """The paragraphs of one file's new version, with the old one's removed
    among them, each change marked."""
    out = []
    for i, j in f.pairs:
        a = f.old_text[i] if i is not None else None
        b = f.new_text[j] if j is not None else None
        kind, level = kind_of(b if b is not None else a)
        # a list item's bullet is the list's, not its text's
        skip = len(BULLET) if kind == "item" else 0
        if b is None:
            out.append(Para(kind, level, list(pieces(a, skip, len(a), "del")), "del"))
            continue
        if a is None:
            out.append(Para(kind, level, list(pieces(b, skip, len(b), "ins")), "ins"))
            continue
        a_skip = skip if kind_of(a)[0] == "item" else 0
        para = Para(kind, level)
        for op, o1, o2, n1, n2 in word_ops(a[a_skip:], b[skip:]):
            o1, o2, n1, n2 = o1 + a_skip, o2 + a_skip, n1 + skip, n2 + skip
            if op == "equal":
                para.pieces += pieces(b, n1, n2, "", a, o1)
                continue
            if o2 > o1:
                para.pieces += pieces(a, o1, o2, "del")
            if n2 > n1:
                para.pieces += pieces(b, n1, n2, "ins")
        out.append(para)
    return out


def document_paragraphs(comparison: Comparison) -> list[tuple[str, list[Para]]]:
    """Each file that has text, by its path, with its paragraphs."""
    return [(f.path, paragraphs(f)) for f in comparison.files if not f.binary and f.pairs]


def author_of(comparison: Comparison) -> str:
    return comparison.target.author or AUTHOR


def initials(name: str) -> str:
    return "".join(w[0] for w in name.split() if w[:1].isalpha()).upper()[:3]


def comment_date(date: str) -> str:
    """A comment's date ("YYYY-MM-DD HH:MM", or a date alone) as an
    xsd:dateTime; "" when it has none."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\d)(?: (\d\d:\d\d))?", date)
    return f"{m[1]}T{m[2] or '00:00'}:00Z" if m else ""


# Word ------------------------------------------------------------------------------

RUN_PROPERTIES = (
    (STRONG, "w:b"),
    (EM, "w:i"),
    (UNDERLINE, "w:u"),
    (STRIKE, "w:strike"),
)


def run_properties(styles: frozenset[str]) -> OxmlElement:
    """A w:rPr holding styles, in the order the schema wants them."""
    rpr = OxmlElement("w:rPr")
    for style, tag in RUN_PROPERTIES:
        if style in styles:
            el = OxmlElement(tag)
            if tag == "w:u":
                el.set(qn("w:val"), "single")
            rpr.append(el)
    if SUP in styles or SUB in styles:
        el = OxmlElement("w:vertAlign")
        el.set(qn("w:val"), "superscript" if SUP in styles else "subscript")
        rpr.append(el)
    return rpr


def revision(tag: str, registry: RevisionIdRegistry, author: str, date: str) -> OxmlElement:
    el = OxmlElement(tag)
    el.set(qn("w:id"), str(registry.next()))
    el.set(qn("w:author"), author)
    el.set(qn("w:date"), date)
    return el


def write_docx(comparison: Comparison, path: Path) -> Path:
    """The document of tracked changes, as a Word document."""
    doc = docx.Document()
    registry = RevisionIdRegistry(doc)
    author = author_of(comparison)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    date = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    files = document_paragraphs(comparison)
    for path_name, paras in files:
        if len(files) > 1:
            doc.add_paragraph(path_name, style="Title")
        comments = next(f.comments for f in comparison.files if f.path == path_name)
        for para in paras:
            style = (
                f"Heading {para.level}"
                if para.kind == "heading"
                else "List Bullet"
                if para.kind == "item"
                else None
            )
            p = doc.add_paragraph(style=style)
            runs, opened, anchors = [], {}, []
            for piece in para.pieces:
                if isinstance(piece, Mark):
                    if piece.kind == "start":
                        opened[piece.placeholder] = len(runs)
                    elif piece.placeholder in opened:
                        anchors.append(
                            (piece.placeholder, opened.pop(piece.placeholder), len(runs))
                        )
                    continue
                run = p.add_run(piece.text)
                run._r.insert(0, run_properties(piece.styles))
                if piece.old_styles is not None:
                    change = revision("w:rPrChange", registry, author, date)
                    change.append(run_properties(piece.old_styles))
                    run._r.rPr.append(change)
                runs.append((run, piece.change))
            anchors += [(ph, start, len(runs)) for ph, start in opened.items()]
            for change, group in groupby(runs, lambda r: r[1]):
                group = [r for r, _ in group]
                if change:
                    mark = mark_insertion if change == "ins" else mark_deletion
                    mark((group[0], group[-1]), author=author, date=now, id_registry=registry)
            if para.change:
                ppr = p._p.get_or_add_pPr()
                rpr = OxmlElement("w:rPr")
                rpr.append(revision(f"w:{para.change}", registry, author, date))
                ppr.append(rpr)
            add_docx_comments(doc, p, [r for r, _ in runs], anchors, comments)
    return save(doc.save, path)


def add_docx_comments(doc, p, runs: list, anchors: list, comments: Comments | None) -> None:
    """The comments of a paragraph, each over the runs between its marks
    (the run after it when it holds none)."""
    if comments is None:
        return
    for placeholder, start, end in anchors:
        c = comments.get(placeholder)
        if not runs:
            runs.append(p.add_run(""))
        first = min(start, len(runs) - 1)
        last = max(first, min(end, len(runs)) - 1)
        comment = doc.add_comment(
            runs[first : last + 1],
            text="" if c.rich else c.text,
            author=c.author,
            initials=initials(c.author),
        )
        if when := comment_date(c.date):
            comment._comment_elm.set(qn("w:date"), when)
        # its paragraphs as written, in their styles
        for k, runs_of in enumerate(c.rich):
            para = comment.paragraphs[0] if k == 0 else comment.add_paragraph()
            for t, styles in runs_of:
                run = para.add_run(t)
                run._r.insert(0, run_properties(frozenset(styles)))


def save(write, path: Path) -> Path:
    """Write the document with write(path); returns where it went. When
    path is open in Word or LibreOffice, which lock it, beside it instead,
    as NAME__locked_YYYYMMDD_HHMMSS.docx (or .odt), with a warning: one
    open document must not cost the comparison."""
    try:
        write(str(path))
        return path
    except PermissionError:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        other = path.with_name(f"{path.stem}__locked_{stamp}{path.suffix}")
        write(str(other))
        warnings.warn(f"{path} is open and locked: written to {other} instead", stacklevel=2)
        return other


def write_tracked(comparison: Comparison, path: Path, fmt: str) -> Path:
    """Write the document of tracked changes as fmt, one of TRACKED_FORMATS;
    returns where it went (save)."""
    if fmt == "docx":
        return write_docx(comparison, path)
    if fmt == "odt":
        return write_odt(comparison, path)
    raise ValueError(f"format must be one of {TRACKED_FORMATS}, not {fmt!r}")


# OpenDocument ------------------------------------------------------------------------

# The text properties of each style, for the automatic styles of spans.
TEXT_PROPERTIES = {
    STRONG: {"fo:font-weight": "bold"},
    EM: {"fo:font-style": "italic"},
    UNDERLINE: {"style:text-underline-style": "solid", "style:text-underline-width": "auto"},
    STRIKE: {"style:text-line-through-style": "solid"},
    SUP: {"style:text-position": "super 58%"},
    SUB: {"style:text-position": "sub 58%"},
}
BULLETS = "L_bullets"


class OdtWriter:
    """The document of tracked changes as an OpenDocument text, laid out as
    LibreOffice lays out its own: each change a region of text:tracked-
    changes; an insertion's text between a change-start and a change-end;
    a deletion's text kept in its region, a text:change where it was. A
    paragraph inserted whole starts at the end of the one before it, so
    the break between them is inserted with it; one deleted whole is kept
    with the (empty) start of the next, its text:change there, so the
    break goes with it."""

    def __init__(self, comparison: Comparison) -> None:
        self.doc = odfdo.Document("text")
        self.body = self.doc.body
        self.body.clear()
        self.changes = odfdo.TrackedChanges()
        self.body.append(self.changes)
        self.author = author_of(comparison)
        self.date = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
        self.styles: dict[frozenset[str], str] = {}
        self.regions = 0
        self.notes = 0
        self.last = None  # the text of the paragraph written last
        self.list = None  # the list its items go into, while they follow one another
        self.pending: list[Para] = []  # paragraphs deleted whole, before the next
        self.comments: Comments | None = None
        bullets = odfdo.Element.from_tag(
            f'<text:list-style style:name="{BULLETS}">'
            '<text:list-level-style-bullet text:level="1" text:bullet-char="•">'
            "<style:list-level-properties text:list-level-position-and-space-mode="
            '"label-alignment"><style:list-level-label-alignment text:label-followed-by='
            '"listtab" text:list-tab-stop-position="0.635cm" fo:text-indent="-0.635cm" '
            'fo:margin-left="0.635cm"/></style:list-level-properties>'
            "</text:list-level-style-bullet></text:list-style>"
        )
        self.doc.insert_style(bullets, automatic=True)

    def style(self, styles: frozenset[str]) -> str | None:
        """The name of the automatic text style for styles (None for none)."""
        if not styles:
            return None
        if styles not in self.styles:
            name = f"T_{len(self.styles) + 1}"
            style = odfdo.Style("text", name=name)
            properties = {}
            for s in FORMATTING:
                if s in styles:
                    properties.update(TEXT_PROPERTIES[s])
            style.set_properties(properties, area="text")
            self.doc.insert_style(style, automatic=True)
            self.styles[styles] = name
        return self.styles[styles]

    def region(self, change) -> str:
        """A new region of text:tracked-changes holding change (a
        TextInsertion or TextDeletion); its id."""
        self.regions += 1
        name = f"ct{self.regions}"
        region = odfdo.TextChangedRegion()
        region.set_id(name)
        change.set_change_info(creator=self.author, date=self.date)
        region.append(change)
        self.changes.append(region)
        return name

    @staticmethod
    def point(tag: str, name: str):
        el = odfdo.Element.from_tag(f"<{tag}/>")
        el.set_attribute("text:change-id", name)
        return el

    def add_text(self, element, piece: Piece) -> None:
        name = self.style(piece.styles)
        element.append(odfdo.Span(piece.text, style=name) if name else piece.text)

    def block(self, para: Para):
        """An empty paragraph, heading or list item of para's kind: the
        element to put in the body, and the one its text goes into."""
        if para.kind == "heading":
            h = odfdo.Header(para.level, style=f"Heading_20_{min(para.level, 10)}")
            return h, h
        p = odfdo.Paragraph(style="Text_20_body" if para.kind == "p" else None)
        if para.kind == "item":
            item = odfdo.ListItem()
            item.append(p)
            return item, p
        return p, p

    def fill(self, text, para: Para, deleted: bool = False) -> None:
        """Put para's pieces into text: its words inserted and deleted as
        changes (all as they are when deleted, its whole text being in a
        deletion), its comments as annotations."""
        opened: dict[str, str] = {}
        items = para.pieces
        k = 0
        while k < len(items):
            piece = items[k]
            if isinstance(piece, Mark):
                k += 1
                if deleted or self.comments is None:
                    continue
                if piece.kind == "start":
                    c = self.comments.get(piece.placeholder)
                    self.notes += 1
                    name = f"__Annotation__{self.notes}"
                    when = comment_date(c.date)
                    date = dt.datetime.fromisoformat(when[:-1]) if when else None
                    note = odfdo.Annotation(c.text, creator=c.author, date=date, name=name)
                    if c.rich:  # its paragraphs as written, in their styles
                        for p in note.get_elements("text:p"):
                            note.delete(p)
                        for runs_of in c.rich:
                            p = odfdo.Paragraph()
                            for t, styles in runs_of:
                                self.add_text(p, Piece(t, frozenset(styles)))
                            note.append(p)
                    text.append(note)
                    opened[piece.placeholder] = name
                elif piece.placeholder in opened:
                    text.append(odfdo.AnnotationEnd(name=opened.pop(piece.placeholder)))
                continue
            change = "" if deleted else piece.change
            run = [piece]
            k += 1
            while (
                k < len(items) and isinstance(items[k], Piece) and items[k].change == piece.change
            ):
                run.append(items[k])
                k += 1
            if change == "ins":
                name = self.region(odfdo.TextInsertion())
                text.append(self.point("text:change-start", name))
                for p in run:
                    self.add_text(text, p)
                text.append(self.point("text:change-end", name))
            elif change == "del":
                gone = odfdo.Paragraph()
                for p in run:
                    self.add_text(gone, p)
                deletion = odfdo.TextDeletion()
                name = self.region(deletion)
                deletion.append(gone)
                text.append(self.point("text:change", name))
            else:
                for p in run:
                    self.add_text(text, p)
        for name in opened.values():
            text.append(odfdo.AnnotationEnd(name=name))

    def deletion(self, para: Para, before: bool):
        """A region deleting a paragraph whole: its text and the break after
        it (before the next paragraph's start), or, when no paragraph
        follows, the break before it (after the last one's end)."""
        element, text = self.block(para)
        self.fill(text, para, deleted=True)
        if para.kind == "item":
            element = odfdo.List(element, style=BULLETS)
        deletion = odfdo.TextDeletion()
        name = self.region(deletion)
        for el in (element, odfdo.Paragraph()) if before else (odfdo.Paragraph(), element):
            deletion.append(el)
        return self.point("text:change", name)

    def add(self, para: Para) -> None:
        if para.change == "del":
            self.pending.append(para)
            return
        element, text = self.block(para)
        for gone in self.pending:
            text.append(self.deletion(gone, before=True))
        self.pending = []
        end = None
        if para.change == "ins":
            name = self.region(odfdo.TextInsertion())
            (self.last if self.last is not None else text).append(
                self.point("text:change-start", name)
            )
            end = self.point("text:change-end", name)
            para = Para(
                para.kind,
                para.level,
                [Piece(p.text, p.styles) if isinstance(p, Piece) else p for p in para.pieces],
            )
        self.fill(text, para)
        if end is not None:
            text.append(end)
        if para.kind == "item":
            if self.list is None:
                self.list = odfdo.List(style=BULLETS)
                self.body.append(self.list)
            self.list.append(element)
        else:
            self.list = None
            self.body.append(element)
        self.last = text

    def title(self, name: str) -> None:
        self.list = None
        p = odfdo.Paragraph(name, style="Title")
        self.body.append(p)
        self.last = p

    def finish(self) -> None:
        """The paragraphs deleted at the end, after the last one."""
        if not self.pending:
            return
        if self.last is None:
            self.last = odfdo.Paragraph()
            self.body.append(self.last)
        for gone in self.pending:
            self.last.append(self.deletion(gone, before=False))
        self.pending = []


def write_odt(comparison: Comparison, path: Path) -> Path:
    """The document of tracked changes, as an OpenDocument text."""
    w = OdtWriter(comparison)
    files = document_paragraphs(comparison)
    for path_name, paras in files:
        if len(files) > 1:
            w.finish()
            w.title(path_name)
        w.comments = next(f.comments for f in comparison.files if f.path == path_name)
        for para in paras:
            w.add(para)
    w.finish()
    return save(w.doc.save, path)
