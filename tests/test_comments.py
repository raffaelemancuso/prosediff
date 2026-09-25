"""Comments: folding pandoc comment spans into markers, and the comments panel."""

from helpers import END, NOTE

from prosediff import compare, render
from prosediff.comments import NEW_COMMENT_MARK
from prosediff.diff import COMMENT_MARK, PLACEHOLDER, Comments, fold_comments, plain, show_comments

MARKER = 'data-author="Anna" data-date="2026-09-23 23:40" data-text="Too long."'


def test_fold_comments_replaces_span_with_one_placeholder():
    comments = Comments()
    folded = fold_comments(f"Text {NOTE}anchor{END} more.", comments)
    assert len(comments) == 1
    assert len(folded) == len("Text Xanchor more.")
    assert "comment" not in folded
    assert comments.get(folded[5]).author == "Anna"
    assert comments.get(folded[5]).text == "Too long."
    assert comments.get(folded[5]).date == "2026-09-23 23:40"


def test_same_comment_with_new_id_gets_same_placeholder():
    comments = Comments()
    a = fold_comments(NOTE, comments)
    b = fold_comments(NOTE.replace('id="3"', 'id="25"'), comments)
    assert a == b and len(comments) == 1


def test_nested_brackets_in_the_note():
    comments = Comments()
    folded = fold_comments('x [see [1] and [2]]{.comment-start id="1" author="A"} y', comments)
    assert folded[2] != "[" and comments.get(folded[2]).text == "see [1] and [2]"


def test_comments_without_text_are_left_out():
    comments = Comments()
    empty = '[]{.comment-start id="4" author="A" date="2026-09-23T10:00:00Z"}'
    blank = '[  ]{.comment-start id="5" author="B"}'
    folded = fold_comments(f"One{empty} two{blank} three{NOTE}.", comments)
    assert len(comments) == 1  # only the comment with text
    assert PLACEHOLDER.sub("", folded) == "One two three."
    # kept on request: a marker each, "(no text)" where the text would be
    comments = Comments()
    folded = fold_comments(f"One{empty} two{blank} three{NOTE}.", comments, keep_empty=True)
    assert len(comments) == 3  # the two empty ones (by A and by B) and the note
    html = str(show_comments(folded, comments))
    assert 'aria-label="comment by A, 2026-09-23 10:00: (no text)"' in html


def test_empty_comments_switch(builder, tmp_path, capsys):
    from prosediff.cli import main

    empty = '[]{.comment-start id="4" author="Anna" date="2026-09-23T10:00:00Z"}'
    builder.write("p.md", "Text.\n")
    base = builder.commit("first")
    builder.write("p.md", f"Text.{empty}\n")
    target = builder.commit("second")
    assert compare(builder.path, base, target).comments == []
    c = compare(builder.path, base, target, empty_comments=True)
    assert [(e.author, e.text, e.status) for e in c.comments] == [("Anna", "", "new")]
    assert ">(no text)</a>" in render(c)
    out = tmp_path / "r.html"
    assert main([str(builder.path), base, target, "--empty-comments", "-o", str(out)]) == 0
    assert ">(no text)</a>" in out.read_text(encoding="utf-8")


def test_other_spans_and_links_untouched():
    text = "[link](http://x) and [word]{.smallcaps} and \\[not\\]{.comment-start}"
    assert fold_comments(text, Comments()) == text


def test_plain_and_show_comments():
    comments = Comments()
    folded = fold_comments(f"a {NOTE}b", comments)
    assert plain(folded) == f"a {COMMENT_MARK}b"
    html = str(show_comments(folded, comments))
    assert f'<span class="comment" tabindex="0" role="note" {MARKER}' in html
    assert 'aria-label="comment by Anna, 2026-09-23 23:40: Too long."' in html
    assert f">{COMMENT_MARK}</span>" in html
    # a comment added since the base has its own icon
    new = str(show_comments(folded, comments, frozenset(PLACEHOLDER.findall(folded))))
    assert 'class="comment new"' in new and f">{NEW_COMMENT_MARK}</span>" in new
    assert 'aria-label="new comment by Anna' in new


def test_a_comment_on_both_sides_is_not_shown(builder):
    """A comment whose paragraph was deleted lands on the next one: a
    comment both sides have is left out, moved or not."""
    builder.write("p.md", f"First paragraph here.{NOTE}\n\nSecond paragraph here.\n")
    base = builder.commit("first")
    builder.write("p.md", f"{NOTE}Second paragraph here, edited.\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    (f,) = c.files
    row = next(r for r in f.rows if r.right_no is not None and "Second" in str(r.right))
    assert row.changes == ['added ", edited"']
    assert "comment" not in str(row.right)
    assert not any("comment" in str(r.left) for r in f.rows)
    assert c.comments == []


def test_a_shared_comment_leaves_one_space():
    from prosediff.diff import without_shared_comments

    comments = Comments()
    old = [fold_comments(s, comments) for s in [f"a {NOTE}b", f"c {NOTE}.", f"{NOTE} d", NOTE]]
    new = [fold_comments(f"x{NOTE}", comments)]
    lines, _, labels, _ = without_shared_comments(old, new, ["1", "2", "3", "4"], ["1"])
    assert lines == ["a b", "c.", "d"] and labels == ["1", "2", "3"]


def test_a_paragraph_a_comment_only_moved_into_is_not_shown(builder):
    """The comment was already there, on the paragraph before: its moving
    to the next paragraph is no reason to show either of them."""
    lines = [f"Paragraph {k} of the text." for k in range(40)]
    old = list(lines)
    old[19] += NOTE
    builder.write("p.md", "\n\n".join([*old, "The end."]) + "\n")
    base = builder.commit("first")
    new = list(lines)
    new[20] = NOTE + new[20]
    builder.write("p.md", "\n\n".join([*new, "The end, edited."]) + "\n")
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    shown = [r for r in f.rows if r.kind != "skip"]
    assert [r.kind for r in shown if r.changed] == ["replace"]
    assert not any("Paragraph 20" in str(r.right) for r in shown)


def test_a_paragraph_with_only_a_new_comment_is_shown(builder):
    """Its text is unchanged, so it is no edit, but it is not folded away
    with the unchanged lines: the new comment shows, marked new, and the
    navigation stops there."""
    lines = [f"Paragraph {k} of the text." for k in range(40)]
    builder.write("p.md", "\n\n".join(lines) + "\n")
    base = builder.commit("first")
    lines[20] += NOTE
    builder.write("p.md", "\n\n".join(lines) + "\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target)
    (f,) = c.files
    shown = [r for r in f.rows if r.kind != "skip"]
    row = next(r for r in shown if "Paragraph 20" in str(r.right))
    assert row.kind == "equal" and row.changes == []  # no edit of the text
    assert 'class="comment new"' in str(row.right)
    assert (f.additions, f.deletions) == (0, 0)
    assert [e.status for e in c.comments] == ["new"]
    assert row.first_of_change and f.change_count == 1


def test_compare_fold_comments(builder, tmp_path):
    builder.write("p.md", f"Some text{NOTE} here.{END}\n")
    builder.write("p.txt", f"x {NOTE}\n")
    base = builder.commit("first")
    renumbered = NOTE.replace('id="3"', 'id="9"')
    builder.write("p.md", f"Some new text{renumbered} here.{END}\n")
    builder.write("p.txt", f"y {NOTE}\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target)  # folding is the default
    md, txt = c.files
    (row,) = [r for r in md.rows if r.kind == "replace"]
    assert row.changes == ['added "new"']  # the renumbered comment is no change
    assert "comment-start" not in str(row.right)
    assert MARKER not in render(c)  # the comment was already there: not shown
    assert "comment-start" in str(txt.rows[0].right)  # only Markdown is folded
    assert not PLACEHOLDER.search(render(c))
    # without folding the markup is compared as text, and there is no panel
    c = compare(builder.path, base, target, fold_comments_md=False)
    html = render(c)
    assert "comment-start" in html
    assert c.comments == [] and '<section class="comments-panel"' not in html


def test_comments_panel_statuses_and_links(builder):
    kept = NOTE
    gone = NOTE.replace("Too long.", "Old remark.")
    new = NOTE.replace("Too long.", "New remark.")
    lines = [f"Line {i}." for i in range(30)]
    old = list(lines)
    old[2] += kept
    old[20] += gone
    builder.write("p.md", "\n".join(old) + "\n")
    base = builder.commit("first")
    cur = list(lines)
    cur[2] += kept
    cur[21] = "Line 21 edited." + new
    builder.write("p.md", "\n".join(cur) + "\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target, fold_comments_md=True)
    status = {e.text: e.status for e in c.comments}
    # the comment both sides have is left out
    assert status == {"Old remark.": "removed", "New remark.": "new"}
    by_text = {e.text: e for e in c.comments}
    assert by_text["New remark."].line == 22
    assert by_text["Old remark."].line == 21
    html = render(c)
    assert "Comments: 1 new, 1 removed</h2>" in html and "Too long." not in html
    for e in c.comments:
        assert f'id="{e.anchor}"' in html
    # the new comment has its own icon, in the text and in the panel
    assert html.count('class="comment new"') == 1
    assert by_text["New remark."].icon == NEW_COMMENT_MARK
    assert by_text["Old remark."].icon == COMMENT_MARK
    assert not PLACEHOLDER.search(html)  # every comment became a marker
