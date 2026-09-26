"""prosediff --setup-git, --to-markdown and --open: git's own commands
showing documents as prose, and the page opened in the browser."""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest
from helpers import docx, odt_xml

from prosediff.cli import main
from prosediff.gitsetup import ATTRIBUTES


@pytest.fixture
def no_global_git(tmp_path, monkeypatch):
    """git's global configuration and attributes kept in tmp_path, and the
    system's left out (Git for Windows gives .docx files a converter of its
    own there)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


@pytest.fixture
def two_documents(builder, no_global_git):
    for text in ("The cat sat.", "The cat slept."):
        builder.write(
            "paper.docx", docx(builder.path / "paper.docx", [[("run", text)]]).read_bytes()
        )
        builder.commit(text)
    return builder


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """A "browser" that records the address it was asked to open, in the
    file this returns (webbrowser takes BROWSER as a program, not a command
    line)."""
    opened = tmp_path / "opened.txt"
    if sys.platform == "win32":
        program = tmp_path / "browser.bat"
        program.write_text(f'@echo %~1> "{opened}"\n')
    else:
        program = tmp_path / "browser.sh"
        program.write_text(f'#!/bin/sh\necho "$1" > "{opened}"\n')
        program.chmod(0o755)
    monkeypatch.setenv("BROWSER", str(program))
    return opened


def git(repo, *args, **kw):
    """git's output; its output and errors in the message when it fails."""
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, encoding="utf-8", **kw)
    assert done.returncode == 0, (
        f"git {' '.join(args)}: {done.returncode}\n{done.stdout}{done.stderr}"
    )
    return done.stdout


def test_to_markdown(tmp_path, capsysbinary):
    d = docx(tmp_path / "a.docx", [[("run", "Kept "), ("del", "gone"), ("ins", "new")]])
    assert main(["--to-markdown", str(d)]) == 0
    assert capsysbinary.readouterr().out == b"Kept new\n"
    assert main(["--to-markdown", str(d), "--docx-changes", "reject"]) == 0
    assert capsysbinary.readouterr().out == b"Kept gone\n"
    # git may hand over a file without its extension: the content tells
    o = odt_xml(tmp_path / "b.odt", "<text:p>Città.</text:p>")
    o.rename(tmp_path / "b")
    assert main(["--to-markdown", str(tmp_path / "b")]) == 0
    assert capsysbinary.readouterr().out == "Città.\n".encode()


def test_to_markdown_errors(tmp_path, capsys):
    (tmp_path / "x.docx").write_bytes(b"not a zip")
    assert main(["--to-markdown", str(tmp_path / "x.docx")]) == 1
    assert "not a readable Word document" in capsys.readouterr().err
    assert main(["--to-markdown", str(tmp_path / "missing.docx")]) == 1


def test_setup_git_makes_git_diff_and_difftool_show_documents(two_documents, browser, capsys):
    """After --setup-git, git diff shows documents as text and git difftool -d
    writes a page and opens it."""
    b = two_documents
    assert "\n+The cat slept.\n" not in git(b.path, "diff", "HEAD~1", "HEAD")
    assert main(["--setup-git", str(b.path)]) == 0
    assert "git difftool -d -t prosediff" in capsys.readouterr().out
    diff = git(b.path, "diff", "HEAD~1", "HEAD")
    assert "\n-The cat sat.\n+The cat slept.\n" in diff
    attributes = (b.path / ".git" / "info" / "attributes").read_text().splitlines()
    assert attributes == list(ATTRIBUTES)
    # set twice, written once
    assert main(["--setup-git", str(b.path)]) == 0
    assert (b.path / ".git" / "info" / "attributes").read_text().splitlines() == attributes
    # --no-symlinks: where git init found symlinks to work (GitHub's Windows
    # runners), git difftool -d tries to link the right side to the working
    # tree, and on Windows fails ("could not symlink").
    difftool = ["difftool", "-d", "--no-symlinks", "-t", "prosediff", "--no-prompt"]
    git(
        b.path,
        *difftool,
        "HEAD~1",
        "HEAD",
    )
    page = browser.read_text().strip()
    assert page.startswith("file:") and page.endswith(".html")
    html = Path(url2pathname(urlparse(page).path)).read_text(encoding="utf-8")
    # the changed letters are marked: s<mark>lept</mark>
    assert "slept" in re.sub(r"<[^>]+>", "", html) and "converted from Word" in html


def test_setup_git_global(no_global_git, capsys):
    tmp = no_global_git
    assert main(["--setup-git", "--global"]) == 0
    config = (tmp / "gitconfig").read_text()
    assert "textconv" in config and "--to-markdown" in config and "$LOCAL" in config
    assert (tmp / "xdg" / "git" / "attributes").read_text().splitlines() == list(ATTRIBUTES)


def test_setup_git_outside_a_repository(tmp_path, no_global_git, capsys):
    (tmp_path / "plain").mkdir()
    assert main(["--setup-git", str(tmp_path / "plain")]) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_open_writes_a_fresh_page_and_opens_it(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.md").write_text("one\n")
    (tmp_path / "b.md").write_text("two\n")
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    files = ["--files", str(tmp_path / "a.md"), str(tmp_path / "b.md")]
    assert main([*files, "--open"]) == 0
    assert "a.md..b.md" in capsys.readouterr().out
    assert main([*files, "--open"]) == 0
    # two pages, both in the temporary folder, neither overwriting the other
    assert len(set(opened)) == 2 and all("/prosediff/prosediff_" in u for u in opened)
    out = tmp_path / "r.html"
    assert main([*files, "--open", "-o", str(out)]) == 0
    assert opened[-1] == out.resolve().as_uri()
