"""The language of the prose: given or guessed, and what it sets on the page."""

import re

import pytest

from prosediff import compare_paths, render
from prosediff.cli import main
from prosediff.language import detect_language, normalize_language

ITALIAN = (
    "Le politiche regionali per l'economia circolare hanno effetti diversi sulle "
    "imprese esistenti e sulla nascita di nuove imprese, secondo il contesto locale."
)
ITALIAN_EDITED = ITALIAN.replace("effetti diversi", "effetti molto diversi")
ENGLISH = (
    "Most empirical tests of the Porter Hypothesis have focused on how existing "
    "firms respond to regulation, and much less on the entry of new firms."
)


def pair(tmp_path, name, old, new):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "old" / name).write_text(old, encoding="utf-8")
    (tmp_path / "new" / name).write_text(new, encoding="utf-8")
    return tmp_path / "old", tmp_path / "new"


def test_normalize_language():
    assert [normalize_language(v) for v in ("auto", " IT ", "pt_BR", None)] == [
        "auto",
        "it",
        "pt-br",
        "auto",
    ]
    with pytest.raises(ValueError, match="not a language code"):
        normalize_language("italian!")


def test_detect_language():
    assert detect_language(ENGLISH) == "en"
    # markup and citation keys do not sway it
    assert detect_language(f"# Titolo\n\n{ITALIAN} [@porter1995; @ambec2013]{{.cite}}") == "it"
    # too short to tell: "Hello world" would read as Fulfulde
    assert detect_language("Hello world") is None


def test_guessed_per_file_and_hyphenated(tmp_path):
    """auto guesses each prose file's language; the page hyphenates by it."""
    old, new = pair(tmp_path, "paper.md", ITALIAN + "\n", ITALIAN_EDITED + "\n")
    c = compare_paths(old, new)
    (f,) = c.files
    assert (f.language, f.language_guessed) == ("it", True)
    html = render(c)
    assert '<table class="prose" lang="it">' in html
    assert "language: it (guessed)" in html
    # soft hyphens, by Italian rules, and the text otherwise intact
    assert "eco\u00adno\u00admia" in html
    assert "effetti molto diversi" in re.sub(r"<[^>]+>|\u00ad", "", html)


def test_no_language_no_hyphenation(tmp_path):
    """Prose too short to tell, and code, get no language: nothing is
    hyphenated by guess."""
    old, new = pair(tmp_path, "note.md", "Hello world\n", "Hello there\n")
    (tmp_path / "old" / "notes.txt").write_text(ENGLISH + "\n", encoding="utf-8")
    (tmp_path / "new" / "notes.txt").write_text(ENGLISH + " More.\n", encoding="utf-8")
    c = compare_paths(old, new)
    assert [f.language for f in c.files] == ["", ""]
    assert "\u00ad" not in render(c)


def test_cli_language(tmp_path, capsys):
    """A given language is used as it is, not guessed; a bad one is refused."""
    old, new = pair(tmp_path, "paper.md", ITALIAN + "\n", ITALIAN_EDITED + "\n")
    out = tmp_path / "page.html"
    assert main(["--files", str(old), str(new), "-o", str(out), "--language", "DE"]) == 0
    assert "language: de<" in out.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--files", str(old), str(new), "--language", "italian!"])
    assert "--language: not a language code" in capsys.readouterr().err
