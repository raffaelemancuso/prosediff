"""Whole lines and passages moved together: paragraphs and the sentences or
clauses inside them (split by paragraph), sentences and the clauses inside
them (split by sentence), each found where it went and nothing else."""

from prosediff import compare_paths, render
from prosediff.diff import Row, align, git_opcodes, move_defaults

# Six paragraphs of a local newspaper, each on its own subject.
HARBOUR = (
    "The harbour opened at dawn and the first boats left for the northern banks. "
    "Fishermen checked their nets twice before sailing. "
    "Gulls followed them well past the lighthouse."
)
BAKERY = (
    "In the village the bakery sold out of bread by eight o'clock. "
    "Its owner blamed the new flour mill for the shortage of rye."
)
COUNCIL = (
    "The council debated the harbour fees for most of the afternoon. "
    "A proposal to double them was rejected by a narrow margin. "
    "The mayor promised a new study before the winter."
)
SCHOOLS = (
    "Schools reopened after the long summer break with fewer pupils than last year. "
    "Teachers asked for smaller classes and better heating."
)
STORM = (
    "A storm reached the coast on Friday night and damaged several piers. "
    "Repairs are expected to take at least three weeks."
)
MARKET = (
    "The weekly market moved to the square near the church, where stalls sell "
    "cheese, fruit and wool from the hills, and opens at seven."
)
# Unchanged paragraphs between them: moving the bakery paragraph past
# three of them is plainly one move (past one, "it moved down" and "the
# next moved up" would say the same).
LIBRARY = "The library extended its opening hours on Thursdays until nine in the evening."
FERRY = "The ferry to the island now runs twice a day, also on Sundays."
CHOIR = "The parish choir will sing at the cathedral in the capital next month."
NETS = "Fishermen checked their nets twice before sailing."
PROPOSAL = "A proposal to double them was rejected by a narrow margin."
STALLS = "where stalls sell cheese, fruit and wool from the hills"


def aligned(old: list[str], new: list[str], sentences: bool) -> list[Row]:
    """The rows of two versions as prosediff compares them: lines aligned by
    git (difflib, align's own fallback, may anchor on the moved line), moves
    by the defaults of paragraphs or sentences."""
    similarity, algorithm = move_defaults(sentences)
    (ops,) = git_opcodes([(old, new)])
    rows, _, _ = align(old, new, None, ops, move_similarity=similarity, move_algorithm=algorithm)
    return rows


def moves(rows: list[Row], old: list[str], new: list[str]):
    """The moves the rows show: whole lines as {(old line, new line)}, and
    passages as {(old line, new line, old text, new text)}, lines 1-based."""
    outs = {r.move_pair: r.left_no for r in rows if r.kind == "moved-out"}
    lines = {(outs[r.move_pair], r.right_no) for r in rows if r.kind == "moved-in"}
    ends: dict[int, dict] = {}
    for r in rows:
        for m in r.old_moves:
            ends.setdefault(m.pair, {})["old"] = (r.left_no, old[r.left_no - 1][m.start : m.end])
        for m in r.new_moves:
            ends.setdefault(m.pair, {})["new"] = (r.right_no, new[r.right_no - 1][m.start : m.end])
    passages = {(e["old"][0], e["new"][0], e["old"][1], e["new"][1]) for e in ends.values()}
    return lines, passages


def test_paragraphs_and_the_sentences_inside_them_moved():
    """Split by paragraph: a paragraph moved whole, a sentence moved (and
    edited) from one paragraph into another, two sentences of a paragraph
    swapped, and a clause moved inside its paragraph."""
    old = [HARBOUR, BAKERY, LIBRARY, COUNCIL, FERRY, SCHOOLS, STORM, CHOIR, MARKET]
    new = [
        HARBOUR.replace(NETS + " ", ""),
        LIBRARY,
        f"{PROPOSAL} " + COUNCIL.replace(PROPOSAL + " ", ""),
        FERRY,
        f"{SCHOOLS} " + NETS.replace("twice", "three times"),
        STORM,
        BAKERY,  # moved whole, after the storm
        CHOIR,
        f"Near the church, {STALLS}, the weekly market moved to the square and opens at seven.",
    ]
    rows = aligned(old, new, sentences=False)
    lines, passages = moves(rows, old, new)
    # the bakery paragraph, moved whole from line 2 to line 7
    assert lines == {(2, 7)}
    by_text = {(o, n): (a, b) for o, n, a, b in passages}
    # the nets sentence, from the harbour paragraph into the schools one,
    # edited on the way
    assert by_text[1, 5] == (NETS, NETS.replace("twice", "three times"))
    # the proposal swapped with the sentence before it, in the council
    # paragraph: one of the two is shown moved, whole
    swapped = by_text[4, 3]
    assert swapped[0] == swapped[1] and swapped[0] in (PROPOSAL, COUNCIL.split(". ")[0] + ".")
    # inside the market paragraph, the stalls clause and the words of the
    # market moving past each other: either is shown moved, whole
    market = "weekly market moved to the square"
    moved_in_market = by_text[9, 9]
    assert any(all(part in end for end in moved_in_market) for part in (STALLS, market))
    # and nothing else is taken for a move
    assert set(by_text) == {(1, 5), (4, 3), (9, 9)}
    # a moved passage's words are neither removed nor added, but for those
    # edited on the way (twice -> three times)
    harbour = next(r for r in rows if r.left_no == 1)
    schools = next(r for r in rows if r.right_no == 5)
    assert (harbour.words_removed, schools.words_added) == (1, 2)  # twice, three times
    assert harbour.without_passages.words_removed == len(NETS.split())
    # hidden, each moved passage is removed and added in its own place
    assert schools.without_passages.words_added == len(NETS.split()) + 1
    # the moved paragraph counts neither as added nor as removed
    additions = sum(r.kind in ("insert", "replace") and not r.only_moved for r in rows)
    deletions = sum(r.kind in ("delete", "replace") and not r.only_moved for r in rows)
    assert (additions, deletions) == (4, 4)


# Six sentences of a report, as prosediff compares prose sentence by sentence.
SURVEY = "The survey covered twelve regions, including the remote northern islands, over two years."
RESPONSES = "Response rates were high in the north and low in the large cities."
COSTS = "Most households reported rising costs for heating and for transport."
REPORT = (
    "The report, published in the middle of May, drew criticism from several well known economists."
)
AUTHORS = "Its authors defended their sampling method in a long public letter."
WAVE = "A second wave of interviews is planned for the next spring."
FUNDING = "The ministry has confirmed the funding for both waves of the study."
DATA = "The anonymised data will be published on the institute's website."
TEAM = "A team of eight researchers carried out the field work across the country."
ISLANDS = "including the remote northern islands"
MAY = "published in the middle of May"


def test_sentences_and_the_clauses_inside_them_moved():
    """Split by sentence: a sentence moved whole, a clause moved from one
    sentence into another, and a clause moved inside its sentence."""
    old = [SURVEY, RESPONSES, COSTS, REPORT, AUTHORS, WAVE, FUNDING, DATA, TEAM]
    new = [
        "The survey covered twelve regions over two years.",
        RESPONSES,
        f"Most households, {ISLANDS}, reported rising costs for heating and for transport.",
        f"The report drew criticism from several well known economists, {MAY}.",
        WAVE,
        FUNDING,
        DATA,
        TEAM,
        AUTHORS,  # moved whole, to the end, past four unchanged sentences
    ]
    rows = aligned(old, new, sentences=True)
    lines, passages = moves(rows, old, new)
    assert lines == {(5, 9)}
    by_text = {(o, n): (a, b) for o, n, a, b in passages}
    # the islands clause, from the first sentence into the third, with the
    # comma that closed it
    assert by_text[1, 3] == (f"{ISLANDS},", f"{ISLANDS},")
    # the date clause, moved inside its own sentence
    assert MAY in by_text[4, 4][0] and MAY in by_text[4, 4][1]
    assert set(by_text) == {(1, 3), (4, 4)}


def test_both_splits_of_one_document(tmp_path):
    """The same revision read from Markdown files, split both ways: whole
    lines and passages moved are found in each, and the HTML report lets
    the passages be hidden."""
    old_text = (
        "\n\n".join([HARBOUR, BAKERY, LIBRARY, COUNCIL, FERRY, SCHOOLS, STORM, CHOIR, MARKET])
        + "\n"
    )
    new_text = (
        "\n\n".join(
            [
                HARBOUR.replace(NETS + " ", ""),
                LIBRARY,
                f"{PROPOSAL} " + COUNCIL.replace(PROPOSAL + " ", ""),
                FERRY,
                f"{SCHOOLS} {NETS}",
                STORM,
                BAKERY,
                CHOIR,
                MARKET,
            ]
        )
        + "\n"
    )
    (tmp_path / "a.md").write_text(old_text, encoding="utf-8")
    (tmp_path / "b.md").write_text(new_text, encoding="utf-8")
    paragraphs = compare_paths(tmp_path / "a.md", tmp_path / "b.md", context=None)
    sentences = compare_paths(tmp_path / "a.md", tmp_path / "b.md", context=None, by_sentence=True)
    # by paragraph: the bakery paragraph moved whole; the nets sentence and
    # the swapped council sentence moved as passages
    assert paragraphs.moved == 1 and paragraphs.moved_passages == 2
    # by sentence: every sentence is a line, so the moves are whole lines:
    # the nets sentence, the proposal and the bakery's two sentences
    assert sentences.moved >= 3 and sentences.moved_passages == 0
    html = render(paragraphs, sentences=sentences, split="both")
    assert 'data-toggle="passages"' in html
    assert html.count('class="moved"') == 2 * paragraphs.moved_passages
