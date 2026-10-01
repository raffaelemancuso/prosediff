"""The AI's assessment of the changes: the backends asked, what they are sent,
and the report and file it goes to. A fake backend stands in for the
models, so no test needs one, but for one with a real local model."""

import pytest
from helpers import two_files

from prosediff import assess as assess_module
from prosediff import compare_paths, render
from prosediff.assess import (
    ANNOTATE,
    MAX_DIFF_CHARS,
    SYSTEM,
    Annotation,
    AssessError,
    Assessment,
    AssessRequest,
    ModelInfo,
    assess,
    cut,
    instructions_from,
    parse_backend,
    split_annotations,
)
from prosediff.cli import main
from prosediff.render import assess_comparison, new_version

ANSWER = """## Verdict
**Improves**: the introduction is tighter.

## What changed
- The second sentence was cut.

## Improvements
- Shorter.

## Problems to fix
1. "a claim" is no longer supported.
"""


def fake(answer: str = ANSWER, model: str = "fake-1"):
    """A backend answering answer, recording what it was asked."""
    asked = []

    def runner(backend, system, prompt, model_asked, effort, timeout):
        asked.append((backend, system, prompt, model_asked, effort, timeout))
        return answer, model

    runner.asked = asked
    return runner


def test_backends_parsed():
    assert parse_backend("claude") == ("claude", "")
    assert parse_backend("Claude/opus") == ("claude", "opus")
    assert parse_backend("codex") == ("codex", "")
    assert parse_backend("ollama/qwen3:8b") == ("ollama", "qwen3:8b")
    with pytest.raises(AssessError, match="ollama/MODEL"):
        parse_backend("ollama")
    with pytest.raises(AssessError, match="say which AI"):
        parse_backend(" ")


def test_the_model_is_sent_what_the_request_says(tmp_path):
    """The model gets the instructions, the word diff of the changed lines,
    the whole new version when asked, and the effort; its answer is the
    assessment, verdict read."""
    old, new = two_files(tmp_path, "One line.\n\nKept.\n", "One changed line.\n\nKept.\n")
    c = compare_paths(old, new)
    runner = fake()
    request = AssessRequest("claude/opus", effort="high", context="changes", timeout=60)
    a = assess_comparison_with(c, request, runner)
    ((backend, system, prompt, model, effort, timeout),) = runner.asked
    # the problems to be marked in the text, by default
    assert system == SYSTEM + ANNOTATE
    assert (backend, model, effort, timeout) == ("claude", "opus", "high", 60)
    assert "One {+changed +}line." in prompt and "Kept." not in prompt
    assert "<document>" not in prompt and "<instructions>" not in prompt
    assert (a.backend, a.model, a.error, a.verdict) == ("claude/opus", "fake-1", "", "improves")
    assert a.how == "effort high, from the changes only"
    # the whole new version, by default
    runner = fake()
    assess_comparison_with(c, AssessRequest("codex"), runner)
    prompt = runner.asked[0][2]
    assert "<document>\nOne changed line.\n\nKept.\n</document>" in prompt
    assert prompt.index("<document>") < prompt.index("<diff>")


def assess_comparison_with(c, request, runner):
    from prosediff.unified import unified

    document = new_version(c) if request.context == "document" else ""
    return assess(unified(c, 0, "wdiff"), c.repo_name, request, document, runner)


MARKED = """## Verdict
**Mixed**: tighter, but a sentence is broken.

## Problems to fix
1. A broken sentence.

```json
[
 {"side": "new", "start": "One changed", "end": "line.", "problem": "Vague.",
  "solution": "Say what changed.", "replacement": "One line, reworded."},
 {"side": "old", "start": "One line.", "end": "One line.", "problem": "Lost.",
  "replacement": "An old passage: none kept."},
 {"side": "new", "start": "", "problem": "No words: left out."},
 "not an object"
]
```
"""


def test_the_problems_marked_in_the_text(tmp_path):
    """With annotate (the default) the model is asked to mark the problems
    in the text; its JSON list is taken out of the assessment, each item
    read, one without its words left out, a replacement only for the new
    version; without annotate, nothing is asked, nor taken out."""
    text, notes = split_annotations(MARKED)
    assert text.endswith("1. A broken sentence.") and "```" not in text
    assert notes == [
        Annotation(
            "new", "One changed", "line.", "Vague.", "Say what changed.", "One line, reworded."
        ),
        Annotation("old", "One line.", "One line.", "Lost.", ""),
    ]
    assert split_annotations("## Verdict\nNo list.") == ("## Verdict\nNo list.", [])
    assert split_annotations("```json\n[not json\n```") == ("```json\n[not json\n```", [])
    old, new = two_files(tmp_path, "One line.\n", "One changed line.\n")
    c = compare_paths(old, new)
    a = assess_comparison_with(c, AssessRequest("claude"), fake(MARKED))
    assert [n.start for n in a.annotations] == ["One changed", "One line."]
    assert "```" not in a.markdown
    runner = fake(MARKED)
    plain = assess_comparison_with(c, AssessRequest("claude", annotate=False), runner)
    assert runner.asked[0][1] == SYSTEM and plain.annotations == []
    assert "```json" in plain.markdown


def test_instructions_given_or_read_from_a_file(tmp_path):
    """The instructions of the person asking follow the diff, as text or
    from the file they name."""
    notes = tmp_path / "notes.txt"
    notes.write_text("The journal is Research Policy.\n", encoding="utf-8")
    assert instructions_from(str(notes)) == "The journal is Research Policy."
    assert instructions_from("Cut the introduction.") == "Cut the introduction."
    runner = fake()
    assess("[-a-]{+b+}", "x", AssessRequest("claude", instructions=str(notes)), runner=runner)
    prompt = runner.asked[0][2]
    assert "<instructions>\nThe journal is Research Policy.\n</instructions>" in prompt
    assert prompt.index("<diff>") < prompt.index("<instructions>")


def test_the_new_version_of_every_file(tmp_path):
    """The whole new version: each changed file's lines, comments written
    out, headed by its path when there are several."""
    for side, text in ((tmp_path / "old", "One.\n"), (tmp_path / "new", "Two.\n")):
        side.mkdir()
        (side / "a.md").write_text(text, encoding="utf-8")
        (side / "b.md").write_text(text + "\nMore.\n", encoding="utf-8")
    text = new_version(compare_paths(tmp_path / "old", tmp_path / "new"))
    assert text == "=== a.md ===\n\nTwo.\n\n=== b.md ===\n\nTwo.\n\nMore."
    note = '[Why?]{.comment-start id="1" author="A" date="2026-09-23T23:40:00Z"}'
    old, new = two_files(tmp_path, "One.\n", f"Two.{note}\n")
    assert new_version(compare_paths(old, new)) == "Two.{>>A (2026-09-23 23:40): Why?<<}"


def test_verdicts_read():
    assert Assessment("x", "## Verdict\n**Worsens**: less clear.").verdict == "worsens"
    assert Assessment("x", "## Verdict\nMixed. Some good, some bad.").verdict == "mixed"
    assert Assessment("x", "## Verdict\nHard to say.").verdict == ""


def test_a_failure_is_the_assessment_s_error():
    """A backend that fails, a wrong backend, or no changes: an assessment
    holding the error, never an exception."""

    def failing(*_):
        raise AssessError("no login")

    assert assess("diff", "x", AssessRequest("codex"), runner=failing).error == "no login"
    assert "ollama/MODEL" in assess("diff", "x", AssessRequest("ollama"), runner=fake()).error
    assert "no changes" in assess("  \n", "x", AssessRequest("claude"), runner=fake()).error
    assert "no answer" in assess("diff", "x", AssessRequest("claude"), runner=fake("")).error


def test_a_missing_extra_says_which_to_install(monkeypatch):
    """A backend whose package is not installed names the extra to add,
    asked to assess or to list its models."""
    import builtins

    real = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name.split(".")[0] in ("claude_agent_sdk", "openai_codex", "any_llm"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    for spec, extra in (("claude", "claude"), ("codex", "codex"), ("ollama/qwen3", "models")):
        error = assess("diff", "x", AssessRequest(spec)).error
        assert f"prosediff[{extra}]" in error, error
        with pytest.raises(AssessError, match=rf"prosediff\[{extra}\]"):
            assess_module.models_of(spec.split("/")[0])


def test_a_long_text_is_cut_and_the_model_told():
    long = "\n".join("x" * 99 for _ in range(MAX_DIFF_CHARS // 50))
    sent = cut(long, MAX_DIFF_CHARS, "diff")
    assert len(sent) < MAX_DIFF_CHARS + 200
    assert sent.endswith("more characters left out.]")
    assert cut("short", MAX_DIFF_CHARS, "diff") == "short"


def test_the_report_holds_the_assessment(tmp_path):
    """The assessment is a drawer of the HTML report, opened from its verdict
    in the top bar, its Markdown rendered and its HTML escaped, its verdict a
    badge, how it was asked said; the problems it marked held for the
    script to find in the text (and card in the margin), the top bar
    stepping through them; the text sent to the AI at the end, when asked
    for; a failed one says why."""
    old, new = two_files(tmp_path, "One line.\n", "One changed line.\n")
    c = compare_paths(old, new)
    a = Assessment("ollama/qwen3", ANSWER + "\n<script>x</script>\n", "qwen3", 42.4)
    html = render(c, assessment=a)
    assert '<aside class="drawer" id="assessment"' in html
    assert 'data-drawer="assessment"' in html
    assert "Text sent to the AI" not in html
    assert '<span class="verdict verdict-improves">Improves</span>' in html
    assert "by ollama/qwen3 (qwen3), in 42 seconds" in html
    assert "<h2>Problems to fix</h2>" in html and "<ol>" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    # no marks: no data, no arrows, no margin
    assert 'id="ai-notes-data"' not in html and 'data-ai-nav="1"' not in html
    assert 'class="notes"' not in html
    a.effort, a.context = "max", "document"
    a.annotations = split_annotations(MARKED)[1]
    html = render(c, assessment=a)
    assert (
        "by ollama/qwen3 (qwen3), effort max, from the changes and the new version, in 42 seconds"
        in (html)
    )
    assert "⚠ 2 problems" in html and '<td class="notes"></td>' in html
    assert "It marked 2 problems in the text" in html
    assert '<script type="application/json" id="ai-notes-data">' in html
    assert 'data-ai-nav="1"' in html
    assert "Say what changed." in html
    a.system, a.prompt, a.save_prompt = "Be a reviewer.", "The <changes>.", True
    html = render(c, assessment=a)
    assert '<details class="prompt-sent">' in html
    assert "===== system prompt =====\nBe a reviewer.\n" in html
    assert "The &lt;changes&gt;." in html
    failed = render(c, assessment=Assessment("codex", error="no login"))
    assert "The assessment failed: no login" in failed
    assert 'id="assessment"' not in render(c)


def test_cli_assess_writes_the_report(tmp_path, monkeypatch, capsys):
    """--assess puts the assessment in the report, and nothing beside it,
    asked as --assess-effort, --assess-context and --assess-instructions say;
    --assess-save-prompt puts the exact text sent at its end, none without
    it; a failure still writes the report, and the text sent, and says so; a
    .diff has no place for it."""
    old, new = two_files(tmp_path, "One line.\n", "One changed line.\n")
    runner = fake(model="claude-opus-5-5")
    monkeypatch.setattr(assess_module, "run_backend", runner)
    out = tmp_path / "r.html"
    args = ["--files", str(old), str(new), "-o", str(out), "--assess", "claude"]
    asked = ["--assess-effort", "max", "--assess-instructions", "Be brief."]
    assert main([*args, *asked, "--assess-save-prompt"]) == 0
    ((_, _, prompt, _, effort, _),) = runner.asked
    assert effort == "max" and "<document>" in prompt and "Be brief." in prompt
    html = out.read_text(encoding="utf-8")
    assert "verdict-improves" in html
    assert "by Claude Code (claude-opus-5-5), effort max, from the changes and the new" in html
    assert "The text prosediff sent to the model (claude, effort max, from the" in html
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["a.md", "b.md", "r.html"]
    assert "assessment (improves), at the top of the HTML report" in capsys.readouterr().out
    assert main([*args, "--assess-context", "changes"]) == 0
    assert "<document>" not in runner.asked[1][2]
    assert "Text sent to the AI" not in out.read_text(encoding="utf-8")

    def failing(*_):
        raise AssessError("no login")

    monkeypatch.setattr(assess_module, "run_backend", failing)
    assert main([*args, "--assess-save-prompt"]) == 0
    html = out.read_text(encoding="utf-8")
    assert "The assessment failed: no login" in html
    assert "===== message =====\nThe whole new version of a.md" in html
    assert "the assessment failed: no login" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([*args, "--format", "diff"])
    assert "--assess goes in the HTML report" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args,message",
    [
        (["--assess", "ollama"], "--assess: 'ollama': give the model too"),
        (["--assess-effort", "high"], "go with --assess"),
        (["--assess-ai-writing"], "go with --assess"),
    ],
)
def test_cli_refuses_what_makes_no_sense(tmp_path, capsys, args, message):
    old, new = two_files(tmp_path, "a\n", "b\n")
    with pytest.raises(SystemExit):
        main(["--files", str(old), str(new), *args])
    assert message in capsys.readouterr().err


def test_cli_lists_the_models_an_ai_reports(monkeypatch, capsys):
    """--list-models prints the models the AI reports, their efforts, the
    model's default one starred."""
    found = [
        ModelInfo(
            "gpt-6-astra", "GPT-6-Astra, Codex's default", (("low", ""), ("high", "")), "low"
        ),
        ModelInfo("gpt-5.5", "GPT-5.5"),
    ]
    monkeypatch.setattr("prosediff.cli.models_of", lambda ai: found)
    assert main(["--list-models", "codex"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "gpt-6-astra  GPT-6-Astra, Codex's default",
        "             effort: low*, high",
        "gpt-5.5      GPT-5.5",
        "(*: the model's default effort)",
    ]

    def failing(ai):
        raise AssessError("no key")

    monkeypatch.setattr("prosediff.cli.models_of", failing)
    assert main(["--list-models", "openai"]) == 1
    assert "no key" in capsys.readouterr().err


# A local model small enough to answer in seconds on any computer (292 MB):
# the test checks the way to a real model and back, not the quality of its
# reading, which a model this size cannot give.
TEST_MODEL = "gemma3:270m"


@pytest.mark.timeout(300)
def test_a_real_local_model_assesses(tmp_path):
    """A real Ollama model, reached through any-llm, reports itself and
    answers; skipped when any-llm, Ollama or the model is missing."""
    pytest.importorskip("any_llm")
    try:
        names = [m.name for m in assess_module.models_of("ollama", timeout=10)]
    except AssessError as e:
        pytest.skip(f"Ollama is needed: {e}")
    if TEST_MODEL not in names:
        pytest.skip(f"Ollama with {TEST_MODEL} is needed: ollama pull {TEST_MODEL}")
    old, new = two_files(tmp_path, "The cat sat on the mat.\n", "The cat sat on the red mat.\n")
    request = AssessRequest(f"ollama/{TEST_MODEL}", timeout=240)
    a = assess_comparison(compare_paths(old, new), request)
    assert a.error == "", a.error
    assert a.markdown.strip() and a.model
    assert a.seconds > 0


def test_word_files_come_with_the_notation_explained(tmp_path, monkeypatch):
    """Comparing Word documents, the model is told that # and ** are the
    tool's notation for their styles, not text of theirs to report as
    changed; comparing text files, it is not."""
    import docx

    from prosediff.assess import DOCUMENTS

    runner = fake()
    monkeypatch.setattr(assess_module, "run_backend", runner)
    for name, title in (("old", "Results"), ("new", "Main results")):
        d = docx.Document()
        d.add_heading(title, level=2)
        d.save(tmp_path / f"{name}.docx")
    c = compare_paths(tmp_path / "old.docx", tmp_path / "new.docx")
    assess_comparison(c, AssessRequest("claude", annotate=False))
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    assess_comparison(compare_paths(old, new), AssessRequest("claude", annotate=False))
    (word_system, text_system) = [asked[1] for asked in runner.asked]
    assert word_system == SYSTEM + DOCUMENTS and "Heading 2 style" in DOCUMENTS
    assert text_system == SYSTEM


def test_the_ai_is_asked_apart_whether_the_new_text_reads_as_ai_written(tmp_path, monkeypatch):
    """Asked apart, a second assessment: its own instructions, which say
    such a judgement is circumstantial, no problem marked in the text, its
    verdict likely, possibly or unlikely; in the report, a chip of its own
    in the top bar, opening its drawer, which says it is no proof."""
    from prosediff.assess import SYSTEM_WRITING

    answer = "## Verdict\n**Possibly**: stock phrases.\n\n## Signs of AI writing\n- x"
    runner = fake(answer)
    monkeypatch.setattr(assess_module, "run_backend", runner)
    old, new = two_files(tmp_path, "One line.\n", "One pivotal line.\n")
    c = compare_paths(old, new)
    request = AssessRequest("claude")
    writing = assess_comparison(c, request, kind="writing")
    ((_, system, prompt, *_),) = runner.asked
    assert system.startswith(SYSTEM_WRITING) and "circumstantial" in SYSTEM_WRITING
    assert "One {+pivotal +}line." in prompt
    assert writing.kind == "writing" and writing.verdict == "possibly"
    assert writing.annotations == []
    html = render(c, assessment=Assessment("claude", ANSWER), writing=writing)
    assert 'data-drawer="writing"' in html and '<aside class="drawer" id="writing"' in html
    assert '<span class="verdict verdict-possibly">Possibly</span>' in html
    assert "an indication, not a proof" in html
