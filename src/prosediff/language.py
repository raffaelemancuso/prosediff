"""The language of a document's prose: the rules its sentences are split by,
and the hyphenation the browser applies to it.

Given as a language code (en, it, de, pt-BR, ...), or guessed from the text
when "auto", by py3langid, a naive Bayes classifier over byte n-grams that
knows some 140 languages. A guess is kept only when the text is long enough
and the classifier sure enough of it; otherwise the language stays unknown,
and the page does not hyphenate what it cannot name.
"""

import re
from collections import Counter
from functools import cache

AUTO = "auto"
# A language tag as BCP 47 writes it, in the forms the page and yasbd use.
TAG = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
# Below these, a guess is noise: "Hello world" reads as Fulfulde. A
# paragraph's vote needs less confidence, since the majority of the
# document's letters must agree too.
MIN_LETTERS = 50
MIN_CONFIDENCE = 0.8
PARAGRAPH_CONFIDENCE = 0.6
# The start of a long document (in characters) says as much as all of it.
SAMPLE = 20_000
# What would sway the guess without being prose: URLs, and the attributes,
# citation keys and markup of pandoc's Markdown.
NOISE = re.compile(r"https?://\S+|\{[^{}\n]*\}|@[\w:.-]+|[#*_`~|>\[\]()^=-]+")


def normalize_language(value: str | None) -> str:
    """A language option as given (a tag, or "auto"), in lower case;
    ValueError when it is neither."""
    value = (value or AUTO).strip().lower().replace("_", "-")
    if value != AUTO and not TAG.match(value):
        raise ValueError(f"not a language code: {value!r} (e.g. en, it, de, pt-br, or auto)")
    return value


@cache
def _identifier():
    # Imported on first use: loading the model takes a third of a second.
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    return LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def detect_language(text: str) -> str | None:
    """The language the text is written in, or None when it is too short or
    too mixed to tell.

    Each paragraph long enough to judge votes for its language, with its
    number of letters, when the classifier is fairly sure of it; the
    language of most of those letters wins, if it holds a majority. A
    reference list in English does not make an Italian paper English, as it
    does when the whole text is judged at once. Text without such a
    paragraph (a list, a table) is judged whole, and more strictly.
    """
    ident = _identifier()
    votes: Counter[str] = Counter()
    seen = 0
    for paragraph in text.split("\n"):
        prose = NOISE.sub(" ", paragraph)
        letters = _letters(prose)
        if letters < MIN_LETTERS:
            continue
        language, confidence = ident.classify(prose)
        if confidence >= PARAGRAPH_CONFIDENCE:
            votes[language] += letters
        seen += len(paragraph)
        if seen > SAMPLE:
            break
    if votes:
        language, letters = votes.most_common(1)[0]
        return language if letters > sum(votes.values()) / 2 else None
    whole = NOISE.sub(" ", text[:SAMPLE])
    if _letters(whole) < MIN_LETTERS:
        return None
    language, confidence = ident.classify(whole)
    return language if confidence >= MIN_CONFIDENCE else None


def resolve_language(option: str, text: str) -> tuple[str | None, bool]:
    """The language of a document's text under the option, and whether it
    was guessed."""
    if option == AUTO:
        return detect_language(text), True
    return option, False
