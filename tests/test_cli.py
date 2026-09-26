"""The command line: its options reach the comparison, and its errors."""

import sys

import pytest

from prosediff.cli import main


def test_cli_writes_report(two_commits, tmp_path, capsys):
    b, base, target = two_commits
    out = tmp_path / "report.html"
    assert main([str(b.path), base, target, "--align", "justify", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert ">world</del>" in html and "text-align: justify" in html
    assert b"\r\n" not in out.read_bytes()
    assert "+2 -1" in capsys.readouterr().out
    # -p restricts the comparison, and can be given more than once
    assert main([str(b.path), base, target, "-p", "nothing_here", "-o", str(out)]) == 0
    assert "No differences" in out.read_text(encoding="utf-8")
    assert main([str(b.path), base, target, "-p", "nothing", "-p", "doc.md", "-o", str(out)]) == 0
    assert ">world</del>" in out.read_text(encoding="utf-8")


def test_cli_worktree_index_and_untracked(two_commits, tmp_path, capsys):
    b, _, target = two_commits
    (b.path / "doc.md").write_text("Changed on disk.\n", encoding="utf-8")
    (b.path / "u.txt").write_text("untracked\n")
    out = tmp_path / "r.html"
    assert main([str(b.path), "HEAD", "--untracked", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "working tree" in html and "disk" in html and "u.txt" in html
    assert f"{target[:7]}..working tree" in capsys.readouterr().out
    # a re-indented line staged: the index differs, but not when -w ignores it
    b.write("doc.md", "Hello there.\n  Second line.\n<script>x</script>\n")
    assert main([str(b.path), "HEAD", "--cached", "-o", str(out)]) == 0
    assert f"{target[:7]}..index: 1 file, +1 -1" in capsys.readouterr().out
    assert main([str(b.path), "HEAD", "--cached", "-w", "-o", str(out)]) == 0
    assert "index: 1 file, +0 -0" in capsys.readouterr().out


def test_cli_errors(two_commits, tmp_path, capsys):
    """A failure exits with 1 and says why."""
    b, base, target = two_commits
    (tmp_path / "plain").mkdir()
    fail = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    out = ["-o", str(tmp_path / "r.html")]
    for args, message in [
        ([str(b.path), "HEAD", "nosuchrev", *out], "not a commit"),
        ([str(tmp_path / "nope"), "a", "b"], "no such folder"),
        ([str(tmp_path / "plain"), "a"], "--files"),
        (["--files", str(tmp_path / "plain"), str(tmp_path / "nope")], "no such file"),
        (
            [str(b.path), base, target, "--md-filter", fail, *out],
            "filter failed on doc.md (exit 3)",
        ),
    ]:
        assert main(args) == 1
        assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        [".", "HEAD", "HEAD", "--align", "center"],
        [".", "HEAD", "HEAD", "-U", "-1"],
        [".", "HEAD", "HEAD", "--move-similarity", "0"],
        [".", "HEAD", "HEAD", "--move-similarity", "1.5"],
        [".", "HEAD", "HEAD", "--encoding", "no-such-codec"],
        [".", "HEAD", "HEAD", "--cached"],
        [".", "HEAD", "--cached", "--untracked"],
        ["--files", "HEAD", "b", "c"],
        ["--files", "a", "b", "--cached"],
        ["--files", "a", "b", "--untracked"],
        ["--global"],
        ["--setup-git", "a", "b"],
        ["--setup-git", "--global", "a"],
        ["--to-markdown", "x.docx", "a"],
        ["a"],
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
