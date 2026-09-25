"""Comments: folding pandoc comment spans into markers, and the comments panel."""

from helpers import END, NOTE

from sidediff import compare, render
from sidediff.comments import NEW_COMMENT_MARK
from sidediff.diff import COMMENT_MARK, PLACEHOLDER, Comments, fold_comments, plain, show_comments

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


def test_a_comment_moving_is_not_a_changed_word(builder):
    """A comment whose paragraph was deleted lands on the next one: it is
    not highlighted as added text, and not described as a change."""
    builder.write("p.md", f"First paragraph here.{NOTE}\n\nSecond paragraph here.\n")
    base = builder.commit("first")
    builder.write("p.md", f"{NOTE}Second paragraph here, edited.\n")
    target = builder.commit("second")
    (f,) = compare(builder.path, base, target).files
    row = next(r for r in f.rows if r.right_no is not None and "Second" in str(r.right))
    assert row.changes == ['added ", edited"']
    assert '<span class="comment"' in str(row.right)
    assert "<ins" not in str(row.right).split("Second")[0]  # the marker is not highlighted


def test_a_paragraph_with_only_a_new_comment_is_shown(builder):
    """Its text is unchanged, so it is no edit, but it is not folded away
    with the unchanged lines: the new comment shows, marked new."""
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


def test_compare_fold_comments(builder, tmp_path):
    builder.write("p.md", f"Some text{NOTE} here.{END}\n")
    base = builder.commit("first")
    renumbered = NOTE.replace('id="3"', 'id="9"')
    builder.write("p.md", f"Some new text{renumbered} here.{END}\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target, fold_comments_md=True)
    (f,) = c.files
    (row,) = [r for r in f.rows if r.kind == "replace"]
    assert row.changes == ['added "new"']  # the renumbered comment is no change
    html = render(c)
    assert "comment-start" not in html
    assert html.count(MARKER) == 2
    assert 'class="comment new"' not in html  # the comment was already there
    # folding is the default; without it the markup is compared as text
    assert "comment-start" not in render(compare(builder.path, base, target))
    c = compare(builder.path, base, target, fold_comments_md=False)
    assert "comment-start" in render(c)


def test_fold_comments_only_markdown(builder):
    builder.write("p.txt", f"x {NOTE}\n")
    base = builder.commit("first")
    builder.write("p.txt", f"y {NOTE}\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target, fold_comments_md=True))
    assert "comment-start" in html


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
    assert status == {"Too long.": "unchanged", "Old remark.": "removed", "New remark.": "new"}
    by_text = {e.text: e for e in c.comments}
    assert by_text["New remark."].line == 22
    assert by_text["Old remark."].line == 21
    # the unchanged comment sits among hidden lines: its row still has an id
    html = render(c)
    anchor = by_text["Too long."].anchor
    assert anchor != c.files[0].anchor and f'id="{anchor}"' in html
    assert "1 unchanged comment" in html
    # the new comment has its own icon, in the text and in the panel
    assert html.count('class="comment new"') == 1
    assert by_text["New remark."].icon == NEW_COMMENT_MARK
    assert by_text["Too long."].icon == COMMENT_MARK


def test_no_panel_without_folding(builder):
    builder.write("p.md", f"a{NOTE}\n")
    base = builder.commit("first")
    builder.write("p.md", f"b{NOTE}\n")
    target = builder.commit("second")
    c = compare(builder.path, base, target, fold_comments_md=False)
    assert c.comments == [] and '<section class="comments-panel"' not in render(c)


def test_placeholders_never_leak(builder):
    builder.write("p.md", f"x{NOTE}{END}\n")
    base = builder.commit("first")
    builder.write("p.md", f"y{NOTE}{END}\n")
    target = builder.commit("second")
    html = render(compare(builder.path, base, target, fold_comments_md=True))
    assert not PLACEHOLDER.search(html)
