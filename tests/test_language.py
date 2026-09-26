"""The language of the prose: given or guessed, and what it sets in the HTML report."""

import re
import zipfile
from html import unescape

import pytest
from helpers import docx_xml, odt_xml

from prosediff import compare_paths, render
from prosediff.cli import main
from prosediff.flags import flag_css, flag_html
from prosediff.language import detect_language, flag_code, normalize_language
from prosediff.sentences import split_sentences
from prosediff.sources import SourceError, read_document

ITALIAN = (
    "Le politiche regionali per l'economia circolare hanno effetti diversi sulle "
    "imprese esistenti e sulla nascita di nuove imprese, secondo il contesto locale."
)
ITALIAN_EDITED = ITALIAN.replace("effetti diversi", "effetti molto diversi")
ENGLISH = (
    "Most empirical tests of the Porter Hypothesis have focused on how existing "
    "firms respond to regulation, and much less on the entry of new firms."
)


def pair(tmp_path, name, old, new):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "old" / name).write_text(old, encoding="utf-8")
    (tmp_path / "new" / name).write_text(new, encoding="utf-8")
    return tmp_path / "old", tmp_path / "new"


def test_normalize_language():
    assert [normalize_language(v) for v in ("guess", " IT ", "pt_BR", "Document", None)] == [
        "guess",
        "it",
        "pt-br",
        "document",
        "default",
    ]
    with pytest.raises(ValueError, match="not a language code"):
        normalize_language("italian!")


def test_detect_language():
    assert detect_language(ENGLISH) == "en"
    # markup and citation keys do not sway it
    assert detect_language(f"# Titolo\n\n{ITALIAN} [@porter1995; @ambec2013]{{.cite}}") == "it"
    # too short to tell: "Hello world" would read as Fulfulde
    assert detect_language("Hello world") is None


def test_guessed_per_file_and_hyphenated(tmp_path):
    """guess guesses each prose file's language; the HTML report hyphenates by it."""
    old, new = pair(tmp_path, "paper.md", ITALIAN + "\n", ITALIAN_EDITED + "\n")
    c = compare_paths(old, new)
    (f,) = c.files
    assert (f.language, f.language_source) == ("it", "guessed")
    html = render(c)
    assert '<table class="prose" lang="it">' in html
    assert "language: it (guessed)" in html
    # soft hyphens, by Italian rules, and the text otherwise intact
    assert "eco\u00adno\u00admia" in html
    assert "effetti molto diversi" in re.sub(r"<[^>]+>|\u00ad", "", html)


def test_no_language_no_hyphenation(tmp_path):
    """Prose too short to tell, and code, get no language: nothing is
    hyphenated by guess."""
    old, new = pair(tmp_path, "note.md", "Hello world\n", "Hello there\n")
    (tmp_path / "old" / "notes.txt").write_text(ENGLISH + "\n", encoding="utf-8")
    (tmp_path / "new" / "notes.txt").write_text(ENGLISH + " More.\n", encoding="utf-8")
    c = compare_paths(old, new)
    assert [f.language for f in c.files] == ["", ""]
    assert "\u00ad" not in render(c)


def test_cli_language(tmp_path, capsys):
    """A given language is used as it is, not guessed; a bad one is refused."""
    old, new = pair(tmp_path, "paper.md", ITALIAN + "\n", ITALIAN_EDITED + "\n")
    out = tmp_path / "page.html"
    assert main(["--folders", str(old), str(new), "-o", str(out), "--language", "DE"]) == 0
    assert "language: de<" in out.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--folders", str(old), str(new), "--language", "italian!"])
    assert "--language: not a language code" in capsys.readouterr().err


# The language a Word or OpenDocument file marks its text with ------------------

W_LANG = '<w:rPr><w:lang w:val="{}"/></w:rPr>'
GERMAN = (
    "Die Donaudampfschifffahrtsgesellschaft veröffentlichte gestern ihren "
    "ausführlichen Jahresbericht über die wirtschaftliche Entwicklung."
)
GERMAN_EDITED = GERMAN.replace("gestern", "heute")


# A flag in the HTML report: its country, its language, its tooltip.
FLAG = re.compile(
    r'<span class="flag flag-(\w+)" role="img" aria-label="([^"]*)"(?: title="([^"]*)")?></span>'
)


def marked(data: bytes, name: str) -> str | None:
    """The language most of a document's letters are marked with."""
    return read_document(data, name).language


def word_pair(tmp_path, runs_old, runs_new, styles=None):
    """Two Word documents of one paragraph each, runs as (language, text)."""

    def body(runs):
        return (
            "<w:p>"
            + "".join(
                f"<w:r>{W_LANG.format(lang) if lang else ''}<w:t>{t}</w:t></w:r>"
                for lang, t in runs
            )
            + "</w:p>"
        )

    old = docx_xml(tmp_path / "old.docx", body(runs_old))
    new = docx_xml(tmp_path / "new.docx", body(runs_new))
    if styles:
        for path in (old, new):
            with zipfile.ZipFile(path, "a") as z:
                z.writestr("word/styles.xml", styles)
    return old, new


def test_word_language_from_the_document(tmp_path):
    """By default a Word document's language is the one most of its letters
    are marked with, not a guess: here, English marks on Italian text."""
    old, new = word_pair(
        tmp_path, [("en-US", ITALIAN), ("it-IT", "Sì.")], [("en-US", ITALIAN_EDITED)]
    )
    (f,) = compare_paths(old, new).files
    assert (f.language, f.language_source) == ("en-us", "document")
    html = render(compare_paths(old, new))
    assert "language: en-us (from the document)" in html
    # one language: one flag, in the file header, which says how it was found
    assert re.findall(FLAG, html) == [("us", "English (United States)", "")]
    # drawn from its SVG, embedded once
    assert html.count('.flag-us { background-image: url("data:image/svg+xml;base64,') == 1
    assert "English (United States): the language most of the document" in html
    # "document" says the same; "guess" guesses
    assert compare_paths(old, new, language="document").files[0].language == "en-us"
    assert compare_paths(old, new, language="guess").files[0].language == "it"


def test_word_language_through_styles(tmp_path):
    """Unmarked runs take the language of the document defaults, through the
    default paragraph style."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    styles = (
        f'<w:styles xmlns:w="{w}"><w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:lang w:val="de-DE"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:rPr><w:lang w:val="it-IT"/></w:rPr></w:style></w:styles>'
    )
    _, new = word_pair(tmp_path, [(None, ITALIAN)], [(None, ITALIAN_EDITED)], styles)
    assert marked(new.read_bytes(), "new.docx") == "it-it"


def test_unmarked_document(tmp_path):
    """A document without language marks is guessed by default, and has no
    language under "document"."""
    old, new = word_pair(tmp_path, [(None, ITALIAN)], [(None, ITALIAN_EDITED)])
    (f,) = compare_paths(old, new).files
    assert (f.language, f.language_source) == ("it", "guessed")
    (f,) = compare_paths(old, new, language="document").files
    assert (f.language, f.language_source) == ("", "")


def test_odt_language(tmp_path):
    """An OpenDocument text's language comes from its styles: a span's over
    its paragraph's, a paragraph style's through its parent."""
    styles = (
        '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Base"/>'
        '<style:style style:name="Base" style:family="paragraph">'
        '<style:text-properties fo:language="it" fo:country="IT"/></style:style>'
        '<style:style style:name="T1" style:family="text">'
        '<style:text-properties fo:language="en" fo:country="GB"/></style:style>'
    )
    english = '<text:span text:style-name="T1">{}</text:span>'
    body = f'<text:p text:style-name="P1">{ITALIAN} {english.format("Yes.")}</text:p>'
    odt = odt_xml(tmp_path / "a.odt", body, styles).read_bytes()
    assert marked(odt, "a.odt") == "it-it"
    body = f'<text:p text:style-name="P1">{english.format(ENGLISH)}</text:p>'
    odt = odt_xml(tmp_path / "b.odt", body, styles).read_bytes()
    assert marked(odt, "b.odt") == "en-gb"
    with pytest.raises(SourceError):
        marked(b"not a zip", "c.odt")


def test_document_language_refuses_other_files(tmp_path, capsys):
    """ "document" is for Word and OpenDocument files: a Markdown or text file
    is an error; by default it is guessed."""
    old, new = pair(tmp_path, "paper.md", ITALIAN + "\n", ITALIAN_EDITED + "\n")
    with pytest.raises(SourceError, match=r"paper\.md is not a Word or OpenDocument"):
        compare_paths(old, new, language="document")
    assert main(["--folders", str(old), str(new), "--language", "document"]) == 1
    assert "paper.md is not a Word or OpenDocument" in capsys.readouterr().err
    assert compare_paths(old, new).files[0].language_source == "guessed"


def german_cell(html: str) -> str:
    cells = re.findall(r'<td class="code right" lang="de-de">(.*?)</td>', html)
    assert len(cells) == 1
    return cells[0]


def test_word_paragraph_languages(tmp_path):
    """A paragraph marked with another language than most of the document
    is hyphenated by that language's rules, in a cell that says so."""

    def para(lang, text):
        return f'<w:p><w:r>{W_LANG.format(lang)}<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'

    old = docx_xml(tmp_path / "old.docx", para("it-IT", ITALIAN) + para("de-DE", GERMAN))
    new = docx_xml(
        tmp_path / "new.docx", para("it-IT", ITALIAN_EDITED) + para("de-DE", GERMAN_EDITED)
    )
    doc = read_document(new.read_bytes(), "new.docx")
    assert ([b.language for b in doc.blocks], doc.language) == (["it-it", "de-de"], "it-it")
    c = compare_paths(old, new)
    (f,) = c.files
    assert (f.language, f.language_source) == ("it-it", "document")
    assert [(r.left_lang, r.right_lang) for r in f.rows] == [
        ("it-it", "it-it"),
        ("de-de", "de-de"),
    ]
    assert f.mixed_languages
    html = render(c)
    assert "\u00ad" in german_cell(html)
    # a flag before each paragraph's number, none in the header
    flags = re.findall(FLAG, html)
    assert [code for code, _, _ in flags] == ["it", "it", "de", "de"]
    assert html.count(".flag-it {") == html.count(".flag-de {") == 1
    assert (
        unescape(flags[2][2])
        == "German (Germany): this paragraph's language, as the document marks it"
    )
    # a language given, or guessed, is the language of every paragraph
    (f,) = compare_paths(old, new, language="en").files
    assert [r.right_lang for r in f.rows] == ["", ""]
    assert not f.mixed_languages


def test_odt_paragraph_languages(tmp_path):
    styles = (
        '<style:style style:name="IT" style:family="paragraph">'
        '<style:text-properties fo:language="it" fo:country="IT"/></style:style>'
        '<style:style style:name="DE" style:family="paragraph">'
        '<style:text-properties fo:language="de" fo:country="DE"/></style:style>'
    )

    def body(italian, german):
        return (
            f'<text:p text:style-name="IT">{italian}</text:p>'
            f'<text:p text:style-name="DE">{german}</text:p>'
        )

    old = odt_xml(tmp_path / "old.odt", body(ITALIAN, GERMAN), styles)
    new = odt_xml(tmp_path / "new.odt", body(ITALIAN_EDITED, GERMAN_EDITED), styles)
    c = compare_paths(old, new, by_sentence=True)
    (f,) = c.files
    assert f.language == "it-it"
    assert [r.right_lang for r in f.rows] == ["it-it", "de-de"]
    assert "\u00ad" in german_cell(render(c))


def test_sentences_split_by_each_line_language():
    """A line with a language of its own is split by that language's rules:
    English knows "Mr." ends no sentence; the stand-in rule for an unknown
    language does not."""
    line = "Mr. Smith went home. He slept."
    assert len(split_sentences([line], "xx")[0]) == 3
    assert len(split_sentences([line], "xx", ["en"])[0]) == 2
    assert len(split_sentences([line], "xx", [None])[0]) == 3


def test_flags():
    """A country's flag, or a globe for a language spoken in no one country."""
    assert (flag_code("it"), flag_code("en"), flag_code("pt-BR")) == ("it", "us", "br")
    assert flag_code("es-419") == flag_code("xx") == ""
    assert 'class="flag flag-br"' in flag_html("br", "pt-br", "tip")
    assert 'class="flag globe" role="img" aria-label="Spanish (Latin America)"' in flag_html(
        "", "es-419"
    )
    assert flag_css({"it", "zz"}).count("background-image") == 1
