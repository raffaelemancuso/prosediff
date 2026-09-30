"""The documents the HTML report offers when the AI marked problems in a
Word document or an OpenDocument text (prosediff.aidocs): the tracked
changes with the AI's comments, and the new version with its fixes."""

import re
import zipfile
from io import BytesIO

import docx
import pytest
from test_tracked import lines, pair

from prosediff.aidocs import Place, downloads, fix_of, haystack, locate
from prosediff.assess import Annotation, Assessment
from prosediff.diff import Options, compare_paths
from prosediff.render import render

MARKDOWN = (
    "## Verdict\n**Mixed**. The effect grew, unsupported.\n\n## What changed\n- One.\n\n"
    "## Improvements\n- None.\n\n## Problems to fix\n1. The effect."
)
FIXED = Annotation(
    "new", "a large and", "significant effect.", "Nothing supports a large effect.",
    "Say a small effect.", "a small and significant effect.",
)  # fmt: skip
ADVICE = Annotation(
    "new", "Robustness checks confirm", "every result.", "Which checks?", "Name them."
)
MISSING = Annotation("new", "words nowhere in it", "", "Not there.", "", "none")
OLD_SIDE = Annotation("old", "Limitations are discussed", "", "Removed.", "Keep it.")


def assessment(notes):
    return Assessment("claude", MARKDOWN, model="opus", annotations=list(notes))


def made(tmp_path, fmt, notes=(FIXED, ADVICE, MISSING, OLD_SIDE)):
    old, new = pair(tmp_path, fmt)
    c = compare_paths(str(old), str(new), Options())
    got = downloads(c, assessment(notes))
    for d in got:
        (tmp_path / d.name).write_bytes(d.data)
    return old, new, got


def test_passages_found_as_the_report_finds_them():
    """Spacing, soft hyphens, curly quotes and comments' placeholders do not
    stop a passage being found; the case, only when it must."""
    lines = ["We find a “large”  ef­fect here.", "Next one."]
    note = Annotation("new", 'a "large" effect', "here.", "p")
    assert locate(lines, note) == Place(0, 8, 0, 33)
    assert locate(lines, Annotation("new", "WE FIND", "", "p")) == Place(0, 0, 0, 7)
    assert locate(lines, Annotation("new", "here. Next", "", "p")) == Place(0, 28, 1, 4)
    assert locate(lines, Annotation("new", "absent", "", "p")) is None
    assert haystack(["a  b", "c"])[0] == "a b c "


def test_a_fix_is_its_words_changed():
    """A fix is the words the replacement changes, whole, from the last;
    none over two paragraphs, or when nothing changes."""
    line = "We find a large and significant effect."
    place = locate([line], FIXED)
    assert fix_of(line, place, FIXED) == [(10, 15, "small")]
    assert fix_of(line, Place(0, 8, 1, 3), FIXED) is None
    number = Annotation("new", "fallen about", "", "p", "", "fallen about 19.6 m")
    line2 = "It has fallen about 39.2 m by then."
    assert fix_of(line2, Place(0, 7, 0, 26), number) == [(20, 24, "19.6")]
    unit = Annotation("new", "9.81", "", "p", "", "9.81 m/s², whatever")
    line3 = "about 9.81 m/s, and more"
    assert fix_of(line3, Place(0, 6, 0, 24), unit) == [(16, 24, "whatever"), (11, 15, "m/s²,")]
    same = Annotation("new", "a large", "", "p", "", "a large")
    assert fix_of(line, locate([line], same), same) is None


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_the_tracked_changes_with_the_ais_comments(tmp_path, fmt):
    """The document of tracked changes still reads as the new version
    accepted and as the old one rejected; it has the verdict and a comment
    for each problem found in the new version, with the change proposed."""
    old, new, got = made(tmp_path, fmt)
    assert [d.name for d in got] == [
        f"new_tracked_with_AI_comments.{fmt}",
        f"new_with_AI_fixes.{fmt}",
    ]
    out = tmp_path / got[0].name
    assert lines(out, "accept-all") == lines(new, "accept-all")
    assert lines(out, "reject-all") == lines(old, "accept-all")
    notes = got[0].notes
    assert sorted(notes) == [-1, 0, 1]  # the verdict, and the two passages found
    assert all(len(n["comments"]) == 1 and not n["changes"] for n in notes.values())
    texts = comments_of(out, fmt)
    assert texts[0].startswith("AI assessment by Claude Code (opus)\nMixed. The effect grew")
    assert "tracked changes" not in texts[0]
    assert "Nothing supports a large effect.\nProposed: Say a small effect." in texts
    assert "Which checks?\nProposed: Name them." in texts


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_the_new_version_with_the_ais_fixes(tmp_path, fmt):
    """The new version with each fix a tracked change of the AI's: accepted,
    the fixed text; rejected, the new version as it was. A problem without a
    fix is a comment with the change proposed."""
    _, new, got = made(tmp_path, fmt)
    out = tmp_path / got[1].name
    accepted = lines(out, "accept-all")
    assert "We find a small and significant effect." in accepted
    assert accepted == [s.replace("a large and", "a small and") for s in lines(new, "accept-all")]
    assert lines(out, "reject-all") == lines(new, "accept-all")
    assert got[1].notes[0]["changes"] and not got[1].notes[1]["changes"]
    texts = comments_of(out, fmt)
    assert "fixes it wrote out are tracked changes" in texts[0]
    assert "Nothing supports a large effect." in texts
    assert "Which checks?\nProposed: Name them." in texts
    if fmt == "docx":
        authors = {r.get(W + "author") for r in revisions(out)}
        assert authors == {"Claude Code (opus)"}


def test_overlapping_fixes_the_second_a_comment(tmp_path):
    """Two fixes of one passage: the first made, the second a comment."""
    other = Annotation(
        "new", "large and significant", "", "Too strong.", "Weaken it.", "big and significant"
    )
    _, _, got = made(tmp_path, "docx", (FIXED, other))
    assert got[1].notes[0]["changes"] and not got[1].notes[1]["changes"]
    assert "Too strong.\nProposed: Weaken it." in comments_of(tmp_path / got[1].name, "docx")


def test_none_for_what_is_not_one_document(tmp_path):
    """No document for Markdown, nor without problems marked, nor when the
    assessment failed."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("One.\n", encoding="utf-8")
    b.write_text("Two.\n", encoding="utf-8")
    md = compare_paths(str(a), str(b), Options())
    assert downloads(md, assessment([FIXED])) == []
    old, new = pair(tmp_path, "docx")
    c = compare_paths(str(old), str(new), Options())
    assert downloads(c, assessment([])) == []
    assert downloads(c, Assessment("claude", error="no login", annotations=[FIXED])) == []
    assert downloads(c, None) == []


def test_the_report_carries_them(tmp_path):
    """The report holds each document, its name and its problems' ids, and
    the script that saves it; without problems, neither."""
    old, new = pair(tmp_path, "docx")
    c = compare_paths(str(old), str(new), Options())
    html = render(c, assessment=assessment([FIXED]))
    assert html.count('class="ai-document"') == 2
    assert "new_with_AI_fixes.docx" in html
    assert "_e.deflateSync" in html  # fflate, inlined
    plain = render(c, assessment=assessment([]))
    assert 'class="ai-document"' not in plain and "_e.deflateSync" not in plain
    off = render(c, assessment=assessment([FIXED]), documents=False)
    assert 'class="ai-document"' not in off


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def revisions(path):
    body = docx.Document(str(path)).element.body
    return [el for el in body.iter() if el.tag in (W + "ins", W + "del")]


def comments_of(path, fmt):
    """Each comment's text, its paragraphs one a line, in the order they
    were made."""
    if fmt == "docx":
        return ["\n".join(p.text for p in c.paragraphs) for c in docx.Document(str(path)).comments]
    from lxml import etree

    root = etree.parse(BytesIO(zipfile.ZipFile(path).read("content.xml")))
    office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    out = []
    for a in root.iter(f"{{{office}}}annotation"):
        out.append("\n".join("".join(p.itertext()) for p in a.iter(f"{{{text}}}p")))
    return sorted(out, key=lambda t: not t.startswith("AI assessment"))


@pytest.mark.parametrize("fmt", ["docx", "odt"])
@pytest.mark.parametrize("which", [0, 1])
def test_each_comment_covers_its_passage(tmp_path, fmt, which):
    """A comment covers its passage's words, no more and no less: in the
    fixed version, as the fix left them."""
    _, _, got = made(tmp_path, fmt, (FIXED,))
    d = got[which]
    ids = d.notes[0]["comments"]
    assert len(ids) == 1
    words = "a small and significant effect." if which else "a large and significant effect."
    assert covered(d.data, fmt, ids[0]) == words


def covered(data, fmt, cid):
    """The text between a comment's start and its end, what is deleted and
    what the comment says left out."""
    z = zipfile.ZipFile(BytesIO(data))
    if fmt == "docx":
        xml = z.read("word/document.xml").decode()
        start = xml.index(f'<w:commentRangeStart w:id="{cid}"/>')
        part = xml[start : xml.index(f'<w:commentRangeEnd w:id="{cid}"/>')]
        part = re.sub(r"<w:delText[^>]*>.*?</w:delText>", "", part)
    else:
        xml = z.read("content.xml").decode()
        start = xml.index(f'office:name="{cid}"')
        part = xml[
            xml.index("</office:annotation>", start) : xml.index(
                f'<office:annotation-end office:name="{cid}"'
            )
        ]
    return re.sub(r"<[^>]+>", "", part)


@pytest.mark.parametrize(
    ("start", "end", "fixed"),
    [
        ("a large and", "significant", "surely a large and significant"),
        ("We find a", "a large", "We find a large, robust"),
    ],
)
def test_words_put_in_at_a_passages_edge_stay_in_its_comment(tmp_path, start, end, fixed):
    """Words the fix puts in before a passage's first word, or after its
    last, are within its comment, the passage inside its paragraph."""
    note = Annotation("new", start, end, "p", "", fixed)
    _, _, got = made(tmp_path, "docx", (note,))
    d = got[1]
    assert d.notes[0]["changes"]
    assert covered(d.data, "docx", d.notes[0]["comments"][0]) == fixed
