"""Footnote numbers set aside: a renumbered footnote is no change."""

import re

from sidediff import compare_paths, render
from sidediff.diff import footnote_similarity
from sidediff.footnotes import STAND_IN, match_footnotes, set_aside

NOTES = {
    "1": "The first note explains the data sources used in the study.",
    "2": "The second note, about the Waste Framework Directive of 1975.",
    "3": "The third note lists the keywords used to classify the strategies.",
    "4": "The fourth note describes the Orbis fields searched for keywords.",
}


def text(rows, side):
    return [re.sub(r"<[^>]+>", "", str(getattr(r, side))) for r in rows]


def versions(tmp_path, edit_third=""):
    """The old version with four footnotes; the new one without the second,
    so the third and fourth are renumbered 2 and 3."""
    old = "Body A.[^1] Body B.[^2] Body C.[^3] Body D.[^4]\n\n" + "\n\n".join(
        f"[^{k}]: {t}" for k, t in NOTES.items()
    )
    kept = [NOTES["1"], NOTES["3"] + edit_third, NOTES["4"]]
    new = "Body A.[^1] Body C.[^2] Body D.[^3]\n\n" + "\n\n".join(
        f"[^{k}]: {t}" for k, t in enumerate(kept, start=1)
    )
    (tmp_path / "old.md").write_text(old + "\n")
    (tmp_path / "new.md").write_text(new + "\n")
    return compare_paths(tmp_path / "old.md", tmp_path / "new.md", context=3)


def test_renumbered_footnotes_are_no_change(tmp_path):
    (f,) = versions(tmp_path).files
    rows = [r for r in f.rows if r.kind != "skip"]
    kinds = [(r.kind, r.left_label, r.right_label) for r in rows]
    # the body line lost "Body B.[^2]"; footnote 2 is deleted; 3 and 4 are
    # unchanged although now numbered 2 and 3
    assert kinds == [
        ("replace", "1", "1"),
        ("equal", "3", "3"),
        ("delete", "5", ""),
        ("equal", "7", "5"),
        ("equal", "9", "7"),
    ]
    body = rows[0]
    assert body.changes == ['removed "Body B.[^2]"']
    # each side shows its own numbers
    assert text(rows, "left")[3].startswith("[^3]: The third note")
    assert text(rows, "right")[3].startswith("[^2]: The third note")
    assert "Body C.[^3]" in text(rows, "left")[0] and "Body C.[^2]" in text(rows, "right")[0]


def test_real_edits_to_a_renumbered_footnote_are_shown(tmp_path):
    (f,) = versions(tmp_path, edit_third=" Two more words.").files
    third = next(r for r in f.rows if "third note" in str(r.right))
    assert third.kind == "replace"
    assert third.changes == ['added "Two more words."']
    assert (third.left_label, third.right_label) == ("7", "5")


def test_no_stand_in_reaches_the_page(tmp_path):
    html = render(versions(tmp_path))
    assert not STAND_IN.search(html)
    assert "[^3]: The third note" in html and "[^2]: The third note" in html


def test_matching_by_text():
    old = {"1": "alpha beta gamma delta", "2": "one two three four", "3": "x"}
    new = {"1": "one two three four", "2": "alpha beta gamma DELTA"}
    assert match_footnotes(old, new, footnote_similarity) == {"2": "1", "1": "2"}


def test_files_without_footnotes_are_untouched():
    lines = ["No notes here.", "Nor here."]
    out_old, out_new, notes = set_aside(lines, lines, footnote_similarity)
    assert (out_old, out_new) == (lines, lines) and not notes.old and not notes.new


def test_reference_without_definition_keeps_its_label():
    old, new, notes = set_aside(["See [^x] here."], ["See [^x] there."], footnote_similarity)
    assert old[0][4] == new[0][4] and STAND_IN.match(old[0][4])
    assert notes.old[old[0][4]] == notes.new[new[0][4]] == "x"
