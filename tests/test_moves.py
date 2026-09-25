"""Moved lines: as they were, spacing aside, or lightly edited."""

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
    assert {r.kind for r in rows} >= {"moved-out", "moved-in"}


def test_short_lines_are_not_moves():
    short = "x" * (MIN_MOVE_CHARS - 1)
    rows, add, rem = align([short, "a"], ["a", short], context=None)
    assert "moved-in" not in {r.kind for r in rows}
    assert (add, rem) == (1, 1)


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


def test_dissimilar_lines_are_not_moves():
    old = ["This is one sentence about something.", "a", "b"]
    new = ["a", "b", "Completely different words appear here now."]
    rows, _add, _rem = align(old, new, context=None)
    assert not any(r.kind.startswith("moved") for r in rows)


def test_most_similar_pairs_move_first():
    a = "The quick brown fox jumps over the lazy dog today."
    rows = align(
        [a, "x", "y"],
        ["x", "y", a.replace("today", "now"), a.replace("lazy", "idle")],
        context=None,
    )[0]
    into = next(r for r in rows if r.kind == "moved-in")
    assert into.right_no in (3, 4)
    assert sum(r.kind == "moved-in" for r in rows) == 1
    mark_moves([])  # nothing to do, no error


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


def test_move_similarity_validated(builder, tmp_path):
    import pytest

    from prosediff import compare_paths

    (tmp_path / "a.md").write_text("x\n")
    (tmp_path / "b.md").write_text("y\n")
    for bad in (0, -0.1, 1.5):
        with pytest.raises(ValueError, match="move similarity"):
            compare_paths(tmp_path / "a.md", tmp_path / "b.md", move_similarity=bad)


def test_where_a_moved_line_went_is_printed(tmp_path):
    """On screen a moved line's tooltip says where it went; on paper, a note
    under it (hidden on screen by the page's style)."""
    from prosediff import compare_paths, render

    (tmp_path / "a.md").write_text(f"{EDITED}\na\nb\nc\n")
    (tmp_path / "b.md").write_text(f"a\nb\nc\n{EDITED.replace('almost', 'nearly')}\n")
    html = render(compare_paths(tmp_path / "a.md", tmp_path / "b.md", context=None))
    assert '<span class="print-note" aria-hidden="true">moved to line 4</span>' in html
    assert '<span class="print-note" aria-hidden="true">moved from line 1</span>' in html
