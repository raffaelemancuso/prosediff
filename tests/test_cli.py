"""The command line: its options reach the comparison, and its errors."""

import sys

import pytest
from conftest import two_versions

from prosediff.cli import main


def test_cli_writes_report(two_commits, tmp_path, capsys):
    b, base, target = two_commits
    out = tmp_path / "report.html"
    assert main(["--git", str(b.path), base, target, "--align", "justify", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert ">world</del>" in html and "text-align: justify" in html
    assert b"\r\n" not in out.read_bytes()
    assert "+2 -1" in capsys.readouterr().out
    # -p restricts the comparison, and can be given more than once
    assert main(["--git", str(b.path), base, target, "-p", "nothing_here", "-o", str(out)]) == 0
    assert "No differences" in out.read_text(encoding="utf-8")
    assert (
        main(["--git", str(b.path), base, target, "-p", "nothing", "-p", "doc.md", "-o", str(out)])
        == 0
    )
    assert ">world</del>" in out.read_text(encoding="utf-8")


def test_cli_worktree_index_and_untracked(builder, tmp_path, capsys):
    b, _, target = two_versions(builder)
    (b.path / "doc.md").write_text("Changed on disk.\n", encoding="utf-8")
    (b.path / "u.txt").write_text("untracked\n")
    out = tmp_path / "r.html"
    assert main(["--git", str(b.path), "HEAD", "--untracked", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "working tree" in html and "disk" in html and "u.txt" in html
    assert f"{target[:7]}..working tree" in capsys.readouterr().out
    # a re-indented line staged: the index differs, but not when -w ignores it
    b.write("doc.md", "Hello there.\n  Second line.\n<script>x</script>\n")
    assert main(["--git", str(b.path), "HEAD", "--cached", "-o", str(out)]) == 0
    assert f"{target[:7]}..index: 1 file, +1 -1" in capsys.readouterr().out
    assert main(["--git", str(b.path), "HEAD", "--cached", "-w", "-o", str(out)]) == 0
    assert "index: 1 file, +0 -0" in capsys.readouterr().out


def test_cli_errors(two_commits, tmp_path, capsys):
    """A failure exits with 1 and says why."""
    b, base, target = two_commits
    (tmp_path / "plain").mkdir()
    fail = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    out = ["-o", str(tmp_path / "r.html")]
    for args, message in [
        (["--git", str(b.path), "HEAD", "nosuchrev", *out], "not a commit"),
        (["--git", str(tmp_path / "nope"), "a", "b"], "no such folder"),
        (["--git", str(tmp_path / "plain"), "a"], "--files or --folders"),
        (["--files", str(tmp_path / "plain"), str(tmp_path / "nope")], "no such file"),
        (
            ["--git", str(b.path), base, target, "--md-filter", fail, *out],
            "filter failed on doc.md (exit 3)",
        ),
    ]:
        assert main(args) == 1
        assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--git", ".", "HEAD", "HEAD", "--align", "center"],
        ["--git", ".", "HEAD", "HEAD", "-U", "-1"],
        ["--git", ".", "HEAD", "HEAD", "--move-similarity", "0"],
        ["--git", ".", "HEAD", "HEAD", "--move-similarity", "1.5"],
        ["--git", ".", "HEAD", "HEAD", "--encoding", "no-such-codec"],
        ["--git", ".", "HEAD", "HEAD", "--cached"],
        ["--git", ".", "HEAD", "--cached", "--untracked"],
        ["--files", "HEAD", "b", "c"],
        ["--files", "a", "b", "--cached"],
        ["--files", "a", "b", "--untracked"],
        ["--global"],
        ["--setup-git", "a", "b"],
        ["--setup-git", "--global", "a"],
        ["--to-markdown", "x.docx", "a"],
        ["a"],
        # one and only one of --git, --files and --folders
        [".", "HEAD"],
        ["--git", "--files", ".", "HEAD"],
        ["--files", "--folders", "a", "b"],
        ["--git", "--setup-git"],
        ["--files", "a"],
        ["--folders", "a", "b", "-p", "x", "--cached"],
        ["--files", "a", "b", "-p", "x"],
        ["--files", "a", "b", "--include", "*.md"],
    ],
)
def test_cli_rejects_bad_arguments(args):
    """Arguments that do not go together stop argparse, with its exit code 2."""
    with pytest.raises(SystemExit) as stop:
        main(args)
    assert stop.value.code == 2


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("prosediff ")


def test_cli_encoding(tmp_path, capsys):
    """A given encoding is used as it is; auto reads a Latin-1 file right."""
    old, new, out = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "page.html"
    old.write_bytes("caf\xe9\n".encode("latin-1"))
    new.write_bytes("caff\xe8\n".encode("latin-1"))
    assert main(["--files", str(old), str(new), "-o", str(out), "--encoding", "latin-1"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "\xe8" in page and "read as iso8859-1" in page
    assert main(["--files", str(old), str(new), "-o", str(out)]) == 0
    assert "read as cp1252" in out.read_text(encoding="utf-8")


def test_cli_files_and_folders_are_told_apart(tmp_path, capsys):
    """--files takes two files and --folders two folders; the wrong one says
    which to use (a difftool set up by an older prosediff gave --files two
    folders)."""
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "p.md").write_text(f"{d}\n")
    folders = [str(tmp_path / "a"), str(tmp_path / "b")]
    files = [str(tmp_path / "a" / "p.md"), str(tmp_path / "b" / "p.md")]
    out = ["-o", str(tmp_path / "page.html")]
    with pytest.raises(SystemExit):
        main(["--files", *folders, *out])
    assert "use --folders" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--folders", *files, *out])
    assert "use --files" in capsys.readouterr().err
    assert main(["--folders", *folders, *out]) == 0
    assert main(["--files", *files, *out]) == 0


def test_cli_writes_a_unified_diff(tmp_path, monkeypatch):
    """--format diff, or an output ending in .diff or .patch, writes a
    unified diff instead of the HTML report, with -U lines of context; binary
    files are named, not shown."""
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    lines = [f"line {k}" for k in range(1, 11)]
    (tmp_path / "a" / "t.txt").write_text("\n".join(lines) + "\n")
    lines[4] = "line five"
    (tmp_path / "b" / "t.txt").write_text("\n".join(lines) + "\n")
    old, new = tmp_path / "a" / "t.txt", tmp_path / "b" / "t.txt"
    out = tmp_path / "changes.patch"
    assert main(["--files", str(old), str(new), "-o", str(out), "-U", "1"]) == 0
    assert out.read_bytes().decode() == (
        "--- a/t.txt\n+++ b/t.txt\n@@ -4,3 +4,3 @@\n line 4\n-line 5\n+line five\n line 6\n"
    )
    # --format diff names the default output .diff
    monkeypatch.chdir(tmp_path)
    assert main(["--files", str(old), str(new), "--format", "diff"]) == 0
    assert (tmp_path / "diff.diff").read_text().count("\n ") == 6
    # into the new folder, as prosediff.diff; an image is named, not shown
    (tmp_path / "a" / "p.png").write_bytes(b"\x89PNG\x00a")
    (tmp_path / "b" / "p.png").write_bytes(b"\x89PNG\x00b")
    folders = ["--folders", str(tmp_path / "a"), str(tmp_path / "b"), "--include", ""]
    assert main([*folders, "--format", "diff", "--full"]) == 0
    text = (tmp_path / "b" / "prosediff.diff").read_text()
    assert "Binary files a/p.png and b/p.png differ" in text
    assert "@@ -1,10 +1,10 @@" in text
