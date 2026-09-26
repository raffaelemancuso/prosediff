"""The rendered page: layout, escaping, tooltips, counts, accessibility, views
and print styles (their behaviour: test_browser.py).
"""

import re

from prosediff import compare, render

MOVED = "This sentence travels to the end of the file."


def test_render_side_by_side(two_commits):
    """A self-contained page, its content and commit subjects escaped, the
    changes without tooltips (the highlighting says it), and a footer that
    names the program."""
    b, base, target = two_commits
    html = render(compare(b.path, base, target))
    assert html.startswith("<!DOCTYPE html>")
    assert '<tr class="replace' in html
    assert "<ins>there</ins>" in html and "<del>world</del>" in html
    assert '<td class="code right">' in html
    tables = re.findall(r"<table\b.*?</table>", html, re.S)
    assert tables and not any("title=" in t for t in tables)
    assert 'href="#file-doc-md"' in html and 'id="file-doc-md"' in html
    assert not re.search(r'<(script|link)\b[^>]*(src|href)="http', html)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "first &lt;draft&gt;" in html
    footer = re.search(r"<footer>(.*?)</footer>", html, re.S).group(1)
    assert 'by <a href="https://github.com/raffaelemancuso/prosediff">prosediff</a>' in footer


def test_render_thousand_separators(builder):
    builder.write("f.txt", "")
    base = builder.commit("empty")
    builder.write("f.txt", "".join(f"{i}\n" for i in range(1500)))
    target = builder.commit("big")
    html = render(compare(builder.path, base, target))
    assert "+1,500" in html


def test_moved_rendered(builder):
    builder.write("f.txt", f"{MOVED}\nkeep one\nkeep two\n")
    base = builder.commit("first")
    builder.write("f.txt", f"keep one\nkeep two\n{MOVED}\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    assert c.moved == 1
    html = render(c)
    assert '<tr class="moved-out' in html and '<tr class="moved-in' in html
    assert "1 moved line" in html


def test_left_out_gap_rendered(builder):
    builder.write("f.txt", "".join(f"{i}\n" for i in range(50)))
    base = builder.commit("first")
    builder.write("f.txt", "".join(f"{i}\n" for i in range(50)).replace("25\n", "X\n"))
    target = builder.commit("second")
    html = render(compare(builder.path, base, target, max_hidden=5))
    assert "left out of the page" in html
    assert "<tbody hidden>" not in html


def test_signs_screen_reader_text_and_print_styles(builder):
    """What the page offers without the browser tests' script: signs beside
    the colours, text for screen readers, a print layout."""
    builder.write("f.txt", "keep\nthe old line of text\ngone\n")
    base = builder.commit("first")
    builder.write("f.txt", "keep\nthe new line of text\nadded\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target))
    assert '<span class="sign" aria-hidden="true">~</span>' in html
    assert '<span class="sr">changed line, old: </span>' in html
    assert '<caption class="sr" lang="en">Changes in f.txt' in html
    assert 'aria-live="polite"' in html and 'aria-pressed="false"' in html
    assert "@media print" in html and "beforeprint" in html
