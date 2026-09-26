"""Reading OpenDocument texts (.odt) into Markdown (odfdo), as Word documents are."""

import pytest
from helpers import odt_xml

from prosediff import compare_paths
from prosediff.odt import odt_to_markdown

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
    assert odt_to_markdown(d).strip().split("\n\n") == [
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
    assert odt_to_markdown(d, "accept").strip() == "start-ups grow."
    assert odt_to_markdown(d, "reject").strip() == "start-up entry grow."
    date = 'author="Ben" date="2026-02-02T10:00:00"'
    assert odt_to_markdown(d, "all").strip() == (
        f"start-up[ entry]{{.deletion {date}}}[s]{{.insertion {date}}} grow."
    )
    with pytest.raises(ValueError):
        odt_to_markdown(d, "maybe")


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
    assert odt_to_markdown(d, "accept").strip().split("\n\n") == ["Kept.", span + "New paragraph."]
    # the paragraph goes, its comment joins the one before
    assert odt_to_markdown(d, "reject").strip() == "Kept." + span


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
