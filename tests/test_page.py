"""The rendered page: layout, escaping, tooltips, counts, accessibility, views
and print styles (their behaviour: test_browser.py).
"""

import re

from sidediff import compare, render

MOVED = "This sentence travels to the end of the file."


def test_render_side_by_side(two_commits):
    b, base, target = two_commits
    html = render(compare(b.path, base, target))
    assert html.startswith("<!DOCTYPE html>")
    assert '<tr class="replace' in html
    assert ">world</del>" in html and ">there</ins>" in html
    assert 'href="#file-doc-md"' in html and 'id="file-doc-md"' in html


def test_render_tooltips(two_commits):
    b, base, target = two_commits
    html = render(compare(b.path, base, target))
    change = "changed &#34;world&#34; to &#34;there&#34;"
    # on the changed word, and on the whole line (both cells)
    assert f'<ins title="{change}">there</ins>' in html
    assert html.count(f'<td class="code right" title="{change}">') == 1
    assert html.count(f'<td class="code left" title="{change}">') == 1
    assert 'title="added this line"' in html


def test_render_escapes_content_and_subjects(two_commits):
    b, base, target = two_commits
    html = render(compare(b.path, base, target))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "first &lt;draft&gt;" in html


def test_render_no_differences(builder):
    builder.write("f.txt", "x\n")
    sha = builder.commit("only")
    html = render(compare(builder.path, sha, sha))
    assert "No differences between the two sides." in html
    assert "<table" not in html


def test_render_thousand_separators(builder):
    builder.write("f.txt", "")
    base = builder.commit("empty")
    builder.write("f.txt", "".join(f"{i}\n" for i in range(1500)))
    target = builder.commit("big")
    html = render(compare(builder.path, base, target))
    assert "+1,500" in html


def test_render_is_self_contained(two_commits):
    b, base, target = two_commits
    html = render(compare(b.path, base, target))
    assert not re.search(r'<(script|link)\b[^>]*(src|href)="http', html)


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


def test_signs_and_screen_reader_text(builder):
    builder.write("f.txt", "keep\nold\ngone\n")
    base = builder.commit("first")
    builder.write("f.txt", "keep\nnew\nadded\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target))
    assert '<span class="sign" aria-hidden="true">~</span>' in html
    assert '<span class="sr">changed line, old: </span>' in html
    assert '<caption class="sr">Changes in f.txt' in html
    assert 'aria-live="polite"' in html and 'aria-pressed="false"' in html


def test_page_offers_views_and_print_styles(builder):
    builder.write("f.txt", "a\n")
    base = builder.commit("first")
    builder.write("f.txt", "b\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target))
    for view in ("unified", "formatted", "cb"):
        assert f'data-toggle="{view}"' in html
    assert "body.unified tr" in html
    assert "@media print" in html and "beforeprint" in html
