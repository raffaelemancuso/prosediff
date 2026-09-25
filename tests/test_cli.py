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


def test_cli_paths(two_commits, tmp_path):
    b, base, target = two_commits
    out = tmp_path / "r.html"
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
    assert main([str(b.path), "HEAD", "--cached", "-w", "-o", str(out)]) == 0


def test_cli_files(tmp_path, capsys):
    (tmp_path / "a.md").write_text("one\n")
    (tmp_path / "b.md").write_text("two\n")
    out = tmp_path / "r.html"
    assert main(["--files", str(tmp_path / "a.md"), str(tmp_path / "b.md"), "-o", str(out)]) == 0
    assert "a.md..b.md" in capsys.readouterr().out


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
    "extra",
    [
        ["HEAD", "HEAD", "--align", "center"],
        ["HEAD", "HEAD", "-U", "-1"],
        ["HEAD", "HEAD", "--move-similarity", "0"],
        ["HEAD", "HEAD", "--move-similarity", "1.5"],
        ["HEAD", "HEAD", "--cached"],
        ["HEAD", "--cached", "--untracked"],
        ["--files", "HEAD", "b", "c"],
        ["--files", "a", "b", "--cached"],
        ["--files", "a", "b", "--untracked"],
    ],
)
def test_cli_rejects_bad_arguments(extra):
    with pytest.raises(SystemExit):
        main([".", *extra] if extra[0] != "--files" else extra)


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("prosediff ")
