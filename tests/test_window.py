"""prosediff's window, driven in a real (headless) browser: the Open screen's
page (templates/window.html.j2) with the App it shows behind it, and a
report's choices kept in the project through ReportApi. pywebview's window
itself is never opened: the page's calls into Python (window.pywebview.api)
are Playwright's, to the same WindowApi and ReportApi.

Skipped when Playwright's Chromium is not installed
(uv run playwright install chromium).
"""

import json

import pytest
from helpers import ADVICE, FIXED, assessment, pair

from prosediff import Options, gui, render
from prosediff.assess import ModelInfo
from prosediff.saved import project_choices, save_project

sync_api = pytest.importorskip("playwright.sync_api")

# The functions pywebview gives a page, each a call to Python (__call).
SHIM = """
window.pywebview = { api: {} };
NAMES.forEach(function (n) {
  window.pywebview.api[n] = function () {
    return window.__call(n, Array.prototype.slice.call(arguments));
  };
});
"""


class Ui:
    """App's screen, recording what it is asked to show; its dialogs answer
    with answers (a list of files, a folder, a file to save to)."""

    def __init__(self) -> None:
        self.shown: list[tuple] = []
        self.answers: dict[str, object] = {}

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.shown.append((name, *args))
            if name.startswith("ask_"):
                return self.answers.get(name, [] if name == "ask_open" else "")
            return None

        return record

    def kinds(self, kind: str) -> list[tuple]:
        return [s for s in self.shown if s[0] == kind]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser not downloaded
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture(autouse=True)
def nothing_asked(monkeypatch):
    """The AIs and their models found at once, without starting Claude Code
    or asking Ollama."""
    models = {
        "claude": [ModelInfo("default", "Default"), ModelInfo("opus", "Opus 5.5")],
        "ollama": [ModelInfo("gemma3:270m"), ModelInfo("qwen3:8b")],
    }
    monkeypatch.setattr(gui, "models_of", lambda ai: models.get(ai, []))
    monkeypatch.setattr(gui, "providers", lambda: ["ollama", "openai"])


def opened(browser, path, bridge, names):
    """The page at path in a new page of browser, its calls into Python
    answered by bridge (a WindowApi or a ReportApi)."""
    page = browser.new_context().new_page()
    page.expose_function(
        "__call",
        lambda name, args: json.loads(json.dumps(getattr(bridge, name)(*args), default=str)),
    )
    page.add_init_script(SHIM.replace("NAMES", json.dumps(names)))
    page.goto(path.as_uri())
    return page


def open_screen(browser, tmp_path, app):
    """The Open screen of app, shown once it has asked for its first view."""
    out = tmp_path / "window.html"
    out.write_text(gui.page_html(), encoding="utf-8")
    page = opened(
        browser, out, gui.WindowApi(app, app.ui), ["view", "set", "act", "instructions", "log"]
    )
    page.wait_for_function("document.title.startsWith('prosediff:')")
    return page


def refresh(page, app):
    """What App found in the background (WebUi.render, in the window)."""
    app.settle()
    page.evaluate("v => prosediff.render(v)", app.view())


def until(page, app, check) -> None:
    """Let the page's last calls reach App: check(app) holds, soon."""
    for _ in range(50):
        if check(app):
            return
        page.wait_for_timeout(50)
    assert check(app)


def md_pair(tmp_path):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("We find a small effect.\n", encoding="utf-8")
    new.write_text("We find a large effect.\n", encoding="utf-8")
    return old, new


def test_the_open_screen_shows_the_settings_and_sends_back_each_change(browser, tmp_path):
    """The page shows what App holds; a mode chosen shows its fields alone,
    the title and the main button saying what it does; two Markdown files
    grey out the documents of tracked changes; the sides swapped by their
    button, Save to following them."""
    old, new = md_pair(tmp_path)
    app = gui.App(Ui(), gui.Settings(mode="files", old=str(old), new=str(new)))
    page = open_screen(browser, tmp_path, app)
    assert page.input_value("#f-old") == str(old)
    assert page.is_visible("#f-new") and not page.is_visible("#f-single")
    assert page.is_disabled("input[name=output_format][value=docx]")
    assert not page.is_disabled("input[name=output_format][value=diff]")
    assert page.input_value("#f-output").endswith("old_vs_new.html")
    page.click("button[data-act=swap][data-arg=files]")
    until(page, app, lambda a: a.values["old"] == str(new))
    page.wait_for_function(f"document.querySelector('#f-old').value === {json.dumps(str(new))}")
    assert page.input_value("#f-output").endswith("new_vs_old.html")
    page.click("text=One file")
    page.wait_for_function("document.title === 'prosediff: review one file'")
    assert page.is_visible("#f-single") and not page.is_visible("#f-old")
    assert page.inner_text("#run") == "Review"
    # a review compares nothing: the splits and the format left out
    assert not page.is_visible("text=Compare by") and not page.is_visible("text=Unified diff")


def test_a_field_typed_reaches_the_settings(browser, tmp_path):
    """A field typed in is App's once it is left (or Enter pressed); the
    advanced settings, in their dialog, alike."""
    app = gui.App(Ui(), gui.Settings(mode="files", assess="claude"))
    app.settle()
    page = open_screen(browser, tmp_path, app)
    page.fill("#f-instructions", "The journal is Research Policy.")
    page.press("#f-instructions", "Enter")
    until(page, app, lambda a: a.values["assess_instructions"] == "The journal is Research Policy.")
    page.click("text=Advanced settings")
    assert page.is_visible("#advanced")
    page.fill("#f-hidden", "500")
    page.press("#f-hidden", "Tab")
    page.fill("#f-passage\\.min_words", "6")
    page.press("#f-passage\\.min_words", "Tab")
    until(page, app, lambda a: a.values["passage.min_words"] == "6")
    s = app.collect()
    assert s.max_hidden == 500 and s.moved_passages == {"min_words": 6}
    page.click("#advanced >> text=Close")
    assert not page.is_visible("#advanced")


def test_the_ai_chosen_from_its_list_lists_its_models(browser, tmp_path):
    """The AI's list (its button shows every choice) offers those found; one
    chosen, its models are listed, its default chosen; the model and the
    effort greyed out until then, and while no AI assesses."""
    app = gui.App(Ui(), gui.Settings(mode="files"))
    app.settle()
    page = open_screen(browser, tmp_path, app)
    assert page.input_value("#f-ai") == "none" and page.is_disabled("#f-model")
    page.click("#f-ai + button")
    page.click(".combo .menu li:text-is('ollama')")
    until(page, app, lambda a: a.values["assess_ai"] == "ollama")
    refresh(page, app)
    assert page.input_value("#f-model") == "gemma3:270m"
    assert not page.is_disabled("#f-model")
    page.click("#f-model + button")
    assert page.locator(".combo .menu li").all_inner_texts() == ["gemma3:270m", "qwen3:8b"]


def test_the_instructions_written_in_a_dialog(browser, tmp_path):
    """The pencil opens prosediff's prompts and the instructions in large
    boxes: OK keeps what was written, the prompts left as prosediff's kept
    as none of their own."""
    app = gui.App(Ui(), gui.Settings(mode="files", assess="claude"))
    app.settle()
    page = open_screen(browser, tmp_path, app)
    page.click("[data-id=write_instructions]")
    boxes = page.locator("#instruction-boxes textarea")
    assert boxes.count() == 3
    boxes.nth(2).fill("Check the abstract.")
    page.click("#instructions-ok")
    until(page, app, lambda a: a.values["assess_instructions"] == "Check the abstract.")
    assert app.values["assess_prompt"] == ""  # prosediff's, left as it was


def test_the_files_sent_to_the_ai_listed_and_removed(browser, tmp_path):
    """The files sent to the AI, in their dialog: sorted by a heading
    clicked, one chosen and removed."""
    a, b = tmp_path / "b" / "appendix.docx", tmp_path / "a" / "guidelines.pdf"
    s = gui.Settings(mode="files", assess="claude", assess_send_files=True)
    s.assess_files = f"{a};{b}"
    app = gui.App(Ui(), s)
    app.settle()
    page = open_screen(browser, tmp_path, app)
    assert page.inner_text("[data-text=files_summary]") == "2 files: appendix.docx, guidelines.pdf"
    page.click("[data-id=edit_files]")
    page.click("#files th button[data-arg=folder]")
    page.wait_for_function(
        "document.querySelector('#files th button').parentNode.parentNode.textContent.includes('▲')"
    )
    assert page.locator("#files-rows tr td:first-child").all_inner_texts() == [
        "guidelines.pdf",
        "appendix.docx",
    ]
    page.click("#files-rows tr:has-text('appendix.docx')")
    page.click("#files-remove")
    until(page, app, lambda a: a.files_chosen() == [str(b)])


def test_what_app_says_is_shown(browser, tmp_path):
    """An error in a dialog, the next one after it; a notification that goes
    by itself; the progress log shown once it has a line."""
    app = gui.App(Ui(), gui.Settings(mode="files"))
    page = open_screen(browser, tmp_path, app)
    assert not page.is_visible("#progress")
    page.evaluate("prosediff.log('12:00:00  Comparing…')")
    assert page.is_visible("#progress") and "Comparing…" in page.inner_text("#log")
    page.evaluate("prosediff.alert('error', 'First'); prosediff.alert('warning', 'Second')")
    assert page.inner_text("#alert-text") == "First"
    page.click("#alert >> text=OK")
    page.wait_for_function("document.querySelector('#alert-text').textContent === 'Second'")
    page.click("#alert >> text=OK")
    assert not page.is_visible("#alert")
    page.evaluate("prosediff.toast('1 file changed')")
    assert page.is_visible("#toast")


# The choices made in a report ------------------------------------------------


def fixed_report(tmp_path):
    """A review beside the file with the AI's fixes (FIXED applied, ADVICE
    not), with the documents to download."""
    from prosediff.diff import review_file
    from prosediff.pipeline import Run, fixes_shown

    path = pair(tmp_path, "docx")[1]
    a = assessment([FIXED, ADVICE])
    comparison, shown = fixes_shown(Run("review", str(path), tmp_path / "out.html"), a)
    out = tmp_path / "report.html"
    single = review_file(path, Options())
    out.write_text(render(comparison, assessment=shown, documents_of=(single, a)), encoding="utf-8")
    return out


def key(note) -> str:
    return "␟".join((note.start, note.end, note.problem))


ANSWERS = {"format": 2, "run": {"output": "report.html"}, "assessment": {"verdict": "Fair"}}


def open_in_app(browser, report, app, serial):
    return opened(
        browser,
        report,
        gui.ReportApi(app, serial, report, app.ui),
        ["choices", "save_choices", "save_document"],
    )


def test_the_choices_made_in_a_report_kept_in_the_project(browser, tmp_path):
    """In prosediff's window, a fix undone and a problem left out of the
    download are written into the project open, which holds the report's
    answers; the report opened again shows them as they were left."""
    report = fixed_report(tmp_path)
    project = tmp_path / "paper.prosediff"
    save_project(project, {"mode": "review"}, ANSWERS)
    app = gui.App(Ui(), gui.Settings(mode="review"), (project, ANSWERS, None))
    app.report_serial = 1
    page = open_in_app(browser, report, app, 1)
    fixed = page.locator(".card.problem", has_text="Nothing supports")
    other = page.locator(".card.problem", has_text="Which checks?")
    fixed.locator(".fix-toggle").click()
    other.locator(".keep-box").uncheck()
    until(page, app, lambda a: (project_choices(project) or {}).get("left_out"))
    made = project_choices(project)
    assert made["undone"] == [key(FIXED)] and made["left_out"] == [key(ADVICE)]
    page.reload()
    fixed = page.locator(".card.problem", has_text="Nothing supports")
    page.wait_for_function("document.querySelector('.fix-toggle').textContent.includes('Redo')")
    assert (
        not page.locator(".card.problem", has_text="Which checks?")
        .locator(".keep-box")
        .is_checked()
    )
    # the fix undone on the page too: the original words back on the right
    assert page.locator("td.code.right .fix-orig").first.is_visible()
    page.context.close()


def test_choices_of_a_report_the_project_does_not_hold_wait_for_save_project(browser, tmp_path):
    """A report whose answers the project does not hold yet (made since it
    was saved): its choices kept, not written into the project, until Save
    project, which writes them with its answers; a report opened before the
    last one keeps none."""
    report = fixed_report(tmp_path)
    project = tmp_path / "paper.prosediff"
    save_project(project, {"mode": "review"}, None)
    app = gui.App(Ui(), gui.Settings(mode="review"), (project, ANSWERS, None))
    app.report_serial = 2
    older = open_in_app(browser, report, app, 1)
    older.locator(".card.problem .fix-toggle").first.click()
    older.wait_for_timeout(400)
    assert app.project_choices is None and project_choices(project) is None
    older.context.close()
    page = open_in_app(browser, report, app, 2)
    page.locator(".card.problem .fix-toggle").first.click()
    until(page, app, lambda a: a.project_choices)
    assert project_choices(project) is None and "Save project" in app.status
    app.save_project()
    assert project_choices(project)["undone"] == [key(FIXED)]
    page.context.close()


def test_the_choices_made_in_a_report_opened_as_a_file_kept_by_the_browser(browser, tmp_path):
    """A report opened as a file, not in prosediff's window: its choices kept
    by the browser, for that report, and shown again when it is reloaded."""
    report = fixed_report(tmp_path)
    page = browser.new_context().new_page()
    page.goto(report.as_uri())
    page.locator(".card.problem", has_text="Nothing supports").locator(".fix-toggle").click()
    page.locator(".card.problem", has_text="Which checks?").locator(".resolve-box").check()
    page.wait_for_timeout(400)
    page.reload()
    page.wait_for_function("document.querySelector('.fix-toggle').textContent.includes('Redo')")
    assert (
        page.locator(".card.problem", has_text="Which checks?").locator(".resolve-box").is_checked()
    )
    page.context.close()


def test_a_document_to_download_saved_through_the_window(browser, tmp_path):
    """In prosediff's window, a document to download is saved where its
    dialog says, beside the report at first, not downloaded by the page."""
    report = fixed_report(tmp_path)
    ui = Ui()
    target = tmp_path / "saved" / "with_fixes.docx"
    target.parent.mkdir()
    ui.answers["ask_save"] = str(target)
    app = gui.App(ui, gui.Settings(mode="review"))
    page = open_in_app(browser, report, app, None)
    page.click(".toolbar .ai-download:has-text('With AI fixes')")
    until(page, app, lambda a: target.exists())
    asked = ui.kinds("ask_save")[0]
    assert asked[3] == str(report.parent)  # beside the report
    assert target.read_bytes()[:2] == b"PK"  # a zip: a Word document
    page.context.close()
