"""Word by word: highlighted changes, their plain-English descriptions, letters
within a word, word counts.
"""

import pytest

from prosediff.diff import describe, word_diff


@pytest.mark.parametrize(
    "old,new,left,right,changes",
    [
        # changes separated by spaces are one
        (
            "it makes incumbents adapt",
            "it keeps firms adapt",
            "it <del>makes incumbents</del> adapt",
            "it <ins>keeps firms</ins> adapt",
            ['changed "makes incumbents" to "keeps firms"'],
        ),
        # an unchanged word in between keeps them apart
        (
            "a b c",
            "x b y",
            "<del>a</del> b <del>c</del>",
            "<ins>x</ins> b <ins>y</ins>",
            ['changed "a" to "x"', 'changed "c" to "y"'],
        ),
        # HTML escaped
        (
            "a <b> c",
            "a <i> c",
            "a &lt;<del>b</del>&gt; c",
            "a &lt;<ins>i</ins>&gt; c",
            ['changed "b" to "i"'],
        ),
        # punctuation is a token of its own
        ("end.", "end!", "end<del>.</del>", "end<ins>!</ins>", ['changed "." to "!"']),
        # the changed letters of a similar word marked
        (
            "we repeat it",
            "we repeated it",
            'we <del class="partial">repeat</del> it',
            'we <ins class="partial">repeat<mark>ed</mark></ins> it',
            ['changed "repeat" to "repeated"'],
        ),
        # a dissimilar word marked whole
        ("a cat", "a dog", "a <del>cat</del>", "a <ins>dog</ins>", ['changed "cat" to "dog"']),
    ],
)
def test_word_diff(old, new, left, right, changes):
    w = word_diff(old, new)
    assert (str(w.left), str(w.right), w.changes) == (left, right, changes)


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


def test_word_diff_counts_words():
    w = word_diff("the quick brown fox", "the slow fox jumps high")
    assert (w.words_removed, w.words_added) == (2, 3)
