"""Remake src/prosediff/flags.zip, the flags the page shows languages by,
from the flag-icons package (MIT, https://github.com/lipis/flag-icons).

    uv run python docs/make_flags.py

Its 4:3 flag of every country (the SVGs named by a two-letter code, not the
regions') and its licence are stored compressed in one file: a tenth the
size of the SVGs, of which a page embeds only those it shows.
"""

import io
import json
import re
import tarfile
import urllib.request
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "src" / "prosediff"
REGISTRY = "https://registry.npmjs.org/flag-icons/latest"
COUNTRY = re.compile(r"package/flags/4x3/([a-z]{2})\.svg")


def main() -> None:
    with urllib.request.urlopen(REGISTRY) as r:
        latest = json.load(r)
    with urllib.request.urlopen(latest["dist"]["tarball"]) as r:
        tarball = io.BytesIO(r.read())
    out = PACKAGE / "flags.zip"
    count = 0
    with (
        tarfile.open(fileobj=tarball) as tar,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z,
    ):
        for member in sorted(tar.getmembers(), key=lambda m: m.name):
            data = tar.extractfile(member) if member.isfile() else None
            if data is None:
                continue
            if m := COUNTRY.fullmatch(member.name):
                z.writestr(f"{m[1]}.svg", data.read())
                count += 1
            elif member.name == "package/LICENSE":
                z.writestr("LICENSE", data.read())
    print(f"{out}: {count:,} flags of flag-icons {latest['version']}, {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
