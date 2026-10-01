"""Comments marked resolved: left out by default, shown and sent to the AI
with skip_resolved off. The documents were written by Word (a resolved
comment with a reply, and an open one) and converted by LibreOffice."""

from pathlib import Path

import pytest
from helpers import docx

from prosediff.cli import main
from prosediff.diff import Options, review_file
from prosediff.render import new_version

DATA = Path(__file__).parent / "data" / "resolved_comments"
RESOLVED = ("Cite the source.", "Done, cited.")


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_resolved_comments_left_out(fmt):
    path = DATA / f"resolved.{fmt}"
    skipped = review_file(path, Options())
    assert [e.text for e in skipped.comments] == ["Which data?"]
    text = new_version(skipped)
    assert "Which data?" in text and not any(r in text for r in RESOLVED)

    kept = review_file(path, Options(skip_resolved=False))
    assert sorted(e.text for e in kept.comments) == sorted(["Which data?", *RESOLVED])
    assert all(r in new_version(kept) for r in RESOLVED)


def test_the_switch_on_the_command_line(tmp_path):
    """Compared with the same text without comments, the comments are new:
    the resolved ones shown only with --no-skip-resolved."""
    plain = tmp_path / "plain.docx"
    docx(
        plain,
        [[("run", "The first claim is settled.")], [("run", "The second claim is still open.")]],
    )
    out = tmp_path / "out.html"
    args = ["--files", str(plain), str(DATA / "resolved.docx"), "-o", str(out)]
    assert main([*args, "--no-skip-resolved"]) == 0
    assert "Done, cited." in out.read_text(encoding="utf-8")
    assert main(args) == 0
    page = out.read_text(encoding="utf-8")
    assert "Which data?" in page and "Done, cited." not in page
