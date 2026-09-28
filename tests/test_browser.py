"""The HTML report's interactive parts, driven in a real (headless) browser.

Skipped when Playwright's Chromium is not installed
(uv run playwright install chromium).
"""

import pytest
from conftest import RepoBuilder

from prosediff import Options, compare, compare_paths, render

sync_api = pytest.importorskip("playwright.sync_api")

NOTE = '[Old remark.]{.comment-start id="1" author="Anna" date="2026-09-23T10:15:00Z"}'


def open_report(browser, tmp_path, comparison, **render_options):
    """The HTML report of comparison, opened in a new page of browser."""
    out = tmp_path / "page.html"
    out.write_text(render(comparison, **render_options), encoding="utf-8")
    page = browser.new_context().new_page()
    page.goto(out.as_uri())
    return page


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser not downloaded
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def page_file(tmp_path_factory):
    """An HTML report with two changes far apart in a Markdown file (so unchanged
    lines are folded between them), bold text, and a new comment between
    them; built once, as no test changes it."""
    tmp_path = tmp_path_factory.mktemp("page")
    builder = RepoBuilder(tmp_path / "repo")
    lines = [f"Line {i} of the text." for i in range(40)]
    builder.write("doc.md", "\n".join(lines) + "\n")
    base = builder.commit("first")
    lines[20] += NOTE  # a new comment, on a line whose text did not change
    lines[1] = "Line 1 with **bold** words."
    lines[38] = "Line 38 changed."
    builder.write("doc.md", "\n".join(lines) + "\n")
    target = builder.commit("second")
    out = tmp_path / "page.html"
    out.write_text(
        render(compare(builder.path, base, target, Options(context=3))), encoding="utf-8"
    )
    return out


@pytest.fixture
def page(browser, page_file):
    context = browser.new_context()
    p = context.new_page()
    p.goto(page_file.as_uri())
    yield p
    context.close()


def test_printed_page(browser, page_file):
    """Printed, even from a browser in dark mode: light colours, the toolbar
    left out, the comments written out, quiet folds, the column headings of
    each file repeated on every page."""
    context = browser.new_context(color_scheme="dark")
    p = context.new_page()
    p.goto(page_file.as_uri())

    def style(selector, prop, pseudo="null"):
        return p.locator(selector).first.evaluate(
            f"e => getComputedStyle(e, {pseudo}).getPropertyValue('{prop}')"
        )

    assert style("body", "background-color") != "rgb(255, 255, 255)"
    assert style("thead.print", "display") == "none"
    p.emulate_media(media="print")
    assert style("body", "background-color") == "rgb(255, 255, 255)"
    assert style(".toolbar", "display") == "none"
    assert style("thead.print", "display") == "table-header-group"
    assert style(".expand .verb", "display") == "none"
    assert "Anna" in style(".comment", "content", "'::after'")
    assert style("tr.skip td", "background-color") == "rgba(0, 0, 0, 0)"
    context.close()


def test_next_and_previous_change(page):
    counter = page.locator(".toolbar .counter")
    # two edits, and a paragraph that only gained a comment, in between
    assert counter.inner_text() == "3"
    assert counter.get_attribute("data-help") == "3 changes"
    page.keyboard.press("n")
    assert counter.inner_text() == "1 / 3"
    assert counter.get_attribute("data-help") == "Change 1 of 3"
    page.keyboard.press("n")
    assert counter.inner_text() == "2 / 3"
    assert "Line 20" in page.locator("tr.current").inner_text()
    page.keyboard.press("n")
    page.keyboard.press("n")  # wraps around
    assert counter.inner_text() == "1 / 3"
    page.keyboard.press("p")
    assert counter.inner_text() == "3 / 3"
    page.click('[data-nav="-1"]')
    assert counter.inner_text() == "2 / 3"


def test_unchanged_lines_and_files_open_and_close(page):
    """A fold opens on its own; the files close and open all at once, and
    jumping to a change opens its file."""
    hidden = page.locator("tbody[hidden]").first
    assert not hidden.is_visible()
    folds = page.locator("tbody[hidden]").count()
    page.locator(".expand").first.click()
    assert page.locator("tbody[hidden]").count() == folds - 1
    assert page.get_by_text("Line 10 of the text.").first.is_visible()
    page.click('[data-files="close"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => !d.open)")
    page.keyboard.press("n")
    assert page.evaluate("document.querySelector('details.file').open")
    page.click('[data-files="close"]')
    page.click('[data-files="open"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => d.open)")


def test_views_are_remembered(page):
    """One column (u), raw Markdown (f), tinted edits (t) and comments
    written out (i): each switched on by its key, remembered across a
    reload, switched off by its button."""
    background = "e => getComputedStyle(e).backgroundImage"
    after = "e => getComputedStyle(e, '::after').content"

    def tint():
        return page.locator("tr.replace td.right").first.evaluate(background)

    def comment():
        return page.locator(".comment").first.evaluate(after)

    # the defaults: two columns, formatted, untinted, comments as markers
    assert "unified" not in page.evaluate("document.body.className")
    assert not page.locator(".s-syn").first.is_visible()
    assert page.get_attribute('[data-toggle="formatted"]', "aria-pressed") == "true"
    assert page.locator(".s-strong").first.evaluate("e => getComputedStyle(e).fontWeight") == "700"
    untinted = tint()
    assert comment() in ("none", "normal")
    for key in "ufti":
        page.keyboard.press(key)
    # an unchanged row shows its new side only
    left = page.locator("tr.equal td.left").first
    assert left.evaluate("e => getComputedStyle(e).display") == "none"
    assert page.locator(".s-syn").first.is_visible()
    assert tint() != untinted
    # the tint stops above the space between paragraphs
    cell = page.locator("tr.replace td.right").first
    assert "calc" in cell.evaluate("e => getComputedStyle(e).backgroundSize")
    assert "Anna" in comment() and "Old remark." in comment()
    for name in ("unified", "tint", "inline"):
        assert page.get_attribute(f'[data-toggle="{name}"]', "aria-pressed") == "true"
    page.reload()
    assert "unified" in page.evaluate("document.body.className")
    assert page.locator(".s-syn").first.is_visible()
    assert tint() != untinted
    assert "Anna" in comment()
    for name in ("unified", "tint", "inline"):
        page.click(f'[data-toggle="{name}"]')
    assert "unified" not in page.evaluate("document.body.className")
    assert tint() == untinted
    assert comment() in ("none", "normal")


def test_toolbar_help_tooltips(page):
    tip = page.locator("#tip")
    assert page.locator(".toolbar [title]").count() == 0  # no browser tooltip
    page.hover('[data-toggle="formatted"]')
    assert tip.is_visible()
    assert tip.locator("b").inner_text() == "Formatted"
    assert tip.locator(".when").inner_text() == "key: f"
    page.hover('[data-tips="comments"]')
    assert tip.locator("b").inner_text() == "Comment tooltips"
    page.mouse.move(0, 0)
    assert not tip.is_visible()


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
    # an ARIA spinbutton: focused, the arrow keys, Page Up and End step it
    spin = page.get_by_role("spinbutton", name="Space between paragraphs")
    spin.focus()
    page.keyboard.press("ArrowUp")
    assert spin.get_attribute("aria-valuenow") == "0.25"
    assert spin.get_attribute("aria-valuetext") == "0.25 lines"
    page.keyboard.press("PageUp")
    assert spin.get_attribute("aria-valuenow") == "1.25"
    page.keyboard.press("End")
    assert spin.inner_text() == "3.00" and page.is_disabled('[data-gap="1"]')
    page.keyboard.press("n")  # the HTML report's own keys still work from it
    assert page.locator("tr.current").count() == 1


def test_comment_link_goes_to_its_row(page):
    page.locator(".comments-panel a").first.click()
    # the HTML report reacts to the hash change, which the browser fires afterwards
    page.wait_for_selector("tr.target", state="visible", timeout=5_000)
    target = page.locator("tr.target")
    assert target.count() == 1
    assert "Line 20" in target.inner_text()
    # the paragraph the comment is in flashes, and so does its marker
    page.wait_for_selector("tr.target td.code.right.flash", timeout=5_000)
    assert page.locator("tr.target td.code.right .comment.flash").count() == 1
    page.wait_for_selector("td.code.flash", state="detached", timeout=5_000)
    # following the same link again flashes it again
    page.locator(".comments-panel a").first.click()
    page.wait_for_selector("tr.target td.code.right.flash", timeout=5_000)


def test_comments_sorted_by_place_or_date(browser, tmp_path):
    """The comments panel lists its comments in reading order, or by date,
    oldest first; the choice is remembered."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("First line.\n\nSecond line.\n", encoding="utf-8")
    late = '[Late.]{.comment-start id="1" author="A" date="2026-09-25T10:00:00Z"}'
    early = '[Early.]{.comment-start id="2" author="A" date="2026-09-20T10:00:00Z"}'
    new.write_text(f"First line.{late}\n\nSecond line.{early}\n", encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))

    def listed():
        return page.locator(".comments-panel li a").all_inner_texts()

    assert listed() == ["Late.", "Early."]
    page.click('[data-sort="date"]')
    assert listed() == ["Early.", "Late."]
    assert page.get_attribute('[data-sort="date"]', "aria-pressed") == "true"
    page.reload()
    assert listed() == ["Early.", "Late."]
    page.click('[data-sort="place"]')
    assert listed() == ["Late.", "Early."]
    # the order shown, clicked again, is reversed, and remembered
    page.click('[data-sort="place"]')
    assert listed() == ["Early.", "Late."]
    assert page.inner_text('[data-sort="place"] .dir') == "↑"
    page.click('[data-sort="date"]')
    assert listed() == ["Early.", "Late."]
    page.click('[data-sort="date"]')
    assert listed() == ["Late.", "Early."]
    assert page.get_attribute('[data-sort="date"]', "aria-label") == "date, descending"
    page.reload()
    assert listed() == ["Late.", "Early."]


def anchored_report(browser, tmp_path, **context_options):
    """A report of a comment added on a few words of one paragraph, and one
    whose words run on into the next paragraph."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One two three four.\n\nFive six seven.\n", encoding="utf-8")
    one = '[Here.]{.comment-start id="1" author="A" date="2026-09-25T10:00:00Z"}'
    two = '[Across.]{.comment-start id="2" author="A" date="2026-09-26T10:00:00Z"}'
    new.write_text(
        f'One {one}two three[]{{.comment-end id="1"}} {two}four.\n\n'
        'Five six[]{.comment-end id="2"} seven.\n',
        encoding="utf-8",
    )
    out = tmp_path / "page.html"
    out.write_text(render(compare_paths(old, new, Options(context=None))), encoding="utf-8")
    page = browser.new_context(**context_options).new_page()
    page.goto(out.as_uri())
    return page


def highlighted(page) -> str:
    return page.evaluate(
        """() => { const h = CSS.highlights.get("comment-flash");
                   return h ? [...h].map(r => r.toString()).join("|") : null; }"""
    )


def test_comment_link_flashes_the_words_it_is_anchored_to(browser, tmp_path):
    """Following a comment's link highlights the words it is anchored to,
    not its paragraph, across paragraphs too; then the highlight goes."""
    page = anchored_report(browser, tmp_path)
    page.locator(".comments-panel a", has_text="Here.").click()
    page.wait_for_function('CSS.highlights.has("comment-flash")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "two three"
    assert page.locator("td.code.flash").count() == 0
    assert page.locator(".comment.flash").count() == 1
    page.wait_for_function('!CSS.highlights.has("comment-flash")', timeout=5_000)
    page.locator(".comments-panel a", has_text="Across.").click()
    page.wait_for_function('CSS.highlights.has("comment-flash")', timeout=5_000)
    first, second = highlighted(page).replace("­", "").split("|")
    assert first == "four." and second.startswith("Five six")
    assert "seven" not in second


def test_comment_whose_words_are_gone_flashes_its_marker_only(browser, tmp_path):
    """A comment whose words went (its text ends where it starts) rings its
    marker and highlights nothing: not its paragraph either."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One two.\n", encoding="utf-8")
    note = '[Gone.]{.comment-start id="1" author="A" date="2026-09-25T10:00:00Z"}'
    new.write_text(f'One two.{note}[]{{.comment-end id="1"}}\n', encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    page.locator(".comments-panel a").first.click()
    page.wait_for_selector(".comment.flash", timeout=5_000)
    assert highlighted(page) is None
    assert page.locator("td.code.flash").count() == 0


def test_comment_marker_click_flashes_its_words(browser, tmp_path):
    """Clicking a comment's marker in the text, or Enter on it, flashes the
    words it is anchored to, as its link in the panel does."""
    page = anchored_report(browser, tmp_path)
    marker = page.locator('td.code .comment[data-text="Here."]').first
    marker.click()
    page.wait_for_function('CSS.highlights.has("comment-flash")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "two three"
    assert page.locator(".comment.flash").count() == 1
    page.wait_for_function('!CSS.highlights.has("comment-flash")', timeout=5_000)
    page.locator('td.code .comment[data-text="Across."]').first.focus()
    page.keyboard.press("Enter")
    page.wait_for_function('CSS.highlights.has("comment-flash")', timeout=5_000)
    assert highlighted(page).replace("­", "").startswith("four.|Five six")


def test_comment_flash_with_reduced_motion(browser, tmp_path):
    """A system asking for reduced motion still sees the words flash: once,
    steadily, not animated away."""
    page = anchored_report(browser, tmp_path, reduced_motion="reduce")
    page.locator(".comments-panel a", has_text="Here.").click()
    page.wait_for_function('CSS.highlights.has("comment-flash")', timeout=5_000)
    page.wait_for_timeout(1_000)
    assert highlighted(page).replace("­", "") == "two three"
    page.wait_for_function('!CSS.highlights.has("comment-flash")', timeout=5_000)


def test_comments_filtered_by_status(browser, tmp_path):
    """The comments panel shows all its comments, or only the new or the
    removed ones, with a dashed line between those shown; remembered."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    gone = '[Gone.]{.comment-start id="1" author="A" date="2026-09-20T10:00:00Z"}'
    came = '[Came.]{.comment-start id="2" author="A" date="2026-09-25T10:00:00Z"}'
    old.write_text(f"First line.{gone}\n\nSecond line.\n", encoding="utf-8")
    new.write_text(f"First line.\n\nSecond line.{came}\n", encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))
    items = page.locator(".comments-panel li")

    def shown():
        return [li.locator("a").inner_text() for li in items.all() if li.is_visible()]

    def top_borders():
        return [
            li.evaluate("e => getComputedStyle(e).borderTopStyle")
            for li in items.all()
            if li.is_visible()
        ]

    assert shown() == ["Gone.", "Came."]
    assert top_borders() == ["none", "dashed"]
    page.click('[data-show="new"]')
    assert shown() == ["Came."] and top_borders() == ["none"]
    page.click('[data-show="removed"]')
    assert shown() == ["Gone."]
    page.reload()
    assert shown() == ["Gone."]
    assert page.get_attribute('[data-show="removed"]', "aria-pressed") == "true"
    page.click('[data-show="all"]')
    assert shown() == ["Gone.", "Came."]


def test_comment_tooltip(page):
    """Hovering a comment shows it; hovering a change, or the row it is in,
    shows nothing; the comment tooltips can be switched off, remembered."""
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
    # changes carry no tooltip, neither on the word nor on the line
    assert page.locator("table [title]").count() == 0
    page.locator("ins").first.hover()
    assert not tip.is_visible()
    box = page.locator("tr.replace td.code.right").first.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] - 3)
    assert not tip.is_visible()
    page.uncheck('[data-tips="comments"]')
    page.reload()
    assert not page.is_checked('[data-tips="comments"]')
    page.locator(".comment").first.hover()
    assert not tip.is_visible()


def test_tracked_and_formatting_changes(browser, tmp_path):
    """A tracked change kept as markup says who made it and when. Off by
    default, the formatting changes of a document stay out of sight; the
    switch (m) unfolds the lines that have them, marks them and says on
    hover what changed."""
    from helpers import docx_xml, run

    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "p.md").write_text("The cat sat on the mat.\n", encoding="utf-8")
    (new / "p.md").write_text(
        'The cat [slept]{.insertion author="Riccardo" date="2026-09-25T10:15:00Z"} on the mat.\n',
        encoding="utf-8",
    )
    docx_xml(old / "d.docx", f"<w:p>{run('Some words.')}</w:p><w:p>{run('Old.')}</w:p>")
    docx_xml(
        new / "d.docx",
        f"<w:p>{run('Some ')}{run('words', '<w:rPr><w:b/></w:rPr>')}{run('.')}</w:p>"
        f"<w:p>{run('New.')}</w:p>",
    )
    page = open_report(browser, tmp_path, compare_paths(old, new))
    tip = page.locator("#tip")
    tracked = page.locator(".s-tc-ins").first
    assert "underline" in tracked.evaluate("e => getComputedStyle(e).textDecorationLine")
    tracked.hover()
    assert tip.locator("b").inner_text() == "Tracked insertion"
    assert "by Riccardo" in tip.inner_text() and "2026-09-25 10:15" in tip.inner_text()
    page.mouse.move(0, 0)
    # formatting changes: shown by default, their lines unfolded
    mark = page.locator(".fmt").first
    assert mark.is_visible() and page.locator(".fmt-count").is_visible()
    assert mark.evaluate("e => getComputedStyle(e).borderBottomStyle") == "dotted"
    mark.hover()
    assert tip.locator("b").inner_text() == "Formatting changed"
    assert 'made "words" bold' in tip.inner_text()
    page.mouse.move(0, 0)
    page.keyboard.press("m")  # hidden: no mark, no count
    assert mark.evaluate("e => getComputedStyle(e).borderBottomStyle") == "none"
    assert not page.locator(".fmt-count").is_visible()
    page.context.close()


def test_moved_line_numbers_tell_where(browser, tmp_path):
    """Hovering a moved line's number tells where it went; hovering the
    number where it arrived, where it came from; and whether it was edited
    on the way."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    moved = "This paragraph is moved further down in the new version of the text."
    edited = "This paragraph is moved further down in the new version of this text."
    old.write_bytes(f"{moved}\n\nFirst kept paragraph.\n\nSecond kept paragraph.\n".encode())
    new.write_bytes(f"First kept paragraph.\n\nSecond kept paragraph.\n\n{edited}\n".encode())
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    tip = page.locator("#tip")
    page.locator("tr.moved-out td.no.l").hover()
    # a Markdown file's numbers are its lines, blank ones included
    assert tip.locator("b").inner_text() == "Moved to line 5"
    assert "edited on the way" in tip.inner_text()
    page.locator("tr.moved-in td.no.r").hover()
    assert tip.locator("b").inner_text() == "Moved from line 1"
    page.context.close()


def test_both_splits_switch_counts_and_move_lines(browser, tmp_path):
    """A report holding both splits shows one at a time, the toolbar (or s)
    switching; each tells its counts; a line joins a moved line's ends, and
    the toggle (l) hides them."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(
        b"The first sentence stays here. This sentence moves to the end of the text.\n\n"
        b"A middle paragraph that stays.\n"
    )
    new.write_bytes(
        b"The first sentence stays here.\n\n"
        b"A middle paragraph that stays. This sentence moves to the end of the text.\n"
    )
    page = open_report(
        browser,
        tmp_path,
        compare_paths(old, new, Options(context=None)),
        sentences=compare_paths(old, new, Options(context=None, by_sentence=True)),
    )
    paragraphs = page.locator('.split[data-split="paragraph"]')
    sentences = page.locator('.split[data-split="sentence"]')
    assert paragraphs.is_visible() and not sentences.is_visible()
    assert "Paragraphs: 2 changed" in paragraphs.locator(".units").inner_text()
    page.keyboard.press("s")
    assert sentences.is_visible() and not paragraphs.is_visible()
    assert "1 moved" in sentences.locator(".units").inner_text()
    switch = page.locator("[data-split-switch]")
    assert switch.get_attribute("aria-label") == "Compared sentence by sentence"
    assert switch.locator("svg.sentences").is_visible()
    assert not switch.locator("svg.paragraphs").is_visible()
    # drawn on the next frame: the assertions wait for it
    sync_api.expect(sentences.locator("svg.move-links path")).to_have_count(1)
    page.keyboard.press("l")
    sync_api.expect(sentences.locator("svg.move-links")).to_have_count(0)
    page.locator("[data-split-switch]").click()
    assert paragraphs.is_visible()
    # each view says which it is, as does the toolbar, and switches to the other
    assert paragraphs.locator(".view-label b").inner_text() == "Paragraph view"
    assert switch.locator(".split-name").inner_text() == "Paragraphs"
    paragraphs.locator("[data-split-to]").click()
    assert sentences.is_visible() and not paragraphs.is_visible()
    assert sentences.locator(".view-label b").inner_text() == "Sentence view"
    assert switch.locator(".split-name").inner_text() == "Sentences"
    page.context.close()


def test_moved_passage_tells_where_and_is_joined(browser, tmp_path):
    """A sentence moved from one paragraph into another, compared paragraph
    by paragraph: hovering either end tells where it went or came from and
    lights up both; a line joins them."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    moved = "This sentence moves to the end of the text."
    old.write_bytes(f"The first sentence stays here. {moved}\n\nA middle one stays.\n".encode())
    new.write_bytes(f"The first sentence stays here.\n\nA middle one stays. {moved}\n".encode())
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    assert "1 moved passage" in page.locator("details.file summary").inner_text()
    tip = page.locator("#tip")
    page.locator("td.left .moved").hover()
    assert tip.locator("b").inner_text() == "Moved to line 3"
    sync_api.expect(page.locator(".moved.pair-hot")).to_have_count(2)
    page.locator("td.right .moved").hover()
    assert tip.locator("b").inner_text() == "Moved from line 1"
    sync_api.expect(page.locator("svg.move-links path")).to_have_count(1)
    page.context.close()


def test_moved_passages_switch(browser, tmp_path):
    """The "Moved passages" switch: off, a moved passage is removed in one
    place and added in the other, the counts follow and its line goes; the
    key v turns it back on, and the choice is remembered."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    moved = "This sentence moves to the end of the text."
    old.write_bytes(f"The first sentence stays here. {moved}\n\nA middle one stays.\n".encode())
    new.write_bytes(f"The first sentence stays here.\n\nA middle one stays. {moved}\n".encode())
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    switch = page.locator('[data-toggle="passages"]')
    summary = page.locator("details.file summary")
    assert switch.get_attribute("aria-pressed") == "true"
    assert "1 moved passage" in summary.inner_text()
    sync_api.expect(page.locator("svg.move-links path")).to_have_count(1)
    switch.click()
    assert switch.get_attribute("aria-pressed") == "false"
    assert not page.locator("td.left .moved").is_visible()
    assert page.locator("td.left del").filter(has_text="This sentence moves").is_visible()
    assert page.locator("td.right ins").filter(has_text="This sentence moves").is_visible()
    assert "moved passage" not in summary.inner_text()
    sync_api.expect(page.locator("svg.move-links path")).to_have_count(0)
    page.reload()
    assert switch.get_attribute("aria-pressed") == "false"
    page.keyboard.press("v")
    assert page.locator("td.left .moved").is_visible()
    assert "1 moved passage" in summary.inner_text()
    page.context.close()


def test_no_moved_passages_switch_without_passages(page):
    """A report with no moved passage has no switch for them."""
    assert page.locator('[data-toggle="passages"]').count() == 0
