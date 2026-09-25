"""Comparing outside git (--files): two files, two folders, Word documents."""

import pytest
from helpers import docx, docx_xml

from sidediff import compare_paths, render
from sidediff.sources import SourceError, docx_to_markdown


def test_two_files_with_different_names(tmp_path):
    (tmp_path / "v1.md").write_text("Hello world.\n")
    (tmp_path / "v2.md").write_text("Hello there.\n")
    c = compare_paths(tmp_path / "v1.md", tmp_path / "v2.md")
    (f,) = c.files
    assert f.change == "renamed" and (f.old_path, f.new_path) == ("v1.md", "v2.md")
    assert f.additions == 1 and ">there</ins>" in str(f.rows[0].right)
    assert c.base.short == "v1.md" and c.target.short == "v2.md"
    assert c.base.subject == "file" and c.base.date


def test_identical_files(tmp_path):
    (tmp_path / "a.txt").write_text("same\n")
    (tmp_path / "b.txt").write_text("same\n")
    assert compare_paths(tmp_path / "a.txt", tmp_path / "b.txt").files == []


def test_two_folders(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for d in (old, new):
        (d / "sub").mkdir(parents=True)
        (d / ".git").mkdir()
        (d / ".git" / "HEAD").write_text("ignored")
    (old / "same.txt").write_text("x\n")
    (new / "same.txt").write_text("x\n")
    (old / "mod.txt").write_text("a\n")
    (new / "mod.txt").write_text("b\n")
    (old / "gone.txt").write_text("bye\n")
    (new / "added.txt").write_text("hi\n")
    (old / "sub" / "moved.txt").write_text("content\n")
    (new / "moved_here.txt").write_text("content\n")
    c = compare_paths(old, new)
    by = {f.path: f.change for f in c.files}
    assert by == {
        "mod.txt": "modified",
        "gone.txt": "deleted",
        "added.txt": "added",
        "moved_here.txt": "renamed",
    }
    assert c.base.subject == "folder"
    c = compare_paths(old, new, paths=["sub"])
    assert [f.change for f in c.files] == ["deleted"]


def test_file_against_folder_is_an_error(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    (tmp_path / "d").mkdir()
    with pytest.raises(SourceError, match="a file with a file"):
        compare_paths(tmp_path / "f.txt", tmp_path / "d")
    with pytest.raises(SourceError, match="no such file"):
        compare_paths(tmp_path / "nope", tmp_path / "d")


def test_docx_changes_accepted_rejected_or_kept(tmp_path):
    d = docx(
        tmp_path / "a.docx",
        [[("run", "start-up"), ("del", " entry"), ("ins", "s"), ("run", " grow.")]],
    )
    data = d.read_bytes()
    assert docx_to_markdown(data, "a.docx", "accept").strip() == b"start-ups grow."
    assert docx_to_markdown(data, "a.docx", "reject").strip() == b"start-up entry grow."
    assert b"{.deletion" in docx_to_markdown(data, "a.docx", "all")


def test_comment_on_deleted_text_is_kept(tmp_path):
    """Word writes the whole comment inside the deletion when the commented
    text is deleted: accepting drops the text, not the comment."""
    d = docx_xml(
        tmp_path / "a.docx",
        "<w:p><w:r><w:t>Keep</w:t></w:r>"
        '<w:del w:id="1" w:author="A"><w:commentRangeStart w:id="0"/>'
        '<w:r><w:delText>gone</w:delText></w:r><w:commentRangeEnd w:id="0"/>'
        '<w:r><w:commentReference w:id="0"/></w:r></w:del>'
        '<w:ins w:id="2" w:author="A"><w:r><w:t xml:space="preserve"> new</w:t></w:r></w:ins>'
        "</w:p>",
        comments='<w:comment w:id="0" w:author="Anna" w:date="2026-01-01T00:00:00Z">'
        "<w:p><w:r><w:t>Why?</w:t></w:r></w:p></w:comment>",
    )
    accepted = docx_to_markdown(d.read_bytes(), "a.docx", "accept").decode()
    assert "gone" not in accepted and "Keep" in accepted and " new" in accepted
    assert '[Why?]{.comment-start id="0" author="Anna"' in accepted
    rejected = docx_to_markdown(d.read_bytes(), "a.docx", "reject").decode()
    assert "gone" in rejected and "new" not in rejected and "Why?" in rejected


def test_compare_two_docx_with_comments_panel(tmp_path):
    a = docx(tmp_path / "v1.docx", [[("run", "The first draft.")], [("run", "Unchanged.")]])
    b = docx(
        tmp_path / "v2.docx",
        [[("run", "The second draft.")], [("run", "Unchanged.")]],
        comment="Why second?",
    )
    c = compare_paths(a, b, fold_comments_md=True)
    (f,) = c.files
    assert f.markdown and "converted from Word" in f.note
    assert [(e.status, e.author, e.text) for e in c.comments] == [("new", "Anna", "Why second?")]
    html = render(c)
    assert "Comments: 1 new, 0 removed, 0 unchanged" in html
    assert f'href="#{c.comments[0].anchor}"' in html and f'id="{c.comments[0].anchor}"' in html


def test_broken_docx_is_listed_as_binary(tmp_path):
    a, b = tmp_path / "a.docx", tmp_path / "b.docx"
    a.write_bytes(b"not a zip")
    b.write_bytes(b"not a zip either")
    (f,) = compare_paths(a, b).files
    assert f.binary and "not a readable Word document" in f.note
