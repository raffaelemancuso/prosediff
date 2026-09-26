"""The formatted view: inline Markdown styles, character by character."""

import pytest

from prosediff import compare_paths, render
from prosediff.diff import word_diff
from prosediff.mdstyle import md_styles, styled


def classes_of(line, text):
    styles = md_styles(line)
    k = line.index(text)
    return styles[k]


@pytest.mark.parametrize(
    "line,text,cls",
    [
        ("a **bold** b", "bold", "strong"),
        ("a *it* b", "it", "em"),
        ("a `x*y*` b", "x", "code"),
        ("see [the site](http://x.org) now", "the site", "link"),
        ("# Title", "Title", "h1"),
        ("> quoted", "quoted", "quote"),
        ("as shown [@smith2020, p. 3]", "@smith", "cite"),
    ],
)
def test_md_styles(line, text, cls):
    assert cls in classes_of(line, text)


@pytest.mark.parametrize(
    "line,syntax",
    [
        ("a **bold** b", "**"),
        ("# Title", "# "),
        ("see [x](http://y) now", "](http://y)"),
        ("[w]{.smallcaps}", "]{.smallcaps}"),
    ],
)
def test_md_syntax_is_marked_hideable(line, syntax):
    styles = md_styles(line)
    k = line.index(syntax)
    assert all("syn" in styles[i] for i in range(k, k + len(syntax)))


def test_code_is_not_styled_inside():
    styles = md_styles("`a *b* c`")
    assert "em" not in styles[4]


def test_styled_runs():
    html = str(styled("a **b**", md_styles("a **b**")))
    assert html == (
        'a <span class="s-syn">**</span><span class="s-strong">b</span>'
        '<span class="s-syn">**</span>'
    )


def test_tracked_changes_are_styled_with_author_and_date():
    """Tracked changes kept as markup: the text marked as an insertion or a
    deletion, with who made it and when; the span syntax hideable."""
    line = (
        '[new]{.insertion author="Riccardo" date="2026-09-25T10:15:00Z"}'
        '[old]{.deletion author="Laura" date="2026-09-24T09:00:00Z"}'
    )
    out = str(styled(line, md_styles(line)))
    assert (
        '<span class="s-tc-ins" data-author="Riccardo" data-date="2026-09-25 10:15">new</span>'
        in out
    )
    assert (
        '<span class="s-tc-del" data-author="Laura" data-date="2026-09-24 09:00">old</span>' in out
    )
    assert '<span class="s-syn">]{.insertion' in out


def test_word_diff_keeps_styles_inside_changes():
    old, new = "a **big** dog", "a **small** dog"
    w = word_diff(old, new, md_styles(old), md_styles(new))
    assert '<span class="s-strong">small</span>' in str(w.right)
    assert '<span class="s-syn">**</span>' in str(w.right)


def test_markdown_files_are_styled(tmp_path):
    def page(ext):
        old, new = tmp_path / f"old{ext}", tmp_path / f"new{ext}"
        old.write_text("a *b*\n")
        new.write_text("a *c*\n")
        return render(compare_paths(old, new))

    html = page(".md")
    assert "s-em" in html and "s-syn" in html
    assert "s-em" not in page(".txt").split("<main>")[1]
