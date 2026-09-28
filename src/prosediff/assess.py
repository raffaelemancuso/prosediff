"""An AI's assessment of the changes, taken as a whole: whether the new
version is better than the old, what changed, and what to fix.

The changes go to the model as the word diff of the comparison
([-removed-]{+added+}, comments in CriticMarkup), with, by default, the
whole new version, for the model to check the changes against the rest of
the document. Three kinds of backend answer, each an optional extra of
prosediff:

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
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
# What the model reads: the whole new version and the changes, or the
# changes alone.
CONTEXTS = ("document", "changes")

SYSTEM = """\
You are an experienced editor and peer reviewer of academic and professional \
writing. You are given the changes between two versions of a document as a \
word diff: [-text-] was removed, {+text+} was added, and {>>Author (date): \
text<<} is a reviewer's comment; lines that begin with @@ say where a change \
sits. Unchanged paragraphs are mostly left out of the diff; when the whole \
new version is given too, check the changes against the rest of it.

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

## Problems to fix
A numbered list, most serious first, each quoting the words concerned. \
Write "None found." if there are none.

Be specific and brief. Write in the language the document is written in, \
unless the instructions of the person asking say otherwise."""


class AssessError(RuntimeError):
    """The assessment could not be made: a backend missing, a login, a
    model, a timeout."""


@dataclass(frozen=True)
class AssessRequest:
    """What to ask an AI: which (spec: "claude", "claude/opus",
    "ollama/qwen3"), how hard to think (effort: one the model supports, ""
    for its default), what it reads (context: one of CONTEXTS), the
    instructions of the person asking (added to the prompt), and how long
    it may take."""

    spec: str
    effort: str = ""
    context: str = "document"
    instructions: str = ""
    timeout: float = ASSESS_TIMEOUT


@dataclass
class Assessment:
    """What a model made of the changes."""

    backend: str  # as asked: "claude", "codex/gpt-5.5", "ollama/qwen3"
    markdown: str = ""
    model: str = ""  # the model that answered, when the backend says
    seconds: float = 0.0
    error: str = ""  # why there is none
    effort: str = ""
    context: str = ""

    @property
    def verdict(self) -> str:
        """ "improves", "mixed" or "worsens", from the Verdict section; "" when
        the model gave none of them."""
        section = re.search(r"#+\s*Verdict\s*\n(.*?)(?=\n#+\s|\Z)", self.markdown, re.S | re.I)
        words = re.findall(r"[A-Za-z]+", section[1] if section else self.markdown[:200])
        return next((w.lower() for w in words if w.lower() in VERDICTS), "")

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

    def as_markdown(self, subject: str) -> str:
        """The assessment as a Markdown file of its own, subject its title."""
        head = f"# AI assessment of {subject}\n\nBy {self.title}"
        if self.how:
            head += f", {self.how}"
        if self.seconds:
            head += f", in {self.seconds:,.0f} s"
        body = f"Failed: {self.error}" if self.error else self.markdown.strip()
        return f"{head}.\n\n{body}\n"


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


def instructions_from(value: str) -> str:
    """The instructions given: the text itself, or the contents of the file
    it names (UTF-8)."""
    value = value.strip()
    if value and len(value) < 1_000 and "\n" not in value:
        path = Path(value).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return value


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
    if instructions.strip():
        parts.append(
            "The instructions of the person asking for this assessment, to follow "
            f"within the format above:\n\n<instructions>\n{instructions.strip()}\n</instructions>"
        )
    parts.append("Assess the changes as the instructions say.")
    return "\n\n".join(parts)


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
            result = await thread.run(prompt, effort=ReasoningEffort(effort) if effort else None)
        if result.error is not None:
            raise AssessError(f"Codex: {getattr(result.error, 'message', result.error)}")
        return result.final_response or "", model

    with tempfile.TemporaryDirectory() as cwd:
        try:
            return _within(run(cwd), timeout)
        except AssessError:
            raise
        except ValueError as e:  # an effort Codex does not know
            raise AssessError(f"Codex: {e}") from e
        except Exception as e:  # the SDK's errors: no login, no binary, a refusal
            raise AssessError(f"Codex: {e} (log in to ChatGPT with prosediff --login-codex)") from e


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
) -> Assessment:
    """An assessment of the changes of a word diff by the AI request names;
    subject names what changed ("paper.docx"), document is the whole new
    version, sent when request.context is "document"; runner answers in
    place of the models (run_backend), for the tests. It never raises: a
    failure is its error."""
    runner = runner or run_backend
    started = time.monotonic()
    context = request.context if request.context in CONTEXTS else "document"
    if context == "changes":
        document = ""
    made = Assessment(request.spec, effort=request.effort, context=context)
    try:
        backend, model = parse_backend(request.spec)
        if not diff.strip():
            raise AssessError("there are no changes to assess")
        prompt = prompt_for(diff, subject, document, instructions_from(request.instructions))
        text, answered = runner(backend, SYSTEM, prompt, model, request.effort, request.timeout)
        if not text.strip():
            raise AssessError("the model gave no answer")
    except (AssessError, OSError) as e:
        made.error, made.seconds = str(e), time.monotonic() - started
        return made
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
        try:
            found = _within(run(cwd), timeout)
        except AssessError:
            raise
        except Exception as e:
            raise AssessError(f"Claude Code: {e}") from e
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

    try:
        found = [m for m in _within(run(), timeout) if not m.hidden]
    except AssessError:
        raise
    except Exception as e:
        raise AssessError(f"Codex: {e}") from e
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
