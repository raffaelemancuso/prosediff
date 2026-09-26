"""Comparing a git repository: commits, the index, the working tree, renames,
binary files and images, commits in between.
"""

import base64
import sys

from helpers import docx

from prosediff import compare, render
from prosediff.diff import MAX_IMAGE_BYTES, image_uri

PNG_1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


PNG_2 = PNG_1 + b"\x00trailer"


def test_compare_modified_added_deleted(builder):
    builder.write("keep.txt", "a\nb\n")
    builder.write("gone.txt", "bye\n")
    base = builder.commit("first")
    builder.write("keep.txt", "a\nc\n")
    builder.write("new.txt", "hello\n")
    builder.remove("gone.txt")
    target = builder.commit("second")

    c = compare(builder.path, base, target)
    by_path = {f.path: f for f in c.files}
    assert by_path["keep.txt"].change == "modified"
    assert by_path["new.txt"].change == "added"
    assert by_path["new.txt"].old_path is None
    assert by_path["gone.txt"].change == "deleted"
    assert by_path["gone.txt"].new_path is None
    assert (c.additions, c.deletions) == (2, 2)
    assert [f.path for f in c.files] == sorted(by_path)
    assert c.base.subject == "first"
    assert c.target.subject == "second"
    assert c.base.short == base[:7]


def test_compare_detects_rename(builder):
    body = "".join(f"line {i}\n" for i in range(20))
    builder.write("old_name.txt", body)
    base = builder.commit("first")
    builder.move("old_name.txt", "new_name.txt")
    target = builder.commit("rename")

    (f,) = compare(builder.path, base, target).files
    assert f.change == "renamed"
    assert (f.old_path, f.new_path) == ("old_name.txt", "new_name.txt")
    assert f.rows == []


def test_compare_path_filter_refs_and_subfolder(builder):
    """paths restricts the comparison; refs name commits; a subfolder finds
    its repository and compares all of it."""
    builder.write("a/one.txt", "1\n")
    builder.write("b/two.txt", "2\n")
    base = builder.commit("first")
    builder.write("a/one.txt", "1!\n")
    builder.write("b/two.txt", "2!\n")
    target = builder.commit("second")

    c = compare(builder.path, base, target, paths=["a"])
    assert [f.path for f in c.files] == ["a/one.txt"]
    c = compare(builder.path / "a", "HEAD~1", "HEAD")
    assert [f.path for f in c.files] == ["a/one.txt", "b/two.txt"]


def test_compare_non_utf8_and_crlf(builder):
    builder.write("latin1.txt", "caf\xe9\n".encode("latin-1"))
    builder.write("w.txt", "a\r\nb\r\n")
    base = builder.commit("first")
    builder.write("latin1.txt", "caff\xe8\n".encode("latin-1"))
    builder.write("w.txt", "a\r\nc\r\n")
    target = builder.commit("second")
    latin1, crlf = compare(builder.path, base, target).files
    # not UTF-8: read in the likeliest encoding, and said so
    assert "caf" in latin1.rows[0].left and "\xe9" in latin1.rows[0].left
    assert "\xe8" in latin1.rows[0].right
    assert latin1.note == "read as cp1252"
    assert (crlf.additions, crlf.deletions) == (1, 1)
    assert "\r" not in "".join(r.left + r.right for r in crlf.rows)


def test_compare_worktree(builder):
    builder.write("mod.txt", "old\n")
    builder.write("del.txt", "bye\n")
    builder.write("same.txt", "same\n")
    builder.commit("first")
    (builder.path / "mod.txt").write_text("new\n")
    (builder.path / "del.txt").unlink()
    (builder.path / "untracked.txt").write_text("ignored\n")

    c = compare(builder.path, "HEAD")
    by_path = {f.path: f for f in c.files}
    assert set(by_path) == {"mod.txt", "del.txt"}
    assert by_path["mod.txt"].change == "modified"
    assert "new" in by_path["mod.txt"].rows[0].right
    assert by_path["del.txt"].change == "deleted"
    assert c.target.short == "working tree"


def test_compare_md_filter_only_on_markdown(builder):
    # a filter that upper-cases and re-flows the text: a pure re-wrap of a
    # Markdown file disappears, a text file is left alone
    builder.write("a.md", "one two\n")
    builder.write("p.md", "one two three\n")
    builder.write("a.txt", "one two\n")
    base = builder.commit("first")
    builder.write("a.md", "one three\n")
    builder.write("p.md", "one two\nthree\n")
    builder.write("a.txt", "one three\n")
    target = builder.commit("second")
    flow = (
        f'"{sys.executable}" -c "import sys; '
        "sys.stdout.write(' '.join(sys.stdin.read().upper().split()) + chr(10))\""
    )
    c = compare(builder.path, base, target, md_filter=flow)
    by_path = {f.path: f for f in c.files}
    assert "ONE" in by_path["a.md"].rows[0].left
    assert "one" in by_path["a.txt"].rows[0].left
    assert by_path["p.md"].rows == []  # "Content unchanged"


def test_compare_cached(builder):
    builder.write("f.txt", "one\n")
    builder.commit("first")
    builder.write("f.txt", "staged\n")  # write() also stages
    (builder.path / "f.txt").write_text("on disk only\n")
    c = compare(builder.path, "HEAD", cached=True)
    (f,) = c.files
    assert "staged" in f.rows[0].right
    assert c.target.short == "index"
    c = compare(builder.path, "HEAD")
    assert "disk" in c.files[0].rows[0].right


def test_compare_untracked_and_no_differences(builder):
    builder.write("f.txt", "one\n")
    (builder.path / ".gitignore").write_text("*.log\n")
    builder.repo.index.add([".gitignore"])
    sha = builder.commit("first")
    c = compare(builder.path, sha, sha)
    assert c.files == []
    html = render(c)
    assert "No differences between the two sides." in html and "<table" not in html
    (builder.path / "new.txt").write_text("hello\n")
    (builder.path / "debug.log").write_text("ignored\n")
    (builder.path / "sub").mkdir()
    (builder.path / "sub" / "x.txt").write_text("x\n")

    assert compare(builder.path, "HEAD").files == []
    c = compare(builder.path, "HEAD", untracked=True)
    by_path = {f.path: f for f in c.files}
    assert set(by_path) == {"new.txt", "sub/x.txt"}
    assert by_path["new.txt"].change == "untracked"
    assert by_path["new.txt"].additions == 1
    c = compare(builder.path, "HEAD", paths=["sub"], untracked=True)
    assert [f.path for f in c.files] == ["sub/x.txt"]


def test_compare_ignore_whitespace(builder):
    builder.write("f.py", "def f():\n  return 1\n")
    base = builder.commit("first")
    builder.write("f.py", "def f():\n    return 1\n")
    target = builder.commit("reindent")
    (f,) = compare(builder.path, base, target).files
    assert (f.additions, f.deletions) == (1, 1)
    (f,) = compare(builder.path, base, target, ignore_whitespace=True).files
    assert f.rows == []


def test_image_uri():
    assert image_uri("a.png", PNG_1).startswith("data:image/png;base64,")
    assert image_uri("a.bin", PNG_1) is None
    assert image_uri("a.png", b"x" * (MAX_IMAGE_BYTES + 1)) is None


def test_images_side_by_side_other_binaries_not_shown(builder):
    builder.write("pic.png", PNG_1)
    builder.write("img.bin", b"\x89PNG\0\0\x01")
    base = builder.commit("first")
    builder.write("pic.png", PNG_2)
    builder.write("added.png", PNG_1)
    builder.write("img.bin", b"\x89PNG\0\0\x02")
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    added, other, changed = c.files
    assert added.old_image is None and added.new_image
    assert changed.binary and changed.old_image and changed.new_image
    assert other.binary and other.rows == [] and other.old_image is None
    html = render(c)
    assert html.count('<img src="data:image/png;base64,') == 3
    assert html.count("Binary file, not shown") == 1


def test_commits_in_between(builder, monkeypatch):
    import prosediff.diff as d

    shas = []
    for k in range(5):
        builder.write("f.txt", f"{k}\n")
        shas.append(builder.commit(f"commit {k}"))
    c = compare(builder.path, shas[0], shas[3])
    assert [r.subject for r in c.commits] == ["commit 3", "commit 2", "commit 1"]
    assert c.commits_total == 3
    assert "3 commits in between" in render(c)
    # a long list is capped
    monkeypatch.setattr(d, "MAX_LISTED_COMMITS", 2)
    c = compare(builder.path, shas[0], shas[4])
    assert len(c.commits) == 2 and c.commits_total == 4
    assert "and 2 older" in render(c)
    # against the working tree: the commits up to HEAD
    c = compare(builder.path, shas[3])
    assert [r.subject for r in c.commits] == ["commit 4"]
    assert "up to HEAD" in render(c)


def test_word_counts_per_file_and_total(builder):
    builder.write("f.txt", "one two three\ngone line here\n")
    base = builder.commit("first")
    builder.write("f.txt", "one 2 three four\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    (f,) = c.files
    # "two" -> "2 ... four": 1 removed, 2 added; the removed line has 3 words
    assert (f.words_added, f.words_removed) == (2, 4)
    assert (c.words_added, c.words_removed) == (2, 4)
    html = render(c)
    assert 'title="words added">+2<' in html and 'title="words removed">−4<' in html


def test_docx_in_git(builder):
    docx(builder.path / "d.docx", [[("run", "Old text.")]])
    builder.repo.index.add(["d.docx"])
    base = builder.commit("first")
    docx(builder.path / "d.docx", [[("run", "New text.")]])
    builder.repo.index.add(["d.docx"])
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    assert not f.binary and f.markdown
    assert f.rows[0].changes == ['changed "Old" to "New"']
