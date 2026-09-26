"""The rendered page: layout, escaping, tooltips, counts, accessibility, views
and print styles (their behaviour: test_browser.py).
"""

import re

import pytest
from conftest import RepoBuilder

from prosediff import compare, render

MOVED = "This sentence travels to the end of the file."


def gap_file(changed):
    return "".join(f"{i}\n" for i in range(50)).replace("25\n", changed)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """A Markdown file gaining markup to escape, from a commit whose subject
    needs escaping too, a file that grew by 1,500 lines, one with a line
    moved, one with a long unchanged stretch and one with a line changed,
    removed and added; the repository, the commits and the HTML report built once,
    as no test changes them."""
    b = RepoBuilder(tmp_path_factory.mktemp("page") / "repo")
    b.write_all(
        {
            "doc.md": "Hello world.\nSecond line.\n",
            "big.txt": "",
            "moved.txt": f"{MOVED}\nkeep one\nkeep two\n",
            "gap.txt": gap_file("25\n"),
            "signs.txt": "keep\nthe old line of text\ngone\n",
        }
    )
    base = b.commit("first <draft>")
    b.write_all(
        {
            "doc.md": "Hello there.\nSecond line.\n<script>x</script>\n",
            "big.txt": "".join(f"{i}\n" for i in range(1500)),
            "moved.txt": f"keep one\nkeep two\n{MOVED}\n",
            "gap.txt": gap_file("X\n"),
            "signs.txt": "keep\nthe new line of text\nadded\n",
        }
    )
    target = b.commit("second")
    c = compare(b.path, base, target)
    yield b, base, target, c, render(c)
    b.repo.close()


def test_render_side_by_side(page):
    """A self-contained page, its content and commit subjects escaped, the
    changes without tooltips (the highlighting says it), and a footer that
    names the program."""
    html = page[4]
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


def test_render_thousand_separators(page):
    assert "+1,500" in page[4]


def test_moved_rendered(page):
    c, html = page[3], page[4]
    assert c.moved == 1
    assert '<tr class="moved-out' in html and '<tr class="moved-in' in html
    assert "1 moved line" in html


def test_left_out_gap_rendered(page):
    """An unchanged stretch longer than max_hidden is left out of the HTML report,
    not hidden in it."""
    b, base, target, _, html = page
    assert "left out of the HTML report" not in html
    html = render(compare(b.path, base, target, paths=["gap.txt"], max_hidden=5))
    assert "left out of the HTML report" in html
    assert "<tbody hidden>" not in html


def test_signs_screen_reader_text_and_print_styles(page):
    """What the HTML report offers without the browser tests' script: signs beside
    the colours, text for screen readers, a print layout."""
    html = page[4]
    assert '<span class="sign" aria-hidden="true">~</span>' in html
    assert '<span class="sr">changed line, old: </span>' in html
    assert '<caption class="sr" lang="en">Changes in signs.txt' in html
    assert 'aria-live="polite"' in html and 'aria-pressed="false"' in html
    assert "@media print" in html and "beforeprint" in html
