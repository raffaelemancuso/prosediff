"""The window: choosing the sides, generating the HTML report, remembering
choices. The Open screen's rules are App's, tested here without a window:
App is given a screen that records what it is asked to show (Screen)."""

import json
import queue
import re
import threading
import time
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from helpers import FIXED, NEW, assessment, pair, two_files, word_file

from prosediff import gui, pipeline
from prosediff.assess import (
    SYSTEM,
    SYSTEM_REVIEW,
    SYSTEM_WRITING,
    SYSTEM_WRITING_REVIEW,
    AssessError,
    Assessment,
    ModelInfo,
)
from prosediff.diff import MOVED_PASSAGE_DEFAULTS, MovedPassageSettings
from prosediff.gui import (
    INDEX,
    PASSAGE,
    WORKTREE,
    App,
    Settings,
    default_sides,
    list_choices,
    load_settings,
    run_of,
    save_settings,
    settings_from_args,
)
from prosediff.pipeline import execute


def test_no_second_console_when_there_is_one(monkeypatch):
    """The invisible console is made only for a program with none (pythonw,
    prosediff-gui.exe): made once at most, and never away from Windows."""
    gui.invisible_console()
    assert gui.invisible_console() is False
    monkeypatch.setattr(gui.sys, "platform", "linux")
    assert gui.invisible_console() is False


def test_arguments_prefill_a_repository(history):
    b, _ = history
    remembered = Settings(mode="files", repo="elsewhere", base="abc", target="def", paths=["x"])
    s, note = settings_from_args([str(b.path)], remembered)
    assert note == "" and s.mode == "git" and s.repo == str(b.path)
    assert (s.base, s.target, s.paths) == ("", "", [])  # the sides start from the defaults
    # a folder inside the repository names the repository
    s, _ = settings_from_args([str(b.path / "sub")], remembered)
    assert s.repo == str(b.path)
    assert remembered.repo == "elsewhere"  # the remembered settings are not touched


@pytest.mark.parametrize("mode", ["files", "folders"])
def test_arguments_prefill_two_files_or_folders(tmp_path, mode):
    """Two files fill in the files tab, two folders the folders tab; the
    output is the default, next to the new side (App.follow_sides)."""
    if mode == "files":
        a, b = tmp_path / "v1.md", tmp_path / "v2.DOCX"
        a.write_text("x")
        b.write_bytes(b"x")
    else:
        a, b = tmp_path / "submitted", tmp_path / "revised"
        a.mkdir()
        b.mkdir()
    s, note = settings_from_args([str(a), str(b)], Settings(mode="git", output="elsewhere.html"))
    sides = (s.old, s.new) if mode == "files" else (s.old_folder, s.new_folder)
    assert note == "" and s.mode == mode and sides == (str(a), str(b)) and s.output == ""


@pytest.mark.parametrize(
    "args,message",
    [
        (["plain"], "Not a folder"),
        (["x.txt", "y.txt"], "two Markdown, Word or OpenDocument files"),
        (["", "v1.md"], "or two folders"),  # a folder and a file
        (["a", "b", "c"], "Give one git repository"),
        ([""], "Not a git repository"),  # tmp_path itself
    ],
)
def test_unusable_arguments_are_ignored(tmp_path, args, message):
    """Arguments that name nothing usable are set aside with a note saying
    why, the remembered settings kept."""
    s, note = settings_from_args([str(tmp_path / a) for a in args], Settings(repo="kept"))
    assert message in note and s.repo == "kept"


def test_one_file_fills_in_the_one_file_tab(tmp_path):
    sent = tmp_path / "draft.docx"
    sent.write_bytes(b"x")
    s, note = settings_from_args([str(sent)], Settings(mode="git", output="elsewhere.html"))
    assert note == "" and s.mode == "review" and s.single == str(sent) and s.output == ""


def test_a_json_file_fills_in_the_rebuild_tab(tmp_path):
    saved = tmp_path / "paper_review.ai.json"
    saved.write_text("{}")
    s, note = settings_from_args([str(saved)], Settings(mode="git", output="elsewhere.html"))
    assert note == "" and s.mode == "rebuild" and s.rebuild_file == str(saved) and s.output == ""


def test_folders_only_in_common(tmp_path):
    """The folders tab's switch compares only the files in both folders."""
    old, new = tmp_path / "a", tmp_path / "b"
    for d in (old, new):
        d.mkdir()
        (d / "both.md").write_text(f"{d.name}\n")
    (new / "added.md").write_text("new\n")
    s = Settings(mode="folders", old_folder=str(old), new_folder=str(new))
    s.output = str(tmp_path / "page.html")
    every = execute(run_of(s)).comparison
    assert sorted(f.path for f in every.files) == ["added.md", "both.md"]
    s.common_only = True
    assert [f.path for f in execute(run_of(s)).comparison.files] == ["both.md"]


def test_context_lines_box():
    from prosediff.gui import context_of

    assert context_of(Settings()) == "auto"
    assert context_of(Settings(context_lines="2")) == 2
    assert context_of(Settings(context_lines="nonsense")) == "auto"
    assert context_of(Settings(context_lines="2", full=True)) is None
    with pytest.raises(ValueError, match="0 or more"):
        context_of(Settings(context_lines="-2"))
    assert context_of(Settings(context_lines="-2", full=True)) is None


def test_list_choices_and_default_sides(history):
    b, shas = history
    commits, dirty = list_choices(b.path)
    assert [c.ref for c in commits] == shas[::-1] and dirty
    assert "commit 4" in commits[0].label and shas[4][:7] in commits[0].label
    assert default_sides(commits, dirty=True) == (shas[4], "worktree")
    assert default_sides(commits, dirty=False) == (shas[3], shas[4])
    assert default_sides([], dirty=False) == ("", "")


def test_generate_git(history, tmp_path):
    """The new side can be the working tree, the index or a commit."""
    b, shas = history
    for target in ("worktree", "index", shas[4]):
        out = tmp_path / f"{target}.html"
        done = execute(
            run_of(Settings(repo=str(b.path), base=shas[0], target=target, output=str(out)))
        )
        path, c = done.path, done.comparison
        assert path == out and out.read_bytes().startswith(b"<!DOCTYPE html>")
        short = {"worktree": "working tree", "index": "index"}.get(target, shas[4][:7])
        assert c.target.short == short


def test_generate_files_and_default_output(tmp_path):
    """Two files make an HTML report next to the new one by default, named after
    both, compared sentence by sentence when asked; without both files, an
    error."""
    moved = "Firms that adopted the new technology are compared with the others."
    a, b = two_files(
        tmp_path,
        f"First paragraph here. {moved}\n\nSecond paragraph.\n",
        f"First paragraph here.\n\nSecond paragraph. {moved}\n",
    )
    s = Settings(mode="files", old=str(a), new=str(b))
    done = execute(run_of(s))
    path, c = done.path, done.comparison
    assert path == tmp_path / "a_vs_b.html" and len(c.files) == 1
    assert c.counts.moved == 0
    s.split = "sentence"
    assert execute(run_of(s)).comparison.counts.moved == 1
    s.output_format = "diff"
    path = execute(run_of(s)).path
    assert path.suffix == ".diff" and path.read_text().startswith("--- a/a.md\n+++ b/b.md\n")
    s.output = str(tmp_path / "a_vs_b.html")  # the format chosen wins over the suffix
    assert execute(run_of(s)).path == tmp_path / "a_vs_b.diff"
    with pytest.raises(ValueError, match="old and the new file"):
        execute(run_of(Settings(mode="files")))
    with pytest.raises(ValueError, match="old and the new folder"):
        execute(run_of(Settings(mode="folders", old=s.old, new=s.new)))


def test_settings_are_remembered(tmp_path):
    f = tmp_path / "gui.json"
    s = Settings(mode="files", old="a", new="b", align="left", paths=["p"])
    save_settings(s, f)
    assert load_settings(f) == s
    f.write_text("{ not json")
    assert load_settings(f) == Settings()


def test_the_stages_are_told(tmp_path):
    """The run tells each stage as it starts: both comparisons, then the
    report."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    stages = []
    execute(
        run_of(Settings(mode="files", old=str(old), new=str(new), open_page=False)), stages.append
    )
    assert stages == [
        "Comparing paragraph by paragraph…",
        "Comparing sentence by sentence…",
        "Writing the report…",
    ]
    stages.clear()
    s = Settings(mode="files", old=str(old), new=str(new), output_format="wdiff")
    execute(run_of(s), stages.append)
    assert stages == ["Comparing…", "Writing the diff…"]


def test_the_ai_reads_only_an_approved_preview(tmp_path, monkeypatch):
    """With an AI chosen, the report is first written without it and the
    preview shown; the AI is asked only once it is approved."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    asked = []
    monkeypatch.setattr(
        pipeline,
        "assess_comparison",
        lambda c, request, **kw: asked.append(request) or Assessment("claude"),
    )
    s = Settings(mode="files", old=str(old), new=str(new), assess="claude", split="paragraph")
    previews = []

    def refuse(path):
        previews.append(path)
        assert path.is_file()  # written before the question
        return False

    done = execute(run_of(s), approve=refuse)
    path, assessment = done.path, done.assessment
    assert previews == [path] and asked == [] and assessment is None
    stages = []
    execute(run_of(s), stages.append, approve=lambda path: True)
    assert len(asked) == 1
    assert stages == [
        "Comparing…",
        "Writing the preview…",
        "Asking claude to assess the changes…",
        "Writing the report…",
    ]


REPORTED = {
    "claude": [
        ModelInfo("default", "Default (recommended): Opus 5.5", (("low", ""), ("max", ""))),
        ModelInfo("opus", "Opus 5.5", (("low", ""), ("max", ""))),
        ModelInfo("haiku", "Haiku 4.5"),
    ],
    "codex": [
        ModelInfo(
            "gpt-6-astra",
            "GPT-6-Astra, Codex's default",
            (("low", "Fast"), ("medium", "Balanced"), ("ultra", "Deepest")),
            "low",
        ),
        ModelInfo("gpt-5.5", "GPT-5.5", (("low", ""), ("medium", "")), "medium"),
    ],
    "ollama": [ModelInfo("gemma3:270m"), ModelInfo("qwen3:8b")],
}


def reported(ai):
    if ai not in REPORTED:
        raise AssessError(f"{ai}: no API key")
    return REPORTED[ai]


class Screen:
    """App's screen in the tests: what it was asked to show and say (each
    status shown, the progress log's lines, the alerts, the notifications,
    the reports and files opened), and what its file dialogs answer
    (opened, folder, saved), set by each test."""

    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.lines: list[str] = []
        self.shown: list[str] = []  # the alerts' texts
        self.kinds: list[str] = []  # and their kinds
        self.toasts: list[str] = []
        self.reports: list[tuple[Path, int | None]] = []
        self.files: list[Path] = []
        self.opened: list[str] = []
        self.folder = ""
        self.saved = ""
        self.title = ""

    def render(self, view: dict) -> None:
        self.statuses.append(view["text"]["status"])

    def log(self, line: str) -> None:
        self.lines.append(line)

    def clear_log(self) -> None:
        self.lines.clear()

    def alert(self, kind: str, text: str) -> None:
        self.kinds.append(kind)
        self.shown.append(text)

    def toast(self, text: str) -> None:
        self.toasts.append(text)

    def open_report(self, path: Path, serial: int | None) -> None:
        self.reports.append((path, serial))

    def open_file(self, path: Path) -> None:
        self.files.append(path)

    def ask_open(self, title: str, types: list[str], many: bool = False) -> list[str]:
        return list(self.opened)

    def ask_folder(self, title: str, start: str = "") -> str:
        return self.folder

    def ask_save(self, title: str, types: list[str], start: str = "", name: str = "", window=None):
        return self.saved

    def retitle(self, title: str) -> None:
        self.title = title


@pytest.fixture
def screen(monkeypatch):
    """A screen of its own for each test; what the window looks for in the
    background (the models an AI reports, any-llm's providers) found at
    once, without starting Claude Code or Codex or asking Ollama."""
    monkeypatch.setattr(gui, "models_of", reported)
    monkeypatch.setattr(gui, "providers", lambda: ["anthropic", "ollama", "openai"])
    return Screen()


def off(app: App, key: str) -> bool:
    """Whether the page shows key (a field, a field and its value, a
    button's id) greyed out."""
    return key in app.view()["disabled"]


def gone(app: App, key: str) -> bool:
    """Whether the page leaves key (a field, a row's or a card's id) out."""
    return key in app.view()["hidden"]


def finish(app: App, seconds: float = 60) -> None:
    """Wait for the comparison's process to end (it starts a Python of its
    own: a few seconds), and for its result to be shown."""
    for _ in range(int(seconds * 10)):
        if app.job is None:
            app.settle()
            return
        time.sleep(0.1)
    raise AssertionError("the comparison never ended")


def test_the_preview_is_asked_about_on_the_open_screen(screen, tmp_path):
    """The preview's question is a bar of the Open screen, not a dialog the
    report's window would cover: shown with the report opened, its answer
    sent to the comparison's process, gone once answered."""
    app = App(screen, Settings(mode="files", assess="claude"))
    app.job, app.job_settings = object(), app.collect()
    app.messages, app.replies = queue.Queue(), queue.Queue()
    page = tmp_path / "page.html"
    page.write_text("")
    app.preview(page)
    assert "page.html" in app.view()["text"]["preview"]
    assert screen.reports == [(page, None)]  # a preview's choices are not kept
    app.answer_preview(True)
    app.job = None  # no process to watch
    assert app.replies.get_nowait() is True and app.view()["text"]["preview"] == ""


def test_a_comparison_runs_apart_and_can_be_cancelled(screen, tmp_path):
    """Compare starts the comparison in a process of its own and becomes
    Cancel, which stops it and all it started, and becomes Compare again;
    the status line tells the stage, the report opens in a window."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    app = App(screen, Settings(mode="files", old=str(old), new=str(new)))
    app.run()
    view = app.view()
    assert view["text"]["run"] == "Cancel" and view["running"] and app.job is not None
    pid = app.job.pid
    assert app.status == "Starting…"
    app.cancel()
    view = app.view()
    assert view["text"]["run"] == "Compare" and app.job is None and not view["running"]
    assert app.status == "Cancelled."
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == "zombie"
    # and a comparison let run tells its stages, then its result
    app.run()
    finish(app)
    seen = {s.split("…")[0] + "…" for s in screen.statuses}  # the stage, its time aside
    assert "Writing the report…" in seen or "Comparing sentence by sentence…" in seen
    assert "changed" in app.status and screen.toasts
    assert [(p.name, serial) for p, serial in screen.reports] == [("a_vs_b.html", 1)]


def test_the_model_list_waits_for_the_ai_to_answer(screen, monkeypatch):
    """The AI, model and effort saved are shown at once, checked once the AI
    reports its models; an AI chosen then has its model and effort fields
    greyed out, its own default asked for, until it does."""
    answer = threading.Event()

    def slow(ai):
        answer.wait(10)
        return REPORTED[ai]

    monkeypatch.setattr(gui, "models_of", slow)
    app = App(screen, Settings(mode="files", assess="claude/opus", assess_effort="max"))
    v = app.values
    assert (v["assess_ai"], v["assess_model"], v["assess_effort"]) == ("claude", "opus", "max")
    s = app.collect()
    assert (s.assess, s.assess_effort) == ("claude/opus", "max")
    app.set("assess_ai", "codex")
    assert off(app, "assess_model") and off(app, "assess_effort")
    s = app.collect()
    assert (s.assess, s.assess_effort) == ("codex", "")
    answer.set()
    app.settle()
    assert app.values["assess_model"] == "gpt-6-astra" and not off(app, "assess_model")
    app.set("assess_ai", "claude")
    assert app.values["assess_model"] == "default" and screen.shown == []  # opus was offered


def test_the_ai_list_says_loading_until_the_ais_are_known(screen, monkeypatch):
    """With no AI saved, the AI list says Loading and cannot be used until
    the AIs are found in the background; then no AI is chosen."""
    answer = threading.Event()

    def slow():
        answer.wait(10)
        return ["openai"]

    monkeypatch.setattr(gui, "providers", slow)
    app = App(screen, Settings(mode="files"))
    assert app.values["assess_ai"] == "Loading…" and off(app, "assess_ai")
    assert app.collect().assess == "" and off(app, "assess_model")
    answer.set()
    app.settle()
    assert app.values["assess_ai"] == "none" and not off(app, "assess_ai")
    assert app.view()["lists"]["assess_ai"] == [*gui.AIS, "openai"] and screen.shown == []


def test_a_saved_ai_or_model_no_longer_offered_is_an_error(screen):
    """A saved AI no longer offered (any-llm's provider gone), or a saved
    model its AI no longer reports, is said in an error, and replaced: by no
    AI, by the AI's default model."""
    app = App(screen, Settings(mode="files", assess="mistral/large"))
    app.settle()
    assert "The AI saved, mistral, is not available" in screen.shown[0]
    assert app.values["assess_ai"] == "none" and app.collect().assess == ""
    screen.shown.clear()
    app = App(screen, Settings(mode="files", assess="claude/opus-3"))
    app.settle()
    assert screen.shown == [
        "The model saved, opus-3, is not one claude offers any more: its default, "
        "default, is chosen instead."
    ]
    assert app.values["assess_model"] == "default" and app.collect().assess == "claude"


def test_ai_model_and_effort_as_the_ai_reports(screen):
    """The AI assessment: the AI, then its model and effort among those it
    reports, each AI's own default chosen (Claude's "default", Codex's
    default model and that model's default effort), making the --assess
    spec; a saved choice comes back as it was."""
    app = App(screen, Settings(mode="files"))
    app.settle()
    lists = lambda: app.view()["lists"]  # noqa: E731
    ais = ["none", "claude", "codex", "ollama", "anthropic", "openai"]
    assert lists()["assess_ai"] == ais
    assert app.assess_spec() == "" and off(app, "assess_model")
    # the switches of the assessment, greyed out without an AI; marking on
    assert all(off(app, s) for s in gui.AI_SWITCHES)
    assert app.collect().assess_annotate is True
    app.set("assess_ai", "claude")
    app.settle()
    assert not any(off(app, s) for s in gui.AI_SWITCHES)
    assert lists()["assess_model"] == ["default", "opus", "haiku"]
    assert app.values["assess_model"] == "default" and app.assess_spec() == "claude"
    # Claude says no default effort: "default", its own, shown, not an empty box
    assert lists()["assess_effort"] == ["default", "low", "max"]
    assert app.values["assess_effort"] == "default" and app.collect().assess_effort == ""
    app.set("assess_model", "opus")
    assert app.values["assess_effort"] == "default"
    app.set("assess_effort", "max")
    s = app.collect()
    assert (s.assess, s.assess_effort) == ("claude/opus", "max")
    app.set("assess_model", "haiku")  # it reports no effort
    assert lists()["assess_effort"] == [] and app.values["assess_effort"] == ""
    app.set("assess_ai", "codex")
    app.settle()
    assert lists()["assess_model"] == ["gpt-6-astra", "gpt-5.5"]
    assert (app.values["assess_model"], app.values["assess_effort"]) == ("gpt-6-astra", "low")
    app.set("assess_model", "gpt-5.5")
    assert app.values["assess_effort"] == "medium"
    app.set("assess_ai", "ollama")
    app.settle()
    assert app.assess_spec() == "ollama/gemma3:270m"
    app.set("assess_ai", "openai")  # its models unknown without a key: typed
    app.settle()
    assert "no API key" in app.status
    app.set("assess_model", "gpt-5")
    assert app.assess_spec() == "openai/gpt-5"
    app.set("assess_context", "changes")
    app.set("assess_instructions", "Be brief.")
    assert app.collect().assess_save_prompt is False  # off by default
    app.set("assess_save_prompt", True)
    s = app.collect()
    assert (s.assess_context, s.assess_instructions) == ("changes", "Be brief.")
    assert s.assess_save_prompt is True
    again = App(
        screen,
        Settings(
            mode="files", assess="codex/gpt-5.5", assess_effort="low", assess_context="changes"
        ),
    )
    again.settle()
    v = again.values
    assert (v["assess_ai"], v["assess_model"]) == ("codex", "gpt-5.5")
    assert (v["assess_effort"], v["assess_context"]) == ("low", "changes")
    again.reset_options()
    assert again.assess_spec() == "" and again.collect().assess_context == "document"


def test_window_loads_a_repository(screen, history):
    b, shas = history
    app = App(screen, Settings(repo=str(b.path)))
    assert app.values["target"] == WORKTREE
    assert shas[4][:7] in app.values["base"]
    assert app.view()["lists"]["target"][:2] == [WORKTREE, INDEX]
    s = app.collect()
    assert (s.mode, s.base, s.target) == ("git", shas[4], "worktree")
    # a ref typed by hand goes through as it is
    app.set("base", "HEAD~2")
    assert app.collect().base == "HEAD~2"
    # the move similarity, kept within 0.05 and 1
    app.set("move_similarity", "0.6")
    assert app.collect().move_similarity == 0.6
    app.set("move_similarity", "3")
    assert app.collect().move_similarity == 1.0
    # moved passages followed by default, and the switch turns them off
    assert app.collect().move_passages
    app.set("move_passages", False)
    assert not app.collect().move_passages
    # untracked files only make sense with the working tree
    assert not off(app, "untracked")
    app.set("target", INDEX)
    assert off(app, "untracked")


def test_window_rejects_a_folder_that_is_not_a_repository(screen, tmp_path):
    app = App(screen, Settings(repo=str(tmp_path)))
    assert "Not a git repository" in app.status


def test_save_to_follows_the_new_side(screen, tmp_path):
    """Save to is, by default, next to the new file, or in the new folder, and
    follows them as they change, swapped too; kept as "" so it follows next
    time; a file chosen stays where it is."""
    old, new = two_files(tmp_path, "a\n", "b\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    moved = elsewhere / "c.md"
    moved.write_text("c\n", encoding="utf-8")
    app = App(screen, Settings(mode="files", old=str(old), new=str(new)))
    assert app.values["output"] == str(tmp_path / "a_vs_b.html")
    assert app.collect().output == ""
    app.set("new", str(moved))
    assert app.values["output"] == str(elsewhere / "a_vs_c.html")
    app.swap("files")
    assert app.values["output"] == str(tmp_path / "c_vs_a.html")
    app.set("output_format", "diff")
    assert app.values["output"] == str(tmp_path / "c_vs_a.diff")
    app.set("mode", "folders")
    app.set("old_folder", str(tmp_path))
    app.set("new_folder", str(elsewhere))
    assert app.values["output"] == str(elsewhere / "prosediff.diff")
    chosen = str(tmp_path / "mine.diff")
    app.set("output", chosen)
    app.set("new_folder", str(tmp_path))
    assert app.values["output"] == chosen and app.collect().output == chosen
    # Save to chosen in its dialog, and swapping folders
    screen.saved = str(tmp_path / "picked.diff")
    app.pick_output()
    assert app.values["output"] == screen.saved
    app.swap("folders")
    assert (app.values["old_folder"], app.values["new_folder"]) == (str(tmp_path),) * 2


def test_the_ai_card_is_greyed_out_but_for_the_html_report(screen, monkeypatch):
    """The AI assessment goes in the HTML report only: for a .diff or a
    .wdiff its card is greyed out whole, and no AI is asked."""
    monkeypatch.setattr(gui, "models_of", lambda ai: [])
    app = App(screen, Settings(mode="files", assess="codex/gpt-5.5"))
    app.settle()
    assert not off(app, "assess_ai")
    app.set("output_format", "wdiff")
    assert off(app, "assess_ai") and off(app, "assess_model")
    assert all(off(app, s) for s in gui.AI_SWITCHES)
    assert app.collect().assess == "codex/gpt-5.5"  # kept for when HTML is back
    app.set("output_format", "html")
    assert not off(app, "assess_ai")
    assert not any(off(app, s) for s in gui.AI_SWITCHES)


def test_format_renames_the_output(screen):
    """Choosing a format gives the Save to file its extension; a name of
    another kind, or none, is left as it is."""
    app = App(screen, Settings(output="C:/p/a_vs_b.html"))
    for fmt, expected in (
        ("diff", "a_vs_b.diff"),
        ("wdiff", "a_vs_b.wdiff"),
        ("html", "a_vs_b.html"),
    ):
        app.set("output_format", fmt)
        assert Path(app.values["output"]).name == expected
    for kept in ("changes.patch", "notes.txt", ""):
        app.set("output", kept)
        app.values["output_format"] = "diff"
        app.rename_output()
        assert app.values["output"] == kept


def test_tracked_formats_greyed_for_other_files(screen):
    """Word, tracked and OpenDocument, tracked are greyed out when two files
    are compared that are not both of their kind; a repository or folders
    keep them, their files known only once compared."""
    app = App(screen, Settings(mode="files", old="a.md", new="b.docx"))

    def enabled(fmt):
        return not off(app, f"output_format:{fmt}")

    assert not enabled("docx") and not enabled("odt") and enabled("html")
    app.set("old", "C:/p/a.DOCX")
    assert enabled("docx") and not enabled("odt")
    app.set("old", "a.odt")
    app.set("new", "b.odt")
    assert enabled("odt") and not enabled("docx")
    app.set("mode", "git")
    assert enabled("docx") and enabled("odt")


def test_mode_switch(screen):
    """The segmented button shows the fields of one source at a time, and
    what it shows is what is compared."""
    app = App(screen, Settings(mode="files"))
    assert not gone(app, "side:files") and gone(app, "side:folders")
    app.set("mode", "folders")
    assert not gone(app, "side:folders") and gone(app, "side:files")
    assert app.collect().mode == "folders"


def test_comments_choice(screen, tmp_path):
    """One choice for the comments; comments without text are a choice of
    markers only."""
    app = App(screen, Settings(comments="none"))
    assert app.collect().comments == "none"
    assert off(app, "empty_comments")
    app.set("comments", "markers")
    assert not off(app, "empty_comments")


def test_invalid_arguments_show_an_error_and_exit(monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(gui, "own_taskbar_button", lambda: None)
    monkeypatch.setattr(gui, "load_settings", Settings)
    monkeypatch.setattr(gui, "show_error", shown.append)
    monkeypatch.setattr(gui, "App", lambda *a: pytest.fail("the window must not open"))
    (tmp_path / "a b.docx").write_text("")
    with pytest.raises(SystemExit) as exited:
        gui.main([str(tmp_path / "notes.txt"), str(tmp_path / "a b.docx")])
    assert exited.value.code == 2
    assert shown[0].startswith("Two arguments must be two Markdown, Word or OpenDocument files")
    assert "Usage: prosediff-gui" in shown[0] and len(shown) == 1
    assert (
        f"Received 2 arguments:\n1. “{tmp_path / 'notes.txt'}”  (not found)\n"
        f"2. “{tmp_path / 'a b.docx'}”\n\n" in shown[0]
    )


def test_the_window_has_the_logo():
    here = Path(gui.__file__).parent
    assert (here / "logo.ico").is_file() and (here / "logo.png").is_file()
    assert gui.icon_path().is_file()


def test_options_saved_only_when_asked_and_reset(screen, tmp_path, monkeypatch):
    """Compare saves nothing; Save options writes the choices, Reset to
    defaults puts every option back, leaving what is compared alone."""
    f = tmp_path / "gui.json"
    monkeypatch.setattr(gui, "settings_file", lambda: f)
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(b"One.\n")
    new.write_bytes(b"Two.\n")
    app = App(screen, Settings(mode="files", old=str(old), new=str(new), open_page=False))
    app.set("comments", "none")
    app.set("move_algorithm", "token-set")
    app.set("output_format", "wdiff")
    app.run()
    finish(app)
    assert app.view()["text"]["run"] == "Compare" and "changed" in app.status
    assert not f.exists() and screen.reports == screen.files == []  # not opened
    app.save_options()
    saved = load_settings(f)
    assert (saved.comments, saved.move_algorithm) == ("none", "token-set")
    assert b"\r" not in f.read_bytes()
    app.reset_options()
    s = app.collect()
    assert (s.comments, s.move_algorithm, s.output_format) == ("markers", None, "html")
    assert s.old == str(old)


def test_a_failed_comparison_shows_an_error(screen, tmp_path):
    """An error in the comparison's process reaches the window: an error
    shown, and the Compare button back, not a window waiting forever."""
    app = App(
        screen,
        Settings(
            mode="files",
            old=str(tmp_path / "a.md"),
            new=str(tmp_path / "gone.md"),
            open_page=False,
        ),
    )
    app.run()
    finish(app)
    assert app.view()["text"]["run"] == "Compare"
    assert app.status == "Not compared."
    assert screen.shown and "no such file or folder" in screen.shown[0]
    assert screen.kinds[0] == "error"


def test_move_settings_of_paragraphs_and_sentences(screen, tmp_path):
    """Paragraphs and sentences have their own moved-line settings, shown at
    their defaults and remembered only when changed; comparing both ways
    writes an HTML report holding both, and a diff paragraph by paragraph."""
    from prosediff.diff import move_defaults

    app = App(screen, Settings())
    v = app.values
    assert (round(float(v["move_similarity"]), 6), v["move_algorithm"]) == move_defaults(False)
    shown = (round(float(v["sentence_move_similarity"]), 6), v["sentence_move_algorithm"])
    assert shown == move_defaults(True)
    app.set("sentence_move_similarity", "0.65")
    app.set("move_algorithm", "token-set")
    app.set("split", "both")
    s = app.collect()
    assert (s.move_similarity, s.sentence_move_similarity, s.split) == (None, 0.65, "both")
    assert (s.move_algorithm, s.sentence_move_algorithm) == ("token-set", None)
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(b"One sentence here. Another one there.\n")
    new.write_bytes(b"Another one there. One sentence here.\n")
    s.mode, s.old, s.new, s.output = "files", str(old), str(new), str(tmp_path / "r.html")
    path = execute(run_of(s)).path
    page = path.read_text(encoding="utf-8")
    assert 'data-split="paragraph"' in page and 'data-split="sentence"' in page
    # a diff holds one split: both compares paragraph by paragraph
    s.output_format, s.output = "diff", str(tmp_path / "r.diff")
    path = execute(run_of(s)).path
    assert path.suffix == ".diff" and path.read_text(encoding="utf-8")


def test_advanced_moved_passage_settings(screen, tmp_path):
    """One field for each moved-passage setting, at its default; only the
    values changed from prosediff's defaults are kept, and saved."""
    app = App(screen, Settings(mode="files"))
    names = {k[len(PASSAGE) :] for k in app.values if k.startswith(PASSAGE)}
    assert names == {f.name for f in fields(MovedPassageSettings)}
    assert int(app.values[PASSAGE + "max_pairs"]) == MOVED_PASSAGE_DEFAULTS.max_pairs
    assert app.collect().moved_passages == {}
    app.set(PASSAGE + "min_words", "6")
    app.set(PASSAGE + "partial_share", "0.5")
    app.set(PASSAGE + "rounds", "not a number")  # refused, as Compare says
    with pytest.raises(ValueError, match="must be a number"):
        app.collect()
    app.run()
    assert app.job is None and "must be a number" in screen.shown[-1]
    app.set(PASSAGE + "rounds", str(MOVED_PASSAGE_DEFAULTS.rounds))
    s = app.collect()
    assert s.moved_passages == {"min_words": 6, "partial_share": 0.5}
    assert MovedPassageSettings.from_choices(s.moved_passages).min_words == 6
    path = tmp_path / "gui.json"
    assert save_settings(s, path)
    assert load_settings(path).moved_passages == {"min_words": 6, "partial_share": 0.5}
    app.reset_options()  # "Reset to defaults"
    assert app.collect().moved_passages == {}


def test_settings_no_longer_offered_give_way(tmp_path):
    """A setting saved that is no choice the window offers any more
    (prosediff upgraded) gives way to its default; a moved-passage setting
    out of range is refused."""
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"output_format": "pdf", "split": "sentence"}), encoding="utf-8")
    s = load_settings(path)
    assert (s.output_format, s.split) == ("html", "sentence")
    with pytest.raises(ValueError, match="partial_share"):
        MovedPassageSettings.from_choices({"partial_share": 3})


def test_a_review_has_no_preview(tmp_path, monkeypatch):
    """A file reviewed alone goes to the AI without a preview asked first."""
    old, _ = two_files(tmp_path, "One.\n", "Two.\n")
    asked = []
    monkeypatch.setattr(
        pipeline,
        "assess_comparison",
        lambda c, request, **kw: asked.append(request) or Assessment("claude"),
    )
    s = Settings(mode="review", single=str(old), assess="claude", open_page=False)
    execute(run_of(s), approve=lambda path: pytest.fail("a preview was asked"))
    assert len(asked) == 1


def test_documents_to_download(tmp_path, monkeypatch, screen):
    """The documents made of the AI's problems go in the report unless
    switched off; the switch is greyed out while the problems are not
    marked in the text."""
    old, new = pair(tmp_path, "docx")
    monkeypatch.setattr(pipeline, "assess_comparison", lambda c, request, **kw: assessment([FIXED]))
    s = Settings(mode="files", old=str(old), new=str(new), assess="claude", assess_preview=False)
    path = execute(run_of(s)).path
    assert path.read_text(encoding="utf-8").count('class="ai-document"') == 2
    path = execute(run_of(replace(s, assess_documents=False))).path
    assert 'class="ai-document"' not in path.read_text(encoding="utf-8")
    app = App(screen, Settings(mode="files"))
    app.settle()
    assert app.collect().assess_documents is True
    app.set("assess_ai", "claude")
    app.settle()
    assert not off(app, "assess_documents")
    app.set("assess_annotate", False)
    assert off(app, "assess_documents")
    app.set("assess_annotate", True)
    app.set("assess_documents", False)
    assert app.collect().assess_documents is False


def test_one_file_reviewed(screen, tmp_path, monkeypatch):
    """The "One file" tab: one file, reviewed by the AI chosen, into an
    HTML report next to it; the options of a comparison greyed out, the
    other formats too; no AI, no review."""
    path = word_file(tmp_path / "paper.docx", NEW)
    asked = []

    def assessed(c, request, **kw):
        asked.append((c, request))
        return Assessment("claude", "## Verdict\n**Good**.", annotations=[FIXED], kind="review")

    monkeypatch.setattr(pipeline, "assess_comparison", assessed)
    s = Settings(mode="review", single=str(path), assess="claude", assess_preview=False)
    stages = []
    done = execute(run_of(s), stages.append)
    out, c, a = done.path, done.comparison, done.assessment
    assert out == tmp_path / "paper_review.html" and c.single and a.verdict == "good"
    assert stages == [
        "Reading the file…",
        "Asking claude to review the file…",
        "Writing the report…",
    ]
    assert out.read_text(encoding="utf-8").count('class="ai-document"') == 1
    with pytest.raises(ValueError, match="choose an AI"):
        execute(run_of(replace(s, assess="")))
    with pytest.raises(ValueError, match="choose the file"):
        execute(run_of(replace(s, single="")))
    app = App(screen, Settings(mode="files", output_format="wdiff"))
    app.settle()
    app.set("mode", "review")
    assert not gone(app, "side:review")
    assert app.values["output_format"] == "html" and app.view()["text"]["run"] == "Review"
    assert all(off(app, f"output_format:{f}") for f in ("diff", "docx", "odt"))
    comparing_only = [*(f"split:{s}" for s in gui.SPLITS), "ignore_whitespace"]
    assert all(off(app, w) for w in comparing_only)
    app.set("single", str(path))
    assert app.values["output"] == str(tmp_path / "paper_review.html")
    app.set("assess_ai", "claude")
    app.settle()
    assert all(off(app, w) for w in gui.CHANGES_ONLY)
    assert not off(app, "assess_documents")
    got = app.collect()
    assert got.mode == "review" and got.single == str(path)
    app.set("assess_ai", gui.NO_ASSESSMENT)
    app.run()
    assert app.job is None and "Choose an AI" in screen.shown[-1]
    # a negative number of context lines refused, as the command line does
    app.set("context_lines", "-3")
    app.run()
    assert app.job is None and "Context lines must be 0 or more" in screen.shown[-1]
    app.set("context_lines", "auto")
    app.set("mode", "files")
    assert app.view()["text"]["run"] == "Compare"
    assert not any(off(app, w) for w in comparing_only)


def test_the_window_has_the_options_of_the_command_line(screen, tmp_path):
    """The Markdown filter, the hidden lines and the AI's timeout, as
    --md-filter, --max-hidden and --assess-timeout: kept with the other
    options, put in the run, refused out of range as the command line
    refuses them, and reset to their defaults."""
    from prosediff.assess import ASSESS_TIMEOUT
    from prosediff.diff import MAX_HIDDEN

    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    s = Settings(mode="files", old=str(old), new=str(new), md_filter="cat", max_hidden=7)
    app = App(screen, replace(s, assess_timeout=90))
    got = app.collect()
    assert (got.md_filter, got.max_hidden, got.assess_timeout) == ("cat", 7, 90)
    run = run_of(replace(got, assess="claude"))
    assert run.options.md_filter == "cat" and run.options.max_hidden == 7
    assert run.request.timeout == 90
    app.set("max_hidden", "-1")
    app.run()
    assert app.job is None and "Hidden lines must be 0 or more" in screen.shown[-1]
    app.set("max_hidden", "seven")
    app.run()
    assert app.job is None and screen.shown[-1] == "Hidden lines must be a number."
    app.set("max_hidden", "7")
    app.set("assess_timeout", "0")
    app.run()
    assert app.job is None and "timeout must be above 0" in screen.shown[-1]
    app.reset_options()
    got = app.collect()
    assert (got.md_filter, got.max_hidden, got.assess_timeout) == ("", MAX_HIDDEN, ASSESS_TIMEOUT)


def test_a_failed_ai_writing_assessment_is_shown(screen, tmp_path, monkeypatch):
    """The AI-writing assessment failing is said, as the command line says
    it: its error goes back with the result, and the window shows it."""
    from prosediff.gui import run_job

    def assessed(c, request, kind="value", **kw):
        if kind == "writing":
            return Assessment("claude", error="no login")
        return Assessment("claude", "## Verdict\n**Mixed**: fine.")

    monkeypatch.setattr(pipeline, "assess_comparison", assessed)
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    s = Settings(
        mode="files",
        old=str(old),
        new=str(new),
        assess="claude",
        assess_ai_writing=True,
        assess_preview=False,
        open_page=False,
    )
    messages = queue.Queue()
    run_job(s, messages, queue.Queue())
    while (message := messages.get_nowait())[0] == "stage":
        pass
    kind, result = message
    assert kind == "done" and result.writing_error == "no login"
    app = App(screen, s)
    app.job, app.job_settings = object(), s
    app.handle(kind, result)
    assert "The AI-writing assessment failed: no login" in screen.shown
    assert (
        screen.kinds[screen.shown.index("The AI-writing assessment failed: no login")] == "warning"
    )


def test_compare_by_offers_the_splits_the_output_can_hold(screen, tmp_path):
    """Both greyed out while a diff is chosen, Sentences while a document of
    tracked changes is: one chosen gives way to Paragraphs, and comes back
    with an output that can hold it, unless another was chosen since."""
    old, new = tmp_path / "a.docx", tmp_path / "b.docx"
    app = App(screen, Settings(mode="files", old=str(old), new=str(new)))

    def choose(fmt):
        app.set("output_format", fmt)

    split = lambda: app.values["split"]  # noqa: E731
    assert split() == "both" and not off(app, "split:both")
    choose("diff")
    assert off(app, "split:both") and split() == "paragraph"
    assert not off(app, "split:sentence")
    choose("html")
    assert split() == "both" and not off(app, "split:both")
    app.set("split", "sentence")
    choose("docx")
    assert off(app, "split:sentence") and off(app, "split:both")
    assert split() == "paragraph"
    choose("wdiff")
    assert split() == "sentence"  # a word diff holds sentences
    choose("diff")
    app.set("split", "paragraph")
    choose("html")
    assert split() == "paragraph"  # chosen since: it stays
    # settings saved with a split the output cannot hold
    other = App(screen, Settings(mode="files", split="both", output_format="diff"))
    assert other.values["split"] == "paragraph" and off(other, "split:both")


def test_the_instructions_written_in_a_box_of_their_own(screen):
    """The large box opens with the instructions, and OK puts what was
    written back in their field; Cancel (nothing sent back) leaves it as it
    was."""
    app = App(screen, Settings(assess_instructions="Be brief.", mode="files"))
    # prosediff's two prompts, then the instructions
    *_, mine = app.instructions()
    assert mine["field"] == "assess_instructions" and mine["text"] == "Be brief."
    assert mine["default"] == "" and not mine["readonly"]
    assert app.values["assess_instructions"] == "Be brief."
    app.set_instructions({mine["field"]: mine["text"] + "\nCite the journal."})
    assert app.values["assess_instructions"] == "Be brief.\nCite the journal."


def test_prosediffs_prompts_edited_in_their_boxes(screen):
    """The boxes show prosediff's prompts for the tab: two for two versions
    (the assessment, and AI writing), two for a file reviewed (its review,
    and AI writing). An edited prompt is kept; one left as prosediff's, or
    restored, is kept as "", to follow prosediff's."""
    app = App(screen, Settings(mode="files", assess_writing_prompt="Spot the AI."))
    value, writing, mine = app.instructions()
    assert value["text"] == SYSTEM and value["default"] == SYSTEM
    assert writing["text"] == "Spot the AI." and writing["default"] == SYSTEM_WRITING
    app.set_instructions(
        {
            value["field"]: "Be harsh. " + SYSTEM,
            writing["field"]: writing["default"],  # Restore default
            mine["field"]: mine["text"],
        }
    )
    assert app.values["assess_prompt"] == "Be harsh. " + SYSTEM
    assert app.values["assess_writing_prompt"] == ""
    s = app.collect()
    assert (s.assess_prompt, s.assess_writing_prompt) == ("Be harsh. " + SYSTEM, "")

    app.set("mode", "review")
    review, writing, mine = app.instructions()
    assert review["text"] == SYSTEM_REVIEW and writing["text"] == SYSTEM_WRITING_REVIEW
    app.set_instructions(
        {review["field"]: "Review it.", writing["field"]: writing["text"], mine["field"]: ""}
    )
    assert app.values["assess_review_prompt"] == "Review it."
    assert app.values["assess_prompt"] == "Be harsh. " + SYSTEM


def test_instructions_from_a_file_shown_read_only(screen, tmp_path):
    """Instructions a file holds are shown read-only in their box, and OK
    keeps the file, to be read again at every run, not its text."""
    notes = tmp_path / "notes.txt"
    notes.write_text("The journal is Research Policy.\n", encoding="utf-8")
    app = App(screen, Settings(mode="files", assess_instructions=str(notes)))
    *_, mine = app.instructions()
    assert mine["text"] == "The journal is Research Policy." and mine["readonly"]
    assert "from notes.txt" in mine["label"]
    app.set_instructions({mine["field"]: "Something else."})
    assert app.values["assess_instructions"] == str(notes)
    # a file chosen in its dialog
    screen.opened = [str(tmp_path / "other.txt")]
    app.pick("assess_instructions")
    assert app.values["assess_instructions"] == str(tmp_path / "other.txt")


def test_the_one_file_tab(screen):
    """Reviewing one file: the card and the window name a file, not
    versions; the options of a comparison are left out, not greyed out
    (Compare by, Ignore whitespace, what the AI reads, the preview, the
    output format); Comments offers markers or none, its tooltip saying
    what the AI is sent, text given up for markers. All back with two
    versions."""
    app = App(screen, Settings(mode="files", comments="text"))
    view = app.view()
    assert view["text"]["source"] == "Versions"
    assert not any(w in view["hidden"] for w in gui.REVIEW_HIDDEN)
    assert "assess_preview" not in view["hidden"]
    assert "text" in view["lists"]["comments"]

    app.set("mode", "review")
    view = app.view()
    assert view["text"]["source"] == "File"
    assert view["text"]["title"] == "prosediff: review one file"
    assert all(w in view["hidden"] for w in gui.REVIEW_HIDDEN)
    assert "assess_preview" in view["hidden"]
    assert app.values["comments"] == "markers"
    assert "text" not in view["lists"]["comments"]
    assert view["text"]["comments_tip"] == gui.REVIEW_COMMENTS_TIP

    app.set("mode", "files")
    view = app.view()
    assert view["text"]["source"] == "Versions"
    assert not any(w in view["hidden"] for w in gui.REVIEW_HIDDEN)
    assert "assess_preview" not in view["hidden"]
    assert app.values["comments"] == "text"
    assert view["text"]["comments_tip"] == gui.COMMENTS_TIP


def test_the_window_greys_out_the_files_while_not_sending_them(screen):
    app = App(screen, Settings(mode="files", assess="claude"))
    assert off(app, "edit_files")
    app.set("assess_send_files", True)
    assert not off(app, "edit_files")
    # the card says how many and which; their list shows them, one a row,
    # and Remove takes the chosen off
    app.set("assess_files", r"C:\docs\guide.pdf;C:\docs\report.docx")
    view = app.view()
    assert view["text"]["files_summary"] == "2 files: guide.pdf, report.docx"
    assert [r[1] for r in view["files"]["rows"]] == ["guide.pdf", "report.docx"]
    app.remove_files([r"C:\docs\guide.pdf"])
    assert app.values["assess_files"] == r"C:\docs\report.docx"
    assert app.view()["text"]["files_summary"] == "1 file: report.docx"


def test_the_rebuild_tab(screen, tmp_path):
    """Rebuilding a report from saved answers: no comparison options, no AI
    card, its button Rebuild, the report by default over the one the
    answers were saved beside; all back on another tab."""
    saved = tmp_path / "paper_review.ai.json"
    app = App(screen, Settings(mode="files"))
    app.set("mode", "rebuild")
    assert gone(app, "card:compared") and gone(app, "card:ai")
    view = app.view()
    assert view["text"]["run"] == "Rebuild" and view["text"]["source"] == "Saved report"
    assert view["text"]["title"] == "prosediff: rebuild a report"
    screen.opened = [str(saved)]
    app.pick("rebuild_file")
    assert app.values["output"] == str((tmp_path / "paper_review.html").resolve())
    app.set("mode", "files")
    assert not gone(app, "card:compared") and not gone(app, "card:ai")
    assert app.view()["text"]["run"] == "Compare"


def test_the_progress_log(screen):
    """The progress log holds a line for each stage; each line has its time;
    the AI's news go in as they come."""
    app = App(screen, Settings(mode="files"))
    assert screen.lines == [] and app.view()["log"] == 0
    app.set_stage("Asking claude to assess the changes…")
    app.handle("live", ("thinking", ["Thinking", "  The claim is unsupported."]))
    text = "\n".join(screen.lines)
    assert app.view()["log"] == 3 and screen.lines == app.log_lines
    assert "Asking claude to assess the changes…" in text
    assert "  The claim is unsupported." in text and re.search(r"^\d\d:\d\d:\d\d  ", text, re.M)
    assert app.status == "Asking claude to assess the changes…"


def test_a_project_saved_and_opened_again(screen, tmp_path):
    """Save project keeps every setting shown, what is compared and the
    context files included, the AI's answers of the last report and the
    choices made in it; opened, the window shows them all again, its title
    naming the project, its Rebuild tab set to it, the report over the one
    the answers were saved with."""
    from prosediff.gui import project_settings
    from prosediff.saved import project_choices

    old, new, guide = (tmp_path / n for n in ("a.md", "b.md", "guide.md"))
    for f in (old, new, guide):
        f.write_text("Text.\n", encoding="utf-8")
    shown = Settings(
        mode="files",
        old=str(old),
        new=str(new),
        assess_send_files=True,
        assess_files=str(guide),
        split="sentence",
    )
    app = App(screen, shown)
    report = {"run": {"output": str(tmp_path / "a_vs_b.html")}, "assessment": {"markdown": "x"}}
    choices = {"left_out": ["a"], "resolved": [], "undone": ["b"]}
    app.project_report, app.project_choices = report, choices
    screen.saved = str(tmp_path / "paper")  # Save project as, the suffix added
    app.save_project()
    assert app.project_path == (tmp_path / "paper.prosediff").resolve()
    assert app.status.startswith("Project saved with the last report's AI answers")
    assert "paper.prosediff" in app.view()["text"]["title"]
    s, kept = project_settings(app.project_path)
    assert (s.old, s.new, s.assess_files, s.split) == (str(old), str(new), str(guide), "sentence")
    assert kept == report and s.rebuild_file == str(app.project_path.resolve())
    assert project_choices(app.project_path) == choices
    again = App(screen, Settings())
    screen.opened = [str(app.project_path)]
    again.open_project()
    assert again.collect().assess_files == str(guide) and again.project_report == report
    assert again.project_choices == choices
    assert again.values["mode"] == "files" and "paper.prosediff" in again.title()
    again.set("mode", "rebuild")
    assert again.sides_page() == str(tmp_path / "a_vs_b.html")
    # a run's answers kept for the next Save project, without the choices
    # made in another report
    answers = tmp_path / "r.ai.json"
    data = {"format": 2, "run": {}, "assessment": {"markdown": "y"}}
    answers.write_text(json.dumps(data), encoding="utf-8")
    again.keep_answers(again.collect(), SimpleNamespace(saved=str(answers)))
    assert again.project_report["assessment"]["markdown"] == "y"
    assert again.project_choices is None


def test_a_file_not_a_project_is_refused(screen, tmp_path):
    """A file opened as a project that is none is said in an error, the
    window as it was."""
    other = tmp_path / "notes.prosediff"
    other.write_text("{}", encoding="utf-8")
    app = App(screen, Settings(mode="files", old="a.md"))
    app.open_project(other)
    assert "not a prosediff project" in screen.shown[-1] and app.values["old"] == "a.md"


def test_the_choices_made_in_the_last_report_are_kept_in_the_project(screen, tmp_path):
    """The choices made in a report's window (which problems are in the
    download, resolved, their fix undone) are the last report's: written
    into the project open at once while it holds that report's answers,
    kept for Save project otherwise; those of an older report's window, or
    a preview's, are not kept."""
    from prosediff.saved import project_choices, save_project

    report = {"run": {}, "assessment": {"markdown": "x"}}
    project = tmp_path / "paper.prosediff"
    save_project(project, {}, report)
    app = App(screen, Settings(mode="files"), (project, report, None))
    app.report_serial = 2  # the last report opened
    made = {"left_out": ["k"], "resolved": [], "undone": []}
    app.keep_choices(None, made)  # a preview's
    app.keep_choices(1, made)  # an older report's
    assert app.project_choices is None and project_choices(project) is None
    app.keep_choices(2, made)
    assert app.project_choices == made and project_choices(project) == made
    assert app.choices_of(2) == made and app.choices_of(1) is None
    # a project holding other answers: kept for Save project, not written
    app.project_report = {"run": {}, "assessment": {"markdown": "y"}}
    later = {"left_out": [], "resolved": ["k"], "undone": []}
    app.keep_choices(2, later)
    assert app.project_choices == later and project_choices(project) == made
    assert "Save project" in app.status
    # no project open: kept for Save project
    loose = App(screen, Settings(mode="files"))
    loose.project_report, loose.report_serial = report, 1
    loose.keep_choices(1, made)
    assert loose.project_choices == made and "Save project" in loose.status


def test_the_files_analysed_are_not_context_files(screen, tmp_path):
    """A file analysed (one of the two compared, the one reviewed, one in a
    folder compared or in the repository) is not taken as a context file:
    refused when added, said why, the others added; and a run with one
    stops before anything is read."""
    from prosediff.assess import AssessRequest
    from prosediff.pipeline import Run, analysed, execute
    from prosediff.sources import SourceError

    old, new, guide = (tmp_path / n for n in ("a.md", "b.md", "guide.md"))
    for f in (old, new, guide):
        f.write_text("Text.\n", encoding="utf-8")
    app = App(screen, Settings(mode="files", old=str(old), new=str(new)))
    screen.opened = [str(new), str(guide)]
    app.pick_files()
    assert app.files_chosen() == [str(guide)] and app.values["assess_send_files"] is True
    assert screen.shown == ["b.md: analysed, so not sent to the AI as a context file too."]
    # by tab: the file reviewed, a folder's files, the repository's
    assert analysed("review", str(old))(old) and not analysed("review", str(old))(new)
    assert analysed("folders", str(tmp_path), "")(tmp_path / "sub" / "x.md")
    assert analysed("git", str(tmp_path), "HEAD~2")(guide)
    assert not analysed("git", str(tmp_path / "repo"), "HEAD")(guide)
    run = Run(
        "files",
        str(old),
        tmp_path / "r.html",
        new=str(new),
        request=AssessRequest("claude", files=(str(guide), str(old))),
    )
    with pytest.raises(SourceError, match=r"a\.md: analysed"):
        execute(run)


def test_the_context_files_sorted_by_their_columns(screen, tmp_path):
    """A heading clicked sorts the list of context files by its column, as
    people sort (table_2 before table_10), again the other way, an arrow
    saying which; the files are sent in the order they were added."""
    files = [tmp_path / "b" / "table_10.docx", tmp_path / "a" / "table_2.docx"]
    app = App(screen, Settings(assess_send_files=True, assess_files=";".join(map(str, files))))

    def listed():
        shown = app.view()["files"]
        return [r[1] for r in shown["rows"]], shown["sort"]

    assert listed() == (["table_10.docx", "table_2.docx"], None)  # as added
    app.sort_files("name")
    assert listed() == (["table_2.docx", "table_10.docx"], ["name", False])
    app.sort_files("name")
    assert listed() == (["table_10.docx", "table_2.docx"], ["name", True])
    app.sort_files("folder")
    assert listed() == (["table_2.docx", "table_10.docx"], ["folder", False])
    assert app.files_chosen() == list(map(str, files))


def test_the_menus(screen):
    """Save options and Reset to defaults are in the Options menu, not
    among the buttons; File opens and saves projects."""
    app = App(screen, Settings(mode="files"))
    bar = gui.menu(app, SimpleNamespace(window=None))
    assert [m.title for m in bar] == ["File", "Options"]
    files = [getattr(i, "title", None) for i in bar[0].items]
    assert files[:3] == ["Open project…", "Save project", "Save project as…"]
    items = [i.title for i in bar[1].items]
    assert items[0].startswith("Save options") and items[1].startswith("Reset to defaults")


def test_the_open_screen_page_has_every_field(screen):
    """Each field App holds is on the page (data-field), and each id App
    greys out or leaves out (data-id, or a segmented button's value)."""
    page = gui.page_html()
    app = App(screen, Settings(mode="files"))
    # the context files and prosediff's prompts: in their dialogs, not fields
    in_dialogs = {"assess_files", "assess_prompt", "assess_writing_prompt"}
    in_dialogs |= {"assess_review_prompt", "assess_review_writing_prompt"}
    for name in set(app.values) - in_dialogs:
        assert f'data-field="{name}"' in page, name
    view = app.view()
    for key in {*gui.AI_SWITCHES, *gui.REVIEW_HIDDEN, "edit_files", "card:ai", "card:compared"}:
        assert f'data-id="{key}"' in page or f'data-field="{key}"' in page, key
    for key in view["disabled"] + view["hidden"]:
        name, _, value = key.partition(":")
        assert (
            f'data-id="{key}"' in page
            or f'data-field="{key}"' in page
            or f'value="{value}" data-field="{name}"' in page
        ), key
