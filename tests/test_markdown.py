"""The formatted view: inline Markdown styles, character by character."""

import pytest

from prosediff import compare, render
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


def test_word_diff_keeps_styles_inside_changes():
    old, new = "a **big** dog", "a **small** dog"
    w = word_diff(old, new, md_styles(old), md_styles(new))
    assert '<span class="s-strong">small</span>' in str(w.right)
    assert '<span class="s-syn">**</span>' in str(w.right)


def test_markdown_files_are_styled(builder):
    builder.write("p.md", "a *b*\n")
    base = builder.commit("first")
    builder.write("p.md", "a *c*\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target))
    assert "s-em" in html and "s-syn" in html
    builder.write("t.txt", "a *b*\n")
    base = builder.commit("third")
    builder.write("t.txt", "a *c*\n")
    target = builder.commit("fourth")
    html = render(compare(builder.path, base, target, paths=["t.txt"]))
    assert "s-em" not in html.split("<main>")[1]
