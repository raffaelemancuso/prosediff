"""One file reviewed alone (--review, the window's "One file" tab): no other
version compared, the AI's review of the whole file, the problems it marked
in the text, and, for a Word document or an OpenDocument text, the file
with its comments and fixes to download. A fake backend stands in for the
models."""

import base64
import re

import pytest
from test_aidocs import ADVICE, FIXED, comments_of, revisions
from test_assess import fake
from test_tracked import NEW, lines, odt_file, word_file

from prosediff import assess as assess_module
from prosediff.aidocs import downloads
from prosediff.assess import (
    ANNOTATE,
    ANNOTATE_REVIEW,
    DOCUMENTS,
    SYSTEM_REVIEW,
    Assessment,
    AssessRequest,
)
from prosediff.cli import main
from prosediff.diff import Options, review_file
from prosediff.render import assess_comparison, render, write_output

REVIEW = """## Verdict
**Fair**: the effect is overstated.

## Summary
A study of regional subsidies.

## Strengths
- Short.

## Problems to fix
1. "a large and significant effect" is not supported.

```json
[{"start": "a large and", "end": "significant effect.",
  "problem": "Nothing supports a large effect.", "solution": "Say a small effect.",
  "replacement": "a small and significant effect."}]
```"""


def document(tmp_path, fmt, comment=False):
    if fmt == "docx":
        return word_file(tmp_path / "paper.docx", NEW, comment)
    return odt_file(tmp_path / "paper.odt", NEW)


def test_a_file_alone_is_its_lines_unchanged(tmp_path):
    """Reviewed alone, a file is one side only: every paragraph an
    unchanged row of the new side, nothing added or removed; its comments
    its own, neither new nor removed."""
    path = document(tmp_path, "docx", comment=True)
    c = review_file(path, Options())
    assert c.single and c.repo_name == "paper.docx" and c.base == c.target
    (f,) = c.files
    assert f.change == "reviewed" and f.old_path is None and f.new_path == "paper.docx"
    assert [r.kind for r in f.rows] == ["equal"] * len(NEW)
    assert all(r.left_no is None and not r.first_of_change for r in f.rows)
    assert f.rows[3].right_no == 4 and "large and significant" in f.rows[3].right
    assert c.change_count == 0 and c.counts.additions == c.counts.deletions == 0
    assert [e.status for e in c.comments] == ["unchanged"]
    marker = re.search(r'<span class="comment[^"]*"', str(f.rows[3].right))[0]
    assert marker == '<span class="comment"'  # no "new" balloon


def test_the_ai_is_sent_the_file_to_review(tmp_path, monkeypatch):
    """The model is sent the whole file, its comments in CriticMarkup, to
    review: no diff, its own instructions and way of marking problems; its
    verdict good, fair or poor."""
    runner = fake(REVIEW)
    monkeypatch.setattr(assess_module, "run_backend", runner)
    c = review_file(document(tmp_path, "docx", comment=True), Options())
    a = assess_comparison(c, AssessRequest("claude", instructions="The journal is Nature."))
    ((_, system, prompt, *_),) = runner.asked
    assert system == SYSTEM_REVIEW + DOCUMENTS + ANNOTATE_REVIEW
    assert '"side"' not in ANNOTATE_REVIEW and '"side"' in ANNOTATE
    assert prompt.startswith("The document to review, paper.docx:\n\n<document>\n# Introduction")
    assert "{>>Anna Rossi" in prompt and "Say how large.<<}We find a large" in prompt
    assert "[-" not in prompt and "The journal is Nature." in prompt
    assert a.kind == "review" and a.verdict == "fair" and a.how == ""
    assert [n.replacement for n in a.annotations] == ["a small and significant effect."]
    with pytest.raises(ValueError, match="no changes whose writing"):
        assess_comparison(c, AssessRequest("claude"), kind="writing")


def test_an_empty_file_is_not_sent(tmp_path, monkeypatch):
    runner = fake(REVIEW)
    monkeypatch.setattr(assess_module, "run_backend", runner)
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    a = assess_comparison(review_file(empty, Options()), AssessRequest("claude"))
    assert a.error == "the document has no text to review" and runner.asked == []


def test_the_report_shows_one_side(tmp_path):
    """The report of a review: one column of text, its problems beside it;
    no changes to step through, no two sides, no counts of changes."""
    c = review_file(document(tmp_path, "docx"), Options())
    a = Assessment("claude", REVIEW, model="opus", annotations=[FIXED, ADVICE], kind="review")
    html = render(c, assessment=a)
    assert "<title>paper.docx: review</title>" in html and '<body class="single">' in html
    assert '<table class="single prose notes-on"' in html
    assert 'td class="code left"' not in html and html.count('td class="code right"') == len(NEW)
    assert '<span class="verdict verdict-fair">Fair</span>' in html
    assert "what the AI made of the document as a whole" in html
    assert 'data-toggle="unified"' not in html and "Base (older)" not in html
    assert "Reviewed whole:</b> 8 paragraphs" in html


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_the_file_comes_back_with_the_ais_fixes(tmp_path, fmt):
    """A Word document (or an OpenDocument text) reviewed alone comes back
    with the AI's fixes as its tracked changes and its problems as comments:
    one document, there being no changes to track."""
    path = document(tmp_path, fmt)
    c = review_file(path, Options())
    a = Assessment("claude", REVIEW, model="opus", annotations=[FIXED, ADVICE], kind="review")
    (d,) = downloads(c, a)
    assert d.name == f"paper_with_AI_fixes.{fmt}"
    assert d.label == "The document with the AI's fixes, tracked"
    out = tmp_path / d.name
    out.write_bytes(d.data)
    before = lines(path, "accept-all")
    assert lines(out, "accept-all") == [s.replace("a large and", "a small and") for s in before]
    assert lines(out, "reject-all") == before
    texts = comments_of(out, fmt)
    assert texts[0].startswith("AI assessment by Claude Code (opus)\nFair: the effect")
    assert "Which checks?\nProposed: Name them." in texts
    if fmt == "docx":
        assert {r.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}author")
                for r in revisions(out)} == {"Claude Code (opus)"}  # fmt: skip
    html = render(c, assessment=a)
    assert html.count('class="ai-document"') == 1
    assert "in the document with its fixes" in html
    data = re.search(r'class="ai-document"[^>]*>([^<]+)<', html)[1]
    assert base64.b64decode(data)[:2] == b"PK"  # a zip: made anew, its dates the time's


def test_markdown_is_reviewed_but_has_no_document(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nWe find a large and significant effect.\n", encoding="utf-8")
    c = review_file(path, Options())
    a = Assessment("claude", REVIEW, annotations=[FIXED], kind="review")
    assert downloads(c, a) == []
    assert [r.kind for r in c.files[0].rows] == ["equal", "equal"]


def test_a_review_is_an_html_report(tmp_path):
    c = review_file(document(tmp_path, "docx"), Options())
    with pytest.raises(ValueError, match="its review is an HTML report"):
        write_output(c, tmp_path / "r.diff", "diff")


def test_cli_review(tmp_path, monkeypatch, capsys):
    """--review FILE --assess AI writes the review next to the file, as
    NAME_review.html; it needs an AI, one file and the HTML report."""
    path = document(tmp_path, "docx")
    runner = fake(REVIEW, model="claude-opus-5-5")
    monkeypatch.setattr(assess_module, "run_backend", runner)
    assert main(["--review", str(path), "--assess", "claude", "--assess-effort", "high"]) == 0
    out = tmp_path / "paper_review.html"
    html = out.read_text(encoding="utf-8")
    assert "verdict-fair" in html and html.count('class="ai-document"') == 1
    assert "by Claude Code (claude-opus-5-5), effort high," in html
    assert "paper.docx reviewed (fair), 1 problem marked" in capsys.readouterr().out
    ((_, _, _, _, effort, _),) = runner.asked
    assert effort == "high"
    for args, message in (
        ([], "say which with --assess"),
        (["--assess", "claude", "other.docx"], "--review takes one FILE"),
        (["--assess", "claude", "-o", "r.diff"], "its output is a .html"),
        (["--assess", "claude", "--split", "sentence"], "paragraph by paragraph"),
        (["--assess", "claude", "--assess-ai-writing"], "--review compares none"),
        (["--assess", "claude", "-p", "x"], "--path picks what to compare"),
    ):
        with pytest.raises(SystemExit):
            main(["--review", str(path), *args])
        assert message in capsys.readouterr().err
    assert main(["--review", str(tmp_path / "gone.docx"), "--assess", "claude"]) == 1
    assert "no such file" in capsys.readouterr().err
