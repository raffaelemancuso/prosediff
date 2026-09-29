"""The document of tracked changes made from the new file itself (the
redline): a copy of the new Word document or OpenDocument text, each change
since the old version marked in it where it stands, all else as it is:
styles, page setup, sections, headers and footers, tables, images, fields,
comments, footnotes.

Each line prosediff compared knows where its paragraphs are in its file
(document.Line.source: the part and the XPath of each). The redline finds
them in a fresh copy of the new file, and the paragraphs of the old one in a
copy of the old; the text of the line is aligned to the text of their runs,
so that each of its characters has its place in the XML. Then, from the
pairing every format shows (FileDiff.pairs) and the word pairing of the
HTML report (diff.word_ops):

- words inserted are wrapped where they stand, as insertions;
- words deleted are put back where they were, as deletions, in the runs
  (and the formatting) they had in the old file;
- words whose formatting alone changed carry their old formatting as a
  formatting change (Word only: LibreOffice reads none back);
- a paragraph, or a table row, added whole is inserted with its paragraph
  mark (its row); one removed whole is copied from the old file, after the
  paragraph before it, and deleted with its paragraph mark (its row), so
  that accepting or rejecting every change leaves no empty paragraph.

A deleted paragraph keeps its text and formatting, not what points into its
old file (images, links, comments, footnote references, bookmarks). ODF
tracks no table rows, only their cells' text: an OpenDocument text marks a
row inserted or deleted whole as LibreOffice (7.2 on) does, each cell's
words a change and the row's style loext:text-changes-only "false"
(OdtRedline.tracked_row). The new file's own tracked changes are accepted first
(or rejected, as it was read): in a Word document with docx-plus, which
leaves a paragraph (or row) deleted whole in place, empty, not merged away.
"""

import copy
import datetime as dt
import difflib
import re
from io import BytesIO

import docx
import odfdo
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx_plus.revisions import (
    RevisionIdRegistry,
    accept_all_revisions,
    reject_all_revisions,
)
from lxml import etree

from prosediff.diff import FileDiff, word_ops
from prosediff.document import BULLET, EM, FORMATTING, STRIKE, STRONG, SUB, SUP, UNDERLINE

STYLES = frozenset(FORMATTING)
HEADING = re.compile(r"h([1-6])")


def kind_of(line: str) -> tuple[str, int]:
    """The kind of the paragraph a line comes from ("p", "heading",
    "item"), and a heading's level."""
    kind = getattr(line, "kind", "p")
    if kind == "heading":
        styles = getattr(line, "styles", None) or [frozenset()]
        level = next((int(m[1]) for s in styles[0] if (m := HEADING.fullmatch(s))), 1)
        return "heading", level
    return ("item", 0) if kind == "item" and line.startswith(BULLET) else ("p", 0)


# Word ------------------------------------------------------------------------------

RUN_PROPERTIES = ((STRONG, "w:b"), (EM, "w:i"), (UNDERLINE, "w:u"), (STRIKE, "w:strike"))


def run_properties(styles: frozenset[str]):
    """A w:rPr holding styles, in the order the schema wants them: for
    words whose run in the old file is not known."""
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


# Where a run's text is not the paragraph's: deleted, moved away, in a text
# box or a drawing.
NOT_THE_TEXT = {
    qn(t) for t in ("w:del", "w:moveFrom", "w:txbxContent", "w:drawing", "w:pict", "w:object")
}
# What a run holds besides text that is read as a space, or a hyphen.
SPACES = {qn(t) for t in ("w:tab", "w:ptab", "w:br", "w:cr")}
# What a paragraph copied from the old file loses: what points into it.
POINTERS = {
    qn(t)
    for t in (
        "w:commentRangeStart",
        "w:commentRangeEnd",
        "w:commentReference",
        "w:footnoteReference",
        "w:endnoteReference",
        "w:bookmarkStart",
        "w:bookmarkEnd",
        "w:drawing",
        "w:pict",
        "w:object",
        "w:proofErr",
        "w:permStart",
        "w:permEnd",
    )
}


class WordFile:
    """A Word document opened for the redline: its parts' XML by name, those
    python-docx keeps as bytes parsed, to be written back."""

    def __init__(self, data: bytes, changes: str) -> None:
        self.doc = docx.Document(BytesIO(data))
        self.roots: dict[str, etree._Element] = {}
        self.blobs: dict[str, object] = {}  # parts kept as bytes, by name
        for part in self.doc.part.package.iter_parts():
            name = str(part.partname)
            if hasattr(part, "element"):
                self.roots[name] = part.element
            elif name.endswith(".xml") and name.startswith("/word/"):
                self.roots[name] = parse_xml(part.blob)
                self.blobs[name] = part
        self.changes = changes

    def find(self, source: tuple[str, str]):
        part, path = source
        root = self.roots.get(part)
        if root is None:
            return None
        found = etree.XPath(path, namespaces={k: v for k, v in root.nsmap.items() if k})(root)
        return found[0] if found else None

    def settle(self) -> None:
        """The document's own tracked changes accepted (or rejected, as the
        file was read), once its paragraphs are found."""
        if self.changes == "reject":
            reject_all_revisions(self.doc)
        else:
            accept_all_revisions(self.doc)

    def save(self, path: str) -> None:
        for name, part in self.blobs.items():
            part._blob = etree.tostring(self.roots[name], xml_declaration=True, standalone=True)
        self.doc.save(path)


def in_text(r, p) -> bool:
    """Whether a run is text of paragraph p, not of something within it."""
    for a in r.iterancestors():
        if a is p:
            return True
        if a.tag in NOT_THE_TEXT:
            return False
    return False


def atoms(paragraphs) -> tuple[str, list]:
    """The text of paragraphs as their runs hold it, and where each
    character is: (run, element, offset in its text, None for a tab or a
    break, read as a space)."""
    text, at = [], []
    for p in paragraphs:
        for r in p.iter(qn("w:r")):
            if not in_text(r, p):
                continue
            for child in r:
                if child.tag == qn("w:t"):
                    for k, ch in enumerate(child.text or ""):
                        text.append(ch)
                        at.append((r, child, k))
                elif child.tag in SPACES:
                    text.append(" ")
                    at.append((r, child, None))
                elif child.tag == qn("w:noBreakHyphen"):
                    text.append("-")
                    at.append((r, child, None))
    return "".join(text), at


def aligned(line: str, text: str) -> list[int]:
    """For each position of line (and its end), the position of text it
    stands at: characters of the line the text has not (a list item's
    bullet, a comment's placeholder, a footnote's number) stand at the next
    one it has."""
    out = [0] * (len(line) + 1)
    matcher = difflib.SequenceMatcher(None, line, text, autojunk=False)
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        for i in range(i1, i2):
            out[i] = j1 + (i - i1) if tag == "equal" else j1
    out[len(line)] = len(text)
    return out


def split_before(atom) -> None:
    """Split a run so that the character at atom starts one."""
    r, el, k = atom
    if el.tag == qn("w:t") and k:
        rest = copy.copy(el)
        rest.text = el.text[k:]
        el.text = el.text[:k]
        for t in (el, rest):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        el.addnext(rest)
        el = rest
    children = list(r)
    idx = children.index(el)
    if not any(c.tag != qn("w:rPr") for c in children[:idx]):
        return  # it starts the run already
    second = copy.deepcopy(r)
    for c in children[idx:]:
        r.remove(c)
    for c in list(second)[:idx]:
        if c.tag != qn("w:rPr"):
            second.remove(c)
    r.addnext(second)


class WordRedline:
    """The changes of one file marked in a copy of its new version."""

    def __init__(self, f: FileDiff, author: str) -> None:
        self.f = f
        self.new = WordFile(f.new_data, f.document_changes)
        self.old = WordFile(f.old_data, f.document_changes) if f.old_data else None
        self.author = author
        self.date = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        # each line's paragraphs, found before the files' own changes are
        # settled (which may merge paragraphs away)
        self.new_paragraphs = [self.found(self.new, line) for line in f.new_text]
        self.old_paragraphs = [self.found(self.old, line) for line in f.old_text]
        self.new.settle()
        if self.old is not None:
            self.old.settle()
        self.ids = RevisionIdRegistry(self.new.doc)

    @staticmethod
    def found(file: WordFile | None, line) -> list:
        if file is None:
            return []
        return [p for p in (file.find(s) for s in getattr(line, "source", ())) if p is not None]

    def revision(self, tag: str):
        el = OxmlElement(tag)
        el.set(qn("w:id"), str(self.ids.next()))
        el.set(qn("w:author"), self.author)
        el.set(qn("w:date"), self.date)
        return el

    def wrap(self, run, tag: str) -> None:
        mark = self.revision(tag)
        run.addprevious(mark)
        mark.append(run)

    def mark_paragraph(self, p, tag: str) -> None:
        """Mark a paragraph's mark inserted or deleted (and its row, when it
        is the first paragraph of a table row)."""
        ppr = p.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            p.insert(0, ppr)
        rpr = ppr.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            later = [c for c in ppr if c.tag in (qn("w:sectPr"), qn("w:pPrChange"))]
            if later:
                later[0].addprevious(rpr)
            else:
                ppr.append(rpr)
        rpr.insert(0, self.revision(tag))

    def mark_row(self, tr, tag: str) -> None:
        trpr = tr.find(qn("w:trPr"))
        if trpr is None:
            trpr = OxmlElement("w:trPr")
            after = tr.find(qn("w:tblPrEx"))
            if after is not None:
                after.addnext(trpr)
            else:
                tr.insert(0, trpr)
        trpr.append(self.revision(tag))

    # One line ---------------------------------------------------------------------

    def inserted_whole(self, paragraphs, row: bool) -> None:
        for p in paragraphs:
            for r in [r for r in p.iter(qn("w:r")) if in_text(r, p)]:
                self.wrap(r, "w:ins")
            self.mark_paragraph(p, "w:ins")
        if row and paragraphs:
            tr = next(paragraphs[0].iterancestors(qn("w:tr")), None)
            if tr is not None:
                self.mark_row(tr, "w:ins")

    def deleted_runs(self, old_line: str, i: int, o1: int, o2: int) -> list:
        """The runs of the old words o1 to o2 of old line i, deleted: copies
        of the old file's runs holding them, in their formatting; runs in
        the line's styles when the old file is not a Word document."""
        paragraphs = self.old_paragraphs[i] if i < len(self.old_paragraphs) else []
        runs = []
        if paragraphs:
            text, at = atoms(paragraphs)
            where = aligned(old_line, text)
            x1, x2 = where[o1], where[o2]
            for r, group in _by_run(at[x1:x2]):
                run = OxmlElement("w:r")
                rpr = r.find(qn("w:rPr"))
                if rpr is not None:
                    run.append(_without_changes(copy.deepcopy(rpr)))
                _deleted_content(run, group)
                runs.append(run)
        if not runs:
            words = "".join(ch for ch in old_line[o1:o2] if ord(ch) < 0xE000)
            if words:
                styles = getattr(old_line, "styles", None)
                run = OxmlElement("w:r")
                run.append(run_properties(styles[o1] & STYLES if styles else frozenset()))
                t = OxmlElement("w:delText")
                t.text = words
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                run.append(t)
                runs.append(run)
        return runs

    def old_formatting(self, old_line: str, i: int, o: int):
        """The run properties of the old character o of old line i."""
        paragraphs = self.old_paragraphs[i] if i < len(self.old_paragraphs) else []
        if paragraphs:
            text, at = atoms(paragraphs)
            x = aligned(old_line, text)[o]
            if x < len(at):
                rpr = at[x][0].find(qn("w:rPr"))
                return copy.deepcopy(rpr) if rpr is not None else OxmlElement("w:rPr")
        styles = getattr(old_line, "styles", None)
        return run_properties(styles[o] & STYLES if styles else frozenset())

    def edited(self, i: int, j: int) -> None:
        """Mark the changes of old line i into new line j, in j's paragraphs."""
        a, b = self.f.old_text[i], self.f.new_text[j]
        paragraphs = self.new_paragraphs[j]
        if not paragraphs:
            return
        text, at = atoms(paragraphs)
        where = aligned(b, text)
        a_styles, b_styles = getattr(a, "styles", None), getattr(b, "styles", None)
        inserted, deleted, formatted = [], [], []
        for op, o1, o2, n1, n2 in word_ops(a, b) if a != b else [("equal", 0, len(a), 0, len(b))]:
            if op == "equal":
                if a_styles and b_styles:
                    for k in range(n2 - n1):
                        if a_styles[o1 + k] & STYLES != b_styles[n1 + k] & STYLES:
                            x = where[n1 + k]
                            if x < len(text) and where[n1 + k + 1] > x:
                                last = formatted[-1] if formatted else None
                                if last and last[1] == x and last[2] + (x - last[0]) == o1 + k:
                                    formatted[-1] = (last[0], x + 1, last[2])
                                else:
                                    formatted.append((x, x + 1, o1 + k))
                continue
            if o2 > o1:
                deleted.append((where[n1], o1, o2))
            if n2 > n1:
                inserted.append((where[n1], where[n2]))
        if not (inserted or deleted or formatted):
            return
        cuts = {x for x1, x2 in inserted for x in (x1, x2)}
        cuts |= {x for x, _, _ in deleted}
        cuts |= {x for x1, x2, _ in formatted for x in (x1, x2)}
        for x in sorted(cuts, reverse=True):
            if 0 < x < len(at):
                split_before(at[x])
        text2, at = atoms(paragraphs)
        assert text2 == text
        # a run of characters whose formatting alone changed: each of its runs
        # with the formatting its first character had
        for x1, x2, o in formatted:
            for r, group in _by_run(at[x1:x2]):
                rpr = r.find(qn("w:rPr"))
                if rpr is None:
                    rpr = OxmlElement("w:rPr")
                    r.insert(0, rpr)
                if rpr.find(qn("w:rPrChange")) is None:
                    first = o + at.index(group[0]) - x1
                    change = self.revision("w:rPrChange")
                    change.append(_without_changes(self.old_formatting(a, i, first)))
                    rpr.append(change)
        # the insertions' runs, found before the deletions go in
        runs_in = [[r for r, _ in _by_run(at[x1:x2])] for x1, x2 in inserted if x2 > x1]
        for x, o1, o2 in deleted:
            runs = self.deleted_runs(a, i, o1, o2)
            if not runs:
                continue
            mark = self.revision("w:del")
            for run in runs:
                mark.append(run)
            if x < len(at):
                at[x][0].addprevious(mark)
            elif at:
                at[-1][0].addnext(mark)
            else:
                ppr = paragraphs[0].find(qn("w:pPr"))
                (ppr.addnext(mark) if ppr is not None else paragraphs[0].insert(0, mark))
        for runs in runs_in:
            for r in runs:
                self.wrap(r, "w:ins")

    def copied(self, i: int) -> list:
        """Old line i's paragraphs (a row: its table row), copied from the old
        file and deleted, for the new one."""
        old_line = self.f.old_text[i]
        paragraphs = self.old_paragraphs[i] if i < len(self.old_paragraphs) else []
        if not paragraphs:
            p = OxmlElement("w:p")
            words = "".join(ch for ch in old_line if ord(ch) < 0xE000)
            run = OxmlElement("w:r")
            run.append(run_properties(frozenset()))
            t = OxmlElement("w:t")
            t.text = words
            run.append(t)
            p.append(run)
            paragraphs = [p]
        if getattr(old_line, "kind", "") == "row":
            tr = next(paragraphs[0].iterancestors(qn("w:tr")), None)
            if tr is not None:
                row = _cleaned(copy.deepcopy(tr))
                for p in row.iter(qn("w:p")):
                    self.delete_paragraph(p)
                self.mark_row(row, "w:del")
                return [row]
        out = []
        for p in paragraphs:
            p = _cleaned(copy.deepcopy(p))
            self.delete_paragraph(p)
            out.append(p)
        return out

    def delete_paragraph(self, p) -> None:
        for r in list(p.iter(qn("w:r"))):
            if not in_text(r, p):
                continue
            for t in r.iter(qn("w:t")):
                t.tag = qn("w:delText")
            for t in r.iter(qn("w:instrText")):
                t.tag = qn("w:delInstrText")
            self.wrap(r, "w:del")
        self.mark_paragraph(p, "w:del")

    # The file ---------------------------------------------------------------------

    def run(self) -> None:
        f = self.f
        pairs = f.pairs
        anchor = None  # the element the next deleted paragraphs go after
        pending: list[int] = []  # deleted lines waiting for their place
        for i, j in pairs:
            if j is None:
                pending.append(i)
                continue
            paragraphs = self.new_paragraphs[j] if j < len(self.new_paragraphs) else []
            if pending and paragraphs:
                self.place(pending, anchor, before=paragraphs[0], row=self.is_row(j))
                pending = []
            if i is None:
                self.inserted_whole(paragraphs, self.is_row(j))
            else:
                self.edited(i, j)
            if paragraphs:
                anchor = self.after_of(paragraphs[-1], self.is_row(j))
        if pending:
            self.place(pending, anchor, before=None, row=False)

    def is_row(self, j: int) -> bool:
        return getattr(self.f.new_text[j], "kind", "") == "row"

    @staticmethod
    def after_of(p, row: bool):
        """What comes after a line's last paragraph: its table row, for a row."""
        if row:
            return next(p.iterancestors(qn("w:tr")), p)
        return p

    def place(self, lines: list[int], anchor, before, row: bool) -> None:
        """Put deleted lines back: after anchor (the element of the line
        before them), or, when there is none, before the next line's first
        paragraph; a row next to a row, a paragraph outside any table."""
        for i in lines:
            copies = self.copied(i)
            is_row = bool(copies) and copies[0].tag == qn("w:tr")
            for el in copies:
                target = self.where(anchor, before, is_row)
                if target is None:
                    body = self.new.doc.element.body
                    sect = body.find(qn("w:sectPr"))
                    (sect.addprevious(el) if sect is not None else body.append(el))
                elif target[0] == "after":
                    target[1].addnext(el)
                else:
                    target[1].addprevious(el)
                anchor = el

    @staticmethod
    def where(anchor, before, is_row: bool):
        """Where a deleted paragraph (or row) goes: ("after", element) or
        ("before", element); None: at the end of the body."""
        ref, side = (anchor, "after") if anchor is not None else (before, "before")
        if ref is None:
            return None
        if ref.tag == qn("w:tr"):
            if is_row:
                return side, ref
            tbl = next(ref.iterancestors(qn("w:tbl")), None)
            return side, tbl if tbl is not None else ref
        if is_row:
            return None if ref is None else (side, ref)
        # a paragraph in a table cell: after (before) the table
        tbl = next(ref.iterancestors(qn("w:tbl")), None)
        return side, tbl if tbl is not None else ref


def _by_run(atoms_) -> list:
    """The atoms grouped by their run, in order."""
    out: list = []
    for atom in atoms_:
        if out and out[-1][0] is atom[0]:
            out[-1][1].append(atom)
        else:
            out.append((atom[0], [atom]))
    return out


def _deleted_content(run, group) -> None:
    """Fill a deleted run with the characters of group: their text as
    w:delText, a tab or a break as it was."""
    text = []

    def flush():
        if text:
            t = OxmlElement("w:delText")
            t.text = "".join(text)
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            run.append(t)
            text.clear()

    for _, el, k in group:
        if k is None:
            flush()
            run.append(copy.deepcopy(el))
        else:
            text.append(el.text[k])
    flush()


def _without_changes(rpr):
    for c in rpr.findall(qn("w:rPrChange")):
        rpr.remove(c)
    return rpr


def _cleaned(el):
    """A paragraph (or row) of the old file with nothing pointing into it:
    links unwrapped to their runs, comments, notes, bookmarks and images
    left out."""
    for h in list(el.iter(qn("w:hyperlink"))):
        for child in list(h):
            h.addprevious(child)
        h.getparent().remove(h)
    for x in [x for x in el.iter() if x.tag in POINTERS]:
        x.getparent().remove(x)
    return el


def redline_docx(f: FileDiff, path: str, author: str) -> None:
    """Write the new Word document of f with its changes since the old one
    marked as tracked changes."""
    r = WordRedline(f, author)
    r.run()
    r.new.save(path)


# OpenDocument ----------------------------------------------------------------------

ODF = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}


def odf(name: str) -> str:
    prefix, local = name.split(":")
    return f"{{{ODF[prefix]}}}{local}"


# What a paragraph holds that is not its text: comments, notes (lines of
# their own), frames, the marks of tracked changes.
ODF_NOT_TEXT = {
    odf(t)
    for t in (
        "office:annotation",
        "office:annotation-end",
        "text:note",
        "draw:frame",
        "draw:a",
        "text:change",
        "text:change-start",
        "text:change-end",
    )
}
ODF_SPACES = {odf("text:tab"), odf("text:line-break")}


def odf_atoms(paragraphs) -> tuple[str, list]:
    """The text of paragraphs and where each character is: (node, "text"
    or "tail", offset), or (element, None, 0) for a space element."""
    text, at = [], []

    def chars(node, where, s):
        for k, ch in enumerate(s or ""):
            text.append(ch)
            at.append((node, where, k))

    def walk(el):
        chars(el, "text", el.text)
        for child in el:
            if not isinstance(child.tag, str) or child.tag in ODF_NOT_TEXT:
                pass
            elif child.tag == odf("text:s"):
                for _ in range(int(child.get(odf("text:c")) or 1)):
                    text.append(" ")
                    at.append((child, None, 0))
            elif child.tag in ODF_SPACES:
                text.append(" ")
                at.append((child, None, 0))
            else:
                walk(child)
            chars(child, "tail", child.tail)

    for p in paragraphs:
        walk(p)
    return "".join(text), at


def odf_insert(at: list, x: int, el, paragraphs) -> None:
    """Put el before the character at x (after the last one, x at the end)."""
    if not at:
        paragraphs[0].append(el)
        return
    end = x >= len(at)
    node, where, k = at[-1] if end else at[x]
    if where is None:
        if end:
            node.addnext(el)
            el.tail, node.tail = node.tail, None
        else:
            node.addprevious(el)
        return
    k += end
    # (another mark put at the same place first may have left no text)
    if where == "text":
        text = node.text or ""
        node.text = text[:k] or None
        node.insert(0, el)
    else:
        text = node.tail or ""
        node.tail = text[:k] or None
        node.addnext(el)
    el.tail = text[k:] or None


def odf_at_start(p, el) -> None:
    el.tail, p.text = p.text, None
    p.insert(0, el)


# The text properties of each style, for the automatic styles of the
# deleted words' spans.
TEXT_PROPERTIES = {
    STRONG: {"fo:font-weight": "bold"},
    EM: {"fo:font-style": "italic"},
    UNDERLINE: {"style:text-underline-style": "solid", "style:text-underline-width": "auto"},
    STRIKE: {"style:text-line-through-style": "solid"},
    SUP: {"style:text-position": "super 58%"},
    SUB: {"style:text-position": "sub 58%"},
}


class OdtStyles:
    """The automatic text styles of the deleted words' spans, one for each
    set of styles, named prefix and a number (made when first asked for)."""

    def __init__(self, doc, prefix: str) -> None:
        self.doc = doc
        self.prefix = prefix
        self.names: dict[frozenset[str], str] = {}

    def name(self, styles: frozenset[str]) -> str | None:
        """The name of the style for styles (None for none)."""
        if not styles:
            return None
        if styles not in self.names:
            name = f"{self.prefix}{len(self.names) + 1}"
            style = odfdo.Style("text", name=name)
            properties = {}
            for s in FORMATTING:
                if s in styles:
                    properties.update(TEXT_PROPERTIES[s])
            style.set_properties(properties, area="text")
            self.doc.insert_style(style, automatic=True)
            self.names[styles] = name
        return self.names[styles]


def lxml_of(element):
    """The lxml element behind an odfdo one."""
    return element._Element__element


class OdtRedline:
    """The changes of one file marked in a copy of its new version, laid
    out as LibreOffice lays out its own : an
    insertion between a change-start and a change-end, a deletion's words
    kept in its region, a text:change where they were. Formatting changes
    are not marked: LibreOffice reads none back."""

    def __init__(self, f: FileDiff, author: str) -> None:
        self.f = f
        self.doc = odfdo.Document(BytesIO(f.new_data))
        self.root = lxml_of(self.doc.get_part("content").root)
        self.new_paragraphs = [self.found(line) for line in f.new_text]
        self.accept_own()
        self.styles = OdtStyles(self.doc, prefix="PD_T")
        self.author = author
        self.date = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
        self.regions = 0
        self.row_styles: dict[str | None, str] = {}
        body = self.root.find(f".//{odf('office:text')}")
        changes = body.find(odf("text:tracked-changes"))
        if changes is None:
            changes = etree.Element(odf("text:tracked-changes"))
            body.insert(0, changes)
        self.changes = changes

    def found(self, line) -> list:
        ns = {k: v for k, v in self.root.nsmap.items() if k}
        out = []
        for part, path in getattr(line, "source", ()):
            if part == "content.xml":
                out += etree.XPath(path, namespaces=ns)(self.root.getroottree())[:1]
        return out

    def accept_own(self) -> None:
        """The document's own tracked changes accepted: its deletions gone,
        its insertions kept, their marks and regions dropped."""
        for tag in ("text:change", "text:change-start", "text:change-end"):
            for el in list(self.root.iter(odf(tag))):
                parent, prev = el.getparent(), el.getprevious()
                if el.tail:
                    if prev is not None:
                        prev.tail = (prev.tail or "") + el.tail
                    else:
                        parent.text = (parent.text or "") + el.tail
                parent.remove(el)
        for region in list(self.root.iter(odf("text:changed-region"))):
            region.getparent().remove(region)

    def region(self, kind: str, content=()) -> str:
        """A new region of text:tracked-changes, an insertion ("ins") or a
        deletion holding content; its id."""
        self.regions += 1
        name = f"pd{self.regions}"
        change = odfdo.TextInsertion() if kind == "ins" else odfdo.TextDeletion()
        change.set_change_info(creator=self.author, date=self.date)
        for el in content:
            lxml_of(change).append(lxml_of(el) if hasattr(el, "_Element__element") else el)
        region = odfdo.TextChangedRegion()
        region.set_id(name)
        region.append(change)
        self.changes.append(lxml_of(region))
        return name

    @staticmethod
    def point(tag: str, name: str):
        el = etree.Element(odf(tag))
        el.set(odf("text:change-id"), name)
        return el

    def words(self, line: str, start: int, end: int, element=None):
        """The words start to end of a line, in their styles, in element (a
        new paragraph when none)."""
        p = odfdo.Paragraph() if element is None else element
        styles = getattr(line, "styles", None)

        def style(n):
            return styles[n] & STYLES if styles else frozenset()

        k = start
        while k < end:
            n = k
            while n < end and style(n) == style(k):
                n += 1
            text = "".join(ch for ch in line[k:n] if ord(ch) < 0xE000)
            if text:
                name = self.styles.name(style(k))
                p.append(odfdo.Span(text, style=name) if name else text)
            k = n
        return p

    def edited(self, i: int, j: int) -> None:
        a, b = self.f.old_text[i], self.f.new_text[j]
        paragraphs = self.new_paragraphs[j]
        if not paragraphs or a == b:
            return
        text, at = odf_atoms(paragraphs)
        where = aligned(b, text)
        points = []  # (x, order, element): at one place, ends, deletions, starts
        for op, o1, o2, n1, n2 in word_ops(a, b):
            if op == "equal":
                continue
            if o2 > o1:
                gone = self.words(a, o1, o2)
                if gone.text_recursive:
                    name = self.region("del", [gone])
                    points.append((where[n1], 1, self.point("text:change", name)))
            # words the file holds (not a comment's placeholder, which is no text there)
            if where[n2] > where[n1]:
                name = self.region("ins")
                points.append((where[n1], 2, self.point("text:change-start", name)))
                points.append((where[n2], 0, self.point("text:change-end", name)))
        # from the end, so the places still to come stay where they were
        for x, _, el in sorted(points, key=lambda p: (p[0], p[1]), reverse=True):
            odf_insert(at, x, el, paragraphs)

    def deletion(self, i: int, before: bool, neighbour):
        """A text:change deleting old line i whole, with the break after it
        (at the start of neighbour, the next paragraph) or, at the end, the
        one before it (after neighbour, the last). The deletion holds the
        neighbour's edge as an empty paragraph of its style (in its list), as
        LibreOffice's does: the paragraph left once it is accepted is the
        neighbour, as it was."""
        line = self.f.old_text[i]
        kind, level = kind_of(line)
        start = len(BULLET) if kind == "item" else 0
        if kind == "heading":
            element = self.words(line, 0, len(line), odfdo.Header(level))
        else:
            element = self.words(line, start, len(line))
        empty = etree.Element(neighbour.tag, attrib=dict(neighbour.attrib))
        item = neighbour.getparent()
        if item is not None and item.tag == odf("text:list-item"):
            wrapper = etree.Element(odf("text:list"))
            etree.SubElement(wrapper, odf("text:list-item")).append(empty)
            empty = wrapper
        name = self.region("del", [element, empty] if before else [empty, element])
        return self.point("text:change", name)

    # Table rows ---------------------------------------------------------------------
    # ODF tracks the text of a row's cells, not the row: LibreOffice (7.2 on)
    # tracks it whole when the row's style says loext:text-changes-only
    # "false", each cell's words a change of their own. A reader of plain
    # ODF sees the cells' words inserted or deleted, the row in place.

    def tracked_row(self, tr) -> None:
        """Give a row a style of its own tracking it whole: its style's
        properties, and loext:text-changes-only="false"."""
        name = tr.get(odf("table:style-name"))
        if name not in self.row_styles:
            auto = self.root.find(odf("office:automatic-styles"))
            if auto is None:
                auto = etree.Element(odf("office:automatic-styles"))
                self.root.find(odf("office:body")).addprevious(auto)
            old = next(
                (
                    st
                    for st in auto.iter(odf("style:style"))
                    if name and st.get(odf("style:name")) == name
                ),
                None,
            )
            style = copy.deepcopy(old) if old is not None else etree.Element(odf("style:style"))
            style.set(odf("style:name"), f"PD_R{len(self.row_styles) + 1}")
            style.set(odf("style:family"), "table-row")
            props = style.find(odf("style:table-row-properties"))
            if props is None:
                props = etree.SubElement(style, odf("style:table-row-properties"))
            props.set(odf("loext:text-changes-only"), "false")
            auto.append(style)
            self.row_styles[name] = style.get(odf("style:name"))
        tr.set(odf("table:style-name"), self.row_styles[name])

    def inserted_row(self, tr, paragraphs) -> None:
        """A row inserted whole: its cells' words inserted, the row tracked."""
        for p in paragraphs:
            if odf_atoms([p])[0].strip():
                name = self.region("ins")
                odf_at_start(p, self.point("text:change-start", name))
                p.append(self.point("text:change-end", name))
        self.tracked_row(tr)

    def deleted_row(self, i: int, like):
        """Old line i, a table row deleted whole, as a row of the new table
        made like the row like (its cells' styles), each cell holding the
        old cell's words deleted, the row tracked."""
        line = self.f.old_text[i]
        row = copy.deepcopy(like)
        cells = [c for c in row if c.tag == odf("table:table-cell")]
        # the old cells, as the line joins them
        bounds, start = [], 0
        while (k := line.find(" | ", start)) != -1:
            bounds.append((start, k))
            start = k + 3
        bounds.append((start, len(line)))
        if len(bounds) > len(cells) and cells:  # the rest in the last cell
            bounds[len(cells) - 1 :] = [(bounds[len(cells) - 1][0], len(line))]
        for c, cell in enumerate(cells):
            first = cell.find(odf("text:p"))
            p = etree.Element(odf("text:p"), attrib=dict(first.attrib) if first is not None else {})
            for child in list(cell):
                cell.remove(child)
            cell.text = None
            cell.append(p)
            if c < len(bounds):
                gone = self.words(line, *bounds[c])
                if gone.text_recursive:
                    p.append(self.point("text:change", self.region("del", [gone])))
        self.tracked_row(row)
        return row

    # The file ----------------------------------------------------------------------

    def row_of(self, j: int, paragraphs):
        """The table row new line j is, None when it is no row."""
        if getattr(self.f.new_text[j], "kind", "") != "row":
            return None
        return next(paragraphs[0].iterancestors(odf("table:table-row")), None)

    def flush(self, pending: list[int], before, before_row, last, last_row) -> None:
        """Put back the lines deleted whole between two lines: a row next to
        a row of the table (before the next, or after the last), a
        paragraph at the start of the next paragraph, or, when there is none
        outside a table, after the last one."""
        at_start = []  # deletions going at the start of before, in order
        for k in pending:
            if getattr(self.f.old_text[k], "kind", "") == "row":
                if before_row is not None:
                    before_row.addprevious(self.deleted_row(k, before_row))
                    continue
                if last_row is not None:
                    row = self.deleted_row(k, last_row)
                    last_row.addnext(row)
                    last_row = row
                    continue
            if before is not None and not in_table(before):
                at_start.append(k)
            elif last is not None and not in_table(last):
                last.append(self.deletion(k, False, last))
            elif before is not None:
                at_start.append(k)
            else:
                body = self.root.find(f".//{odf('office:text')}")
                last = etree.SubElement(body, odf("text:p"))
                last.append(self.deletion(k, False, last))
        for k in reversed(at_start):
            odf_at_start(before, self.deletion(k, True, before))

    def run(self) -> None:
        last = last_row = None  # the last line's last paragraph, its row
        pending: list[int] = []  # lines deleted whole, before the next one
        # the end of a paragraph inserted with no paragraph before it (out
        # of a table), which goes at the start of the next, so the break
        # after it is inserted with it
        end = None
        for i, j in self.f.pairs:
            paragraphs = self.new_paragraphs[j] if j is not None else []
            if not paragraphs:
                if i is not None and j is None:
                    pending.append(i)
                continue
            row = self.row_of(j, paragraphs)
            self.flush(pending, paragraphs[0], row, last, last_row)
            pending = []
            if end is not None:
                if in_table(paragraphs[0]):
                    last.append(end)
                else:
                    odf_at_start(paragraphs[0], end)
                end = None
            if i is None and row is not None:
                self.inserted_row(row, paragraphs)
            elif i is None:
                name = self.region("ins")
                if last is not None and not in_table(last):
                    last.append(self.point("text:change-start", name))
                    paragraphs[-1].append(self.point("text:change-end", name))
                else:
                    odf_at_start(paragraphs[0], self.point("text:change-start", name))
                    end = self.point("text:change-end", name)
            else:
                self.edited(i, j)
            last, last_row = paragraphs[-1], row
        if end is not None:  # no paragraph after it
            last.append(end)
        self.flush(pending, None, None, last, last_row)


def in_table(p) -> bool:
    return next(p.iterancestors(odf("table:table-cell")), None) is not None


def redline_odt(f: FileDiff, path: str, author: str) -> None:
    """Write the new OpenDocument text of f with its changes since the old
    one marked as tracked changes."""
    r = OdtRedline(f, author)
    r.run()
    r.doc.save(path)
