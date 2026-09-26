"""Soft hyphens in the HTML report's prose, placed across its markup."""

from markupsafe import Markup

from prosediff.hyphenate import SOFT_HYPHEN as SHY
from prosediff.hyphenate import hyphenate


def test_words_get_soft_hyphens():
    assert hyphenate(Markup("the regulation"), "en") == f"the reg{SHY}u{SHY}la{SHY}tion"
    assert hyphenate(Markup("economia"), "it") == f"eco{SHY}no{SHY}mia"
    # short words, unknown languages and empty cells are left alone
    assert hyphenate(Markup("the cat sat"), "en") == "the cat sat"
    assert hyphenate(Markup("regulation"), "") == "regulation"
    assert hyphenate(Markup("regulation"), "xx") == "regulation"
    assert hyphenate(Markup(""), "en") == ""


def test_a_word_split_by_its_changes_is_hyphenated_whole():
    """A word whose end changed breaks as the whole word does, its soft
    hyphens inside and outside the element."""
    out = hyphenate(Markup("regu<ins>lation</ins>"), "en")
    assert out == f"reg{SHY}u<ins>{SHY}la{SHY}tion</ins>"


def test_markup_and_entities_are_left_alone():
    html = Markup('<span class="comment" data-text="regulation">💬</span> &amp; competitiveness')
    out = hyphenate(html, "en")
    assert SHY in out and out.replace(SHY, "") == html
    assert 'data-text="regulation"' in out
    # either side of a <br> is a word of its own
    out = hyphenate(Markup("regula<br>tion"), "en")
    assert out == hyphenate(Markup("regula"), "en") + Markup("<br>tion")
