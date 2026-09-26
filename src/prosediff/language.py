"""The language of a document's prose: the rules its sentences are split by,
and the hyphenation the browser applies to it.

Given as a language code (en, it, de, pt-BR, ...); read from the file when
"document", as the language Word or an OpenDocument editor marked its text
with (the one most of its letters are in); or guessed from the text when
"guess", by py3langid, a naive Bayes classifier over byte n-grams that knows
some 140 languages. A guess is kept only when the text is long enough and
the classifier sure enough of it; otherwise the language stays unknown, and
the page does not hyphenate what it cannot name. By default ("default"),
Word and OpenDocument files say their language, and the others, or a
document that marks none, are guessed.
"""

import re
import zipfile
from collections import Counter
from functools import cache
from io import BytesIO

from lxml import etree

GUESS = "guess"
DOCUMENT = "document"
DEFAULT = "default"
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
    """A language option as given (a tag, "document", "guess" or "default";
    none is "default"), in lower case; ValueError when it is none of them."""
    value = (value or DEFAULT).strip().lower().replace("_", "-")
    if value not in (GUESS, DOCUMENT, DEFAULT) and not TAG.match(value):
        raise ValueError(
            f"not a language code: {value!r} (e.g. en, it, de, pt-br, or document, guess)"
        )
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


def resolve_language(option: str, text: str, marked: str | None = None) -> tuple[str, str]:
    """The language of a file's text under the option, and where it came
    from: "given", "document", "guessed", or "" when it is unknown. marked
    is the language the file itself records (most of its letters), if any."""
    if option in (DOCUMENT, DEFAULT) and marked:
        return marked, "document"
    if option == DOCUMENT:
        return "", ""
    if option in (GUESS, DEFAULT):
        guess = detect_language(text)
        return (guess, "guessed") if guess else ("", "")
    return option, "given"


# The languages a document marks its text with -----------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
# What a word processor marks text it must not proofread with: no language.
NO_LANGUAGE = {"x-none", "zxx", "none", "und"}
# Word and ODF mark the language of each kind of script apart: the Latin one
# (Greek and Cyrillic too), the East Asian one, and the complex scripts'
# (right to left, and those of South and South-East Asia).
ASIAN = re.compile("[\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
COMPLEX = re.compile("[\u0590-\u08ff\u0900-\u0eff\ufb1d-\ufdff\ufe70-\ufefe]")
# Not text of an OpenDocument's paragraphs: the deleted text tracked changes
# keep, comments, and notes (a note's text is counted as a paragraph of its own).
ODT_SKIPPED = {f"{TEXT}tracked-changes", f"{OFFICE}annotation", f"{TEXT}note"}

Marks = dict[str, str]  # kind of script ("latin", "asian", "complex") -> tag


def _tag(language: str | None, country: str | None = None) -> str | None:
    if not language or language.lower() in NO_LANGUAGE:
        return None
    if country and country.lower() not in NO_LANGUAGE:
        language = f"{language}-{country}"
    tag = language.lower().replace("_", "-")
    return tag if TAG.match(tag) else None


def _script(text: str) -> str:
    if COMPLEX.search(text):
        return "complex"
    return "asian" if ASIAN.search(text) else "latin"


def _found(**tags: str | None) -> Marks:
    return {k: v for k, v in tags.items() if v}


def _inherited(styles: dict, key, parent) -> Marks:
    """The marks of a style merged over those of the styles it derives
    from; styles maps a key to (parent's name, own marks), parent turns a
    parent's name into its key."""
    chain, seen = [], set()
    while key in styles and key not in seen:
        seen.add(key)
        name, own = styles[key]
        chain.append(own)
        key = parent(name)
    out: Marks = {}
    for own in reversed(chain):
        out.update(own)
    return out


def _parse(data: bytes, name: str):
    """A part of a zip package as XML, or None."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            return etree.fromstring(z.read(name))
    except (zipfile.BadZipFile, KeyError, OSError, etree.XMLSyntaxError):
        return None


def most_letters(counts: Counter[str]) -> str | None:
    """The language most letters are in, or None when none is marked."""
    return counts.most_common(1)[0][0] if counts else None


class WordLanguages:
    """The languages the runs of a Word document's paragraphs are marked with.

    A run's language is its own (w:lang), else its character style's, else
    its paragraph style's (or the default paragraph style's), else the
    document defaults', else the one its theme fonts were chosen for; a
    style's through those it is based on. Right-to-left runs are in the
    complex scripts' language (w:bidi), East Asian text in the East Asian
    one (w:eastAsia).
    """

    def __init__(self, data: bytes) -> None:
        self.styles: dict[str, tuple[str | None, Marks]] = {}
        self.base: Marks = {}
        self.default_paragraph = None
        if (settings := _parse(data, "word/settings.xml")) is not None:
            self.base = self.marks(settings.find(f"{W}themeFontLang"))
        if (root := _parse(data, "word/styles.xml")) is not None:
            for st in root.iter(f"{W}style"):
                based = st.find(f"{W}basedOn")
                self.styles[st.get(f"{W}styleId")] = (
                    based.get(f"{W}val") if based is not None else None,
                    self.marks(st.find(f"{W}rPr/{W}lang")),
                )
                if st.get(f"{W}type") == "paragraph" and st.get(f"{W}default") in ("1", "true"):
                    self.default_paragraph = st.get(f"{W}styleId")
            self.base |= self.marks(root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang"))
        self._style_marks: dict[str | None, Marks] = {}

    @staticmethod
    def marks(lang) -> Marks:
        if lang is None:
            return {}
        return _found(
            latin=_tag(lang.get(f"{W}val")),
            asian=_tag(lang.get(f"{W}eastAsia")),
            complex=_tag(lang.get(f"{W}bidi")),
        )

    def style_marks(self, sid: str | None) -> Marks:
        if sid not in self._style_marks:
            self._style_marks[sid] = _inherited(self.styles, sid, lambda name: name)
        return self._style_marks[sid]

    @staticmethod
    def _ref(el, path: str) -> str | None:
        ref = el.find(path)
        return ref.get(f"{W}val") if ref is not None else None

    def letters(self, p) -> Counter[str]:
        """The letters of a paragraph (w:p) in each language, its deleted
        text and the paragraphs nested in it (text boxes) left out."""
        pstyle = self._ref(p, f"{W}pPr/{W}pStyle") or self.default_paragraph
        paragraph = self.base | self.style_marks(pstyle)
        counts: Counter[str] = Counter()
        for r in p.iter(f"{W}r"):
            if next(r.iterancestors(f"{W}p")) is not p:
                continue
            text = "".join(t.text or "" for t in r.iter(f"{W}t"))
            if not (letters := _letters(text)):
                continue
            run = paragraph | self.style_marks(self._ref(r, f"{W}rPr/{W}rStyle"))
            run |= self.marks(r.find(f"{W}rPr/{W}lang"))
            rtl = r.find(f"{W}rPr/{W}rtl") is not None or r.find(f"{W}rPr/{W}cs") is not None
            if tag := run.get("complex" if rtl else _script(text)):
                counts[tag] += letters
        return counts

    def of(self, paragraphs) -> tuple[str | None, Counter[str]]:
        """The language most letters of the paragraphs are in, and the
        letters in each language."""
        counts: Counter[str] = Counter()
        for p in paragraphs:
            counts += self.letters(p)
        return most_letters(counts), counts


class OdtLanguages:
    """The languages the text of an OpenDocument's paragraphs is marked with.

    Text is in the language of the innermost span or paragraph whose style
    (or a parent of it) sets one: fo:language and fo:country, or the Asian
    or complex scripts' own; else in the default paragraph style's.
    """

    def __init__(self, data: bytes) -> None:
        # (family, name) -> (parent's name, own marks); styles.xml first, as
        # the automatic styles of content.xml may derive from its styles.
        self.styles: dict[tuple[str, str], tuple[str | None, Marks]] = {}
        self.base: Marks = {}
        for part in ("styles.xml", "content.xml"):
            if (root := _parse(data, part)) is None:
                continue
            for st in root.iter(f"{STYLE}style"):
                key = (st.get(f"{STYLE}family"), st.get(f"{STYLE}name"))
                self.styles[key] = (st.get(f"{STYLE}parent-style-name"), self.marks(st))
            for st in root.iter(f"{STYLE}default-style"):
                if st.get(f"{STYLE}family") == "paragraph":
                    self.base = self.marks(st)
        self._style_marks: dict[tuple[str, str | None], Marks] = {}

    @staticmethod
    def marks(el) -> Marks:
        props = el.find(f"{STYLE}text-properties")
        if props is None:
            return {}
        return _found(
            latin=_tag(props.get(f"{FO}language"), props.get(f"{FO}country")),
            asian=_tag(props.get(f"{STYLE}language-asian"), props.get(f"{STYLE}country-asian")),
            complex=_tag(
                props.get(f"{STYLE}language-complex"), props.get(f"{STYLE}country-complex")
            ),
        )

    def style_marks(self, family: str, name: str | None) -> Marks:
        key = (family, name)
        if key not in self._style_marks:
            self._style_marks[key] = _inherited(self.styles, key, lambda parent: (family, parent))
        return self._style_marks[key]

    def letters(self, p) -> Counter[str]:
        """The letters of a paragraph (text:p or text:h, an lxml element) in
        each language; tracked deletions, comments and notes left out."""
        counts: Counter[str] = Counter()

        def count(text: str | None, current: Marks) -> None:
            if (letters := _letters(text or "")) and (tag := current.get(_script(text))):
                counts[tag] += letters

        def walk(el, current: Marks) -> None:
            if not isinstance(el.tag, str) or el.tag in ODT_SKIPPED:
                return
            if name := el.get(f"{TEXT}style-name"):
                family = "paragraph" if el.tag in (f"{TEXT}p", f"{TEXT}h") else "text"
                current = current | self.style_marks(family, name)
            count(el.text, current)
            for child in el:
                walk(child, current)
                count(child.tail, current)

        walk(p, self.base)
        return counts

    def of(self, paragraphs) -> tuple[str | None, Counter[str]]:
        """The language most letters of the paragraphs are in, and the
        letters in each language."""
        counts: Counter[str] = Counter()
        for p in paragraphs:
            counts += self.letters(p)
        return most_letters(counts), counts


# How the page shows a language ----------------------------------------------------

# How the language of a file was found, as its tooltip says it.
SOURCES = {
    "document": "the language most of the document's text is marked with",
    "guessed": "guessed from the file's text",
    "given": "as given (--language)",
}


@cache
def _language(tag: str):
    import langcodes

    try:
        language = langcodes.Language.get(tag)
    except (langcodes.LanguageTagError, ValueError):
        return None
    return language if language.is_valid() and language.language else None


def language_name(tag: str) -> str:
    """The language's name in English, with its country when the tag has
    one: "Italian", "Portuguese (Brazil)"."""
    language = _language(tag)
    return language.display_name() if language is not None else tag


def flag_code(tag: str) -> str:
    """The country a language's flag is that of, as a two-letter code in
    lower case: the tag's own, or the country the language is most spoken
    in when the tag names none (en: us); "" when there is no such country."""
    language = _language(tag) if tag else None
    territory = language.maximize().territory if language is not None else None
    if not territory or not territory.isalpha() or len(territory) != 2:
        return ""
    return territory.lower()


def file_language_note(tag: str, source: str) -> str:
    """The tooltip of a file's language."""
    how = SOURCES.get(source)
    return f"{language_name(tag)}: {how}" if how else language_name(tag)


def paragraph_language_note(tag: str, file_tag: str, source: str) -> str:
    """The tooltip of a paragraph's language: its own (tag), as the document
    marks it, or else the file's."""
    if tag:
        return f"{language_name(tag)}: this paragraph's language, as the document marks it"
    return f"{file_language_note(file_tag, source)}; this paragraph marks none of its own"
