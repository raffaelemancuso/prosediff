"""The unified diff: made from the HTML report's line pairing, a patch for text
files, comments and formatting of prose written out."""

import subprocess

from helpers import END, NOTE

from prosediff import compare_paths
from prosediff.unified import unified


def rows_of(comparison):
    """The (old, new) line numbers of the HTML report's rows, unchanged ones aside."""
    (f,) = comparison.files
    return [(r.left_no, r.right_no) for r in f.rows if r.kind not in ("equal", "skip")]


def test_pairing_is_the_pages(tmp_path):
    """An edited line faces its new text in the diff as in the HTML report: its
    removal is followed by its new version, line by line."""
    old, new = tmp_path / "a.txt", tmp_path / "b.txt"
    old.write_text("keep\nthe quick brown fox\njumps over the dog\nkeep\n")
    new.write_text("keep\nthe quick red fox\njumps over the lazy dog\nkeep\n")
    c = compare_paths(old, new)
    assert rows_of(c) == [(2, 2), (3, 3)]
    assert unified(c, 1).splitlines()[2:] == [
        "@@ -1,4 +1,4 @@",
        " keep",
        "-the quick brown fox",
        "+the quick red fox",
        "-jumps over the dog",
        "+jumps over the lazy dog",
        " keep",
    ]


def test_a_text_files_diff_applies(tmp_path):
    """A text file's diff is a patch: git apply turns the old file into the
    new one."""
    old_lines = [f"line {k}" for k in range(1, 30)]
    new_lines = list(old_lines)
    new_lines[3] = "line four"
    del new_lines[10:12]
    new_lines[20:20] = ["inserted", "lines"]
    new_lines.append("end")
    work = tmp_path / "work"
    work.mkdir()
    (work / "t.txt").write_bytes(("\n".join(old_lines) + "\n").encode())
    (tmp_path / "t.txt").write_bytes(("\n".join(new_lines) + "\n").encode())
    c = compare_paths(work / "t.txt", tmp_path / "t.txt")
    patch = tmp_path / "t.diff"
    patch.write_bytes(unified(c).encode())
    # autocrlf off: git on Windows runners would write the file with CRLF
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", str(patch)], cwd=work, check=True, timeout=60
    )
    assert (work / "t.txt").read_bytes() == (tmp_path / "t.txt").read_bytes()


def test_comments_in_criticmarkup(tmp_path):
    """A comment added is written in CriticMarkup, its author and date with
    it; a comment both sides have is left out, as in the HTML report."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text(f"One {NOTE}sentence{END}.\n\nTwo words.\n")
    other = NOTE.replace("Too long.", "Which two?")
    new.write_text(f"One {NOTE}sentence{END}.\n\n{other}Two{END} words.\n")
    text = unified(compare_paths(old, new))
    assert "-Two words.\n+{>>Anna (2026-09-23 23:40): Which two?<<}Two words.\n" in text
    assert text.count("{>>") == 1


def test_comments_left_out(tmp_path):
    """With the comments left out, a line whose only change was a comment is
    unchanged, and the diff empty."""
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("One sentence.\n\nTwo words.\n")
    new.write_text(f"One sentence.\n\n{NOTE}Two{END} words.\n")
    assert "{>>" in unified(compare_paths(old, new))
    assert unified(compare_paths(old, new, drop_comments=True)) == ""


def test_word_diff(tmp_path):
    """The word diff marks the words changed within each line, paired as on
    the HTML report; lines removed or added whole are marked whole."""
    old, new = tmp_path / "a.txt", tmp_path / "b.txt"
    old.write_text("keep\nthe quick brown fox\ngone for good\nkeep\n")
    new.write_text("keep\nthe quick red fox\nkeep\nnew line\n")
    assert unified(compare_paths(old, new), 1, "wdiff").splitlines()[2:] == [
        "@@ -1,4 +1,4 @@",
        "keep",
        "the quick [-brown-]{+red+} fox",
        "[-gone for good-]",
        "keep",
        "{+new line+}",
    ]


def test_cli_word_diff(tmp_path):
    """An output ending in .wdiff writes the word diff; --comments none
    leaves the comments out, --comments text writes them as pandoc does."""
    from prosediff.cli import main

    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_text("A plain claim.\n")
    new.write_text(f"A {NOTE}bold{END} claim.\n")
    out = tmp_path / "changes.wdiff"
    assert main(["--files", str(old), str(new), "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "[-plain-]{+{>>Anna (2026-09-23 23:40): Too long.<<}bold+}" in text
    files = ["--files", str(old), str(new), "-o", str(out)]
    assert main([*files, "--comments", "none"]) == 0
    assert "A [-plain-]{+bold+} claim." in out.read_text(encoding="utf-8")
    assert main([*files, "--comments", "text"]) == 0
    assert ".comment-start" in out.read_text(encoding="utf-8")
