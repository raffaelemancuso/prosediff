"""Remake the README screenshots from a demo repository.

    uv run --with pillow python docs/make_screenshots.py

The page is photographed by Playwright's Chromium (uv run playwright
install chromium, once), the window by Pillow, which needs a desktop: the
window shows on screen for a moment. The demo text and its authors are
made up.
"""

import ctypes
import sys
import tempfile
import tkinter as tk
from pathlib import Path

import git
from PIL import ImageGrab
from playwright.sync_api import sync_playwright

from sidediff import compare, render
from sidediff.gui import App, Settings

DOCS = Path(__file__).parent


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


def shoot_page(repo: Path, out: Path) -> None:
    page_file = repo.parent / "page.html"
    page_file.write_text(render(compare(repo, "HEAD~1", "HEAD"), align="justify"), encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        page.locator(".comment.new").first.hover()  # show a comment tooltip
        page.screenshot(path=str(out))
        browser.close()


def shoot_window(repo: Path, out: Path) -> None:
    if sys.platform == "win32":  # pixel coordinates, whatever the display scaling
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    app = App(root, Settings(repo=str(repo), output="", align="justify"))
    # The demo sits in a temporary folder, whose path names the user.
    app.repo.set(r"C:\Users\me\books\wonderland")
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
    with tempfile.TemporaryDirectory() as tmp:
        repo, _ = demo_repo(Path(tmp) / "wonderland")
        shoot_page(repo, DOCS / "screenshot_page.png")
        shoot_window(repo, DOCS / "screenshot_window.png")
    print("written:", DOCS / "screenshot_page.png", DOCS / "screenshot_window.png")
