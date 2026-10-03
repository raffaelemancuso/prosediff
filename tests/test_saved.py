"""The AI's answers kept beside the report (NAME.ai.json), and the report
made again from them without asking the AI (--rebuild; the tab Rebuild)."""

import json
import queue
from pathlib import Path

import pytest
from helpers import ANSWER, fake, two_files

from prosediff import assess as assess_module
from prosediff.assess import AssessRequest
from prosediff.cli import main
from prosediff.diff import Options
from prosediff.gui import Settings, rebuild_run
from prosediff.pipeline import Run, execute
from prosediff.saved import SavedError, load, saved_path


def assessed(tmp_path, monkeypatch, files=()):
    """A comparison the AI assessed (a stand-in), its report and answers
    written; files, other files sent to it as context."""
    monkeypatch.setattr(assess_module, "run_backend", fake(ANSWER))
    old, new = two_files(tmp_path, "The cat sat.\n", "The cat sat down.\n")
    run = Run(
        "files",
        str(old),
        tmp_path / "report.html",
        new=str(new),
        options=Options(),
        request=AssessRequest(
            "claude", effort="high", instructions="Be brief.", files=tuple(map(str, files))
        ),
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
    again, assessment, writing = load(kept)
    assert (again.mode, again.request.effort, writing) == ("files", "high", None)
    assert again.options == run.options
    done.path.unlink()
    remade = execute(again, saved=(assessment, writing))
    page = remade.path.read_text(encoding="utf-8")
    assert "verdict-improves" in page and remade.saved is None


def test_a_file_changed_since_stops_the_rebuild(tmp_path, monkeypatch, capsys):
    """The answers hold each file's checksum: a file changed since the AI
    read it, or gone, and the report is not made again, from the command
    line or the window; the earlier report left as it was."""
    _, done, new = assessed(tmp_path, monkeypatch)
    before = done.path.read_bytes()
    new.write_text("The dog sat down.\n", encoding="utf-8")
    with pytest.raises(SavedError, match="changed since the AI read it"):
        load(done.saved)
    assert main(["--rebuild", str(done.saved)]) != 0
    assert "changed since the AI read it" in capsys.readouterr().err
    with pytest.raises(SavedError, match="changed since"):
        rebuild_run(Settings(mode="rebuild", rebuild_file=str(done.saved)), queue.Queue())
    assert done.path.read_bytes() == before
    new.unlink()
    with pytest.raises(SavedError, match="is gone"):
        load(done.saved)
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


def test_a_context_file_changed_since_stops_the_rebuild(tmp_path, monkeypatch):
    """The checksums held are those of the context files sent to the AI
    too: one changed since, and the report is not made again."""
    guide = tmp_path / "guidelines.md"
    guide.write_text("Be concise.\n", encoding="utf-8")
    _, done, _ = assessed(tmp_path, monkeypatch, files=[guide])
    inputs = json.loads(done.saved.read_text(encoding="utf-8"))["inputs"]
    assert str(guide.resolve()) in inputs and len(inputs) == 3  # the two files, the guide
    load(done.saved)
    guide.write_text("Be long.\n", encoding="utf-8")
    with pytest.raises(SavedError, match=r"guidelines\.md changed since the AI read it"):
        load(done.saved)


def test_the_files_fingerprinted_are_those_read(tmp_path):
    """Two folders: each of their files the comparison reads; a file
    edited after the run began is not the one the AI read, so the
    fingerprints are taken as it begins (pipeline.execute)."""
    from prosediff.saved import fingerprints

    for side, text in (("a", "One.\n"), ("b", "Two.\n")):
        (tmp_path / side).mkdir()
        (tmp_path / side / "p.md").write_text(text, encoding="utf-8")
        (tmp_path / side / "skip.bin").write_bytes(b"\x00")
    run = Run("folders", str(tmp_path / "a"), tmp_path / "r.html", new=str(tmp_path / "b"))
    assert sorted(Path(p).relative_to(tmp_path).as_posix() for p in fingerprints(run)) == [
        "a/p.md",
        "b/p.md",
    ]
    assert fingerprints(Run("git", str(tmp_path), tmp_path / "r.html")) == {}


def test_a_project_holds_the_settings_and_the_report_to_make_again(tmp_path, monkeypatch, capsys):
    """A project keeps the window's settings and the AI's answers of a
    report: made again from it, from the command line too, as long as the
    files it read are unchanged; one holding no report is said so, and a
    file that is no project refused."""
    from prosediff.saved import answers, load_project, save_project

    _, done, new = assessed(tmp_path, monkeypatch)
    project = tmp_path / "paper.prosediff"
    save_project(project, {"mode": "files", "old": "a.md"}, answers(done.saved))
    settings, report = load_project(project)
    assert settings == {"mode": "files", "old": "a.md"}
    assert report["assessment"]["markdown"].startswith("## Verdict")
    monkeypatch.setattr(assess_module, "run_backend", lambda *a: pytest.fail("asked"))
    run, assessment, _ = load(project)
    assert run.new == str(new.resolve()) and assessment.verdict == "improves"
    out = tmp_path / "again.html"
    assert main(["--rebuild", str(project), "-o", str(out)]) == 0
    assert "verdict-improves" in out.read_text(encoding="utf-8")
    new.write_text("The cat ran.\n", encoding="utf-8")
    assert main(["--rebuild", str(project), "-o", str(out)]) != 0
    assert "changed since the AI read it" in capsys.readouterr().err
    empty = tmp_path / "empty.prosediff"
    save_project(empty, {}, None)
    with pytest.raises(SavedError, match="holds no report"):
        load(empty)
    with pytest.raises(SavedError, match="not a prosediff project"):
        load_project(done.saved)


def test_answers_an_earlier_prosediff_saved_are_refused(tmp_path, monkeypatch):
    """Answers without the current format (an earlier prosediff's, whose
    checksums left out the context files) are not made again, nor put in
    a project."""
    from prosediff.saved import SAVED_FORMAT, answers, save_project

    _, done, _ = assessed(tmp_path, monkeypatch)
    data = json.loads(done.saved.read_text(encoding="utf-8"))
    assert data["format"] == SAVED_FORMAT
    del data["format"]
    done.saved.write_text(json.dumps(data), encoding="utf-8")
    for read in (load, answers):
        with pytest.raises(SavedError, match="saved by an earlier prosediff"):
            read(done.saved)
    project = tmp_path / "old.prosediff"
    save_project(project, {}, data)
    with pytest.raises(SavedError, match="saved by an earlier prosediff"):
        load(project)
