"""Moved passages: a run of words removed in one place and added in another,
within a line or between two, is shown as moved."""

import re

import pytest
from helpers import two_files

from prosediff import Options, compare_paths, render
from prosediff.cli import main
from prosediff.diff import (
    MovedPassageSettings,
    Row,
    align,
    passages_of,
    slide_ops,
    word_ops,
)

SENTENCE = "The committee met twice in March to review the draft budget."
# Three paragraphs, the sentence moved from the first into the last.
OLD = [f"Opening remarks were brief. {SENTENCE} Then everyone left.", "keep", "Numbers came."]
NEW = ["Opening remarks were brief. Then everyone left.", "keep", f"Numbers came. {SENTENCE}"]


def moved_spans(html: str) -> list[tuple[str, str]]:
    """(data-pair, data-move) of each moved passage in the HTML."""
    return re.findall(r'<span class="moved" data-pair="(\d+)" data-move="([^"]*)"', html)


def test_sentence_moved_between_edited_paragraphs():
    old = [
        f"Opening remarks were brief. {SENTENCE} Then everyone left.",
        "A paragraph that stays as it was, in the middle.",
        "The second part discusses results. Numbers were good.",
    ]
    new = [
        "Opening remarks were brief. Then everyone left.",
        "A paragraph that stays as it was, in the middle.",
        f"The second part discusses results. {SENTENCE} Numbers were good.",
    ]
    rows, _, _ = align(old, new, context=None)
    first, _, last = rows
    assert (first.kind, last.kind) == ("replace", "replace")
    assert moved_spans(str(first.left)) == [("1", "Moved to line 3")]
    assert moved_spans(str(last.right)) == [("1", "Moved from line 1")]
    # the moved passage's ends are the sentence's own
    assert f'data-move="Moved to line 3">{SENTENCE}</span>' in str(first.left)
    assert f'data-move="Moved from line 1">{SENTENCE}</span>' in str(last.right)
    # a move, not words removed and added
    assert (first.words_removed, last.words_added) == (0, 0)
    assert "<del>" not in str(first.left) and "<ins>" not in str(last.right)
    assert first.changes == [f'moved "{SENTENCE}" to line 3']


def test_passage_edited_on_the_way():
    old = [
        f"Opening remarks were brief. {SENTENCE} Then everyone left.",
        "The second part discusses results. Numbers were good.",
    ]
    new = [
        "Opening remarks were brief. Then everyone left.",
        f"The second part discusses results. {SENTENCE.replace('twice', 'two times')} "
        "Numbers were good.",
    ]
    first, last = align(old, new, context=None)[0]
    assert "data-move-edited" in str(first.left)
    assert "<del>twice</del>" in str(first.left)
    assert "<ins>two times</ins>" in str(last.right)
    # the words edited on the way count, as in a moved line
    assert (first.words_removed, last.words_added) == (1, 2)


def test_passage_found_inside_longer_lines():
    """A sentence moved out of a paragraph deleted into a new one: the part
    of each that matches is moved, the rest removed or added."""
    old = [f"A deleted paragraph with many words in it. {SENTENCE} And closing words.", "keep"]
    new = ["keep", f"Brand new paragraph. {SENTENCE}"]
    rows, additions, deletions = align(old, new, context=None)
    out, _, into = rows
    assert (out.kind, into.kind) == ("delete", "insert")
    assert moved_spans(str(out.left)) == [("1", "Moved to line 2")]
    assert f'data-move="Moved to line 2">{SENTENCE}</span>' in str(out.left)
    assert f'data-move="Moved from line 1">{SENTENCE}</span>' in str(into.right)
    assert out.words_removed == 11  # the words not moved
    assert into.words_added == 3
    assert (additions, deletions) == (1, 1)
    assert not out.only_moved and not into.only_moved


def test_line_whose_words_all_moved_is_no_deletion():
    """A paragraph moved whole into another is not a paragraph deleted."""
    results = "The results section opens here, with a long account of the method used."
    old = [results, SENTENCE]
    new = [f"{results} {SENTENCE}"]
    rows, additions, deletions = align(old, new, context=None)
    gone = next(r for r in rows if r.kind == "delete")
    assert gone.only_moved and gone.words_removed == 0
    assert (additions, deletions) == (1, 1)  # the paragraph edited, only


def test_passages_moved_within_a_line():
    first = "Our first finding concerns the growth of small firms in the south."
    second = "The second one is about exports to the rest of the continent."
    rows, _, _ = align([f"{first} {second}"], [f"{second} {first}"], context=None)
    (row,) = rows
    assert len(moved_spans(str(row.left))) == len(moved_spans(str(row.right))) == 1
    assert row.words_added == row.words_removed == 0


@pytest.mark.parametrize(
    "old,new",
    [
        pytest.param(
            ["Alpha beta. Gamma delta.", "x"],
            ["Alpha. Gamma delta.", "x beta"],
            id="fewer_than_four_words",
        ),
        pytest.param(
            ["We measured the output of every plant in the region."],
            ["We measured the output of every factory across the country."],
            id="rewritten_in_place",
        ),
        # too short for a line's move key (under MIN_MOVE_CHARS): compared by
        # their words, not taken as identical
        pytest.param(
            ["It showed the very elite of the county at the ball.", "keep"],
            ["It showed the county at the ball.", "keep the benefits of the"],
            id="short_and_different",
        ),
        # sharing their articles and prepositions, hardly a word of meaning
        pytest.param(
            ["The report was found in several members of the same party.", "keep"],
            ["The report was found.", "keep, in both of the same"],
            id="alike_in_little_words_only",
        ),
    ],
)
def test_not_a_moved_passage(old, new):
    """Passages too short, rewritten in place, or alike in little words only
    are not one passage moved."""
    rows, _, _ = align(old, new, context=None)
    assert not any(moved_spans(str(r.left) + str(r.right)) for r in rows)


def test_holes_join_the_runs_of_a_passage():
    """Runs of changes a word or two apart are one passage; further apart,
    two."""
    old = "Start here and end here."
    near = "Start with one two three here four five six and end here."
    (passage,) = [p for p in passages_of(Row("replace"), old, near) if not p.old]
    assert near[passage.start : passage.end] == "with one two three here four five six"
    far = (
        "Start eleven twelve thirteen fourteen here and end "
        "fifteen sixteen seventeen eighteen here."
    )
    assert len([p for p in passages_of(Row("replace"), old, far) if not p.old]) == 2


def test_move_passages_can_be_turned_off(tmp_path):
    first = "Opening remarks were brief"
    second = "The second part discusses results"
    a, b = two_files(
        tmp_path,
        f"{first}. {SENTENCE} Then.\n\n{second}. Good.\n",
        f"{first}. Then.\n\n{second}. {SENTENCE} Good.\n",
    )
    on = compare_paths(a, b)
    assert on.counts.moved_passages == 1
    html = render(on)
    assert len(moved_spans(html)) == 2
    assert "1 moved passage" in html
    # the counts without them: the moved sentence's words removed and added
    plain, words = on.without_passages, len(SENTENCE.split())
    assert plain.moved_passages == 0
    assert plain.words_removed == on.counts.words_removed + words
    assert plain.words_added == on.counts.words_added + words
    # both counts are in the page, the "Moved passages" switch showing one
    shown, hidden = f"{on.counts.words_added:,}", f"{plain.words_added:,}"
    assert f'<span class="pv-on">{shown}</span><span class="pv-off">{hidden}</span>' in html
    off = compare_paths(a, b, Options(move_passages=False))
    assert off.counts.moved_passages == 0
    assert not moved_spans(render(off))
    # and from the command line
    out = tmp_path / "r.html"
    args = ["--files", str(a), str(b), "-o", str(out)]
    assert main(args) == 0
    assert moved_spans(out.read_text(encoding="utf-8"))
    assert main([*args, "--no-move-passages"]) == 0
    assert not moved_spans(out.read_text(encoding="utf-8"))


def test_sentence_removed_takes_its_own_full_stop():
    """A sentence removed from between two others is removed with its own
    full stop, not the one of the sentence before it (slide_ops); and a
    sentence added, likewise."""
    long = f"Opening remarks were brief. {SENTENCE} Then everyone left."
    short = "Opening remarks were brief. Then everyone left."

    def changes(old: str, new: str) -> list[tuple[str, str]]:
        return [
            (old[o1:o2], new[n1:n2]) for tag, o1, o2, n1, n2 in word_ops(old, new) if tag != "equal"
        ]

    assert changes(long, short) == [(f"{SENTENCE} ", "")]
    assert changes(short, long) == [("", f"{SENTENCE} ")]


def test_removal_slides_to_the_line_start_past_an_abbreviation():
    """A sentence removed from the start of a line that begins, as the next
    one does, with an abbreviation ("Mr.") slides to the start of the line:
    the full stop of "Mr." scores as the end of a sentence, but the line's
    start is a surer bound. A matcher may put the removal after the first
    "Mr. " (patiencediff and similar-rs do); the slide must not keep it
    there."""
    removed = "Mr. Collins was not left long to his thoughts. "
    old = removed + "Mr. Collins received them with pleasure."
    new = "Mr. Collins received them with pleasure."
    # as patiencediff pairs it: "Mr. " kept, then "Collins ... Mr. " removed
    k = len("Mr. ")
    ops = [
        ("equal", 0, k, 0, k),
        ("delete", k, k + len(removed), k, k),
        ("equal", k + len(removed), len(old), k, len(new)),
    ]
    changes = [(old[o1:o2], tag) for tag, o1, o2, *_ in slide_ops(ops, old, new) if tag != "equal"]
    assert changes == [(removed, "delete")]
    # and likewise an addition
    flipped = [({"delete": "insert"}.get(tag, tag), n1, n2, o1, o2) for tag, o1, o2, n1, n2 in ops]
    changes = [
        (old[n1:n2], tag) for tag, _, _, n1, n2 in slide_ops(flipped, new, old) if tag != "equal"
    ]
    assert changes == [(removed, "insert")]


def test_rows_keep_how_they_looked_without_passages():
    first, keep, last = align(OLD, NEW, context=None)[0]
    assert keep.without_passages is None
    plain = first.without_passages
    assert plain is not None and "<del>" in str(plain.left) and "moved" not in str(plain.left)
    assert plain.words_removed == 11 and first.words_removed == 0
    assert last.without_passages.words_added == 11 and last.words_added == 0


def test_many_pairs_are_narrowed_not_given_up():
    """Past max_pairs pairs of passages, the pairs sharing a rare word are
    still tried: the move is found instead of none."""
    narrow = MovedPassageSettings(max_pairs=1)
    first, _, _ = align(OLD, NEW, context=None, moved_passage_settings=narrow)[0]
    assert moved_spans(str(first.left)) == [("1", "Moved to line 3")]


def test_moved_passage_settings(tmp_path):
    for bad in ({"min_words": 0}, {"max_gap": -1}, {"partial_share": 0}, {"rare_share": 2}):
        with pytest.raises(ValueError, match=next(iter(bad))):
            MovedPassageSettings(**bad).check()
    strict = MovedPassageSettings(min_words=20)
    rows, _, _ = align(OLD, NEW, context=None, moved_passage_settings=strict)
    assert not any(r.old_moves for r in rows)
    # from the command line: one option for each setting
    a, b = two_files(tmp_path, "\n\n".join(OLD) + "\n", "\n\n".join(NEW) + "\n")
    out = tmp_path / "r.html"
    args = ["--files", str(a), str(b), "-o", str(out)]
    assert main([*args, "--passage-min-words", "20"]) == 0
    assert not moved_spans(out.read_text(encoding="utf-8"))
    assert main([*args, "--passage-min-words", "4"]) == 0
    assert moved_spans(out.read_text(encoding="utf-8"))
    with pytest.raises(SystemExit):
        main([*args, "--passage-partial-share", "1.5"])
