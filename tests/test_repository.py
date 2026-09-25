"""Comparing a git repository: commits, the index, the working tree, renames,
binary files and images, commits in between.
"""

import base64
import sys

from helpers import docx

from sidediff import compare, render
from sidediff.diff import MAX_IMAGE_BYTES, image_uri, is_binary

PNG_1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


PNG_2 = PNG_1 + b"\x00trailer"


def test_is_binary():
    assert is_binary(b"abc\0def")
    assert not is_binary("città".encode())


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


def test_compare_binary(builder):
    builder.write("img.bin", b"\x89PNG\0\0\x01")
    base = builder.commit("first")
    builder.write("img.bin", b"\x89PNG\0\0\x02")
    target = builder.commit("second")

    (f,) = compare(builder.path, base, target).files
    assert f.binary
    assert f.rows == []


def test_compare_path_filter(builder):
    builder.write("a/one.txt", "1\n")
    builder.write("b/two.txt", "2\n")
    base = builder.commit("first")
    builder.write("a/one.txt", "1!\n")
    builder.write("b/two.txt", "2!\n")
    target = builder.commit("second")

    c = compare(builder.path, base, target, paths=["a"])
    assert [f.path for f in c.files] == ["a/one.txt"]


def test_compare_no_differences(builder):
    builder.write("f.txt", "x\n")
    sha = builder.commit("only")
    assert compare(builder.path, sha, sha).files == []


def test_compare_accepts_refs_and_subfolder(builder):
    builder.write("sub/f.txt", "x\n")
    builder.commit("first")
    builder.write("sub/f.txt", "y\n")
    builder.commit("second")
    c = compare(builder.path / "sub", "HEAD~1", "HEAD")
    assert [f.path for f in c.files] == ["sub/f.txt"]


def test_compare_non_utf8_does_not_crash(builder):
    builder.write("latin1.txt", "caf\xe9\n".encode("latin-1"))
    base = builder.commit("first")
    builder.write("latin1.txt", "caff\xe8\n".encode("latin-1"))
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    assert f.rows


def test_compare_crlf_lines(builder):
    builder.write("w.txt", "a\r\nb\r\n")
    base = builder.commit("first")
    builder.write("w.txt", "a\r\nc\r\n")
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    assert (f.additions, f.deletions) == (1, 1)
    assert "\r" not in "".join(r.left + r.right for r in f.rows)


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


def test_compare_worktree_clean(builder):
    builder.write("f.txt", "x\n")
    builder.commit("only")
    assert compare(builder.path, "HEAD").files == []


def test_compare_md_filter_only_on_markdown(builder):
    builder.write("a.md", "one two\n")
    builder.write("a.txt", "one two\n")
    base = builder.commit("first")
    builder.write("a.md", "one three\n")
    builder.write("a.txt", "one three\n")
    target = builder.commit("second")
    upper = f'"{sys.executable}" -c "import sys; sys.stdout.write(sys.stdin.read().upper())"'

    c = compare(builder.path, base, target, md_filter=upper)
    by_path = {f.path: f for f in c.files}
    assert "ONE" in by_path["a.md"].rows[0].left
    assert "one" in by_path["a.txt"].rows[0].left


def test_compare_md_filter_can_remove_differences(builder):
    # a filter that re-flows the text makes a pure re-wrap disappear
    builder.write("p.md", "one two three\n")
    base = builder.commit("first")
    builder.write("p.md", "one two\nthree\n")
    target = builder.commit("rewrap")
    join = (
        f'"{sys.executable}" -c "import sys; '
        "sys.stdout.write(' '.join(sys.stdin.read().split()) + chr(10))\""
    )
    (f,) = compare(builder.path, base, target, md_filter=join).files
    assert f.rows == []


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


def test_compare_untracked(builder):
    builder.write("f.txt", "one\n")
    (builder.path / ".gitignore").write_text("*.log\n")
    builder.repo.index.add([".gitignore"])
    builder.commit("first")
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


def test_changed_image_is_shown_side_by_side(builder):
    builder.write("pic.png", PNG_1)
    base = builder.commit("first")
    builder.write("pic.png", PNG_2)
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    (f,) = c.files
    assert f.binary and f.old_image and f.new_image
    html = render(c)
    assert html.count('<img src="data:image/png;base64,') == 2
    assert "Binary file, not shown" not in html


def test_added_image_has_one_side(builder):
    builder.write("f.txt", "x\n")
    base = builder.commit("first")
    builder.write("pic.png", PNG_1)
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    assert f.old_image is None and f.new_image


def test_commits_in_between(builder):
    shas = []
    for k in range(4):
        builder.write("f.txt", f"{k}\n")
        shas.append(builder.commit(f"commit {k}"))
    c = compare(builder.path, shas[0], shas[3])
    assert [r.subject for r in c.commits] == ["commit 3", "commit 2", "commit 1"]
    assert c.commits_total == 3
    html = render(c)
    assert "3 commits in between" in html


def test_commits_in_between_worktree_up_to_head(builder):
    builder.write("f.txt", "0\n")
    base = builder.commit("first")
    builder.write("f.txt", "1\n")
    builder.commit("second")
    c = compare(builder.path, base)
    assert [r.subject for r in c.commits] == ["second"]
    assert "up to HEAD" in render(c)


def test_commits_list_is_capped(builder, monkeypatch):
    import sidediff.diff as d

    monkeypatch.setattr(d, "MAX_LISTED_COMMITS", 2)
    shas = []
    for k in range(5):
        builder.write("f.txt", f"{k}\n")
        shas.append(builder.commit(f"commit {k}"))
    c = compare(builder.path, shas[0], shas[4])
    assert len(c.commits) == 2 and c.commits_total == 4
    assert "and 2 older" in render(c)


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
