"""Documents compared as paragraphs of styled text, not as Markdown."""

import re

from helpers import docx_xml

from prosediff import compare_paths, render
from prosediff.document import Line, concat, lines, sub
from prosediff.sources import read_document

BOLD = "<w:rPr><w:b/></w:rPr>"


def run(text, props=""):
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def note(number):
    return f'<w:r><w:footnoteReference w:id="{number}"/></w:r>'


def footnote(number, text):
    return f'<w:footnote w:id="{number}"><w:p>{run(text)}</w:p></w:footnote>'


def test_styles_survive_the_pipeline(tmp_path):
    """Bold stays on its words through sentence splitting and footnote
    renumbering; asterisks and brackets typed in Word are text, never
    Markdown."""
    old = docx_xml(
        tmp_path / "old.docx",
        f"<w:p>{run('A ')}{run('bold', BOLD)}{run(' claim.')}{note(1)}"
        f"{run(' Typed *stars* and [brackets].')}</w:p>",
        footnotes=footnote(1, "First note.") + footnote(2, "Second note."),
    )
    new = docx_xml(
        tmp_path / "new.docx",
        f"<w:p>{run('A ')}{run('bold', BOLD)}{run(' claim.')}{note(2)}"
        f"{run(' Typed *stars* and [brackets], edited.')}</w:p>",
        footnotes=footnote(1, "Gone.") + footnote(2, "First note."),
    )
    html = render(compare_paths(old, new, by_sentence=True, context=None))
    assert '<span class="s-strong">bold</span>' in html
    classes = " ".join(re.findall(r'class="([^"]*)"', html))
    assert "*stars*" in html and "s-em" not in classes and "s-syn" not in classes
    assert "**" not in re.sub(r"<[^>]+>", "", html)


def test_lines_of_a_document(tmp_path):
    """Each block a line: headings styled, list items bulleted, table rows
    with their cells, footnotes numbered."""
    doc = docx_xml(
        tmp_path / "d.docx",
        f"<w:p>{run('Plain ')}{run('bold', BOLD)}</w:p>"
        "<w:tbl><w:tr><w:tc><w:p>" + run("a") + "</w:p></w:tc>"
        "<w:tc><w:p>" + run("b|c") + "</w:p></w:tc></w:tr></w:tbl>",
    )
    got = lines(read_document(doc.read_bytes(), "d.docx"), lambda c: "")
    assert [str(x) for x in got] == ["Plain bold", "a | b|c"]
    assert got[0].styles[-4:] == [frozenset({"strong"})] * 4
    assert got[1].kind == "row"


def test_line_operations_keep_styles():
    line = Line("ab[^1]cd", [frozenset({"em"})] * 8, "it", "p")
    replaced = sub(re.compile(r"\[\^1\]"), lambda m: "X", line)
    assert (str(replaced), replaced.lang) == ("abXcd", "it")
    assert replaced.styles == [frozenset({"em"})] * 5
    joined = concat("  ", line.cut(0, 2))
    assert str(joined) == "  ab" and joined.styles[:2] == [frozenset()] * 2
    assert concat("a", "b") == "ab" and not isinstance(concat("a", "b"), Line)


UNDERLINE = '<w:rPr><w:u w:val="single"/></w:rPr>'


def test_formatting_changes(tmp_path):
    """Text made bold or underlined, its words the same, is a formatting
    change: marked on the row, described, counted in the file header, and
    shown unless the page's switch is off (the rows stay equal)."""
    old = docx_xml(
        tmp_path / "old.docx",
        f"<w:p>{run('A plain claim, and a link.')}</w:p><w:p>{run('Other text.')}</w:p>",
    )
    new = docx_xml(
        tmp_path / "new.docx",
        f"<w:p>{run('A ')}{run('plain', BOLD)}{run(' claim, and a ')}{run('link', UNDERLINE)}"
        f"{run('.')}</w:p><w:p>{run('Other text, edited.')}</w:p>",
    )
    c = compare_paths(old, new, context=None)
    (f,) = c.files
    first = f.rows[0]
    assert first.kind == "equal" and not first.changed
    assert first.format_changes == ['made "plain" bold', 'underlined "link"']
    assert f.formatted_rows == 1 and f.change_count == 1
    html = render(c)
    assert '<span class="fmt" data-fmt="made &#34;plain&#34; bold">' in html
    assert 'class="equal fmt-row"' in html
    assert "formatting changed in 1 line" in html
    assert 'data-toggle="formats"' in html


def test_formatting_changes_alone_list_the_file(tmp_path):
    """A document whose only change is formatting still shows its rows."""
    old = docx_xml(tmp_path / "old.docx", f"<w:p>{run('Some words.')}</w:p>")
    new = docx_xml(
        tmp_path / "new.docx", f"<w:p>{run('Some ')}{run('words', BOLD)}{run('.')}</w:p>"
    )
    (f,) = compare_paths(old, new).files
    # the unchanged line is folded away, until the switch shows it
    rows = [row for r in f.rows for row in (r.hidden if r.kind == "skip" else [r])]
    assert [row.format_changes for row in rows] == [['made "words" bold']]


def test_styles_read_and_written(tmp_path):
    """Underline, strikethrough, superscript and subscript are read from Word
    and written in pandoc's Markdown for git diff."""
    from prosediff.document import to_markdown

    props = {
        "u": '<w:rPr><w:u w:val="single"/></w:rPr>',
        "s": "<w:rPr><w:strike/></w:rPr>",
        "sup": '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>',
        "sub": '<w:rPr><w:vertAlign w:val="subscript"/></w:rPr>',
        "bu": '<w:rPr><w:b/><w:u w:val="single"/></w:rPr>',
    }
    body = "<w:p>" + " ".join(run(k, v) + run(" ") for k, v in props.items()) + "</w:p>"
    doc = read_document(docx_xml(tmp_path / "d.docx", body).read_bytes(), "d.docx")
    assert to_markdown(doc) == "[u]{.underline} ~~s~~ ^sup^ ~sub~ **[bu]{.underline}**\n"
    (line,) = lines(doc, lambda c: "")
    assert line.styles[0] == frozenset({"u"}) and line.styles[-1] == frozenset({"strong", "u"})


def test_odt_styles(tmp_path):
    from helpers import odt_xml

    from prosediff.document import to_markdown

    styles = "".join(
        f'<style:style style:name="{name}" style:family="text">'
        f"<style:text-properties {props}/></style:style>"
        for name, props in (
            ("U", 'style:text-underline-style="solid"'),
            ("S", 'style:text-line-through-style="solid"'),
            ("P", 'style:text-position="33% 58%"'),
            ("D", 'style:text-position="sub 58%"'),
        )
    )
    body = (
        "<text:p>"
        + " ".join(f'<text:span text:style-name="{n}">{n}</text:span>' for n in "USPD")
        + "</text:p>"
    )
    doc = read_document(odt_xml(tmp_path / "d.odt", body, styles).read_bytes(), "d.odt")
    assert to_markdown(doc) == "[U]{.underline} ~~S~~ ^P^ ~D~\n"
