"""Line by line: alignment by git, pairing of replaced lines, hidden and left-
out gaps, change stops.
"""

import itertools

import pytest
from helpers import kinds

from sidediff.diff import PAIRING_MAX_CELLS, align, git_opcodes, pair_lines, split_lines


def test_rows_carry_the_changes():
    rows, _, _ = align(["the quick fox", "gone"], ["the slow fox"], context=None)
    replaced = next(r for r in rows if r.kind == "replace")
    assert replaced.changes == ['changed "quick" to "slow"']
    deleted = next(r for r in rows if r.kind == "delete")
    assert deleted.changes == ["removed this line"]
    rows, _, _ = align([], ["new"], context=None)
    assert rows[0].changes == ["added this line"]


def test_align_equal_files():
    # nothing to show: the page says "Content unchanged"
    rows, add, rem = align(["a", "b"], ["a", "b"], context=None)
    assert rows == []
    assert (add, rem) == (0, 0)


def test_align_insert_and_delete():
    rows, add, rem = align(["a", "b", "c"], ["a", "c", "d"], context=None)
    assert kinds(rows) == ["equal", "delete", "equal", "insert"]
    assert (add, rem) == (1, 1)
    deleted = rows[1]
    assert (deleted.left_no, deleted.right_no) == (2, None)
    inserted = rows[3]
    assert (inserted.left_no, inserted.right_no) == (None, 3)


def test_align_replace_pairs_lines_side_by_side():
    rows, add, rem = align(["x", "one two", "y"], ["x", "one three", "y"], context=None)
    replaced = rows[1]
    assert replaced.kind == "replace"
    assert (replaced.left_no, replaced.right_no) == (2, 2)
    assert ">two</del>" in str(replaced.left)
    assert ">three</ins>" in str(replaced.right)
    assert (add, rem) == (1, 1)


def test_align_context_skips_unchanged_lines():
    old = [f"line {i}" for i in range(1, 21)]
    new = list(old)
    new[9] = "changed"
    rows, _, _ = align(old, new, context=2)
    assert kinds(rows) == ["skip", "equal", "equal", "replace", "equal", "equal", "skip"]
    assert rows[0].skipped == 7
    assert rows[-1].skipped == 8
    assert rows[1].left_no == 8


def test_align_full_has_no_skips():
    old = [f"line {i}" for i in range(50)]
    new = [*old[:25], "new", *old[25:]]
    rows, _, _ = align(old, new, context=None)
    assert "skip" not in kinds(rows)
    assert len(rows) == 51


def test_align_zero_context():
    rows, _, _ = align(["a", "b", "c"], ["a", "x", "c"], context=0)
    assert kinds(rows) == ["skip", "replace", "skip"]


def test_pairing_skips_inserted_line():
    old = ["alpha beta gamma delta", "one two three four"]
    new = ["alpha beta gamma DELTA", "brand new line here", "one two three FOUR"]
    assert pair_lines(old, new) == [(0, 0), (None, 1), (1, 2)]


def test_pairing_unrelated_lines_stand_alone():
    # several lines with nothing in common: removed and added, not face to
    # face, each in its place
    assert pair_lines(["a", "b"], ["c", "d", "e"]) == [
        (0, None),
        (None, 0),
        (1, None),
        (None, 1),
        (None, 2),
    ]
    # related enough (a quarter of the words), they are paired in order
    old = ["the report was written in May", "a different line entirely"]
    new = ["the report was finished in June", "another line altogether"]
    assert pair_lines(old, new)[0] == (0, 0)


def test_one_line_rewritten_in_place_is_a_pair():
    assert pair_lines(["a"], ["c"]) == [(0, 0)]


def test_pairing_reordered_keeps_order():
    # pairs never cross: a moved line is shown as deleted and inserted
    old = ["first line of text", "second line of text"]
    new = ["second line of text!", "first line of text!"]
    pairs = pair_lines(old, new)
    paired = [(i, j) for i, j in pairs if i is not None and j is not None]
    assert all(a[0] < b[0] and a[1] < b[1] for a, b in itertools.pairwise(paired))


def test_pairing_large_block_falls_back_to_order():
    n = int(PAIRING_MAX_CELLS**0.5) + 1
    old = [f"line {i}" for i in range(n)]
    new = [f"line {i}!" for i in range(n)]
    assert pair_lines(old, new) == [(i, i) for i in range(n)]


def test_align_uses_pairing():
    old = ["x", "alpha beta gamma delta", "one two three four", "y"]
    new = ["x", "alpha beta gamma DELTA", "brand new line here", "one two three FOUR", "y"]
    rows, add, rem = align(old, new, context=None)
    assert kinds(rows) == ["equal", "replace", "insert", "replace", "equal"]
    assert (add, rem) == (3, 2)


def test_split_lines_counts_like_git():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\r\nb") == ["a", "b"]
    assert split_lines("") == []
    assert split_lines("a\x0cb\n") == ["a\x0cb"]  # form feed is no line break for git


@pytest.mark.parametrize(
    "old,new",
    [
        (["a", "b", "c"], ["a", "c", "d"]),
        ([], ["x", "y"]),
        ([f"l{i}" for i in range(30)], [f"l{i}" for i in range(30) if i % 7] + ["end"]),
    ],
)
def test_git_opcodes_cover_both_files(old, new):
    (ops,) = git_opcodes([(old, new)])
    # the opcodes tile both files without gaps, like difflib's
    i = j = 0
    for _tag, i1, i2, j1, j2 in ops:
        assert (i1, j1) == (i, j)
        i, j = i2, j2
    assert (i, j) == (len(old), len(new))
    rebuilt = []
    for tag, i1, i2, j1, j2 in ops:
        rebuilt += new[j1:j2] if tag != "equal" else old[i1:i2]
    assert rebuilt == new


def test_git_opcodes_many_files_one_call():
    pairs = [(["a"], ["b"]), (["x"], ["x"]), (["1", "2"], ["1", "3", "2"])]
    ops = git_opcodes(pairs)
    assert ops[0] == [("replace", 0, 1, 0, 1)]
    assert ops[1] == [("equal", 0, 1, 0, 1)]
    assert ops[2] == [("equal", 0, 1, 0, 1), ("insert", 1, 1, 1, 2), ("equal", 1, 2, 2, 3)]


def test_git_opcodes_ignore_whitespace():
    (ops,) = git_opcodes([(["a  b", "c"], ["a b", "d"])], ignore_whitespace=True)
    assert ops[0][0] == "equal"
    (ops,) = git_opcodes([(["a  b", "c"], ["a b", "d"])])
    assert ops[0][0] == "replace"


def test_skip_rows_hold_the_hidden_lines():
    old = [f"line {i}" for i in range(1, 21)]
    new = list(old)
    new[9] = "changed"
    rows, _, _ = align(old, new, context=2)
    first, last = rows[0], rows[-1]
    assert [r.left_no for r in first.hidden] == list(range(1, 8))
    assert [r.left_no for r in last.hidden] == list(range(13, 21))


def test_long_gaps_are_left_out():
    old = [f"line {i}" for i in range(100)]
    new = list(old)
    new[0] = "first"
    new[99] = "last"
    rows, _, _ = align(old, new, context=2, max_hidden=10)
    (skip,) = [r for r in rows if r.kind == "skip"]
    assert skip.hidden == [] and skip.omitted == 94 and skip.skipped == 94
    rows, _, _ = align(old, new, context=2, max_hidden=None)
    (skip,) = [r for r in rows if r.kind == "skip"]
    assert len(skip.hidden) == 94


def test_first_of_change_marks_each_run():
    old = ["a", "b", "c", "d", "e"]
    new = ["a", "B", "c", "D", "E"]
    rows, _, _ = align(old, new, context=None)
    firsts = [r.left_no for r in rows if r.first_of_change]
    assert firsts == [2, 4]
    # in prose each changed paragraph is a stop, neighbours included
    old = ["Same.", "The first one.", "Same again.", "The second one.", "The third one."]
    new = ["Same.", "The first edit.", "Same again.", "The second edit.", "The third edit."]
    rows, _, _ = align(old, new, context=None, markdown=True)
    assert [r.left_no for r in rows if r.first_of_change] == [2, 4, 5]
