"""What the model is doing while it answers (Live), the tokens and the cost
it reports, and how long such an assessment usually takes (history)."""

from helpers import ANSWER, two_files

from prosediff import assess as assess_module
from prosediff import history
from prosediff.assess import Assessment, AssessRequest, Live, assess, claude_event, live
from prosediff.diff import Options
from prosediff.pipeline import Run, execute


def test_claudes_stream_events_told_as_they_come():
    """The Anthropic API's stream events, as Claude Code passes them on:
    the tokens read, thinking, then the answer, the tokens written once
    counted, the problems marked so far."""
    heard = []
    tracked = Live(report=lambda t: heard.append(t.describe()))
    claude_event(tracked, {"type": "message_start", "message": {"usage": {"input_tokens": 1200}}})
    claude_event(tracked, {"type": "content_block_start", "content_block": {"type": "thinking"}})
    claude_event(
        tracked,
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "x" * 400}},
    )
    assert (tracked.phase, tracked.output_tokens, tracked.counted) == ("thinking", 0, False)
    assert tracked.describe() == "thinking"  # no sentence whole yet
    tracked.wrote("\n\nThe diff adds a clause. It cites Ryan, and the cla", thinking=True)
    assert tracked.describe() == "thinking: The diff adds a clause."
    tracked.wrote("ims hold up.", thinking=True)
    assert tracked.describe().endswith("It cites Ryan, and the claims hold up.")
    claude_event(tracked, {"type": "content_block_start", "content_block": {"type": "text"}})
    answer = '[{"start": "a", "problem": "b"}, {"start": "c", "problem": "d"}]'
    claude_event(
        tracked, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": answer}}
    )
    claude_event(tracked, {"type": "message_delta", "usage": {"output_tokens": 321}})
    assert tracked.input_tokens == 1200
    tracked.update(force=True)
    assert heard[-1] == "writing the answer, 321 tokens written, 2 problems marked"
    assert heard[0].startswith("thinking") or heard[0] == "sending"


def test_the_assessment_keeps_the_tokens_and_the_cost(tmp_path):
    """What the backend tells its Live ends in the assessment, and in the
    report's line under the verdict."""

    def runner(backend, system, prompt, model, effort, timeout):
        tracked = live()
        tracked.wrote(ANSWER)
        tracked.update(input_tokens=12_345, output_tokens=2_345, counted=True, cost_usd=0.31)
        return ANSWER, "fake-1"

    heard = []
    a = assess(
        "[-a-]{+b+}",
        "x",
        AssessRequest("claude"),
        runner=runner,
        report=lambda t: heard.append(t.phase),
    )
    assert (a.input_tokens, a.output_tokens, a.cost_usd) == (12_345, 2_345, 0.31)
    assert a.usage == "12,345 tokens read, 2,345 written, $0.31 at API prices"
    assert heard[0] == "sending"
    assert Assessment("claude").usage == ""


def test_how_long_it_usually_takes(tmp_path):
    """Three runs alike give an estimate; a failed run, another AI or
    effort, or another kind do not count."""
    a = Assessment("claude", effort="high", kind="value", seconds=150)
    assert history.estimate("claude", "high", "value") is None
    for seconds in (150, 200, 240):
        a.seconds = seconds
        history.record(a, 10_000)
    history.record(Assessment("claude", effort="high", seconds=900, error="no login"), 10_000)
    history.record(Assessment("claude", effort="low", seconds=20), 10_000)
    assert history.estimate("claude", "high", "value") == (150, 240)
    assert history.usually((230, 250)) == "usually about 4 minutes"
    assert history.usually((100, 260)) == "usually 2–4 minutes"
    assert history.usually((32, 61)) == "usually 30–60 seconds"
    assert history.usually(None) == ""


def test_the_run_says_the_estimate_and_what_the_model_does(tmp_path, monkeypatch):
    """Asking the AI, the stage says how long it usually takes; live hears
    what the model is doing."""

    def runner(backend, system, prompt, model, effort, timeout):
        live().update(force=True, phase="thinking")
        return ANSWER, "fake-1"

    monkeypatch.setattr(assess_module, "run_backend", runner)
    old, new = two_files(tmp_path, "The cat sat.\n", "The cat sat down.\n")
    for _ in range(3):
        history.record(Assessment("claude", kind="value", seconds=150), 1_000)
    stages, heard = [], []
    run = Run(
        "files",
        str(old),
        tmp_path / "out.html",
        new=str(new),
        options=Options(),
        request=AssessRequest("claude"),
    )
    execute(run, stages.append, live=lambda detail, news: heard.append(detail))
    assert "Asking claude to assess the changes (usually about 2 minutes)…" in stages
    assert "thinking" in heard


def test_the_progress_log_tells_each_thing_once():
    """news gives what happened since it was last asked: the phase moved to,
    each whole sentence of the thinking, every 500 tokens, each problem."""
    tracked = Live()
    tracked.update(phase="thinking")
    tracked.wrote("Reading the diff. The claim is unsupp", thinking=True)
    assert tracked.news() == ["Thinking", "  Reading the diff."]
    tracked.wrote("orted here.", thinking=True)
    assert tracked.news() == ["  The claim is unsupported here."]
    tracked.wrote('[{"problem": "a"}, {"problem": "b"}' + "x" * 2_000)
    assert tracked.news() == [
        "Writing the answer",
        "  about 508 tokens written",
        "  problem 1 marked",
        "  problem 2 marked",
    ]
    assert tracked.news() == []
