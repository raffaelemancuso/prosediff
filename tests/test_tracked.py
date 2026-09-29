"""Documents of tracked changes (prosediff.tracked): two versions without
tracked changes, compared into a .docx or an .odt whose changes, accepted,
give the new version and, rejected, the old one."""

import re
import zipfile

import docx
import odfdo
import pytest
from docx_plus.revisions import read_revisions

from prosediff import document, tracked
from prosediff.cli import main
from prosediff.diff import Options, compare_paths
from prosediff.render import check_split, format_of, write_output
from prosediff.sources import read_document

OLD = [
    ("h", "Introduction"),
    ("p", "Innovation policy has long relied on grants to firms."),
    ("p", "It studies the effect of **regional** subsidies on patenting."),
    ("p", "We find a small but significant effect."),
    ("p", "Limitations are discussed in the last section."),
    ("l", "Patents granted"),
    ("l", "Firms treated"),
]
NEW = [
    ("h", "Introduction and aims"),
    ("p", "Innovation policy has long relied on grants and loans to firms."),
    ("p", "It studies the effect of *regional* subsidies on patenting."),
    ("p", "We find a large and significant effect."),
    ("l", "Patents granted"),
    ("l", "Firms treated, by region"),
    ("l", "Workers hired"),
    ("p", "Robustness checks confirm every result."),
]


def parts(text):
    for m in re.finditer(r"\*\*(.+?)\*\*|\*(.+?)\*|([^*]+)", text):
        yield (m[1] or m[2] or m[3]), bool(m[1]), bool(m[2])


def word_file(path, blocks, comment=False):
    d = docx.Document()
    for kind, text in blocks:
        if kind == "h":
            d.add_heading(text, level=1)
            continue
        p = d.add_paragraph(style="List Bullet" if kind == "l" else None)
        for t, bold, italic in parts(text):
            r = p.add_run(t)
            r.bold, r.italic = bold or None, italic or None
    if comment:
        d.add_comment(d.paragraphs[3].runs[0], text="Say how large.", author="Anna Rossi")
    d.save(path)
    return path


def odt_file(path, blocks):
    d = odfdo.Document("text")
    body = d.body
    body.clear()
    d.insert_style(odfdo.Style("text", name="B", bold=True), automatic=True)
    d.insert_style(odfdo.Style("text", name="I", italic=True), automatic=True)
    for kind, text in blocks:
        if kind == "h":
            body.append(odfdo.Header(1, text))
        elif kind == "l":
            last = body.children[-1] if body.children else None
            if last is None or last.tag != "text:list":
                last = odfdo.List()
                body.append(last)
            last.append(odfdo.ListItem(text))
        else:
            p = odfdo.Paragraph()
            for t, bold, italic in parts(text):
                p.append(odfdo.Span(t, style="B" if bold else "I") if bold or italic else t)
            body.append(p)
    d.save(path)
    return path


def pair(tmp_path, ext, comment=False):
    if ext == "docx":
        return (
            word_file(tmp_path / "old.docx", OLD),
            word_file(tmp_path / "new.docx", NEW, comment),
        )
    return odt_file(tmp_path / "old.odt", OLD), odt_file(tmp_path / "new.odt", NEW)


def lines(path, changes):
    """The text prosediff reads from a document, its tracked changes
    accepted or rejected: a line per paragraph."""
    doc = read_document(path.read_bytes(), path.name, changes)
    return [str(line) for line in document.lines(doc, lambda mark: "")]


def same(a, b, changes):
    """Whether two documents read alike, b's tracked changes settled so."""
    return lines(a, "accept") == lines(b, changes)


@pytest.mark.parametrize("source", ["docx", "odt"])
@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_accepted_the_new_version_rejected_the_old(tmp_path, source, fmt):
    """Neither version has tracked changes; the output's changes, accepted,
    read as the new version and, rejected, as the old one."""
    old, new = pair(tmp_path, source)
    c = compare_paths(str(old), str(new), Options())
    out = write_output(c, tmp_path / f"out.{fmt}", fmt)
    assert out == tmp_path / f"out.{fmt}"
    assert same(new, out, "accept")
    assert same(old, out, "reject")


def test_word_revisions_formatting_and_comments(tmp_path):
    """Each change is a revision of its own: words inserted and deleted,
    paragraphs inserted and deleted whole with their marks (no empty
    paragraph left behind), a formatting change; a new comment is a
    comment over its words."""
    old, new = pair(tmp_path, "docx", comment=True)
    out = write_output(compare_paths(str(old), str(new), Options()), tmp_path / "o.docx", "docx")
    d = docx.Document(str(out))
    kinds = [(r.revision_type, r.text) for r in read_revisions(d)]
    assert ("insertion", " and aims") in kinds
    assert ("deletion", "small but") in kinds
    assert ("deletion", "Limitations are discussed in the last section.") in kinds
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert xml.count("<w:rPrChange") == 1  # regional: bold, then italic
    assert xml.count("<w:pPr><w:rPr><w:del ") + xml.count("<w:rPr><w:del ") >= 1
    assert re.search(r'<w:pStyle w:val="ListBullet"/>.*?<w:rPr><w:ins ', xml)
    comments = list(d.comments)
    assert [c.text for c in comments] == ["Say how large."]
    assert comments[0].author == "Anna Rossi"
    styles = [p.style.name for p in d.paragraphs]
    assert styles[0] == "Heading 1" and styles.count("List Bullet") == 3


def test_odt_regions_and_comments(tmp_path):
    """The .odt holds one region per change, a deleted paragraph kept in
    its region; a new comment is an annotation over its words."""
    old = word_file(tmp_path / "old.docx", OLD)
    new = word_file(tmp_path / "new.docx", NEW, comment=True)
    out = write_output(compare_paths(str(old), str(new), Options()), tmp_path / "o.odt", "odt")
    content = zipfile.ZipFile(out).read("content.xml").decode()
    assert content.index("<text:tracked-changes") < content.index("<text:h")
    assert re.search(
        r"<text:deletion>.*?<text:p[^>]*>Limitations are discussed in the last section\.</text:p>"
        r"<text:p(/>|></text:p>)</text:deletion>",
        content,
    )
    assert content.count("<office:annotation ") == 1 and "Say how large." in content
    assert content.count("<office:annotation-end") == 1


def test_a_deleted_last_paragraph(tmp_path):
    """A paragraph deleted after the last one goes with the break before it."""
    old = word_file(tmp_path / "old.docx", [("p", "One."), ("p", "Two.")])
    new = word_file(tmp_path / "new.docx", [("p", "One.")])
    c = compare_paths(str(old), str(new), Options())
    for fmt in ("docx", "odt"):
        out = write_output(c, tmp_path / f"o.{fmt}", fmt)
        assert lines(out, "accept") == ["One."]
        assert lines(out, "reject") == ["One.", "Two."]


def test_several_files_each_under_its_name(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for name in ("x", "y"):
        word_file(tmp_path / "a" / f"{name}.docx", [("p", f"Old {name}.")])
        word_file(tmp_path / "b" / f"{name}.docx", [("p", f"New {name}.")])
    c = compare_paths(str(tmp_path / "a"), str(tmp_path / "b"), Options())
    out = write_output(c, tmp_path / "o.docx", "docx")
    texts = [p.text for p in docx.Document(str(out)).paragraphs]
    assert texts[0] == "x.docx" and "y.docx" in texts


def test_a_locked_document_is_written_beside_it(tmp_path):
    """Open in Word (locked), the output goes beside it, with a warning."""
    target = tmp_path / "o.docx"
    written = []

    def write(path):
        if path == str(target):
            raise PermissionError(13, "locked")
        written.append(path)

    with pytest.warns(UserWarning, match="locked"):
        other = tracked.save(write, target)
    assert re.fullmatch(r"o__locked_\d{8}_\d{6}\.docx", other.name)
    assert written == [str(other)]


def test_formats_by_name_and_split():
    assert format_of("x.docx") == "docx" and format_of("x.ODT") == "odt"
    check_split("paragraph", "docx")
    with pytest.raises(ValueError, match="paragraph by paragraph"):
        check_split("sentence", "odt")


def test_cli_writes_tracked_changes(tmp_path, capsys):
    old, new = pair(tmp_path, "docx")
    out = tmp_path / "tracked.docx"
    assert main(["--files", str(old), str(new), "-o", str(out)]) == 0
    assert same(new, out, "accept") and same(old, out, "reject")
    assert main(["--files", str(old), str(new), "--format", "odt"]) == 0
    assert (tmp_path / "old_vs_new.odt").is_file()


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_a_comment_keeps_its_paragraphs_and_italics(tmp_path, fmt):
    from test_comments import commented_documents

    old, new = commented_documents(tmp_path, "docx")
    out = write_output(compare_paths(str(old), str(new), Options()), tmp_path / f"o.{fmt}", fmt)
    if fmt == "docx":
        (comment,) = list(docx.Document(str(out)).comments)
        assert [p.text for p in comment.paragraphs] == [
            "Please cite:",
            "",
            "See Research Policy, 49.",
        ]
        assert [r.text for r in comment.paragraphs[2].runs if r.italic] == ["Research Policy"]
    else:
        content = zipfile.ZipFile(out).read("content.xml").decode()
        note = re.search(r"<office:annotation .*?</office:annotation>", content, re.S)[0]
        assert re.findall(r"<text:p[^>]*>(.*?)</text:p>|<text:p/>", note)[:2] == [
            "Please cite:",
            "",
        ]
        assert re.search(r'<text:span text:style-name="T_\d+">Research Policy</text:span>', note)
