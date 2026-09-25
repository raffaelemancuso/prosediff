"""Comparing sentence by sentence: splitting prose, labelling the sentences."""

from prosediff import compare_paths, render
from prosediff.sentences import is_supported, sentences, split_sentences


def test_sentences_know_abbreviations_and_footnotes():
    text = "It works, e.g. with Smith et al. (2020). Second one.[^1] Third?"
    assert sentences(text) == [
        "It works, e.g. with Smith et al. (2020).",
        "Second one.[^1]",
        "Third?",
    ]


def test_only_prose_is_split_and_labels_point_to_the_file():
    lines = [
        "# A heading. With two sentences.",
        "",
        "One. Two.",
        "- An item. Its second sentence.",
        "| a. b. | c |",
        "```",
        "code. Not split.",
        "```",
        "Last line.",
    ]
    out, labels = split_sentences(lines)
    assert list(zip(labels, out, strict=True)) == [
        ("1", "# A heading. With two sentences."),
        ("2", ""),
        ("3.1", "One."),
        ("3.2", "Two."),
        ("4.1", "- An item."),
        ("4.2", "  Its second sentence."),
        ("5", "| a. b. | c |"),
        ("6", "```"),
        ("7", "code. Not split."),
        ("8", "```"),
        ("9", "Last line."),
    ]


def test_front_matter_and_dash_tables_kept():
    lines = ["---", "title: A. B.", "---", "", "  -----  ----", "  a. b.  c", "  -----  ----"]
    out, _ = split_sentences(lines)
    assert out == lines


def test_unknown_language_falls_back_to_a_simple_rule():
    assert is_supported("en") and not is_supported("fi")
    assert sentences("Yksi lause. Toinen lause.", "fi") == ["Yksi lause.", "Toinen lause."]


def test_compare_by_sentence(tmp_path):
    moved = "Firms that adopted the new technology are compared with the others."
    (tmp_path / "a.md").write_text(f"First paragraph here. {moved}\n\nSecond paragraph.\n")
    (tmp_path / "b.md").write_text(f"First paragraph here.\n\nSecond paragraph. {moved}\n")
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    # paragraph by paragraph: two edited paragraphs, no move
    (f,) = compare_paths(a, b).files
    assert f.moved == 0
    # sentence by sentence: the sentence moved
    c = compare_paths(a, b, by_sentence=True)
    (f,) = c.files
    assert f.moved == 1
    out = next(r for r in f.rows if r.kind == "moved-out")
    into = next(r for r in f.rows if r.kind == "moved-in")
    assert (out.left_label, into.right_label) == ("1.2", "3.2")
    assert out.changes == ["moved to line 3.2"] and into.changes == ["moved from line 1.2"]
    html = render(c)
    assert ">1.2<" in html and ">3.2<" in html
