"""The AI's answers kept beside the report (NAME.ai.json), and the report
made again from them without asking the AI (--rebuild; the tab Rebuild)."""

import json
import queue

import pytest
from helpers import ANSWER, fake, two_files

from prosediff import assess as assess_module
from prosediff.assess import AssessRequest
from prosediff.cli import main
from prosediff.diff import Options
from prosediff.gui import Settings, rebuild_run
from prosediff.pipeline import Run, execute
from prosediff.saved import SavedError, load, saved_path


def assessed(tmp_path, monkeypatch):
    """A comparison the AI assessed (a stand-in), its report and answers
    written."""
    monkeypatch.setattr(assess_module, "run_backend", fake(ANSWER))
    old, new = two_files(tmp_path, "The cat sat.\n", "The cat sat down.\n")
    run = Run(
        "files",
        str(old),
        tmp_path / "report.html",
        new=str(new),
        options=Options(),
        request=AssessRequest("claude", effort="high", instructions="Be brief."),
    )
    return run, execute(run), new


def test_the_answers_kept_and_the_report_made_again_without_the_ai(tmp_path, monkeypatch):
    run, done, _ = assessed(tmp_path, monkeypatch)
    kept = saved_path(done.path)
    assert done.saved == kept and kept.name == "report.ai.json"
    data = json.loads(kept.read_text(encoding="utf-8"))
    assert data["assessment"]["markdown"].startswith("## Verdict")
    assert data["run"]["request"]["instructions"] == "Be brief."

    def no_ai(*args, **kwargs):
        pytest.fail("the AI was asked again")

    monkeypatch.setattr(assess_module, "run_backend", no_ai)
    again, assessment, writing, warnings = load(kept)
    assert (again.mode, again.request.effort, warnings, writing) == ("files", "high", [], None)
    assert again.options == run.options
    done.path.unlink()
    remade = execute(again, saved=(assessment, writing))
    page = remade.path.read_text(encoding="utf-8")
    assert "verdict-improves" in page and remade.saved is None


def test_a_file_changed_since_is_warned_of(tmp_path, monkeypatch):
    _, done, new = assessed(tmp_path, monkeypatch)
    new.write_text("The dog sat down.\n", encoding="utf-8")
    *_, warnings = load(done.saved)
    assert len(warnings) == 1 and "changed since the AI read it" in warnings[0]
    bad = tmp_path / "bad.ai.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(SavedError, match=r"bad\.ai\.json"):
        load(bad)


def test_rebuilt_from_the_command_line_and_the_window(tmp_path, monkeypatch, capsys):
    _, done, _ = assessed(tmp_path, monkeypatch)
    monkeypatch.setattr(assess_module, "run_backend", lambda *a: pytest.fail("asked"))
    out = tmp_path / "again.html"
    assert main(["--rebuild", str(done.saved), "-o", str(out)]) == 0
    assert "verdict-improves" in out.read_text(encoding="utf-8")
    assert "report made again from report.ai.json" in capsys.readouterr().out
    run, saved = rebuild_run(Settings(mode="rebuild", rebuild_file=str(done.saved)), queue.Queue())
    assert run.output == done.path.resolve() and saved[0].markdown.startswith("## Verdict")
    with pytest.raises(SystemExit):
        main(["--rebuild"])
