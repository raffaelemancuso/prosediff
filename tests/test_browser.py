"""The HTML report's interactive parts, driven in a real (headless) browser.

Skipped when Playwright's Chromium is not installed
(uv run playwright install chromium).
"""

import pytest
from conftest import RepoBuilder
from helpers import (
    ADVICE,
    FIXED,
    assessment,
    authors_of,
    co_authored,
    commented_documents,
    comments_of,
    lines,
    pair,
)

from prosediff import Options, compare, compare_paths, render
from prosediff.aidocs import FIX_APPLIED
from prosediff.odt import read_odt
from prosediff.word import read_docx

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


def test_the_margin_holds_a_card_for_each_comment(page):
    """Beside the row it is in, a card for each comment: whether it is new,
    who wrote it and when, and what it says; the top bar counts them."""
    card = page.locator(".card")
    assert card.count() == 1
    assert card.locator(".chip").inner_text() == "new comment"
    assert card.locator(".who").inner_text() == "Anna"
    assert card.locator(".when").inner_text() == "2026-09-23 10:15"
    assert "Old remark." in card.locator(".body").inner_text()
    row = card.locator("xpath=ancestor::tr")
    assert "Line 20" in row.inner_text() and row.is_visible()
    # beside its row, level with it
    assert abs(card.bounding_box()["y"] - row.bounding_box()["y"]) < 12
    assert page.locator(".comment-counter").inner_text() == "1 comment"
    # comments are no tooltips: the card says it all
    page.locator("td.code .comment").first.hover()
    assert not page.locator("#tip").is_visible()


def test_printed_page(browser, page_file):
    """Printed, even from a browser in dark mode: light colours, the top bar
    left out, the cards beside their rows, quiet folds, the column headings
    of each file repeated on every page."""
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
    assert style(".card", "display") == "block" and style(".stack", "position") == "static"
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


def test_unchanged_lines_and_a_file_open_and_close(page):
    """A fold opens on its own; a file closes by its heading, and jumping to
    a change opens it. One file has no list of files and no buttons for all
    of them; no moved passage, no switch for them."""
    assert page.locator('[data-toggle="passages"]').count() == 0
    hidden = page.locator("tbody[hidden]").first
    assert not hidden.is_visible()
    folds = page.locator("tbody[hidden]").count()
    page.locator(".expand").first.click()
    assert page.locator("tbody[hidden]").count() == folds - 1
    assert page.get_by_text("Line 10 of the text.").first.is_visible()
    assert page.locator('[data-files], nav[aria-label="Changed files"]').count() == 0
    page.click("details.file > summary")
    assert not page.evaluate("document.querySelector('details.file').open")
    page.keyboard.press("n")
    assert page.evaluate("document.querySelector('details.file').open")


def test_files_listed_and_opened_and_closed_all_at_once(browser, tmp_path):
    """Two files: listed, each linked, and closed and opened all at once."""
    old, new = tmp_path / "old", tmp_path / "new"
    for side, text in ((old, "One.\n"), (new, "Two.\n")):
        side.mkdir()
        (side / "a.md").write_text(text, encoding="utf-8")
        (side / "b.md").write_text(text, encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))
    assert page.locator("nav a").all_inner_texts() == ["a.md", "b.md"]
    assert "2 files changed" in page.inner_text(".files-changed")
    page.click('[data-files="close"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => !d.open)")
    page.click('[data-files="open"]')
    assert page.evaluate("[...document.querySelectorAll('details.file')].every(d => d.open)")


def test_view_menu_opens_and_closes(page):
    """The View menu holds the switches few use: its button opens it, a
    switch leaves it open, Escape or a click outside closes it."""
    panel = page.locator("#view-menu")
    button = page.locator(".menu-button")
    assert not panel.is_visible()
    button.click()
    assert panel.is_visible() and button.get_attribute("aria-expanded") == "true"
    names = panel.locator(".label").all_inner_texts()
    assert names == ["Formatted", "Change highlights", "Formatting changes", "Paragraph spacing"]
    page.click('[data-toggle="formats"]')
    assert panel.is_visible() and "formats" not in page.evaluate("document.body.className")
    page.keyboard.press("Escape")
    assert not panel.is_visible() and button.get_attribute("aria-expanded") == "false"
    button.click()
    page.mouse.click(5, 300)
    assert not panel.is_visible()


def test_change_highlights_switched_off(browser, tmp_path):
    """Change highlights, on by default, off by its switch in the View menu
    or by h: no background on changed words, nor on a line removed or
    added; the gutters keep their tint. Remembered across a reload."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One word here.\n\nGone line.\n", encoding="utf-8")
    new.write_text("One other here.\n\nNew line added here.\n", encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))

    def background(selector):
        return page.locator(selector).first.evaluate("e => getComputedStyle(e).backgroundColor")

    def tint(selector):
        return page.locator(selector).first.evaluate(
            "e => getComputedStyle(e).getPropertyValue('--tint').trim()"
        )

    clear = "rgba(0, 0, 0, 0)"
    assert background("ins") != clear and background("del") != clear
    assert page.get_attribute('[data-toggle="highlights"]', "aria-pressed") == "true"
    page.click(".menu-button")
    page.click('[data-toggle="highlights"]')
    assert background("ins") == clear and background("del") == clear
    assert tint("tr.replace td.code.right") == "transparent"
    for row in ("tr.insert td.code.right", "tr.delete td.code.left"):
        if page.locator(row).count():
            assert tint(row) == "transparent"
    assert tint("tr.replace td.no.r") != "transparent"  # the gutter still marks it
    page.reload()
    assert background("ins") == clear
    page.keyboard.press("h")
    assert background("ins") != clear
    page.context.close()


def test_views_are_remembered(page):
    """One column (u) and raw Markdown (f): each switched on by its key,
    remembered across a reload, switched off by its button. An edited line
    is never tinted as a whole: only its changed words are."""

    def edited_line_colour():
        cell = page.locator("tr.replace td.right").first
        return cell.evaluate("e => getComputedStyle(e).getPropertyValue('--tint').trim()")

    # the defaults: two columns, formatted
    assert "unified" not in page.evaluate("document.body.className")
    assert not page.locator(".s-syn").first.is_visible()
    assert page.get_attribute('[data-toggle="formatted"]', "aria-pressed") == "true"
    assert page.locator(".s-strong").first.evaluate("e => getComputedStyle(e).fontWeight") == "700"
    assert edited_line_colour() == "transparent"
    for key in "uf":
        page.keyboard.press(key)
    # an unchanged row shows its new side only, its cards beside it
    left = page.locator("tr.equal td.left").first
    assert left.evaluate("e => getComputedStyle(e).display") == "none"
    assert page.locator(".s-syn").first.is_visible()
    assert edited_line_colour() == "transparent"
    assert page.locator(".card").is_visible()
    assert page.get_attribute('[data-toggle="unified"]', "aria-pressed") == "true"
    page.reload()
    assert "unified" in page.evaluate("document.body.className")
    assert page.locator(".s-syn").first.is_visible()
    page.click('[data-toggle="unified"]')
    assert "unified" not in page.evaluate("document.body.className")


def test_toolbar_help_tooltips(page):
    """The toolbar's buttons explain themselves on hover; a change, or the
    row it is in, shows nothing."""
    tip = page.locator("#tip")
    # no browser tooltip
    assert page.locator(".toolbar [title], table [title]").count() == 0
    page.locator("ins").first.hover()
    assert not tip.is_visible()
    box = page.locator("tr.replace td.code.right").first.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] - 3)
    assert not tip.is_visible()
    page.click(".menu-button")
    page.hover('[data-toggle="formatted"]')
    assert tip.is_visible()
    assert tip.locator("b").inner_text() == "Formatted"
    assert tip.locator(".when").inner_text() == "key: f"
    page.hover('[data-toggle="highlights"]')
    assert tip.locator("b").inner_text() == "Change highlights"
    page.mouse.move(0, 300)
    assert not tip.is_visible()
    page.hover("[data-review]")
    assert tip.locator("b").inner_text() == "Review"
    page.click("[data-review]")  # clicked, its help goes
    assert not tip.is_visible()


def test_space_between_paragraphs(page):
    cell = page.locator("tr.replace td.right").first
    gap = "e => parseFloat(getComputedStyle(e).paddingBottom)"
    default = cell.evaluate(gap)
    assert default > 0
    assert page.inner_text(".gap-value") == "0.75"
    page.click(".menu-button")  # in the View menu
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
    page.click(".menu-button")
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


def test_a_card_pins_its_comment_and_the_top_bar_steps_through_them(page):
    """A card clicked pins its comment: its paragraph (where its text ends is
    not known) and its marker marked, the card ringed, until clicked again;
    the top bar's comment arrows (c) do the same, saying which."""
    card = page.locator(".card")
    card.click()
    assert page.locator("td.code.right.pinned").count() == 1
    assert page.locator(".comment.pinned").count() == 1
    assert "active" in card.get_attribute("class")
    card.click()
    assert page.locator("td.code.pinned, .comment.pinned, .card.active").count() == 0
    page.keyboard.press("c")
    assert page.locator(".comment-counter").inner_text() == "1 / 1 comments"
    assert page.locator(".comment.pinned").count() == 1 and "active" in card.get_attribute("class")
    page.keyboard.press("Escape")
    assert page.locator(".comment.pinned, .card.active").count() == 0


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


def highlighted(page, name: str = "pin") -> str:
    return page.evaluate(
        f"""() => {{ const h = CSS.highlights.get("{name}");
                   return h ? [...h].map(r => r.toString()).join("|") : null; }}"""
    )


def test_a_comment_pins_the_words_it_is_anchored_to(browser, tmp_path):
    """A comment's card, or its marker, clicked highlights the words it is
    anchored to, not its paragraph, across paragraphs too, until clicked
    again; another comment takes its place; Esc lets go."""
    page = anchored_report(browser, tmp_path)
    here = page.locator(".card", has_text="Here.")
    here.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "two three"
    assert page.locator("td.code.pinned").count() == 0
    assert page.locator(".comment.pinned").count() == 1
    assert page.locator(".card.active").all_inner_texts()[0].find("Here.") >= 0
    page.locator(".card", has_text="Across.").click()
    page.wait_for_function(
        'CSS.highlights.get("pin") && CSS.highlights.get("pin").size == 2',
        timeout=5_000,
    )
    first, second = highlighted(page).replace("­", "").split("|")
    assert first == "four." and second.startswith("Five six")
    assert "seven" not in second
    assert page.locator(".comment.pinned, .card.active").count() == 2  # one of each
    page.locator(".card", has_text="Across.").click()
    page.wait_for_function('!CSS.highlights.has("pin")', timeout=5_000)
    assert page.locator(".comment.pinned, .card.active").count() == 0
    # the marker in the text pins as its card does: clicked, Enter, Esc
    marker = page.locator('td.code .comment[data-text="Here."]').first
    marker.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "two three"
    assert "Here." in page.locator(".card.active").inner_text()
    marker.click()
    page.wait_for_function('!CSS.highlights.has("pin")', timeout=5_000)
    page.locator('td.code .comment[data-text="Across."]').first.focus()
    page.keyboard.press("Enter")
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    page.keyboard.press("Escape")
    page.wait_for_function('!CSS.highlights.has("pin")', timeout=5_000)


def test_comment_whose_words_are_gone_rings_its_marker_only(browser, tmp_path):
    """A comment whose words went (its text ends where it starts) rings its
    marker and highlights nothing: not its paragraph either."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One two.\n", encoding="utf-8")
    note = '[Gone.]{.comment-start id="1" author="A" date="2026-09-25T10:00:00Z"}'
    new.write_text(f'One two.{note}[]{{.comment-end id="1"}}\n', encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    page.locator(".card").first.click()
    page.wait_for_selector(".comment.pinned", timeout=5_000)
    assert highlighted(page) is None
    assert page.locator("td.code.pinned").count() == 0


def test_problems_marked_by_the_ai(browser, tmp_path):
    """The problems the AI marked are found in the text of their side,
    whatever the quotes and the hyphenation: a numbered badge before each,
    and a card beside its row telling the problem and the solution; one
    found nowhere has neither, and review mode lists it as such. Each pins
    as a comment does, from its badge or its card; the top bar's arrows (a,
    Shift+A) step through those found, pinning each, the counter saying
    which."""
    from prosediff.assess import Annotation, Assessment

    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("A sentence kept.\n\nThe old “quoted” claim went away.\n", encoding="utf-8")
    new.write_text(
        "A sentence kept, and extended with considerably longer words.\n\n"
        "A new paragraph, placed here.\n",
        encoding="utf-8",
    )
    a = Assessment("claude", "## Verdict\n**Mixed**.", "m")
    a.annotations = [
        Annotation("new", "extended with", "longer words.", "Wordy.", "Cut it."),
        Annotation("new", "words nowhere", "at all", "Missing.", ""),
        Annotation("old", 'The old "quoted"', "went away.", "Lost.", "Keep it."),
    ]
    c = compare_paths(old, new, Options(context=None))
    page = open_report(browser, tmp_path, c, assessment=a)
    marks = page.locator(".ai-mark")
    # numbered in reading order, the one found nowhere last
    assert marks.all_inner_texts() == ["⚠ 1", "⚠ 2"]
    cards = page.locator(".card.problem")
    assert cards.locator(".chip").all_inner_texts() == ["⚠ Problem 1", "⚠ Problem 2"]
    assert "Wordy." in cards.first.inner_text() and "Proposed: Cut it." in cards.first.inner_text()
    assert "AI, old version" in cards.last.inner_text() and "Lost." in cards.last.inner_text()
    # the passages highlighted only when pinned
    assert page.evaluate("CSS.highlights.size") == 0
    # pinned from its badge, until clicked again, its card ringed
    marks.first.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "extended with considerably longer words."
    assert "active" in cards.first.get_attribute("class")
    marks.first.click()
    page.wait_for_function('!CSS.highlights.has("pin")', timeout=5_000)
    # and from its card
    cards.last.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "The old “quoted” claim went away."
    page.keyboard.press("Escape")
    # the top bar: those found, in turn, the missing one skipped
    counter = page.locator(".ai-counter")
    assert counter.inner_text() == "⚠ 3 problems"
    page.click('[data-ai-nav="1"]')
    assert counter.inner_text() == "⚠ 1 / 3"
    page.keyboard.press("a")
    assert counter.inner_text() == "⚠ 2 / 3"
    assert highlighted(page).replace("­", "") == "The old “quoted” claim went away."
    page.keyboard.press("a")  # round again
    assert counter.inner_text() == "⚠ 1 / 3"
    page.keyboard.press("Shift+A")
    assert counter.inner_text() == "⚠ 2 / 3"
    page.keyboard.press("Escape")
    # review mode lists the one found nowhere, not to be chosen
    page.keyboard.press("r")
    missing = page.locator("#review-list button", has_text="Missing.")
    assert missing.is_disabled() and "not found in the text" in missing.inner_text()
    page.context.close()


def test_a_comment_over_an_ai_mark_leaves_its_badge_out(browser, tmp_path):
    """A comment anchored to the words the AI also marked highlights those
    words, pinned, but not the AI's badge before them, nor the other
    comment's marker."""
    from prosediff.assess import Annotation, Assessment

    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("# A title\n", encoding="utf-8")
    one = '[First.]{.comment-start id="1" author="A" date="2026-09-25T10:00:00Z"}'
    two = '[Second.]{.comment-start id="2" author="A" date="2026-09-25T10:05:00Z"}'
    ends = '[]{.comment-end id="1"}[]{.comment-end id="2"}'
    new.write_text(f"# {one}{two}A new title{ends}\n", encoding="utf-8")
    a = Assessment("claude", "## Verdict\n**Mixed**.", "m")
    a.annotations = [Annotation("new", "A new title", "A new title", "Vague.", "")]
    page = open_report(
        browser, tmp_path, compare_paths(old, new, Options(context=None)), assessment=a
    )
    assert page.locator("td.code .ai-mark").count() == 1
    page.locator('td.code .comment[data-text="First."]').first.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "A new title"
    assert page.locator(".ai-mark.pinned").count() == 0
    page.context.close()


def test_ai_marks_in_a_moved_passage_row(browser, tmp_path):
    """A row holding a moved passage is written twice, with the passage
    shown as moved and without: a problem marked in it has a badge in the
    one shown, whether moved passages are on or off, and one card."""
    from prosediff.assess import Annotation, Assessment

    old, new = tmp_path / "a.md", tmp_path / "b.md"
    moved = "This sentence moves to the end of the text."
    old.write_bytes(f"The first sentence stays here. {moved}\n\nA middle one stays.\n".encode())
    new.write_bytes(f"The first sentence stays here.\n\nA middle one stays. {moved}\n".encode())
    a = Assessment("claude", "## Verdict\n**Mixed**.", "m")
    a.annotations = [Annotation("new", "A middle one", "of the text.", "Moved.", "")]
    c = compare_paths(old, new, Options(context=None))
    page = open_report(browser, tmp_path, c, assessment=a)
    shown = page.locator(".ai-mark:visible")
    assert page.locator("td.right .pv-on").count() > 0  # the row has two versions
    assert shown.all_inner_texts() == ["⚠ 1"]
    assert page.locator(".card.problem").count() == 1
    shown.click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == f"A middle one stays. {moved}"
    page.keyboard.press("Escape")
    page.keyboard.press("v")  # moved passages on: the other version, its own badge
    assert shown.all_inner_texts() == ["⚠ 1"]
    assert shown.evaluate("m => !!m.closest('.pv-on')")
    page.click('[data-ai-nav="1"]')
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert page.locator(".card.active:visible").count() == 1
    page.context.close()


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
    the toggle (l), off by default, shows them."""
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
    sync_api.expect(sentences.locator("svg.move-links")).to_have_count(0)
    page.keyboard.press("l")
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


def test_moved_passages_switch(browser, tmp_path):
    """The "Moved passages" switch, off by default: a sentence moved from one
    paragraph into another is removed in one place and added in the other,
    the counts without it; on, it is shown as moved and counted, hovering
    either end tells where it went or came from and lights up both, and a
    line joins them; the choice remembered, and the key v turns it back
    off."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    moved = "This sentence moves to the end of the text."
    old.write_bytes(f"The first sentence stays here. {moved}\n\nA middle one stays.\n".encode())
    new.write_bytes(f"The first sentence stays here.\n\nA middle one stays. {moved}\n".encode())
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    switch = page.locator('[data-toggle="passages"]')
    summary = page.locator("details.file summary")
    assert switch.get_attribute("aria-pressed") == "false"
    assert not page.locator("td.left .moved").is_visible()
    assert page.locator("td.left del").filter(has_text="This sentence moves").is_visible()
    assert page.locator("td.right ins").filter(has_text="This sentence moves").is_visible()
    assert "moved passage" not in summary.inner_text()
    switch.click()
    assert switch.get_attribute("aria-pressed") == "true"
    assert page.locator("td.left .moved").is_visible()
    assert "1 moved passage" in summary.inner_text()
    tip = page.locator("#tip")
    page.locator("td.left .moved").hover()
    assert tip.locator("b").inner_text() == "Moved to line 3"
    sync_api.expect(page.locator(".moved.pair-hot")).to_have_count(2)
    page.locator("td.right .moved").hover()
    assert tip.locator("b").inner_text() == "Moved from line 1"
    page.keyboard.press("l")  # the lines between moves
    sync_api.expect(page.locator("svg.move-links path")).to_have_count(1)
    page.reload()
    assert switch.get_attribute("aria-pressed") == "true"
    page.keyboard.press("v")
    assert not page.locator("td.left .moved").is_visible()
    assert "moved passage" not in summary.inner_text()
    page.context.close()


def test_a_comment_card_shows_its_paragraphs_and_italics(browser, tmp_path):
    """A Word comment's card has a line for each of its paragraphs, the blank
    one too, and its italics."""
    old, new = commented_documents(tmp_path, "docx")
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    card = page.locator(".card").first
    lines = card.locator(".line")
    assert lines.all_inner_texts() == ["Please cite:", "", "See Research Policy, 49."]
    assert card.locator("i").inner_text() == "Research Policy"
    assert lines.nth(1).evaluate("e => e.getBoundingClientRect().height") > 0
    page.context.close()


def test_the_assessment_is_a_drawer(browser, tmp_path):
    """The AI's assessment opens from its verdict in the top bar, over the
    right of the page, and closes by its button or Esc; on paper it heads
    the report."""
    from prosediff.assess import Assessment

    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One line.\n", encoding="utf-8")
    new.write_text("One changed line.\n", encoding="utf-8")
    a = Assessment("claude", "## Verdict\n**Improves**: tighter.", "m")
    page = open_report(browser, tmp_path, compare_paths(old, new), assessment=a)
    drawer, verdict = page.locator("#assessment"), page.locator(".verdict-button")
    assert not drawer.is_visible() and "Improves" in verdict.inner_text()
    verdict.click()
    assert drawer.is_visible() and verdict.get_attribute("aria-expanded") == "true"
    assert "tighter." in drawer.inner_text()
    page.keyboard.press("Escape")
    assert not drawer.is_visible()
    verdict.click()
    page.click("#assessment .close")
    assert not drawer.is_visible()
    page.emulate_media(media="print")
    assert drawer.is_visible()
    page.context.close()


def test_review_mode(page):
    """Review mode (r, or its button) lists the changes and the comments down
    the left, and shows the one row the chosen one is in, which a line
    names; Next and Previous (j, k) go through them all; r again ends it."""
    page.keyboard.press("r")
    assert "review" in page.evaluate("document.body.className")
    listing = page.locator("#review-list")
    assert listing.locator("h3").all_text_contents() == ["Changes · 3", "Comments · 1"]
    head = page.locator("#review-head")
    assert head.locator(".which").inner_text() == "Change 1 of 3"
    rows = page.locator("details.file tbody tr:visible")
    assert rows.count() == 1 and "Line 1" in rows.first.inner_text()
    page.click('[data-review-step="1"]')
    assert head.locator(".which").inner_text() == "Change 2 of 3"
    page.keyboard.press("j")
    page.keyboard.press("j")  # past the changes, the comment
    assert head.locator(".which").inner_text() == "Comment 1 of 1"
    assert "paragraph 21" in head.locator(".where").inner_text()
    assert page.locator(".card.active").count() == 1
    page.keyboard.press("k")
    assert head.locator(".which").inner_text() == "Change 3 of 3"
    listing.locator("button", has_text="Old remark.").click()
    assert head.locator(".which").inner_text() == "Comment 1 of 1"
    page.keyboard.press("r")
    assert "review" not in page.evaluate("document.body.className")
    assert page.locator("details.file tbody tr:visible").count() > 3


def test_a_note_on_an_unchanged_line_unfolds_that_line_alone(browser, tmp_path):
    """A problem marked on a line among unchanged ones brings that line out,
    its card beside it, the lines before and after it left folded."""
    from prosediff.assess import Annotation, Assessment

    lines = [f"Line {i} of the text." for i in range(30)]
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines[29] = "Line 29 changed."
    new.write_text("\n".join(lines) + "\n", encoding="utf-8")
    a = Assessment("claude", "## Verdict\n**Mixed**.", "m")
    a.annotations = [Annotation("new", "Line 12 of", "the text.", "Vague.", "")]
    page = open_report(browser, tmp_path, compare_paths(old, new), assessment=a)
    split = page.locator("main")
    row = split.locator("tr", has_text="Line 12 of the text.").first
    assert row.is_visible() and split.locator(".card.problem").is_visible()
    folds = split.locator(".expand").all_inner_texts()
    assert [f.replace("show ", "") for f in folds][:2] == [
        "⋯ 12 unchanged lines",
        "⋯ 16 unchanged lines",
    ]
    assert not split.get_by_text("Line 11 of the text.").first.is_visible()
    # brought out, its passage is still highlighted when pinned
    split.locator(".card.problem").click()
    page.wait_for_function('CSS.highlights.has("pin")', timeout=5_000)
    assert highlighted(page).replace("­", "") == "Line 12 of the text."
    page.context.close()


def test_cards_run_on_down_the_margin(browser, tmp_path):
    """The cards of a row taller than it run on down the margin beside the
    rows below that have none, the rows as tall as their text; a row that
    has cards is moved down instead, so they start level with it, never
    after its end; none over another, the file holding them all."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("First.\n\nMiddle.\n\nSecond.\n", encoding="utf-8")
    notes = "".join(
        f"[Note {k}, a comment long enough to take a few lines of the margin.]"
        f'{{.comment-start id="{k}" author="A" date="2026-09-25T10:0{k}:00Z"}}'
        for k in range(1, 4)
    )
    new.write_text(
        f'First.{notes}\n\nMiddle.\n\nSecond.[Last.]{{.comment-start id="9" author="B"}}\n',
        encoding="utf-8",
    )
    page = open_report(browser, tmp_path, compare_paths(old, new, Options(context=None)))
    stacks = page.locator(".stack")
    assert stacks.count() == 2
    one, two = stacks.nth(0).bounding_box(), stacks.nth(1).bounding_box()
    first = page.locator("tr", has_text="First.").first.bounding_box()
    middle = page.locator("tr", has_text="Middle.").first.bounding_box()
    second = page.locator("tr", has_text="Second.").first.bounding_box()
    assert first["height"] < one["height"]  # the first row kept its height
    assert one["y"] + one["height"] > middle["y"]  # its cards run on beside Middle
    assert two["y"] >= one["y"] + one["height"]  # under the first, not over it
    assert abs(two["y"] - second["y"]) < 8  # level with its own row
    file = page.locator("details.file").first.bounding_box()
    assert file["y"] + file["height"] >= two["y"] + two["height"]  # the file holds them
    page.context.close()


def test_a_fold_moves_down_below_the_cards(browser, tmp_path):
    """Cards that run past their row's end never cross the fold of
    unchanged lines below it: the fold moves down, also when a long comment
    is shown whole, sliding open and shut, the page staying where it was."""
    lines = [f"Line {k} of the text." for k in range(1, 31)]
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("\n".join(lines) + "\n", encoding="utf-8")
    long = " ".join(f"Remark {k} on this line, told at some length." for k in range(40))
    lines[4] = f'Line 5, changed.[{long}]{{.comment-start id="1" author="A"}}'
    new.write_text("\n".join(lines) + "\n", encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))
    card = page.locator(".card").first
    fold = page.locator("tr.skip:visible").nth(1)  # the one after the changed line

    def clear():
        box = card.bounding_box()
        return box["y"] + box["height"] <= fold.bounding_box()["y"]

    body = card.locator(".body")
    assert clear() and "clamped" in body.get_attribute("class")
    page.set_viewport_size({"width": 1400, "height": 500})
    page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")  # its end
    at = page.evaluate("scrollY")
    card.get_by_text("Show all").click()
    assert "sliding" in body.get_attribute("class")  # it slides open
    page.wait_for_function(
        "!document.querySelector('.card .body').classList.contains('sliding')", timeout=2_000
    )
    assert card.get_by_text("Show less").is_visible() and clear()
    assert "clamped" not in body.get_attribute("class") and page.evaluate("scrollY") == at
    card.get_by_text("Show less").click()
    page.wait_for_function(
        "document.querySelector('.card .body').classList.contains('clamped')", timeout=2_000
    )
    assert card.get_by_text("Show all").is_visible() and clear()
    page.context.close()


def test_show_all_only_when_the_text_runs_past_its_lines(browser, tmp_path):
    """A card's text is cut to its first lines only when it runs past them
    at the margin's width: a comment that fits in a wide margin has no Show
    all, and gets one in a narrow one."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One line.\n", encoding="utf-8")
    text = " ".join(["A remark of some length on this line."] * 8)  # about 300 characters
    new.write_text(f'One line.[{text}]{{.comment-start id="1" author="A"}}\n', encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))
    more = page.locator(".card .more")
    for width, shown in ((900, False), (200, True)):
        page.evaluate(f"localStorage.setItem('prosediff-notes-width', '{width}')")
        page.reload()
        page.wait_for_timeout(100)
        assert more.is_visible() is shown, width
    page.evaluate("localStorage.removeItem('prosediff-notes-width')")
    page.context.close()


def test_columns_resized_by_dragging_their_handles(page):
    """The line between the old and the new version, and the margin's edge,
    are handles: dragged, they resize the columns, remembered across a
    reload; double-clicked, the table's own widths come back."""
    split, notes = (
        page.locator('.col-resizer[data-resize="split"]'),
        page.locator('.col-resizer[data-resize="notes"]'),
    )
    old_cell = page.locator("tr.replace td.code.left").first
    new_cell = page.locator("tr.replace td.code.right").first
    margin = page.locator("td.notes").first
    before = old_cell.bounding_box()["width"], new_cell.bounding_box()["width"]
    box = split.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] - 150, box["y"] + 40, steps=5)
    page.mouse.up()
    after = old_cell.bounding_box()["width"], new_cell.bounding_box()["width"]
    assert after[0] < before[0] - 100 and after[1] > before[1] + 100
    width = margin.bounding_box()["width"]
    box = notes.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] - 100, box["y"] + 40, steps=5)
    page.mouse.up()
    assert margin.bounding_box()["width"] > width + 60
    kept = old_cell.bounding_box()["width"]  # its share of what the wider margin leaves
    page.reload()
    assert abs(page.locator("tr.replace td.code.left").first.bounding_box()["width"] - kept) < 3
    page.locator('.col-resizer[data-resize="split"]').dblclick()
    # the two versions sharing their width evenly again
    old_width = page.locator("tr.replace td.code.left").first.bounding_box()["width"]
    new_width = page.locator("tr.replace td.code.right").first.bounding_box()["width"]
    assert abs(old_width - new_width) < 3


def test_column_handles_when_the_table_starts_folded(browser, tmp_path):
    """A table that starts with unchanged lines folded away still has its
    handles, placed by the rows that show."""
    lines = [f"Line {k} of the text." for k in range(1, 31)]
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines[24] = 'Line 25, changed.[A note.]{.comment-start id="1" author="A"}'
    new.write_text("\n".join(lines) + "\n", encoding="utf-8")
    page = open_report(browser, tmp_path, compare_paths(old, new))
    assert not page.get_by_text("Line 1 of the text.").first.is_visible()  # folded away
    split = page.locator('.col-resizer[data-resize="split"]')
    notes = page.locator('.col-resizer[data-resize="notes"]')
    assert split.is_visible() and notes.is_visible()
    cell = page.locator("tr.replace td.code.left").first.bounding_box()
    box = split.bounding_box()
    assert abs(box["x"] + box["width"] / 2 - (cell["x"] + cell["width"])) < 3
    page.context.close()


def test_the_margin_hidden_and_shown(page):
    """Margin (g) hides the margin, the two versions taking its width, and
    brings it back; remembered across a reload."""
    new_cell = page.locator("tr.replace td.code.right").first
    width = new_cell.bounding_box()["width"]
    assert page.locator(".card").is_visible()
    page.click('[data-toggle="margin"]')
    assert not page.locator(".card").is_visible()
    assert new_cell.bounding_box()["width"] > width + 100
    assert page.get_attribute('[data-toggle="margin"]', "aria-pressed") == "false"
    page.reload()
    assert not page.locator(".card").is_visible()
    page.keyboard.press("g")
    assert page.locator(".card").is_visible()


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_the_ai_documents_downloaded(browser, tmp_path, fmt):
    """The drawer, and the top bar, save each document the AI's problems were put in; a
    problem left out in review mode (its box unticked) is not in either:
    no comment of its, its fix rejected."""
    old, new = pair(tmp_path, fmt)
    c = compare_paths(str(old), str(new), Options())
    page = open_report(browser, tmp_path, c, assessment=assessment([FIXED, ADVICE]))
    included = page.locator(".ai-documents .ai-included")

    def save(label, name):
        page.click(".verdict-button")
        with page.expect_download() as d:
            page.click(f".ai-documents .ai-download:has-text('{label}')")
        out = tmp_path / name
        d.value.save_as(out)
        assert (
            d.value.suggested_filename
            == f"new_{'with_AI_fixes' if 'fixes' in label else 'tracked_with_AI_comments'}.{fmt}"
        )
        page.keyboard.press("Escape")
        return out

    assert "2 of the 2 problems" in included.inner_text()
    fixed = save("fixes", f"all.{fmt}")
    assert "We find a small and significant effect." in lines(fixed, "accept-all")
    fixed_note = f"Nothing supports a large effect.\nProposed: Say a small effect.\n{FIX_APPLIED}"
    assert fixed_note in comments_of(fixed, fmt)
    # the fixed problem left out in review mode
    page.keyboard.press("r")
    page.locator("#review-list .with-check", has_text="Nothing supports").locator("input").uncheck()
    page.keyboard.press("r")
    assert "1 of the 2 problems" in included.inner_text()
    fewer = save("fixes", f"fewer.{fmt}")
    assert lines(fewer, "accept-all") == lines(new, "accept-all")
    texts = comments_of(fewer, fmt)
    assert not any(t.startswith("Nothing supports") for t in texts)
    assert "Which checks?\nProposed: Name them." in texts
    tracked = save("comments", f"tracked.{fmt}")
    # the same from the top bar, the drawer closed
    with page.expect_download() as d:
        page.click(f".toolbar .ai-download:has-text('With AI fixes .{fmt}')")
    assert d.value.suggested_filename == f"new_with_AI_fixes.{fmt}"
    assert page.locator("#assessment").is_hidden()
    assert lines(tracked, "reject-all") == lines(old, "accept-all")
    assert [t for t in comments_of(tracked, fmt) if "Nothing supports" in t] == []
    page.context.close()


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_a_fix_left_out_gives_the_co_authors_words_back(browser, tmp_path, fmt):
    """A fix of words a co-author put in, left out in review mode: the
    downloaded file as the co-author left it, their insertion and deletion
    still tracked, all accepted the text unfixed, all rejected the text
    before them."""
    from prosediff.diff import review_file

    new = co_authored(pair(tmp_path, fmt)[1], fmt)
    page = open_report(
        browser, tmp_path, review_file(new, Options()), assessment=assessment([FIXED, ADVICE])
    )
    page.keyboard.press("r")
    page.locator("#review-list .with-check", has_text="Nothing supports").locator("input").uncheck()
    page.keyboard.press("r")
    with page.expect_download() as d:
        page.click(".toolbar .ai-download")
    out = tmp_path / f"out.{fmt}"
    d.value.save_as(out)
    assert lines(out, "accept-all") == lines(new, "accept-all")
    assert lines(out, "reject-all") == lines(new, "reject-all")
    assert "We find a very significant effect." in lines(out, "reject-all")
    assert authors_of(out, fmt) == {"Anna Rossi"}


def test_a_problem_left_out_from_its_card_and_the_review_list_resized(browser, tmp_path):
    """A problem's card has the box that puts it in the documents to
    download, ticked alike in the review list, the count on the download
    buttons; the review list starts with a jump to its problems, and its
    edge, dragged, widens it, remembered, back to its width on a
    double-click."""
    old, new = pair(tmp_path, "docx")
    c = compare_paths(str(old), str(new), Options())
    page = open_report(browser, tmp_path, c, assessment=assessment([FIXED, ADVICE]))
    button = page.locator(".toolbar .ai-download").first
    assert "(2 of 2 problems)" in button.get_attribute("data-help")
    card = page.locator(".card.problem", has_text="Nothing supports")
    card.locator(".keep-box").uncheck()
    assert "(1 of 2 problems)" in button.get_attribute("data-help")
    page.evaluate("document.activeElement.blur()")  # keys typed in a box are its own
    page.keyboard.press("r")
    row = page.locator("#review-list .with-check", has_text="Nothing supports")
    assert not row.locator("input").is_checked()
    row.locator("input").check()
    assert card.locator(".keep-box").is_checked()
    assert page.locator("#review-list .jump").count() == 1
    handle = page.locator("#review-resizer")
    box = handle.bounding_box()
    page.mouse.move(box["x"] + 3, box["y"] + 100)
    page.mouse.down()
    page.mouse.move(box["x"] + 203, box["y"] + 100)
    page.mouse.up()
    width = page.locator("#review-list").bounding_box()["width"]
    assert width > box["x"] + 150
    assert page.evaluate("localStorage.getItem('prosediff-review-width')") is not None
    handle.dblclick()
    assert page.evaluate("localStorage.getItem('prosediff-review-width')") is None
    page.context.close()


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_a_problems_comment_marked_resolved_in_the_download(browser, tmp_path, fmt):
    """Its card's Resolved box marks the problem's comment resolved in the
    downloaded document (Word's w15:done, LibreOffice's loext:resolved), the
    others not; the box is greyed out while the problem is left out."""
    old, new = pair(tmp_path, fmt)
    c = compare_paths(str(old), str(new), Options())
    page = open_report(browser, tmp_path, c, assessment=assessment([FIXED, ADVICE]))
    card = page.locator(".card.problem", has_text="Which checks?")
    card.locator(".resolve-box").check()
    card.locator(".keep-box").uncheck()
    assert card.locator(".resolve-box").is_disabled()
    card.locator(".keep-box").check()
    assert card.locator(".resolve-box").is_enabled() and card.locator(".resolve-box").is_checked()
    with page.expect_download() as d:
        page.click(f".toolbar .ai-download:has-text('With AI fixes .{fmt}')")
    out = tmp_path / f"resolved.{fmt}"
    d.value.save_as(out)
    page.context.close()
    reader = read_docx if fmt == "docx" else read_odt
    found = {}

    def walk(o):
        if hasattr(o, "resolved") and hasattr(o, "text"):
            found[o.text] = o.resolved
        elif isinstance(o, (list, tuple)):
            for x in o:
                walk(x)
        elif hasattr(o, "__dict__"):
            for x in vars(o).values():
                walk(x)

    walk(reader(out.read_bytes()))
    assert found["Which checks? Proposed: Name them."] is True
    assert not any(v for k, v in found.items() if not k.startswith("Which checks?"))


def test_a_fix_applied_shown_on_its_card(browser, tmp_path):
    """In the report of one file's fixes, a problem whose fix the version on
    the right holds says so, its card set apart; one without a fix does not."""
    from prosediff.pipeline import Run, fixes_shown, review_diff  # noqa: F401

    path = tmp_path / "paper.md"
    path.write_text(
        "We find a large and significant effect.\n\nRobustness checks confirm every result.\n",
        encoding="utf-8",
    )
    a = assessment([FIXED, ADVICE])
    run = Run("review", str(path), tmp_path / "out.html")
    comparison, shown = fixes_shown(run, a)
    page = open_report(browser, tmp_path, comparison, assessment=shown)
    applied = page.locator(".card.problem.applied")
    assert applied.count() == 1
    assert "Fix already applied" in applied.inner_text()
    assert "Nothing supports" in applied.inner_text()
    other = page.locator(".card.problem", has_text="Which checks?")
    assert "applied" not in other.get_attribute("class")
    page.context.close()
