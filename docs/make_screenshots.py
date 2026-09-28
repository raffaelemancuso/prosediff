"""Remake the README screenshots from a demo repository.

    uv run --with pillow python docs/make_screenshots.py [--assess AI]

The page is photographed by Playwright's Chromium (uv run playwright
install chromium, once); the window by Pillow, which needs a desktop: the window
shows on screen for a moment. The demo text and its authors are made up.
With --assess (e.g. claude), the AI named really assesses the demo's changes,
once: the report photographed whole (screenshot_page.png) then holds its
assessment and the problems it marked, one pinned; its assessment and its
marks are photographed apart too (screenshot_assessment.png,
screenshot_marks.png). Without it, the report has neither, a comment's
tooltip open, and those two screenshots are left as they are.
"""

import argparse
import ctypes
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

import git
from PIL import ImageGrab
from playwright.sync_api import sync_playwright

from prosediff import compare, render
from prosediff.assess import AssessRequest
from prosediff.gui import App, Settings
from prosediff.render import assess_comparison

DOCS = Path(__file__).parent
# The demo sits in a temporary folder, whose path names the user: the report
# and the window show this one instead.
SHOWN_PATH = r"C:\Users\me\books\wonderland"


def note(text: str, author: str, date: str, cid: int) -> str:
    return f'[{text}]{{.comment-start id="{cid}" author="{author}" date="{date}T10:15:00Z"}}'


# From Lewis Carroll, Alice's Adventures in Wonderland (1865, public domain):
# the opening of Chapter I and a line of Chapter II, lightly edited on the
# second side.
OLD = f"""# Alice's Adventures in Wonderland

## Down the Rabbit-Hole

Alice was beginning to get very tired of sitting by her sister on the bank, and of \
having nothing to do: once or twice she had peeped into the book her sister was reading, \
but it had no pictures or conversations in it, "and what is the use of a book," thought \
Alice "without pictures or conversations?"\
{note("Keep the original punctuation here.", "Anna Keller", "2026-09-10", 1)}

So she was considering in her own mind (as well as she could, for the hot day made her \
feel very sleepy and stupid), whether the pleasure of making a daisy-chain would be worth \
the trouble of getting up and picking the daisies, when suddenly a White Rabbit with pink \
eyes ran close by her.

There was nothing so very remarkable in that; nor did Alice think it so very much out of \
the way to hear the Rabbit say to itself, "Oh dear! Oh dear! I shall be late!"

## The Pool of Tears

"Curiouser and curiouser!" cried Alice (she was so much surprised, that for the moment \
she quite forgot how to speak good English).
"""

NEW = f"""# Alice's Adventures in Wonderland

## Down the Rabbit-Hole

Alice was beginning to get rather tired of sitting by her sister on the bank, and of \
having nothing to do: once or twice she had peeped into the book her sister was reading, \
but it had no pictures or conversations in it, "and what is the use of a book," thought \
Alice "without pictures or conversations?"\
{note("Keep the original punctuation here.", "Anna Keller", "2026-09-10", 7)}

So she was considering in her own mind (as well as she could, for the warm day made her \
feel very sleepy and stupid), whether the pleasure of making a daisy-chain would be worth \
the trouble of getting up and picking the daisies, when suddenly a White Rabbit with pink \
eyes ran close by her.{note("Mention the waistcoat-pocket here?", "Tom Weber", "2026-09-24", 8)}

There was nothing so very remarkable in that; nor did Alice think it so very much out of \
the way to hear the Rabbit say to itself, "Oh dear! Oh dear! I shall be too late!"

Burning with curiosity, she ran across the field after it.

## The Pool of Tears

"Curiouser and curiouser!" cried Alice (she was so much surprised, that for the moment \
she quite forgot how to speak good English).
"""


def demo_repo(root: Path) -> tuple[Path, str]:
    repo = git.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Anna Keller")
        cw.set_value("user", "email", "anna@example.org")
    for text, message in ((OLD, "First draft"), (NEW, "Revision after Tom's review")):
        (root / "wonderland.md").write_text(text, encoding="utf-8", newline="\n")
        repo.index.add(["wonderland.md"])
        repo.index.commit(message)
    return root, repo.head.commit.hexsha


def report(repo: Path, ai: str | None) -> Path:
    """The demo's HTML report, with the assessment of the AI named, made now
    (none without one); where it is written."""
    c = compare(repo, "HEAD~1", "HEAD")
    c.location = SHOWN_PATH
    assessment = None
    if ai:
        assessment = assess_comparison(c, AssessRequest(ai))
        if assessment.error:
            sys.exit(f"the assessment failed: {assessment.error}")
    page_file = repo.parent / "page.html"
    page_file.write_text(render(c, align="justify", assessment=assessment), encoding="utf-8")
    return page_file


def open_panels(page) -> None:
    """Open the report's panels, closed until opened, to show what they hold."""
    page.evaluate(
        "document.querySelectorAll('details.comments-panel, details.assessment')"
        ".forEach(d => { d.open = true; })"
    )


def shoot_page(page_file: Path, out: Path) -> None:
    """The report whole: its first problem the AI marked pinned, its tooltip
    open; without any, a comment's tooltip."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        open_panels(page)
        # as tall as the page, so the whole of it is in view, tooltip included
        height = page.evaluate("document.documentElement.scrollHeight")
        page.set_viewport_size({"width": 1280, "height": height + 60})
        if page.locator(".ai-mark").count():
            page.locator(".ai-mark").first.click()
        else:
            page.locator(".comment.new").first.hover()
        page.screenshot(path=str(out))
        browser.close()


def shoot_assessment(page_file: Path, out: Path, marks: Path) -> bool:
    """The report's AI assessment; then, when it marked problems in the
    text, the AI marks panel and the first problem pinned in the text, its
    tooltip open (marks). Whether marks was made."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        open_panels(page)
        box = page.locator("details.assessment").bounding_box()
        page.screenshot(
            path=str(out),
            clip={"x": 0, "y": 0, "width": 1280, "height": box["y"] + box["height"] + 16},
        )
        if not page.locator(".ai-mark").count():
            print("the AI marked no problem in the text: no screenshot of the marks")
            browser.close()
            return False
        # the page taller than its contents, so what is shot stays in view
        page.set_viewport_size({"width": 1280, "height": 2400})
        page.locator("details.assessment").evaluate("d => d.open = false")
        page.locator(".ai-mark").first.click()
        panel = page.locator("details.ai-notes").bounding_box()
        tip = page.locator("#tip").bounding_box()
        bottom = max(tip["y"] + tip["height"], page.locator(".ai-mark").first.bounding_box()["y"])
        page.screenshot(
            path=str(marks),
            clip={"x": 0, "y": panel["y"] - 8, "width": 1280, "height": bottom - panel["y"] + 40},
        )
        browser.close()
    return True


def shoot_window(repo: Path, out: Path) -> None:
    if sys.platform == "win32":  # pixel coordinates, whatever the display scaling
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    app = App(
        root,
        Settings(
            repo=str(repo), output="", align="justify", assess="claude/opus", assess_effort="high"
        ),
    )
    app.repo.set(SHOWN_PATH)
    # the model lists as the AI reports them, not "Loading…": at most 30 s
    waited = time.monotonic()
    while app.asking and time.monotonic() - waited < 30:
        root.update()
        time.sleep(0.05)
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    root.after(700, root.quit)
    root.mainloop()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(out)
    root.destroy()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Remake the README screenshots.")
    ap.add_argument("--assess", metavar="AI", help="photograph a real AI assessment by AI too")
    args = ap.parse_args()
    written = [DOCS / "screenshot_page.png", DOCS / "screenshot_window.png"]
    # a helper process of the window may still hold the demo folder: left behind
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo, _ = demo_repo(Path(tmp) / "wonderland")
        page_file = report(repo, args.assess)  # the AI asked once, for every shot
        shoot_page(page_file, written[0])
        shoot_window(repo, written[1])
        if args.assess:
            shots = DOCS / "screenshot_assessment.png", DOCS / "screenshot_marks.png"
            if shoot_assessment(page_file, *shots):
                written += shots
            else:
                written.append(shots[0])
    print("written:", *written)
