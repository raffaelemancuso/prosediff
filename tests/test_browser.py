"""The page's interactive parts, driven in a real (headless) browser.

Skipped when Playwright's Chromium is not installed
(uv run playwright install chromium).
"""

import pytest

from sidediff import compare, render

sync_api = pytest.importorskip("playwright.sync_api")

NOTE = '[Old remark.]{.comment-start id="1" author="Anna" date="2026-09-23T10:15:00Z"}'


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser not downloaded
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture
def page_file(builder, tmp_path):
    """A page with two changes far apart in a Markdown file (so unchanged
    lines are folded between them), bold text, and a new comment between
    them."""
    lines = [f"Line {i} of the text." for i in range(40)]
    builder.write("doc.md", "\n".join(lines) + "\n")
    base = builder.commit("first")
    lines[20] += NOTE  # a new comment, on a line whose text did not change
    lines[1] = "Line 1 with **bold** words."
    lines[38] = "Line 38 changed."
    builder.write("doc.md", "\n".join(lines) + "\n")
    target = builder.commit("second")
    out = tmp_path / "page.html"
    out.write_text(render(compare(builder.path, base, target, context=3)), encoding="utf-8")
    return out


@pytest.fixture
def page(browser, page_file):
    context = browser.new_context()
    p = context.new_page()
    p.goto(page_file.as_uri())
    yield p
    context.close()


def test_next_and_previous_change(page):
    counter = page.locator(".toolbar .counter")
    assert counter.inner_text() == "2 changes"
    page.keyboard.press("n")
    assert counter.inner_text() == "change 1 of 2"
    page.keyboard.press("n")
    assert counter.inner_text() == "change 2 of 2"
    assert page.locator("tr.current").count() == 1
    page.keyboard.press("n")  # wraps around
    assert counter.inner_text() == "change 1 of 2"
    page.keyboard.press("p")
    assert counter.inner_text() == "change 2 of 2"
    page.click('[data-nav="-1"]')
    assert counter.inner_text() == "change 1 of 2"


def test_show_unchanged_lines(page):
    hidden = page.locator("tbody[hidden]").first
    assert not hidden.is_visible()
    folds = page.locator("tbody[hidden]").count()
    page.locator(".expand").first.click()
    assert page.locator("tbody[hidden]").count() == folds - 1
    assert page.get_by_text("Line 10 of the text.").first.is_visible()


def test_collapse_and_expand_all(page):
    page.click('[data-files="close"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => !d.open)")
    page.keyboard.press("n")  # jumping to a change opens its file
    assert page.evaluate("document.querySelector('details.file').open")
    page.click('[data-files="close"]')
    page.click('[data-files="open"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => d.open)")


def test_one_column_view_is_remembered(page):
    page.keyboard.press("u")
    assert "unified" in page.evaluate("document.body.className")
    assert page.get_attribute('[data-toggle="unified"]', "aria-pressed") == "true"
    # an unchanged row shows its new side only
    left = page.locator("tr.equal td.left").first
    assert left.evaluate("e => getComputedStyle(e).display") == "none"
    page.reload()
    assert "unified" in page.evaluate("document.body.className")
    page.click('[data-toggle="unified"]')
    assert "unified" not in page.evaluate("document.body.className")


def test_formatted_view_hides_markdown_syntax(page):
    syntax = page.locator(".s-syn").first
    assert syntax.is_visible()
    page.keyboard.press("f")
    assert not syntax.is_visible()
    bold = page.locator(".s-strong").first
    assert bold.evaluate("e => getComputedStyle(e).fontWeight") == "700"


def test_edited_lines_untinted_unless_asked(page):
    cell = page.locator("tr.replace td.right").first
    background = "e => getComputedStyle(e).backgroundImage"
    untinted = cell.evaluate(background)
    page.keyboard.press("t")
    assert cell.evaluate(background) != untinted
    # the tint stops above the space between paragraphs
    assert "calc" in cell.evaluate("e => getComputedStyle(e).backgroundSize")
    assert page.get_attribute('[data-toggle="tint"]', "aria-pressed") == "true"
    page.reload()
    assert page.locator("tr.replace td.right").first.evaluate(background) != untinted
    page.click('[data-toggle="tint"]')
    assert page.locator("tr.replace td.right").first.evaluate(background) == untinted


def test_space_between_paragraphs(page):
    cell = page.locator("tr.replace td.right").first
    gap = "e => parseFloat(getComputedStyle(e).paddingBottom)"
    default = cell.evaluate(gap)
    assert default > 0
    assert page.inner_text(".gap-value") == "0.75"
    page.click('[data-gap="1"]')
    assert cell.evaluate(gap) > default
    assert page.inner_text(".gap-value") == "1.00"
    page.reload()  # remembered
    assert page.locator("tr.replace td.right").first.evaluate(gap) > default
    for _ in range(20):
        page.keyboard.press("[")
    assert page.locator("tr.replace td.right").first.evaluate(gap) == 0
    assert page.is_disabled('[data-gap="-1"]')


def test_colour_blind_palette(page):
    before = page.evaluate("getComputedStyle(document.body).getPropertyValue('--ins-line')")
    page.keyboard.press("c")
    after = page.evaluate("getComputedStyle(document.body).getPropertyValue('--ins-line')")
    assert before.strip() != after.strip()


def test_comment_link_goes_to_its_row(page):
    page.locator(".comments-panel a").first.click()
    # the page reacts to the hash change, which the browser fires afterwards
    page.wait_for_selector("tr.target", state="visible", timeout=5_000)
    target = page.locator("tr.target")
    assert target.count() == 1
    assert "Line 20" in target.inner_text()


def test_comment_tooltip(page):
    marker = page.locator(".comment").first
    marker.hover()
    tip = page.locator("#tip")
    assert tip.is_visible()
    assert tip.locator("b").inner_text() == "Anna"
    assert tip.locator("b").evaluate("e => getComputedStyle(e).fontWeight") == "700"
    assert "Old remark." in tip.inner_text()
    when = tip.locator(".when")
    assert when.inner_text() == "2026-09-23 10:15"
    body_colour = page.evaluate("getComputedStyle(document.body).color")
    assert when.evaluate("e => getComputedStyle(e).color") != body_colour
    # the author, the comment and the date on separate lines
    tops = tip.evaluate("t => [...t.children].map(c => c.getBoundingClientRect().top)")
    assert tops == sorted(tops) and len(set(tops)) == 3
    page.mouse.move(0, 0)
    assert not tip.is_visible()


def test_one_tooltip_at_a_time(browser, builder, tmp_path):
    """A comment inside a changed word, on a line with many changes: hovering
    the comment shows the comment only, not the change nor the line's list."""
    note = '[Why?]{.comment-start id="1" author="Anna" date="2026-09-23T10:15:00Z"}'
    words = [f"w{i}" for i in range(30)]
    builder.write("doc.md", "This " + " ".join(words) + " end.\n")
    base = builder.commit("first")
    changed = [f"W{i}" if i % 2 else w for i, w in enumerate(words)]
    builder.write("doc.md", f"{note}These " + " ".join(changed) + " end.\n")
    target = builder.commit("second")
    out = tmp_path / "page.html"
    out.write_text(render(compare(builder.path, base, target)), encoding="utf-8")
    context = browser.new_context()
    page = context.new_page()
    page.goto(out.as_uri())
    tip = page.locator("#tip")
    # no browser tooltip is left in the tables: they would overlap ours
    assert page.locator("table [title]").count() == 0
    # the change tooltips are off by default: a changed word shows nothing
    assert not page.is_checked('[data-tips="changes"]')
    page.locator("ins").nth(3).hover()
    assert not tip.is_visible()
    page.check('[data-tips="changes"]')
    page.locator(".comment").first.hover()
    assert tip.locator("b").inner_text() == "Anna" and "Why?" in tip.inner_text()
    assert "changed" not in tip.inner_text()
    # a changed word shows its own change
    page.locator("ins").nth(3).hover()
    assert tip.inner_text().startswith("changed")
    assert tip.locator(".line").count() == 1
    # the line lists its changes, at most 12, then how many more
    cell = page.locator("td.code.right").first
    box = cell.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] - 3)
    assert tip.locator(".line").count() == 12
    assert tip.locator(".more").inner_text().startswith("… and ")

    # the change tooltips switched off: changes show nothing, comments still do
    page.uncheck('[data-tips="changes"]')
    page.locator("ins").nth(3).hover()
    assert not tip.is_visible()
    page.locator(".comment").first.hover()
    assert tip.locator("b").inner_text() == "Anna"
    # the comment tooltips off too, after a reload: the choice is remembered
    page.uncheck('[data-tips="comments"]')
    page.reload()
    assert not page.is_checked('[data-tips="changes"]')
    assert not page.is_checked('[data-tips="comments"]')
    page.locator(".comment").first.hover()
    assert not tip.is_visible()
    # changes back on: a comment inside a changed word shows the change
    page.check('[data-tips="changes"]')
    page.locator(".comment").first.hover()
    assert tip.is_visible() and tip.inner_text().startswith(("changed", "added"))
    context.close()
