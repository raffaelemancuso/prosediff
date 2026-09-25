"""Remake the window icons from docs/logo.svg: src/prosediff/logo.ico for
Windows (title bar, taskbar, and the Explorer context menu) and
src/prosediff/logo.png for the other systems.

    uv run --with pillow python docs/make_icon.py

Each size is drawn from the SVG by Playwright's Chromium (uv run playwright
install chromium, once), on a transparent background, rather than scaled
down from the largest, so the small sizes stay sharp.
"""

import base64
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).parent
PACKAGE = DOCS.parent / "src" / "prosediff"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main() -> None:
    svg = (DOCS / "logo.svg").read_bytes()
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    images = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for size in SIZES:
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(
                f'<body style="margin:0"><img src="{uri}" width="{size}" height="{size}"'
                ' style="display:block"></body>'
            )
            png = page.screenshot(omit_background=True)
            images.append(Image.open(io.BytesIO(png)).convert("RGBA"))
        browser.close()
    largest = images[-1]
    # sizes: Pillow's default list leaves out 20 and 40 (125% and 250% scaling)
    largest.save(
        PACKAGE / "logo.ico",
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )
    largest.save(PACKAGE / "logo.png")
    print(f"{PACKAGE / 'logo.ico'}: {', '.join(str(s) for s in SIZES)} px")
    print(f"{PACKAGE / 'logo.png'}: {largest.width} px")


if __name__ == "__main__":
    main()
