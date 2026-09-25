"""Word by word: highlighted changes, their plain-English descriptions, letters
within a word, word counts.
"""

import pytest
from helpers import untitled

from sidediff.diff import describe, word_diff


def test_word_diff_marks_only_changed_word():
    _w = word_diff("the quick fox", "the slow fox")
    left, right, changes = _w.left, _w.right, _w.changes
    assert untitled(left) == "the <del>quick</del> fox"
    assert untitled(right) == "the <ins>slow</ins> fox"
    assert changes == ['changed "quick" to "slow"']


def test_changes_separated_by_spaces_are_one():
    w = word_diff("it makes incumbents adapt", "it keeps firms adapt")
    assert untitled(w.left) == "it <del>makes incumbents</del> adapt"
    assert untitled(w.right) == "it <ins>keeps firms</ins> adapt"
    assert w.changes == ['changed "makes incumbents" to "keeps firms"']
    # an unchanged word in between keeps them apart
    w = word_diff("a b c", "x b y")
    assert untitled(w.right) == "<ins>x</ins> b <ins>y</ins>"


def test_word_diff_escapes_html():
    _w = word_diff("a <b> c", "a <i> c")
    left, right, _ = _w.left, _w.right, _w.changes
    assert untitled(left) == "a &lt;<del>b</del>&gt; c"
    assert untitled(right) == "a &lt;<ins>i</ins>&gt; c"


def test_word_diff_punctuation_is_its_own_token():
    _w = word_diff("end.", "end!")
    left, right, changes = _w.left, _w.right, _w.changes
    assert untitled(left) == "end<del>.</del>"
    assert untitled(right) == "end<ins>!</ins>"
    assert changes == ['changed "." to "!"']


@pytest.mark.parametrize(
    "old,new,text",
    [
        ("repeat", "repeated", 'changed "repeat" to "repeated"'),
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
    title = 'title="changed &#34;repeat&#34; to &#34;repeated&#34;"'
    assert str(w.right) == f'we <ins class="partial" {title}>repeat<mark>ed</mark></ins> it'
    assert str(w.left) == f'we <del class="partial" {title}>repeat</del> it'


def test_word_diff_dissimilar_word_is_marked_whole():
    w = word_diff("a cat", "a dog")
    assert "partial" not in str(w.right) and "<mark>" not in str(w.right)


def test_word_diff_counts_words():
    w = word_diff("the quick brown fox", "the slow fox jumps high")
    assert (w.words_removed, w.words_added) == (2, 3)
