"""Moved lines: as they were, spacing aside, or lightly edited."""

import pytest

from prosediff import compare_paths, render
from prosediff.diff import MIN_MOVE_CHARS, align, mark_moves

EDITED = "This long sentence travels to the end of the file, almost as it was."


MOVED = "This sentence travels to the end of the file."


def test_moved_line_is_marked_both_ends():
    old = [MOVED, "a", "b", "c"]
    new = ["a", "b", "c", MOVED]
    rows, add, rem = align(old, new, context=None)
    kinds = {r.kind for r in rows}
    assert "moved-out" in kinds and "moved-in" in kinds
    out = next(r for r in rows if r.kind == "moved-out")
    into = next(r for r in rows if r.kind == "moved-in")
    assert out.changes == ["moved to line 4"]
    assert into.changes == ["moved from line 1"]
    assert (add, rem) == (0, 0)  # a move is neither


def test_move_ignores_spacing():
    rows, _, _ = align([MOVED, "a"], ["a", "  " + MOVED.replace(" ", "  ")], context=None)
    assert [(r.kind, r.changes) for r in rows] == [
        ("moved-out", ["moved to line 2"]),
        ("equal", []),
        ("moved-in", ["moved from line 1"]),
    ]


def test_short_lines_are_not_moves():
    short = "x" * (MIN_MOVE_CHARS - 1)
    rows, add, rem = align([short, "a"], ["a", short], context=None)
    assert "moved-in" not in {r.kind for r in rows}
    assert (add, rem) == (1, 1)
    mark_moves([])  # no rows, no moves, no error


def test_edited_line_moved_is_a_move_with_its_changes():
    old = [EDITED, "a", "b", "c"]
    new = ["a", "b", "c", EDITED.replace("almost", "nearly")]
    rows, add, rem = align(old, new, context=None)
    out = next(r for r in rows if r.kind == "moved-out")
    into = next(r for r in rows if r.kind == "moved-in")
    assert out.changes[0] == "moved to line 4, edited:"
    assert 'changed "almost" to "nearly"' in into.changes
    assert "nearly" in str(into.right) and "<ins" in str(into.right)
    assert (into.words_added, out.words_removed) == (1, 1)
    assert (add, rem) == (0, 0)


def test_most_similar_pairs_move_first():
    """Two candidates, each a move on its own: the closer one wins, although
    the other comes first."""
    a = "The quick brown fox jumps over the lazy dog today."
    far, near = a.replace("quick brown", "slow grey"), a.replace("today", "now")
    rows = align([a, "x", "y"], ["x", "y", far, near], context=None)[0]
    (into,) = [r for r in rows if r.kind == "moved-in"]
    assert into.right_no == 4


# Similarity 0.686: between the default threshold and 0.6.
REWRITTEN = (
    "This long sentence travels to the end of the file, almost as it was.",
    "The long sentence then travels to the far end of the document, roughly as it stood before.",
)


def moved(similarity: float, pair=REWRITTEN) -> bool:
    rows, _, _ = align(
        [pair[0], "a", "b"], ["a", "b", pair[1]], context=None, move_similarity=similarity
    )
    return any(r.kind == "moved-in" for r in rows)


def test_move_similarity_threshold():
    # a heavier rewrite: not a move at the default 0.8, a move at 0.6
    assert not moved(0.8)
    assert moved(0.6)
    # 1 keeps only the moves of unchanged lines
    assert not moved(1.0, (EDITED, EDITED.replace("almost", "nearly")))
    assert moved(1.0, (MOVED, MOVED))


def test_move_similarity_validated(tmp_path):
    (tmp_path / "a.md").write_text("x\n")
    (tmp_path / "b.md").write_text("y\n")
    for bad in (0, -0.1, 1.5):
        with pytest.raises(ValueError, match="move similarity"):
            compare_paths(tmp_path / "a.md", tmp_path / "b.md", move_similarity=bad)


def test_where_a_moved_line_went_is_printed(tmp_path):
    """On screen a moved line's tooltip says where it went; on paper, a note
    under it (hidden on screen by the HTML report's style)."""
    (tmp_path / "a.md").write_text(f"{EDITED}\na\nb\nc\n")
    (tmp_path / "b.md").write_text(f"a\nb\nc\n{EDITED.replace('almost', 'nearly')}\n")
    html = render(compare_paths(tmp_path / "a.md", tmp_path / "b.md", context=None))
    assert '<span class="print-note" aria-hidden="true">moved to line 4</span>' in html
    assert '<span class="print-note" aria-hidden="true">moved from line 1</span>' in html


def test_a_move_beats_a_weak_pairing():
    """A sentence moved next to an unrelated one is shown as moved, not as a
    rewrite of the unrelated sentence, which shares little more than its
    punctuation and a year in brackets; the text formats pair it alike."""
    from prosediff.diff import align, difflib_opcodes, line_pairs

    moved = (
        "We chose not to use the keyword set of Table A2 in Barbero et al. (2024) for two reasons."
    )
    other = (
        "The first directive dates back to 1975, and the current one was adopted "
        "in 2008 (Grosso et al., 2010)."
    )
    old = [moved, "A line that stays where it is.", "Another line that stays too.", other]
    new = ["A line that stays where it is.", "Another line that stays too.", moved]
    kinds = [r.kind for r in align(old, new, context=None)[0]]
    assert kinds == ["moved-out", "equal", "equal", "delete", "moved-in"]
    pairs = line_pairs(difflib_opcodes(old, new), old, new)
    assert (3, 2) not in [(i, j) for _, i, j in pairs]
    # a rewrite in place with nothing moved stays a pair
    anew = "Something else entirely, written anew."
    rewrite = align(["kept", other], ["kept", anew], context=None)
    assert [r.kind for r in rewrite[0]] == ["equal", "replace"]


def test_two_move_defaults(tmp_path):
    """By paragraph and sentence by sentence, each its own default; None in
    compare_paths picks the one of how the file is compared."""
    from prosediff import compare_paths
    from prosediff.diff import MOVE_ALGORITHMS, move_defaults

    (similarity, algorithm), (s_similarity, s_algorithm) = move_defaults(False), move_defaults(True)
    assert algorithm in MOVE_ALGORITHMS and s_algorithm in MOVE_ALGORITHMS
    assert s_similarity < similarity
    # a sentence moved into another paragraph, half its words kept: alike
    # enough for the sentence default, not for the paragraph one
    kept = "Alpha beta gamma delta epsilon zeta eta theta iota kappa"
    edited = "Alpha beta gamma delta epsilon zeta lambda mu nu xi omicron"
    score = MOVE_ALGORITHMS[s_algorithm]
    alike = score[1](score[0](kept + "."), score[0](edited + "."), 0)
    assert s_similarity <= alike < similarity
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(f"{kept}. The first paragraph goes on here.\n\nAnother one stays.\n".encode())
    new.write_bytes(f"The first paragraph goes on here.\n\nAnother one stays. {edited}.\n".encode())
    (f,) = compare_paths(old, new, by_sentence=True).files
    assert "moved-in" in [r.kind for r in f.rows]
    # the paragraph setting leaves sentences alone; their own setting does not
    (f,) = compare_paths(old, new, by_sentence=True, move_similarity=0.99).files
    assert "moved-in" in [r.kind for r in f.rows]
    (f,) = compare_paths(old, new, by_sentence=True, sentence_move_similarity=similarity).files
    assert "moved-in" not in [r.kind for r in f.rows]
