"""Word by word: highlighted changes, their plain-English descriptions, letters
within a word, word counts.
"""

import pytest

from prosediff.diff import describe, word_diff


def test_changes_separated_by_spaces_are_one():
    w = word_diff("it makes incumbents adapt", "it keeps firms adapt")
    assert str(w.left) == "it <del>makes incumbents</del> adapt"
    assert str(w.right) == "it <ins>keeps firms</ins> adapt"
    assert w.changes == ['changed "makes incumbents" to "keeps firms"']
    # an unchanged word in between keeps them apart
    w = word_diff("a b c", "x b y")
    assert str(w.right) == "<ins>x</ins> b <ins>y</ins>"


def test_word_diff_escapes_html():
    w = word_diff("a <b> c", "a <i> c")
    assert str(w.left) == "a &lt;<del>b</del>&gt; c"
    assert str(w.right) == "a &lt;<ins>i</ins>&gt; c"


def test_word_diff_punctuation_is_its_own_token():
    w = word_diff("end.", "end!")
    assert str(w.left) == "end<del>.</del>"
    assert str(w.right) == "end<ins>!</ins>"
    assert w.changes == ['changed "." to "!"']


@pytest.mark.parametrize(
    "old,new,text",
    [
        ("", "very ", 'added "very"'),
        (" ", "", "removed a space"),
        (" ", "  ", "changed spacing"),
        ("x" * 100, "y", 'changed "' + "x" * 59 + '…" to "y"'),
    ],
)
def test_describe(old, new, text):
    assert describe(old, new) == text


def test_word_diff_marks_changed_letters_of_a_similar_word():
    w = word_diff("we repeat it", "we repeated it")
    assert str(w.right) == 'we <ins class="partial">repeat<mark>ed</mark></ins> it'
    assert str(w.left) == 'we <del class="partial">repeat</del> it'
    assert w.changes == ['changed "repeat" to "repeated"']


def test_word_diff_dissimilar_word_is_marked_whole():
    w = word_diff("a cat", "a dog")
    assert "partial" not in str(w.right) and "<mark>" not in str(w.right)


def test_word_diff_counts_words():
    w = word_diff("the quick brown fox", "the slow fox jumps high")
    assert (w.words_removed, w.words_added) == (2, 3)
