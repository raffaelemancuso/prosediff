"""Remake the README screenshots from a demo repository.

    uv run --with pillow python docs/make_screenshots.py [--assess AI]

The page is photographed by Playwright's Chromium (uv run playwright
install chromium, once); the window by Pillow, which needs a desktop: the window
shows on screen for a moment. The demo text, a short one on free fall, was
written for it, and so were its revision, authors and comments.
With --assess (e.g. claude), the AI named really assesses the demo's changes,
once, its assessment kept in screenshot_assessment.json and used again, the AI
not asked, until the demo text changes (or with --reassess): the report
photographed whole (screenshot_page.png) then holds its
verdict and the problems it marked, one pinned; its assessment, opened in
its drawer, and a paragraph with its marks are photographed apart too
(screenshot_assessment.png, screenshot_marks.png). Without it, the report
has neither, a comment pinned, and those two screenshots are left as they
are.
"""

import argparse
import ctypes
import hashlib
import json
import sys
import tempfile
import time
import tkinter as tk
from dataclasses import asdict
from pathlib import Path

import git
from PIL import ImageGrab
from playwright.sync_api import sync_playwright

from prosediff import compare, render
from prosediff.assess import Annotation, Assessment, AssessRequest
from prosediff.gui import App, Settings
from prosediff.render import assess_comparison

DOCS = Path(__file__).parent
# The demo sits in a temporary folder, whose path names the user: the report
# and the window show this one instead.
SHOWN_PATH = r"C:\Users\me\notes\physics"
# The AI's assessment of the demo, kept so the screenshots can be taken again
# without asking it: the demo it assessed named by a hash of its text.
CACHE = DOCS / "screenshot_assessment.json"


def note(text: str, author: str, date: str, cid: int) -> str:
    return f'[{text}]{{.comment-start id="{cid}" author="{author}" date="{date}T10:15:00Z"}}'


# A short text on free fall, written for the demo. The second side is a
# made-up revision whose changes alter the meaning and bring in errors (the
# fall made to depend on mass, the unit of g, the half dropped from the law of
# distance and the example worked from it, air resistance made to fall with
# speed, a typo), for the AI to find; one comment removed, one added.
OLD = f"""# Free fall

## Galileo's law

Near the surface of the Earth, a body falling freely, with nothing but its weight \
acting on it, moves with a constant acceleration g of about 9.81 m/s², whatever its \
mass. A hammer and a feather dropped together in a vacuum land at the same time, as \
the astronaut David Scott showed on the Moon in 1971.\
{note("Say that the Moon's g is about a sixth of the Earth's.", "Anna Keller", "2026-09-10", 1)}

Released from rest, a body falls a distance d = ½ g t² in a time t and reaches a speed \
v = g t. After 2 seconds a stone dropped from a bridge has fallen about 19.6 m and is \
moving at about 19.6 m/s. The distance grows with the square of the time: in twice the \
time, the body falls four times as far.

## Air resistance

In air the law holds only at low speeds. Air resistance grows with speed until it \
equals the weight of the body; from then on the body stops accelerating and falls at \
a constant terminal velocity, about 55 m/s for a skydiver lying flat.\
{note("Is 55 m/s right for a skydiver?", "Tom Weber", "2026-09-20", 2)}
"""

NEW = f"""# Free fall

## Galileo's law

Near the surface of the Earth, a body falling freely, with nothing but its weight \
acting on it, moves with a constant accleration g of about 9.81 m/s, and a heavier \
body falls faster than a lighter one. A hammer and a feather dropped together in a \
vacuum land at the same time, as the astronaut David Scott showed on the Moon in 1971.\
{note("Say that the Moon's g is about a sixth of the Earth's.", "Anna Keller", "2026-09-10", 7)}

Released from rest, a body falls a distance d = g t² in a time t and reaches a speed \
v = g t. The distance grows with the square of the time: in twice the time, the body \
falls four times as far. After 2 seconds a stone dropped from a bridge has fallen about \
39.2 m and is moving at about 19.6 m/s.

## Air resistance

In air the law holds only at low speeds. Air resistance decreases with speed until it \
equals the weight of the body; from then on the body stops accelerating and falls at \
a constant terminal velocity, about 55 m/s for a skydiver lying \
flat.{note("Add a figure of the speed against time?", "Tom Weber", "2026-09-24", 8)}
"""


def demo_repo(root: Path) -> tuple[Path, str]:
    repo = git.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Anna Keller")
        cw.set_value("user", "email", "anna@example.org")
    for text, message in ((OLD, "First draft"), (NEW, "Revision after Tom's review")):
        (root / "free_fall.md").write_text(text, encoding="utf-8", newline="\n")
        repo.index.add(["free_fall.md"])
        repo.index.commit(message)
    return root, repo.head.commit.hexsha


def demo_key(ai: str) -> str:
    """What an assessment was of: the AI and the demo's two versions."""
    return hashlib.sha256(f"{ai}\0{OLD}\0{NEW}".encode()).hexdigest()


def assessment_of(c, ai: str, again: bool) -> Assessment:
    """The AI's assessment of the demo: the one kept, when it is of this
    demo by this AI and not asked again; else asked now, and kept."""
    if not again and CACHE.is_file():
        kept = json.loads(CACHE.read_text(encoding="utf-8"))
        if kept.get("demo") == demo_key(ai):
            a = kept["assessment"]
            a["annotations"] = [Annotation(**n) for n in a["annotations"]]
            print(f"the assessment kept in {CACHE.name}: {ai} not asked")
            return Assessment(**a)
    assessment = assess_comparison(c, AssessRequest(ai))
    if assessment.error:
        sys.exit(f"the assessment failed: {assessment.error}")
    kept = {"demo": demo_key(ai), "assessment": asdict(assessment)}
    CACHE.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return assessment


def report(repo: Path, ai: str | None, again: bool = False) -> Path:
    """The demo's HTML report, with the assessment of the AI named (the one
    kept, unless again; none without an AI); where it is written."""
    c = compare(repo, "HEAD~1", "HEAD")
    c.location = SHOWN_PATH
    assessment = assessment_of(c, ai, again) if ai else None
    page_file = repo.parent / "page.html"
    page_file.write_text(render(c, align="justify", assessment=assessment), encoding="utf-8")
    return page_file


def shoot_page(page_file: Path, out: Path) -> None:
    """The report whole: its first problem the AI marked pinned, its passage
    highlighted and its card ringed in the margin; without any, a comment's."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        if page.locator(".ai-mark:visible").count():
            page.locator(".ai-mark:visible").first.click()
        else:
            page.locator(".card:visible").first.click()
        # as tall as the page, so the whole of it is in view, margin included
        height = page.evaluate("document.documentElement.scrollHeight")
        page.set_viewport_size({"width": 1280, "height": height + 20})
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out))
        browser.close()


def shoot_assessment(page_file: Path, out: Path, marks: Path) -> bool:
    """The report's AI assessment, opened in its drawer from the verdict in
    the top bar; then, when it marked problems in the text, the paragraph of
    the first, pinned, beside it the cards of its margin (marks). Whether
    marks was made."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        page.locator(".verdict-button").click()
        drawer = page.locator("#assessment")
        # the window as tall as the drawer's contents, so all of it shows
        tall = page.evaluate(
            "document.getElementById('assessment').scrollHeight"
            " + document.querySelector('.toolbar').offsetHeight"
        )
        page.set_viewport_size({"width": 1280, "height": tall + 20})
        box = drawer.bounding_box()
        page.screenshot(
            path=str(out), clip={"x": 0, "y": 0, "width": 1280, "height": box["y"] + box["height"]}
        )
        if not page.locator(".ai-mark:visible").count():
            print("the AI marked no problem in the text: no screenshot of the marks")
            browser.close()
            return False
        page.keyboard.press("Escape")  # the drawer closed
        page.set_viewport_size({"width": 1280, "height": 2400})
        mark = page.locator(".ai-mark:visible").first
        mark.click()
        row = mark.locator("xpath=ancestor::tr").bounding_box()
        stack = mark.locator("xpath=ancestor::tr").locator(".stack").bounding_box()
        top = row["y"] - 30
        bottom = max(row["y"] + row["height"], stack["y"] + stack["height"] if stack else 0) + 16
        page.screenshot(
            path=str(marks), clip={"x": 0, "y": top, "width": 1280, "height": bottom - top}
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
    ap.add_argument(
        "--reassess", action="store_true", help=f"ask the AI again, not using {CACHE.name}"
    )
    args = ap.parse_args()
    written = [DOCS / "screenshot_page.png", DOCS / "screenshot_window.png"]
    # a helper process of the window may still hold the demo folder: left behind
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo, _ = demo_repo(Path(tmp) / "physics")
        page_file = report(repo, args.assess, args.reassess)  # one for every shot
        shoot_page(page_file, written[0])
        shoot_window(repo, written[1])
        if args.assess:
            shots = DOCS / "screenshot_assessment.png", DOCS / "screenshot_marks.png"
            if shoot_assessment(page_file, *shots):
                written += shots
            else:
                written.append(shots[0])
    print("written:", *written)
