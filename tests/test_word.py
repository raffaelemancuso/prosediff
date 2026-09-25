"""Reading Word documents into Markdown (python-docx, no pandoc)."""

import docx as python_docx
import pytest
from helpers import docx_xml

from sidediff.word import WordError, docx_to_markdown, omml_text


@pytest.fixture
def written(tmp_path):
    """A document written by python-docx: headings, lists, a table,
    formatting, a link and a comment."""
    d = python_docx.Document()
    d.add_heading("The title", level=0)
    d.add_heading("Introduction", level=1)
    p = d.add_paragraph("Plain, ")
    p.add_run("bold").bold = True
    p.add_run(" and ")
    p.add_run("italic ").italic = True
    p.add_run("words.")
    d.add_paragraph("First point.", style="List Bullet")
    d.add_paragraph("Second point.", style="List Number")
    table = d.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "a", (0, 1): "b|c", (1, 0): "1", (1, 1): "2"}.items():
        table.cell(r, c).text = text
    commented = d.add_paragraph("A sentence with a [bracket] and a remark.")
    d.add_comment(commented.runs, text="Is [this] right?", author="Anna", initials="A")
    path = tmp_path / "written.docx"
    d.save(path)
    return docx_to_markdown(path.read_bytes())


def test_structure(written):
    lines = written.strip().split("\n\n")
    assert lines[0] == "# The title"
    assert lines[1] == "# Introduction"
    assert lines[2] == "Plain, **bold** and *italic* words."
    assert lines[3] == "- First point."
    assert lines[4] == "- Second point."
    assert lines[5].split("\n") == ["| a | b\\|c |", "|---|---|", "| 1 | 2 |"]


def test_comment_is_a_span_with_escaped_brackets(written):
    assert '[Is \\[this\\] right?]{.comment-start id="0" author="Anna" date="' in written, written
    assert written.rstrip().endswith("A sentence with a [bracket] and a remark.")


def test_comment_brackets_are_unescaped_when_folded(tmp_path, written):
    from sidediff.comments import Comments, fold_comments

    comments = Comments()
    folded = fold_comments(written, comments)
    (placeholder,) = [ch for ch in folded if 0xE000 <= ord(ch) <= 0xF8FF]
    assert comments.get(placeholder).text == "Is [this] right?"


def test_tracked_changes_three_ways(tmp_path):
    d = docx_xml(
        tmp_path / "c.docx",
        '<w:p><w:r><w:t>start-up</w:t></w:r><w:del w:id="1" w:author="A" w:date="D">'
        '<w:r><w:delText xml:space="preserve"> entry</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="A" w:date="D"><w:r><w:t>s</w:t></w:r></w:ins>'
        '<w:r><w:t xml:space="preserve"> grow.</w:t></w:r></w:p>',
    ).read_bytes()
    # the space of " entry" goes with it: not "start-up s" (jgm/pandoc#4427)
    assert docx_to_markdown(d, "accept").strip() == "start-ups grow."
    assert docx_to_markdown(d, "reject").strip() == "start-up entry grow."
    assert docx_to_markdown(d, "all").strip() == (
        'start-up[ entry]{.deletion author="A" date="D"}[s]{.insertion author="A" date="D"} grow.'
    )
    with pytest.raises(ValueError):
        docx_to_markdown(d, "maybe")


def test_footnotes_numbered_in_order_and_dropped_with_their_text(tmp_path):
    d = docx_xml(
        tmp_path / "f.docx",
        '<w:p><w:r><w:t>One.</w:t></w:r><w:r><w:footnoteReference w:id="7"/></w:r>'
        '<w:del w:id="1" w:author="A"><w:r><w:footnoteReference w:id="3"/></w:r></w:del>'
        '<w:r><w:t xml:space="preserve"> Two.</w:t></w:r>'
        '<w:r><w:footnoteReference w:id="5"/></w:r></w:p>',
        footnotes='<w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>'
        '<w:footnote w:id="3"><w:p><w:r><w:t>Deleted note.</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="5"><w:p><w:r><w:t>Second note.</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="7"><w:p><w:r><w:t>First note.</w:t></w:r></w:p></w:footnote>',
    ).read_bytes()
    assert docx_to_markdown(d).strip().split("\n\n") == [
        "One.[^1] Two.[^2]",
        "[^1]: First note.",
        "[^2]: Second note.",
    ]


def test_equation_as_linear_text(tmp_path):
    math = (
        "<m:oMath><m:sSub><m:e><m:r><m:t>DV</m:t></m:r></m:e><m:sub><m:r><m:t>it</m:t></m:r>"
        "</m:sub></m:sSub><m:r><m:t>=β⋅</m:t></m:r><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num>"
        "<m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>"
    )
    d = docx_xml(tmp_path / "m.docx", f"<w:p>{math}</w:p>").read_bytes()
    assert docx_to_markdown(d).strip() == "DV_(it) = β ⋅ (a)/(b)"


def test_omml_brackets_and_sums():
    from docx.oxml import parse_xml

    m = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
    el = parse_xml(
        f"<m:oMath {m}><m:nary><m:naryPr><m:chr m:val='∑'/></m:naryPr><m:sub><m:r><m:t>i</m:t>"
        "</m:r></m:sub><m:sup/><m:e><m:d><m:e><m:r><m:t>x</m:t></m:r></m:e></m:d></m:e></m:nary>"
        "</m:oMath>"
    )
    assert omml_text(el) == "∑_(i) (x)"


def test_not_a_word_document():
    with pytest.raises(WordError):
        docx_to_markdown(b"not a zip")
