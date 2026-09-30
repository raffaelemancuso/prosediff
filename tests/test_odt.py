"""Reading OpenDocument texts (.odt) into Markdown (odfdo), as Word documents are."""

import pytest
from helpers import markdown_of, odt_xml

from prosediff import compare_paths

STYLES = (
    '<style:style style:name="B" style:family="text">'
    '<style:text-properties fo:font-weight="bold"/></style:style>'
    '<style:style style:name="I" style:family="text">'
    '<style:text-properties fo:font-style="italic"/></style:style>'
    # italic through its parent
    '<style:style style:name="I2" style:family="text" style:parent-style-name="I"/>'
    '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Title"/>'
)


def changes(cid, kind, content=""):
    return (
        f'<text:changed-region text:id="{cid}"><text:{kind}><office:change-info>'
        "<dc:creator>Ben</dc:creator><dc:date>2026-02-02T10:00:00</dc:date>"
        f"</office:change-info>{content}</text:{kind}></text:changed-region>"
    )


def test_structure(tmp_path):
    d = odt_xml(
        tmp_path / "s.odt",
        '<text:p text:style-name="P1">The title</text:p>'
        '<text:h text:outline-level="2">Methods</text:h>'
        '<text:p>Plain, <text:span text:style-name="B">bold</text:span> and '
        '<text:span text:style-name="I2">italic </text:span>words,<text:s text:c="2"/>'
        'a <text:a xlink:href="https://example.org">link</text:a>.</text:p>'
        "<text:list><text:list-item><text:p>First point.</text:p></text:list-item>"
        "<text:list-item><text:p>Second point.</text:p></text:list-item></text:list>"
        "<table:table><table:table-row><table:table-cell><text:p>a</text:p></table:table-cell>"
        "<table:table-cell><text:p>b|c</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:table-cell><text:p>1</text:p></table:table-cell>"
        "<table:table-cell><text:p>2</text:p></table:table-cell></table:table-row></table:table>"
        '<text:p>With a note.<text:note text:note-class="footnote"><text:note-citation>1'
        "</text:note-citation><text:note-body><text:p>The note.</text:p></text:note-body>"
        "</text:note></text:p>",
        STYLES,
    ).read_bytes()
    assert markdown_of(d, "a.odt").strip().split("\n\n") == [
        "# The title",
        "## Methods",
        "Plain, **bold** and *italic* words,  a [link](https://example.org).",
        "- First point.",
        "- Second point.",
        "| a | b\\|c |\n|---|---|\n| 1 | 2 |",
        "With a note.[^1]",
        "[^1]: The note.",
    ]


def test_tracked_changes_three_ways(tmp_path):
    d = odt_xml(
        tmp_path / "c.odt",
        "<text:tracked-changes>"
        + changes("d1", "deletion", "<text:p> entry</text:p>")
        + changes("i1", "insertion")
        + "</text:tracked-changes>"
        '<text:p>start-up<text:change text:change-id="d1"/>'
        '<text:change-start text:change-id="i1"/>s<text:change-end text:change-id="i1"/>'
        " grow.</text:p>",
    ).read_bytes()
    assert markdown_of(d, "a.odt", "accept-all").strip() == "start-ups grow."
    assert markdown_of(d, "a.odt", "reject-all").strip() == "start-up entry grow."
    date = 'author="Ben" date="2026-02-02T10:00:00"'
    assert markdown_of(d, "a.odt", "show").strip() == (
        f"start-up[ entry]{{.deletion {date}}}[s]{{.insertion {date}}} grow."
    )
    with pytest.raises(ValueError):
        markdown_of(d, "a.odt", "maybe")


def test_comment_is_a_span_and_survives_a_rejected_insertion(tmp_path):
    d = odt_xml(
        tmp_path / "k.odt",
        "<text:tracked-changes>" + changes("i1", "insertion") + "</text:tracked-changes>"
        "<text:p>Kept.</text:p>"
        '<text:p><text:change-start text:change-id="i1"/><office:annotation>'
        "<dc:creator>Anna</dc:creator><dc:date>2026-01-01T09:30:00.123</dc:date>"
        "<text:p>Is [this] right?</text:p></office:annotation>New paragraph."
        '<text:change-end text:change-id="i1"/></text:p>',
    ).read_bytes()
    span = r'[Is \[this\] right?]{.comment-start id="0" author="Anna" date="2026-01-01T09:30:00"}'
    assert markdown_of(d, "a.odt", "accept-all").strip().split("\n\n") == [
        "Kept.",
        span + "New paragraph.",
    ]
    # the paragraph goes, its comment joins the one before
    assert markdown_of(d, "a.odt", "reject-all").strip() == "Kept." + span


def test_compared_like_a_word_document(tmp_path):
    a = odt_xml(tmp_path / "a.odt", "<text:p>The cat sat on the mat.</text:p>")
    b = odt_xml(tmp_path / "b.odt", "<text:p>The cat slept on the mat.</text:p>")
    (f,) = compare_paths(a, b).files
    assert f.markdown and f.note == "read from OpenDocument, tracked changes accepted"
    assert f.additions == f.deletions == 1


def test_broken_odt_is_listed_as_binary(tmp_path):
    (tmp_path / "a.odt").write_bytes(b"not a zip")
    (tmp_path / "b.odt").write_bytes(b"not a zip either")
    (f,) = compare_paths(tmp_path / "a.odt", tmp_path / "b.odt").files
    assert f.binary and "not a readable OpenDocument text" in f.note


def test_paragraphs_deleted_whole_come_back_rejected(tmp_path):
    """LibreOffice keeps a paragraph deleted whole in its region with the
    (empty) start of the next, a text:change at that start, in a list when
    the next is a list item: rejected, it comes back as a paragraph of its
    own, a deleted heading a heading; accepted, it goes, no empty paragraph
    left; shown ("show"), it is one run of deleted text, as before."""
    tracked = changes(
        "ct1", "deletion", '<text:h text:outline-level="2">Old heading</text:h><text:p/>'
    ) + changes(
        "ct2",
        "deletion",
        "<text:p>Old paragraph.</text:p>"
        "<text:list><text:list-item><text:p/></text:list-item></text:list>",
    )
    d = odt_xml(
        tmp_path / "d.odt",
        f"<text:tracked-changes>{tracked}</text:tracked-changes>"
        '<text:p>Kept.</text:p><text:p><text:change text:change-id="ct1"/>Next.</text:p>'
        '<text:list><text:list-item><text:p><text:change text:change-id="ct2"/>An item.'
        "</text:p></text:list-item></text:list>",
    ).read_bytes()
    assert markdown_of(d, "d.odt", "reject-all").strip().split("\n\n") == [
        "Kept.",
        "## Old heading",
        "Next.",
        "Old paragraph.",
        "- An item.",
    ]
    assert markdown_of(d, "d.odt", "accept-all").strip().split("\n\n") == [
        "Kept.",
        "Next.",
        "- An item.",
    ]
    shown = markdown_of(d, "d.odt", "show").strip().split("\n\n")
    assert shown[1].startswith("[Old heading ]{.deletion") and shown[1].endswith("Next.")


def test_rows_tracked_whole(tmp_path):
    """A table row LibreOffice tracks whole (its style's
    loext:text-changes-only "false", each cell's words a change of their
    own) goes, deleted and accepted or inserted and rejected; it stays
    otherwise."""
    tracked = changes("d1", "deletion", "<text:p>Gone</text:p>") + changes("i1", "insertion")
    styles = (
        '<style:style style:name="R" style:family="table-row">'
        '<style:table-row-properties loext:text-changes-only="false"/></style:style>'
    )

    def cell(content):
        return f"<table:table-cell><text:p>{content}</text:p></table:table-cell>"

    d = odt_xml(
        tmp_path / "r.odt",
        f"<text:tracked-changes>{tracked}</text:tracked-changes>"
        "<table:table><table:table-row>" + cell("Kept") + "</table:table-row>"
        '<table:table-row table:style-name="R">'
        + cell('<text:change text:change-id="d1"/>')
        + "</table:table-row>"
        '<table:table-row table:style-name="R">'
        + cell('<text:change-start text:change-id="i1"/>New<text:change-end text:change-id="i1"/>')
        + "</table:table-row></table:table>",
        styles,
    ).read_bytes()
    assert markdown_of(d, "r.odt", "accept-all").split("\n")[:3] == ["| Kept |", "|---|", "| New |"]
    assert markdown_of(d, "r.odt", "reject-all").split("\n")[:3] == [
        "| Kept |",
        "|---|",
        "| Gone |",
    ]


def test_a_comment_in_a_table_cell_is_not_its_text(tmp_path):
    """A comment in a table cell is a comment: its paragraphs are not read
    as the cell's text."""
    from helpers import odt_xml

    from prosediff.document import lines
    from prosediff.sources import read_document

    cell = (
        '<table:table-cell><text:p><office:annotation office:name="c1">'
        "<dc:creator>Anna</dc:creator><text:p>Check the unit.</text:p>"
        '</office:annotation>watt<office:annotation-end office:name="c1"/></text:p>'
        "</table:table-cell>"
    )
    path = odt_xml(
        tmp_path / "t.odt",
        "<table:table><table:table-row><table:table-cell><text:p>Energy</text:p>"
        f"</table:table-cell>{cell}</table:table-row></table:table>",
    )
    doc = read_document(path.read_bytes(), path.name, "accept-all")
    assert [str(line) for line in lines(doc, lambda mark: "")] == ["Energy | watt"]


def test_an_insertion_in_a_link_is_settled(tmp_path):
    """Words inserted as a tracked change within a link are kept when the
    changes are accepted and dropped when they are rejected."""
    from helpers import odt_xml

    from prosediff.document import lines
    from prosediff.sources import read_document

    region = (
        '<text:tracked-changes><text:changed-region text:id="c1" xml:id="c1"><text:insertion>'
        "<office:change-info><dc:creator>Anna</dc:creator><dc:date>2026-01-01T00:00:00"
        "</dc:date></office:change-info></text:insertion></text:changed-region>"
        "</text:tracked-changes>"
    )
    path = odt_xml(
        tmp_path / "l.odt",
        f'{region}<text:p>See <text:a xlink:href="https://example.org">the '
        '<text:change-start text:change-id="c1"/>original <text:change-end text:change-id="c1"/>'
        "paper</text:a>.</text:p>",
    )
    for changes, text in (
        ("accept-all", "See the original paper."),
        ("reject-all", "See the paper."),
    ):
        doc = read_document(path.read_bytes(), path.name, changes)
        assert [str(line) for line in lines(doc, lambda mark: "")] == [text]
