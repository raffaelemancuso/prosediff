"""Comparing a git repository: commits, the index, the working tree, renames,
binary files and images, commits in between.
"""

import base64
import sys

import pytest
from conftest import RepoBuilder
from helpers import docx

from prosediff import compare, render
from prosediff.diff import MAX_IMAGE_BYTES, image_uri

PNG_1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


PNG_2 = PNG_1 + b"\x00trailer"


@pytest.fixture(scope="module")
def versions(tmp_path_factory):
    """Two commits changing files of every kind, and their comparison; built
    once, as no test changes them."""
    b = RepoBuilder(tmp_path_factory.mktemp("versions") / "repo")
    b.write_all(
        {
            "keep.txt": "a\nb\n",
            "gone.txt": "bye\n",
            "old_name.txt": "".join(f"line {i}\n" for i in range(20)),
            "a/one.txt": "1\n",
            "b/two.txt": "2\n",
            "latin1.txt": "caf\xe9\n".encode("latin-1"),
            "w.txt": "a\r\nb\r\n",
            # for the Markdown filter
            "a.md": "one two\n",
            "p.md": "one two three\n",
            "a.txt": "one two\n",
            "f.py": "def f():\n  return 1\n",
            "words.txt": "one two three\ngone line here\n",
            "pic.png": PNG_1,
            "img.bin": b"\x89PNG\0\0\x01",
            "d.docx": docx(b.path / "d.docx", [[("run", "Old text.")]]).read_bytes(),
        }
    )
    base = b.commit("first")
    b.remove("gone.txt")
    b.move("old_name.txt", "new_name.txt")
    b.write_all(
        {
            "keep.txt": "a\nc\n",
            "new.txt": "hello\n",
            "a/one.txt": "1!\n",
            "b/two.txt": "2!\n",
            "latin1.txt": "caff\xe8\n".encode("latin-1"),
            "w.txt": "a\r\nc\r\n",
            "a.md": "one three\n",
            "p.md": "one two\nthree\n",
            "a.txt": "one three\n",
            "f.py": "def f():\n    return 1\n",
            "words.txt": "one 2 three four\n",
            "pic.png": PNG_2,
            "added.png": PNG_1,
            "img.bin": b"\x89PNG\0\0\x02",
            "d.docx": docx(b.path / "d.docx", [[("run", "New text.")]]).read_bytes(),
        }
    )
    target = b.commit("second")
    c = compare(b.path, base, target)
    yield b, base, target, c, {f.path: f for f in c.files}
    b.repo.close()


def test_compare_modified_added_deleted(versions):
    """Each file's change and counts, the totals their sums, the files in
    order, and the commits named."""
    _, base, _, c, by_path = versions
    assert by_path["keep.txt"].change == "modified"
    assert by_path["new.txt"].change == "added"
    assert by_path["new.txt"].old_path is None
    assert by_path["gone.txt"].change == "deleted"
    assert by_path["gone.txt"].new_path is None
    counts = {p: (by_path[p].additions, by_path[p].deletions) for p in ("keep.txt", "new.txt")}
    assert counts == {"keep.txt": (1, 1), "new.txt": (1, 0)}
    assert (by_path["gone.txt"].additions, by_path["gone.txt"].deletions) == (0, 1)
    assert c.additions == sum(f.additions for f in c.files)
    assert c.deletions == sum(f.deletions for f in c.files)
    assert [f.path for f in c.files] == sorted(by_path)
    assert c.base.subject == "first"
    assert c.target.subject == "second"
    assert c.base.short == base[:7]


def test_compare_detects_rename(versions):
    f = versions[4]["new_name.txt"]
    assert f.change == "renamed"
    assert (f.old_path, f.new_path) == ("old_name.txt", "new_name.txt")
    assert f.rows == []
    assert "old_name.txt" not in versions[4]


def test_compare_path_filter_refs_and_subfolder(versions):
    """paths restricts the comparison; refs name commits; a subfolder finds
    its repository and compares all of it."""
    b, base, target, _, by_path = versions
    c = compare(b.path, base, target, paths=["a"])
    assert [f.path for f in c.files] == ["a/one.txt"]
    c = compare(b.path / "a", "HEAD~1", "HEAD")
    assert [f.path for f in c.files] == sorted(by_path)


def test_compare_non_utf8_and_crlf(versions):
    latin1, crlf = versions[4]["latin1.txt"], versions[4]["w.txt"]
    # not UTF-8: read in the likeliest encoding, and said so
    assert latin1.rows[0].changes == ['changed "café" to "caffè"']
    assert latin1.note == "read as cp1252"
    assert (crlf.additions, crlf.deletions) == (1, 1)
    assert "\r" not in "".join(r.left + r.right for r in crlf.rows)


def test_compare_md_filter_only_on_markdown(versions):
    # a filter that upper-cases and re-flows the text: a pure re-wrap of a
    # Markdown file disappears, a text file is left alone
    b, base, target, _, _ = versions
    flow = (
        f'"{sys.executable}" -c "import sys; '
        "sys.stdout.write(' '.join(sys.stdin.read().upper().split()) + chr(10))\""
    )
    c = compare(b.path, base, target, md_filter=flow, paths=["a.md", "p.md", "a.txt"])
    by_path = {f.path: f for f in c.files}
    assert "ONE" in by_path["a.md"].rows[0].left
    assert "one" in by_path["a.txt"].rows[0].left
    assert by_path["p.md"].rows == []  # "Content unchanged"


def test_compare_ignore_whitespace(versions):
    b, base, target, _, by_path = versions
    f = by_path["f.py"]
    assert (f.additions, f.deletions) == (1, 1)
    (f,) = compare(b.path, base, target, paths=["f.py"], ignore_whitespace=True).files
    assert f.rows == []


def test_image_uri():
    assert image_uri("a.png", PNG_1).startswith("data:image/png;base64,")
    assert image_uri("a.bin", PNG_1) is None
    assert image_uri("a.png", b"x" * (MAX_IMAGE_BYTES + 1)) is None


def test_images_side_by_side_other_binaries_not_shown(versions):
    _, _, _, c, by_path = versions
    added, other, changed = by_path["added.png"], by_path["img.bin"], by_path["pic.png"]
    assert added.old_image is None and added.new_image
    assert changed.binary and changed.old_image and changed.new_image
    assert other.binary and other.rows == [] and other.old_image is None
    html = render(c)
    assert html.count('<img src="data:image/png;base64,') == 3
    assert html.count("Binary file, not shown") == 1


def test_word_counts_per_file_and_total(versions):
    _, _, _, c, by_path = versions
    f = by_path["words.txt"]
    # "two" -> "2 ... four": 1 removed, 2 added; the removed line has 3 words
    assert (f.words_added, f.words_removed) == (2, 4)
    assert c.words_added == sum(f.words_added for f in c.files)
    assert c.words_removed == sum(f.words_removed for f in c.files)
    html = render(c)
    assert 'title="words added">+2<' in html and 'title="words removed">−4<' in html
    total = f'title="words added">+{c.words_added:,}<'
    assert total in html


def test_docx_in_git(versions):
    f = versions[4]["d.docx"]
    assert not f.binary and f.markdown
    assert f.rows[0].changes == ['changed "Old" to "New"']


@pytest.fixture(scope="module")
def dirty(tmp_path_factory):
    """A commit, then a change staged and changed again on disk, a file
    changed and one deleted on disk only, and untracked files, one of them
    ignored; built once, as no test changes it."""
    b = RepoBuilder(tmp_path_factory.mktemp("dirty") / "repo")
    b.write_all(
        {
            "mod.txt": "old\n",
            "del.txt": "bye\n",
            "same.txt": "same\n",
            "f.txt": "one\n",
            ".gitignore": "*.log\n",
        }
    )
    sha = b.commit("first")
    b.write("f.txt", "staged\n")  # write() also stages
    (b.path / "f.txt").write_text("on disk only\n")
    (b.path / "mod.txt").write_text("new\n")
    (b.path / "del.txt").unlink()
    (b.path / "new.txt").write_text("hello\n")
    (b.path / "debug.log").write_text("ignored\n")
    (b.path / "sub").mkdir()
    (b.path / "sub" / "x.txt").write_text("x\n")
    yield b, sha
    b.repo.close()


def test_compare_worktree(dirty):
    """The working tree against a commit: the changes on disk, untracked
    files left out."""
    b, _ = dirty
    c = compare(b.path, "HEAD")
    by_path = {f.path: f for f in c.files}
    assert set(by_path) == {"mod.txt", "del.txt", "f.txt"}
    assert by_path["mod.txt"].change == "modified"
    assert "new" in by_path["mod.txt"].rows[0].right
    assert "disk" in by_path["f.txt"].rows[0].right
    assert by_path["del.txt"].change == "deleted"
    assert c.target.short == "working tree"


def test_compare_cached(dirty):
    """The index against a commit: what was staged, not what is on disk."""
    b, _ = dirty
    c = compare(b.path, "HEAD", cached=True)
    (f,) = c.files
    assert "staged" in f.rows[0].right
    assert c.target.short == "index"


def test_compare_untracked_and_no_differences(dirty):
    """Untracked files join the working tree on request, the ignored ones
    left out, paths restricting them too; a commit against itself has no
    differences, and the HTML report says so."""
    b, sha = dirty
    c = compare(b.path, sha, sha)
    assert c.files == []
    html = render(c)
    assert "No differences between the two sides." in html and "<table" not in html
    c = compare(b.path, "HEAD", untracked=True)
    by_path = {f.path: f for f in c.files}
    assert set(by_path) == {"mod.txt", "del.txt", "f.txt", "new.txt", "sub/x.txt"}
    assert by_path["new.txt"].change == "untracked"
    assert by_path["new.txt"].additions == 1
    c = compare(b.path, "HEAD", paths=["sub"], untracked=True)
    assert [f.path for f in c.files] == ["sub/x.txt"]


def test_commits_in_between(history, monkeypatch):
    import prosediff.diff as d

    b, shas = history
    c = compare(b.path, shas[0], shas[3])
    assert [r.subject for r in c.commits] == ["commit 3", "commit 2", "commit 1"]
    assert c.commits_total == 3
    assert "3 commits in between" in render(c)
    # a long list is capped
    monkeypatch.setattr(d, "MAX_LISTED_COMMITS", 2)
    c = compare(b.path, shas[0], shas[4])
    assert len(c.commits) == 2 and c.commits_total == 4
    assert "and 2 older" in render(c)
    # against the working tree: the commits up to HEAD
    c = compare(b.path, shas[3])
    assert [r.subject for r in c.commits] == ["commit 4"]
    assert "up to HEAD" in render(c)
