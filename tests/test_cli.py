"""The command line."""

import sys

import pytest
from helpers import NOTE

from sidediff.cli import main


def test_cli_writes_report(two_commits, tmp_path, capsys):
    b, base, target = two_commits
    out = tmp_path / "report.html"
    assert main([str(b.path), base, target, "-o", str(out)]) == 0
    assert ">world</del>" in out.read_text(encoding="utf-8")
    assert b"\r\n" not in out.read_bytes()
    assert "+2 -1" in capsys.readouterr().out


def test_cli_paths(two_commits, tmp_path):
    b, base, target = two_commits
    out = tmp_path / "r.html"
    assert main([str(b.path), base, target, "-p", "nothing_here", "-o", str(out)]) == 0
    assert "No differences" in out.read_text(encoding="utf-8")
    assert main([str(b.path), base, target, "-p", "nothing", "-p", "doc.md", "-o", str(out)]) == 0
    assert ">world</del>" in out.read_text(encoding="utf-8")


def test_cli_worktree(two_commits, tmp_path, capsys):
    b, _, target = two_commits
    (b.path / "doc.md").write_text("Changed on disk.\n", encoding="utf-8")
    out = tmp_path / "r.html"
    assert main([str(b.path), "HEAD", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "working tree" in html
    assert "disk" in html
    assert f"{target[:7]}..working tree" in capsys.readouterr().out


def test_cli_align(two_commits, tmp_path):
    b, base, target = two_commits
    out = tmp_path / "r.html"
    assert main([str(b.path), base, target, "--align", "justify", "-o", str(out)]) == 0
    assert "text-align: justify" in out.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main([str(b.path), base, target, "--align", "center"])


def test_cli_md_filter_failure(two_commits, tmp_path, capsys):
    b, base, target = two_commits
    fail = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    assert (
        main([str(b.path), base, target, "--md-filter", fail, "-o", str(tmp_path / "r.html")]) == 1
    )
    assert "filter failed on doc.md (exit 3)" in capsys.readouterr().err


def test_cli_bad_commit(two_commits, tmp_path, capsys):
    b, _, _ = two_commits
    assert main([str(b.path), "HEAD", "nosuchrev", "-o", str(tmp_path / "r.html")]) == 1
    assert "not a commit" in capsys.readouterr().err


def test_cli_missing_folder(tmp_path, capsys):
    assert main([str(tmp_path / "nope"), "a", "b"]) == 1
    assert "no such folder" in capsys.readouterr().err


def test_cli_negative_context(two_commits):
    b, base, target = two_commits
    with pytest.raises(SystemExit):
        main([str(b.path), base, target, "-U", "-1"])


def test_cli_new_options(builder, tmp_path):
    builder.write("f.txt", "a\n")
    builder.commit("first")
    (builder.path / "u.txt").write_text("untracked\n")
    out = tmp_path / "r.html"
    assert main([str(builder.path), "HEAD", "--untracked", "-o", str(out)]) == 0
    assert "untracked" in out.read_text(encoding="utf-8")
    assert main([str(builder.path), "HEAD", "--cached", "-w", "-o", str(out)]) == 0
    with pytest.raises(SystemExit):
        main([str(builder.path), "HEAD", "HEAD", "--cached"])
    with pytest.raises(SystemExit):
        main([str(builder.path), "HEAD", "--cached", "--untracked"])


def test_cli_fold_comments(builder, tmp_path):
    builder.write("p.md", "Text\n")
    base = builder.commit("first")
    builder.write("p.md", f"Text changed {NOTE}\n")
    target = builder.commit("second")
    out = tmp_path / "r.html"
    assert main([str(builder.path), base, target, "--fold-comments", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "comment-start" not in html and 'class="comment new"' in html


def test_cli_files(tmp_path, capsys):
    (tmp_path / "a.md").write_text("one\n")
    (tmp_path / "b.md").write_text("two\n")
    out = tmp_path / "r.html"
    assert main(["--files", str(tmp_path / "a.md"), str(tmp_path / "b.md"), "-o", str(out)]) == 0
    assert "a.md..b.md" in capsys.readouterr().out
    assert main(["--files", str(tmp_path / "a.md"), str(tmp_path / "nope")]) == 1
    assert "no such file" in capsys.readouterr().err


def test_cli_files_rejects_git_options(tmp_path):
    for extra in (["HEAD"], ["--cached"], ["--untracked"]):
        with pytest.raises(SystemExit):
            main(["--files", "a", "b", *extra])


def test_cli_move_similarity(tmp_path, capsys):
    (tmp_path / "a.md").write_text("x\n")
    (tmp_path / "b.md").write_text("y\n")
    args = [
        "--files",
        str(tmp_path / "a.md"),
        str(tmp_path / "b.md"),
        "-o",
        str(tmp_path / "r.html"),
    ]
    assert main([*args, "--move-similarity", "0.6"]) == 0
    for bad in ("0", "1.5"):
        with pytest.raises(SystemExit):
            main([*args, "--move-similarity", bad])
    assert "--move-similarity must be above 0" in capsys.readouterr().err


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("sidediff ")


def test_cli_not_a_repo_suggests_files(tmp_path, capsys):
    (tmp_path / "plain").mkdir()
    assert main([str(tmp_path / "plain"), "a"]) == 1
    assert "--files" in capsys.readouterr().err
