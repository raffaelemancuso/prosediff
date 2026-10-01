"""An AI's assessment of the changes, taken as a whole: whether the new
version is better than the old, what changed, and what to fix.

The changes go to the model as the word diff of the comparison
([-removed-]{+added+}, comments in CriticMarkup), with, by default, the
whole new version, for the model to check the changes against the rest of
the document. A file alone (diff.review_file) is reviewed instead, whole, no
diff sent (kind "review"). Three kinds of backend answer, each an
optional extra of prosediff:

- "claude": Claude Code, through the Claude Agent SDK (prosediff[claude]),
  on the Claude login of the machine;
- "codex": ChatGPT, through OpenAI's Codex SDK (prosediff[codex]), on the
  ChatGPT login of Codex;
- "PROVIDER/MODEL": any model any-llm reaches (prosediff[models]): a local
  Ollama model ("ollama/qwen3"), or an API with its key in the environment
  ("openai/gpt-5", "anthropic/claude-sonnet-5", "gemini/...", ...).

"claude" and "codex" take a model too ("claude/opus", "codex/gpt-5.5"),
else the one their login defaults to; the models each offers, and the
efforts each model supports, are as the AI reports them (models_of). The
models get no tools and no files: only the text.
"""

import asyncio
import json
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import timedelta
from functools import cache
from pathlib import Path

from markupsafe import Markup

# How long the model may take, at most, by default (seconds): a long revision
# read by a slow local model takes minutes.
ASSESS_TIMEOUT = 900
# How long listing an AI's models may take (seconds): Claude Code and Codex
# start their program, which takes a few seconds.
LIST_TIMEOUT = 60
# The most of the word diff, and of the whole new version, a model is sent,
# in characters (about 100,000 and 85,000 tokens); past it the text is cut,
# and the model told.
MAX_DIFF_CHARS = 400_000
MAX_DOCUMENT_CHARS = 300_000
# The backends that are not any-llm's providers, and the extra of each.
SUBSCRIPTIONS = {"claude": "claude", "codex": "codex"}
# The model Claude Code reports as the one it uses unless told: --assess
# claude asks for it by giving no model.
CLAUDE_DEFAULT = "default"
VERDICTS = ("improves", "mixed", "worsens")
# The verdicts of the review of one document alone (kind "review"): good,
# fair or poor.
REVIEW_VERDICTS = ("good", "fair", "poor")
# The verdicts of the second assessment, whether the new text reads as written
# by an AI (an assessment of kind "writing"): likely, possibly or unlikely.
WRITING_VERDICTS = ("likely", "possibly", "unlikely")
# What the model reads: the whole new version and the changes, or the
# changes alone.
CONTEXTS = ("document", "changes")

# What the prompts below share, word for word.
DIFF_GIVEN = """\
the changes between two versions of a document as a word diff: [-text-] was \
removed, {+text+} was added, and {>>Author (date): text<<} is a reviewer's \
comment; lines that begin with @@ say where a change sits."""
PROBLEMS_SECTION = """\
## Problems to fix
A numbered list, most serious first, each quoting the words concerned. \
Write "None found." if there are none."""
CLOSING = """\
Be specific and brief. Write in the language the document is written in, \
unless the instructions of the person asking say otherwise."""
AI_SIGNS = """\
Look for the signs of such text: generic or inflated wording ("delve", \
"pivotal", "underscore", "intricate", "landscape", "tapestry"), stock \
transitions and summaries, a uniform rhythm of sentences, lists of three, \
balanced hedging without specifics, claims or citations that look invented \
or do not fit what they support, and"""
AI_CAVEAT = """\
These signs are circumstantial: careful human writers show them too, and \
writers in a second language are often taken for an AI wrongly. Say how sure \
you can be, never claim certainty, and judge the text, not the people."""
AI_SECTIONS = """\
## Signs of AI writing
A short list, each quoting the words concerned and naming the sign. Write \
"None found." if there are none.

## Signs against
A short list of what reads as the authors' own. Write "None found." if there \
are none."""

SYSTEM = f"""\
You are an experienced editor and peer reviewer of academic and professional \
writing. You are given {DIFF_GIVEN} Unchanged paragraphs are mostly left out \
of the diff; when the whole new version is given too, check the changes \
against the rest of it.

Assess the value of the changes as a whole: is the new version better than \
the old one, and why? Judge the argument, clarity, concision, accuracy and \
consistency, not personal taste. Check whether the changes introduce errors: \
claims no longer supported, results misstated, broken or unfinished \
sentences, placeholders, citations or cross-references left dangling, \
inconsistent spelling or terms. Say what the comments asked for and whether \
the changes answer them.

Answer in Markdown with exactly these sections:

## Verdict
The first word, in bold, is one of **Improves**, **Mixed** or **Worsens**; \
then two or three sentences on why.

## What changed
A short list of the main changes, grouped by section of the document.

## Improvements
A short list of what the changes do well.

{PROBLEMS_SECTION}

{CLOSING}"""

# The review of one document alone, not of changes (kind "review"; the
# comparison's single file).
SYSTEM_REVIEW = f"""\
You are an experienced editor and peer reviewer of academic and professional \
writing. You are given a document to review, whole; {{>>Author (date): \
text<<}} is a comment its authors or reviewers left in it.

Assess the document as a whole: its argument, structure, clarity, concision, \
accuracy and consistency, not personal taste. Look for errors: claims not \
supported, results misstated or inconsistent between sections, broken or \
unfinished sentences, placeholders, citations or cross-references left \
dangling, inconsistent spelling or terms, grammar and typing errors. Say what \
the comments ask for and whether the text answers them.

Answer in Markdown with exactly these sections:

## Verdict
The first word, in bold, is one of **Good**, **Fair** or **Poor**; then two \
or three sentences on why.

## Summary
What the document sets out to do, and how, in a few sentences.

## Strengths
A short list of what the document does well.

{PROBLEMS_SECTION}

{CLOSING}"""

# The second assessment, of kind "writing", asked apart: whether
# the text the changes added reads as written by an AI. Such a judgement is
# circumstantial, and the model is told so.
SYSTEM_WRITING = f"""\
You are an experienced editor. You are given {DIFF_GIVEN} When the whole new \
version is given too, it shows how its authors write elsewhere.

Assess whether the text the changes added or rewrote (what {{+ +}} added, and \
new paragraphs) reads as written by a generative AI, a large language model, \
rather than by the document's authors. {AI_SIGNS} a register or vocabulary \
unlike the rest of the document. Weigh them against how the authors write in \
the unchanged text. {AI_CAVEAT}

Answer in Markdown with exactly these sections:

## Verdict
The first word, in bold, is one of **Likely**, **Possibly** or **Unlikely** \
(that the new text was written by an AI); then two or three sentences on why, \
and how sure you can be.

{AI_SECTIONS}

{CLOSING}"""

# The same question of one document alone (kind "writing", reviewing one
# file): with no unchanged text to weigh it against, a weaker judgement, and
# the model is told so.
SYSTEM_WRITING_REVIEW = f"""\
You are an experienced editor. You are given a document, whole; {{>>Author \
(date): text<<}} is a comment its authors or reviewers left in it.

Assess whether the document, or parts of it, reads as written by a \
generative AI, a large language model, rather than by its authors. \
{AI_SIGNS} passages whose register or vocabulary differs from the rest. \
There is no earlier version to compare with, so weigh the parts of the \
document against each other. {AI_CAVEAT}

Answer in Markdown with exactly these sections:

## Verdict
The first word, in bold, is one of **Likely**, **Possibly** or **Unlikely** \
(that the document, or parts of it, was written by an AI); then two or three \
sentences on why, and how sure you can be.

{AI_SECTIONS}

{CLOSING}"""

# Added to SYSTEM when the files are Word documents or OpenDocument texts:
# their styles reach the model in prosediff's notation (diff.line_markdown),
# which it must not take for the text, nor report as changed.
DOCUMENTS = """

The documents are Word documents or OpenDocument texts, not Markdown. They \
are given one paragraph per line, their formatting written in a notation of \
the tool that compares them, which is not in the documents: # to ###### \
before a paragraph for a Heading 1 to Heading 6 style, • for a list item, | \
between the cells of a table row, **bold**, *italic*, [text]{.underline}, \
~~struck through~~, ^superscript^, ~subscript~, [^1] for a footnote \
reference and [^1]: before the footnote's text. An equation is written in a \
linear notation of its own (E = mc^(2), x_(i)): that is its text, not \
formatting. Never report these marks \
themselves as added, removed or misplaced: say what changed in the word \
processor's terms (a paragraph that lost its Heading 2 style, words no longer \
bold, a list item become a paragraph), and quote the words without them."""

# Added to SYSTEM when the problems are to be marked in the text (annotate).
ANNOTATE = """

Then, after the sections, mark in the text each passage the changes made \
or touched that has a problem, in one fenced code block of language json \
holding a list; each item an object with these keys:
- "side": "new" for a passage of the new version, "old" for text only the \
old version has (a removal);
- "start": the first 3 to 8 words of the passage, copied exactly from that \
version, punctuation included;
- "end": its last 3 to 8 words, copied exactly (the same as "start" for a \
short passage);
- "problem": what is wrong with it, in a sentence;
- "solution": the change you propose, in a sentence;
- "replacement": when the fix is an edit of the passage's own words, the \
whole passage from its "start" to its "end" as it should read, every word \
it keeps copied exactly, only what must change changed; "" when the fix is \
anything else (text to add elsewhere, a question to answer, a figure to \
make, a passage of the old version) or the passage runs over more than one \
paragraph.
A program uses these words to change the document: it searches the text \
for "start" and "end" character for character, and puts "replacement" in \
the passage's place as a tracked change. So quote back exactly the text \
you are sent in the message: copy "start", "end" and every word "replacement" \
keeps character for character from that version as it is written here \
(the same spelling, capitals, spacing, punctuation, quotes, dashes, \
symbols and equations), never retyped, corrected, translated or tidied, \
and change in "replacement" only what must change. Never copy the diff's markers \
([- -], {+ +}, {>> <<}) nor the notation of a document's formatting (# for \
a heading, • for a list item, **bold**, *italic*, [^1] for a footnote \
reference). Write [] when nothing is to be marked."""


def _reworded(text: str, changes: dict[str, str]) -> str:
    """text with each of changes made; every one must be found."""
    for old, new in changes.items():
        if old not in text:
            raise ValueError(f"not in the prompt: {old!r}")
        text = text.replace(old, new)
    return text


# ANNOTATE for a document reviewed alone: no sides, no diff.
ANNOTATE_REVIEW = _reworded(
    ANNOTATE,
    {
        "each passage the changes made or touched that has": "each passage that has",
        '- "side": "new" for a passage of the new version, "old" for text only the old '
        "version has (a removal);\n": "",
        "copied exactly from that version": "copied exactly from the document",
        ", a passage of the old version)": ")",
        "from that version as it is written here": "from the document as it is written here",
        "the diff's markers ([- -], {+ +}, {>> <<})": "the comments' markers ({>> <<})",
    },
)


def no_edits(marks: str) -> str:
    """ANNOTATE (or ANNOTATE_REVIEW) when the AI may not edit the text: the
    problems marked, no passage rewritten, no key "replacement"."""
    out, n = re.subn(r'- "replacement":.*?paragraph\.\n', "", marks, flags=re.S)
    if n != 1:
        raise ValueError("no replacement key in the prompt")
    return _reworded(
        out,
        {
            "A program uses these words to change the document: it searches the text "
            'for "start" and "end" character for character, and puts "replacement" in '
            "the passage's place as a tracked change.": "You may not edit the text: say "
            "what is wrong and what to do, never a rewording of the passage. A program "
            'uses these words to find the passage: it searches the text for "start" and '
            '"end" character for character.',
            'copy "start", "end" and every word "replacement" keeps character for '
            "character": 'copy "start" and "end" character for character',
            ', and change in "replacement" only what must change. Never': ". Never",
        },
    )


@dataclass(frozen=True)
class Annotation:
    """A problem the model marked in the text: on which side, from which
    words to which, what is wrong and what to do; replacement, the passage
    as it should read, when the fix is an edit of its words ("" else)."""

    side: str  # "new" or "old"
    start: str
    end: str
    problem: str
    solution: str = ""
    replacement: str = ""


# How an annotation's passage is found in the text, by prosediff.aidocs and
# the HTML report's script alike: whatever typographic variants of quotes
# and dashes a model writes plainly, its end words within REACH characters
# of its start words.
TYPOGRAPHIC = {
    **dict.fromkeys("‘’‚‛", "'"),
    **dict.fromkeys("“”„‟", '"'),
    **dict.fromkeys("‐‑‒–—―−", "-"),
}
REACH = 20_000


# The fenced JSON block at the end of an answer, with the marked passages.
JSON_BLOCK = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.S | re.I)


def split_annotations(answer: str) -> tuple[str, list[Annotation]]:
    """An answer's Markdown without its fenced JSON list of marked passages,
    and those passages; the answer as it is, and none, when it has no list
    that reads as one (an item without its words or problem is left out)."""
    found = list(JSON_BLOCK.finditer(answer))
    if not found:
        return answer, []
    block = found[-1]
    try:
        items = json.loads(block[1])
    except ValueError:
        return answer, []
    notes = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        get = {k: str(item.get(k) or "").strip() for k in ("side", "start", "end", "problem")}
        if not get["start"] or not get["problem"]:
            continue
        side = "old" if get["side"].lower() == "old" else "new"
        notes.append(
            Annotation(
                side,
                get["start"],
                get["end"] or get["start"],
                get["problem"],
                str(item.get("solution") or "").strip(),
                # a passage of the new version only: the old one is not in the file
                str(item.get("replacement") or "").strip() if side == "new" else "",
            )
        )
    text = answer[: block.start()] + answer[block.end() :]
    # a heading left with nothing under it, for the list alone
    text = re.sub(r"\n#+[^\n]*\s*$", "", text.rstrip())
    return text.strip(), notes


def duration(seconds: float) -> str:
    """A time taken, to the second, as a person says it: "41 seconds", "3
    minutes and 41 seconds" (humanize)."""
    import humanize

    return humanize.precisedelta(
        timedelta(seconds=round(seconds)), minimum_unit="seconds", format="%0.0f"
    )


class AssessError(RuntimeError):
    """The assessment could not be made: a backend missing, a login, a
    model, a timeout."""


@dataclass(frozen=True)
class AssessRequest:
    """What to ask an AI: which (spec: "claude", "claude/opus",
    "ollama/qwen3"), how hard to think (effort: one the model supports, ""
    for its default), what it reads (context: one of CONTEXTS), the
    instructions of the person asking (added to the prompt), how long it
    may take, whether the text sent is put in the HTML report
    (save_prompt: Assessment.prompt_text), whether the model marks the
    problems in the text (annotate: Assessment.annotations), who its
    comments in the Word and OpenDocument documents are by (author; "":
    Assessment.title), the prompts in place of prosediff's (text, or a
    file; "": prosediff's, default_system): system for the assessment or
    the review, writing_system for whether the new text reads as written
    by an AI. Whether the
    new text reads as written by an AI is asked apart, a second time
    (assess_comparison with kind "writing")."""

    spec: str
    effort: str = ""
    context: str = "document"
    instructions: str = ""
    timeout: float = ASSESS_TIMEOUT
    save_prompt: bool = False
    annotate: bool = True
    author: str = ""
    system: str = ""
    writing_system: str = ""
    # whether the AI may edit the text: propose fixes as rewordings of the
    # passages it marks (Annotation.replacement), or only mark them
    edits: bool = True


def default_system(kind: str, single: bool = False) -> str:
    """prosediff's own prompt for an assessment of kind ("value",
    "review" or "writing"; single: "writing" of one file reviewed alone),
    the one AssessRequest.system (writing_system, for "writing") replaces."""
    if kind == "writing":
        return SYSTEM_WRITING_REVIEW if single else SYSTEM_WRITING
    return SYSTEM_REVIEW if kind == "review" else SYSTEM


def system_of(request: AssessRequest, kind: str, single: bool = False) -> str:
    """The prompt an assessment of kind is sent first: the request's own,
    or prosediff's."""
    own = request.writing_system if kind == "writing" else request.system
    return instructions_from(own) or default_system(kind, single)


@dataclass
class Assessment:
    """What a model made of the changes: their value (kind "value"), or
    whether their new text reads as written by an AI ("writing"); or what
    it made of one document alone ("review")."""

    backend: str  # as asked: "claude", "codex/gpt-5.5", "ollama/qwen3"
    markdown: str = ""
    model: str = ""  # the model that answered, when the backend says
    seconds: float = 0.0
    error: str = ""  # why there is none
    effort: str = ""
    context: str = ""
    # the text sent to the model, as sent: its system prompt and its message
    # ("" when it was never sent), and whether the HTML report shows it
    system: str = ""
    prompt: str = ""
    save_prompt: bool = False
    # the problems the model marked in the text, when asked (annotate)
    annotations: list[Annotation] = field(default_factory=list)
    kind: str = "value"
    # who its comments in the documents are by; "": the title
    author: str = ""

    @property
    def notes_json(self) -> list[dict]:
        """The marked problems, for the report's script to find in the text."""
        return [asdict(a) for a in self.annotations]

    @property
    def verdict_section(self) -> str | None:
        """The text of the model's Verdict section; None for none."""
        section = re.search(r"#+\s*Verdict\s*\n(.*?)(?=\n#+\s|\Z)", self.markdown, re.S | re.I)
        return section[1] if section else None

    @property
    def verdict(self) -> str:
        """ "improves", "mixed" or "worsens" (for a "writing" assessment,
        "likely", "possibly" or "unlikely"; for a "review", "good", "fair"
        or "poor"), from the Verdict section; "" when the model gave none of
        them."""
        words = re.findall(r"[A-Za-z]+", self.verdict_section or self.markdown[:200])
        verdicts = {"writing": WRITING_VERDICTS, "review": REVIEW_VERDICTS}.get(self.kind, VERDICTS)
        return next((w.lower() for w in words if w.lower() in verdicts), "")

    @property
    def html(self) -> Markup:
        """The assessment as HTML: the model's Markdown rendered, its own
        HTML escaped."""
        return Markup(_markdown().render(self.markdown))

    @property
    def title(self) -> str:
        """Who wrote it: "Claude Code (claude-opus-5-5)", "ollama/qwen3"."""
        name = {"claude": "Claude Code", "codex": "Codex (ChatGPT)"}.get(
            self.backend.split("/")[0], self.backend
        )
        return f"{name} ({self.model})" if self.model else name

    @property
    def took(self) -> str:
        """How long the model took: "20 seconds", "1 minute and 3 seconds"."""
        return duration(self.seconds)

    @property
    def how(self) -> str:
        """How it was asked: "effort high, from the changes and the new
        version"."""
        parts = [f"effort {self.effort}"] if self.effort else []
        if self.context:
            parts.append(
                "from the changes and the new version"
                if self.context == "document"
                else "from the changes only"
            )
        return ", ".join(parts)

    def prompt_text(self) -> str:
        """The text sent to the model, exactly: which AI and how it was asked,
        then its system prompt and its message, each between two marker
        lines."""
        asked = ", ".join(filter(None, (self.backend, self.how)))
        return (
            f"The text prosediff sent to the model ({asked}).\n\n"
            f"===== system prompt =====\n{self.system}\n===== end of system prompt =====\n\n"
            f"===== message =====\n{self.prompt}\n===== end of message =====\n"
        )


@dataclass(frozen=True)
class ModelInfo:
    """A model as its AI reports it: its name, what it is, the efforts it
    supports (level, what it is) and its default one ("" when unsaid)."""

    name: str
    description: str = ""
    efforts: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    default_effort: str = ""


@cache
def _markdown():
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).enable("table")


def parse_backend(spec: str) -> tuple[str, str]:
    """The backend and the model of "claude", "claude/opus", "codex",
    "ollama/qwen3": ("claude", ""), ("claude", "opus"), ...; a provider of
    any-llm needs its model."""
    backend, _, model = spec.strip().partition("/")
    backend = backend.lower()
    if not backend:
        raise AssessError("say which AI: claude, codex, or PROVIDER/MODEL (e.g. ollama/qwen3)")
    if backend not in SUBSCRIPTIONS and not model:
        raise AssessError(
            f"{backend!r}: give the model too, as {backend}/MODEL (e.g. ollama/qwen3), "
            "or use claude or codex"
        )
    return backend, model


def instructions_file(value: str) -> Path | None:
    """The file value names, when it names one: a short one-line value that
    is a file's path; None for text."""
    value = value.strip()
    if value and len(value) < 1_000 and "\n" not in value:
        path = Path(value).expanduser()
        if path.is_file():
            return path
    return None


def instructions_from(value: str) -> str:
    """The instructions given: the text itself, or the contents of the file
    it names (UTF-8)."""
    path = instructions_file(value)
    return path.read_text(encoding="utf-8").strip() if path else value.strip()


def cut(text: str, limit: int, what: str) -> str:
    """text as the model is sent it: cut past limit characters, at a line,
    the cut said."""
    if len(text) <= limit:
        return text
    at = text.rfind("\n", 0, limit)
    kept = text[: at if at > 0 else limit]
    left = len(text) - len(kept)
    return f"{kept}\n\n[The {what} is cut here: {left:,} more characters left out.]"


def prompt_for(diff: str, subject: str, document: str = "", instructions: str = "") -> str:
    """The message the model is sent: the whole new version (when given),
    the word diff, and the instructions of the person asking."""
    parts = []
    if document.strip():
        parts.append(
            f"The whole new version of {subject}, for reference:\n\n"
            f"<document>\n{cut(document, MAX_DOCUMENT_CHARS, 'document')}\n</document>"
        )
    parts.append(
        f"The changes made to {subject}, as a word diff:\n\n"
        f"<diff>\n{cut(diff, MAX_DIFF_CHARS, 'diff')}\n</diff>"
    )
    parts += instructions_part(instructions, "assessment")
    parts.append("Assess the changes as the instructions say.")
    return "\n\n".join(parts)


def review_prompt_for(document: str, subject: str, instructions: str = "") -> str:
    """The message the model is sent to review one document alone: the
    document, and the instructions of the person asking."""
    parts = [
        f"The document to review, {subject}:\n\n"
        f"<document>\n{cut(document, MAX_DIFF_CHARS, 'document')}\n</document>"
    ]
    parts += instructions_part(instructions, "review")
    parts.append("Review the document as the instructions say.")
    return "\n\n".join(parts)


def instructions_part(instructions: str, what: str) -> list[str]:
    """The part of the message giving the instructions of the person
    asking for this what (an assessment, a review); none without them."""
    if not instructions.strip():
        return []
    return [
        f"The instructions of the person asking for this {what}, to follow within the "
        f"format above:\n\n<instructions>\n{instructions.strip()}\n</instructions>"
    ]


def _missing(extra: str, what: str) -> AssessError:
    return AssessError(
        f"{what} is not installed: install prosediff with its {extra!r} extra "
        f"(uv tool install 'prosediff[{extra}]', or pip install 'prosediff[{extra}]')"
    )


def _claude(system: str, prompt: str, model: str, effort: str, timeout: float) -> tuple[str, str]:
    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query
    except ImportError as e:
        raise _missing("claude", "The Claude Agent SDK") from e

    async def messages():
        # the prompt as a message on the CLI's input, not an argument: a long
        # diff would pass the command line's limit
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    async def run(cwd: str) -> tuple[str, str]:
        options = ClaudeAgentOptions(
            system_prompt=system,
            tools=[],
            max_turns=1,
            model=None if model in ("", CLAUDE_DEFAULT) else model,
            effort=effort or None,
            cwd=cwd,
            setting_sources=[],
        )
        answered, text = "", ""
        async for m in query(prompt=messages(), options=options):
            if isinstance(m, AssistantMessage):
                answered = m.model or answered
            elif isinstance(m, ResultMessage):
                if m.is_error:
                    raise AssessError(f"Claude Code: {m.result or m.subtype}")
                text = m.result or ""
        return text, answered

    with tempfile.TemporaryDirectory() as cwd:
        return _within(run(cwd), timeout)


def _codex(system: str, prompt: str, model: str, effort: str, timeout: float) -> tuple[str, str]:
    try:
        from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
        from openai_codex.generated.v2_all import ReasoningEffort
    except ImportError as e:
        raise _missing("codex", "OpenAI's Codex SDK") from e
    try:
        level = ReasoningEffort(effort) if effort else None
    except ValueError as e:  # an effort Codex does not know
        raise AssessError(f"Codex: {e}") from e

    async def run(cwd: str) -> tuple[str, str]:
        async with AsyncCodex(CodexConfig(cwd=cwd)) as codex:
            thread = await codex.thread_start(
                developer_instructions=system,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                ephemeral=True,
                model=model or None,
                cwd=cwd,
            )
            result = await thread.run(prompt, effort=level)
        if result.error is not None:
            raise AssessError(f"Codex: {getattr(result.error, 'message', result.error)}")
        return result.final_response or "", model

    # the SDK's errors: no login, no binary, a refusal
    login = " (log in to ChatGPT with prosediff --login-codex)"
    with tempfile.TemporaryDirectory() as cwd:
        return _within_as("Codex", run(cwd), timeout, login)


def _any_llm(
    provider: str, system: str, prompt: str, model: str, effort: str, timeout: float
) -> tuple[str, str]:
    try:
        import any_llm
    except ImportError as e:
        raise _missing("models", "any-llm") from e
    extra = {}
    if provider == "ollama":
        # Ollama's window is small by default: room for the prompt (about 3.5
        # characters a token) and the answer
        extra["num_ctx"] = min(131_072, (len(system) + len(prompt)) // 3 + 8_192)
    if effort:
        extra["reasoning_effort"] = effort
    try:
        response = any_llm.completion(
            model=model,
            provider=provider,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            # on the client: some providers (Ollama) take no timeout per call
            client_args={"timeout": timeout},
            **extra,
        )
    except Exception as e:  # any-llm's errors, a connection refused, an unknown provider
        raise AssessError(f"{provider}/{model}: {e}") from e
    return response.choices[0].message.content or "", response.model or model


def _within_as(label: str, coroutine, timeout: float, hint: str = ""):
    """_within, any other error of an SDK an AssessError saying label (and
    hint, after the error)."""
    try:
        return _within(coroutine, timeout)
    except AssessError:
        raise
    except Exception as e:
        raise AssessError(f"{label}: {e}{hint}") from e


def _within(coroutine, timeout: float):
    async def bounded():
        return await asyncio.wait_for(coroutine, timeout)

    try:
        return asyncio.run(bounded())
    except TimeoutError as e:
        raise AssessError(f"no answer within {timeout:,.0f} seconds") from e


# backend, system prompt, prompt, model, effort, timeout -> (answer, model)
Runner = Callable[[str, str, str, str, str, float], tuple[str, str]]


def run_backend(
    backend: str, system: str, prompt: str, model: str, effort: str, timeout: float
) -> tuple[str, str]:
    """The model's answer and the model's name, from the backend asked."""
    if backend == "claude":
        return _claude(system, prompt, model, effort, timeout)
    if backend == "codex":
        return _codex(system, prompt, model, effort, timeout)
    return _any_llm(backend, system, prompt, model, effort, timeout)


def assess(
    diff: str,
    subject: str,
    request: AssessRequest,
    document: str = "",
    runner: Runner | None = None,
    documents: bool = False,
    kind: str = "value",
    single: bool = False,
) -> Assessment:
    """An assessment of the changes of a word diff by the AI request names;
    subject names what changed ("paper.docx"), document is the whole new
    version, sent when request.context is "document"; documents: the files
    are Word documents or OpenDocument texts, their formatting written in
    prosediff's notation (DOCUMENTS); runner answers in place of the models
    (run_backend), for the tests. kind "writing" asks instead whether the
    new text reads as written by an AI (SYSTEM_WRITING), no problem marked
    in the text. kind "review" reviews document alone, diff unused
    (SYSTEM_REVIEW): what a file reviewed alone is worth, its problems
    marked when asked; kind "writing" with single asks of document alone
    whether it reads as written by an AI (SYSTEM_WRITING_REVIEW). It never
    raises: a failure is its error."""
    runner = runner or run_backend
    started = time.monotonic()
    # the document read alone, whole, diff unused
    review = kind == "review" or single
    context = request.context if request.context in CONTEXTS else "document"
    if context == "changes" and not review:
        document = ""
    if review:
        context = ""  # the document is all it reads
    made = Assessment(
        request.spec,
        effort=request.effort,
        context=context,
        save_prompt=request.save_prompt,
        kind=kind,
        author=request.author,
    )
    annotate = request.annotate and kind in ("value", "review")
    marks = (ANNOTATE_REVIEW if review else ANNOTATE) if annotate else ""
    if marks and not request.edits:
        marks = no_edits(marks)
    try:
        backend, model = parse_backend(request.spec)
        instructions = instructions_from(request.instructions)
        if review:
            if not document.strip():
                raise AssessError("the document has no text to review")
            prompt = review_prompt_for(document, subject, instructions)
        else:
            if not diff.strip():
                raise AssessError("there are no changes to assess")
            prompt = prompt_for(diff, subject, document, instructions)
        system = system_of(request, kind, single) + (DOCUMENTS if documents else "") + marks
        made.system, made.prompt = system, prompt  # kept even if the model then fails
        text, answered = runner(backend, system, prompt, model, request.effort, request.timeout)
        if not text.strip():
            raise AssessError("the model gave no answer")
    except (AssessError, OSError) as e:
        made.error, made.seconds = str(e), time.monotonic() - started
        return made
    if annotate:
        text, made.annotations = split_annotations(text)
        if not request.edits:  # a rewording given all the same is left out
            made.annotations = [replace(n, replacement="") for n in made.annotations]
    made.markdown, made.model = text.strip(), answered
    made.seconds = time.monotonic() - started
    return made


def models_of(backend: str, timeout: float = LIST_TIMEOUT) -> list[ModelInfo]:
    """The models an AI reports it offers, the one it uses by default first:
    Claude Code's and Codex's as each tells, with the efforts each model
    supports; any-llm's providers' as their API lists them (for most, the
    API key must be set), efforts unsaid. AssessError when the AI cannot
    say."""
    if backend == "claude":
        return _claude_models(timeout)
    if backend == "codex":
        return _codex_models(timeout)
    return _any_llm_models(backend, timeout)


def _claude_models(timeout: float) -> list[ModelInfo]:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    except ImportError as e:
        raise _missing("claude", "The Claude Agent SDK") from e

    async def run(cwd: str) -> list[dict]:
        options = ClaudeAgentOptions(tools=[], cwd=cwd, setting_sources=[])
        async with ClaudeSDKClient(options) as client:
            info = await client.get_server_info() or {}
        return info.get("models") or []

    with tempfile.TemporaryDirectory() as cwd:
        found = _within_as("Claude Code", run(cwd), timeout)
    return [
        ModelInfo(
            m["value"],
            ": ".join(filter(None, (m.get("displayName"), m.get("description")))),
            tuple((level, "") for level in m.get("supportedEffortLevels") or ()),
        )
        for m in found
        if m.get("value")
    ]


def _codex_models(timeout: float) -> list[ModelInfo]:
    try:
        from openai_codex import AsyncCodex
    except ImportError as e:
        raise _missing("codex", "OpenAI's Codex SDK") from e

    async def run():
        async with AsyncCodex() as codex:
            return (await codex.models()).data

    found = [m for m in _within_as("Codex", run(), timeout) if not m.hidden]
    found.sort(key=lambda m: not m.is_default)

    def value(effort) -> str:
        return str(getattr(effort, "value", effort or ""))

    return [
        ModelInfo(
            m.id,
            m.display_name + (", Codex's default" if m.is_default else ""),
            tuple(
                (value(e.reasoning_effort), e.description or "")
                for e in m.supported_reasoning_efforts or ()
            ),
            value(m.default_reasoning_effort),
        )
        for m in found
    ]


def _any_llm_models(provider: str, timeout: float) -> list[ModelInfo]:
    try:
        import any_llm
    except ImportError as e:
        raise _missing("models", "any-llm") from e
    try:
        found = any_llm.list_models(provider, client_args={"timeout": timeout})
    except Exception as e:
        raise AssessError(f"{provider}: {e}") from e
    return sorted((ModelInfo(m.id) for m in found), key=lambda m: m.name)


def providers() -> list[str]:
    """The providers any-llm reaches with what is installed (openai,
    anthropic, gemini, ...); none without any-llm."""
    try:
        from any_llm import AnyLLM
        from any_llm.constants import LLMProvider
    except ImportError:
        return []
    usable = []
    for p in LLMProvider:
        try:
            AnyLLM.get_provider_class(p)
        except Exception:
            continue
        usable.append(p.value)
    return sorted(usable)


def login_codex() -> bool:
    """Log in to ChatGPT for Codex, in the browser: the address printed, the
    login waited for. Whether it succeeded."""
    try:
        from openai_codex import Codex
    except ImportError as e:
        raise _missing("codex", "OpenAI's Codex SDK") from e
    with Codex() as codex:
        login = codex.login_chatgpt()
        print(f"Open this address to log in to ChatGPT:\n{login.auth_url}")
        return bool(login.wait().success)
