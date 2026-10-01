"""The window: choosing the sides, generating the HTML report, remembering choices."""

import json
import queue
import sys
from dataclasses import fields, replace
from pathlib import Path

import pytest

pytest.importorskip("tkinter", reason="the GUI tests need a Python built with Tk")

import tkinter as tk

from helpers import two_files

from prosediff import gui, pipeline
from prosediff.assess import AssessError, Assessment, ModelInfo
from prosediff.diff import MOVED_PASSAGE_DEFAULTS, MovedPassageSettings
from prosediff.gui import (
    INDEX,
    WORKTREE,
    App,
    Settings,
    default_sides,
    generate,
    list_choices,
    load_settings,
    save_settings,
    settings_from_args,
)


def test_no_second_console_when_there_is_one(monkeypatch):
    """The invisible console is made only for a program with none (pythonw,
    prosediff-gui.exe): made once at most, and never away from Windows."""
    gui.invisible_console()
    assert gui.invisible_console() is False
    monkeypatch.setattr(gui.sys, "platform", "linux")
    assert gui.invisible_console() is False


def test_every_drop_down_item_has_a_hint():
    """Each item of the drop-down lists is explained when the pointer rests
    on it (gui.item_hints); a language code by the language's name."""
    from prosediff.diff import COMMENT_MODES, MOVE_ALGORITHMS
    from prosediff.render import ALIGNMENTS
    from prosediff.sources import DOCX_CHANGES

    for values, hints in (
        (DOCX_CHANGES, gui.DOCX_CHANGE_HINTS),
        (COMMENT_MODES, gui.COMMENT_HINTS),
        (ALIGNMENTS, gui.ALIGNMENT_HINTS),
        (tuple(MOVE_ALGORITHMS), gui.MOVE_ALGORITHM_HINTS),
        (gui.ENCODINGS, gui.ENCODING_HINTS),
    ):
        assert set(values) == set(hints)
    assert set(gui.DOCX_CHANGE_LABELS) == set(DOCX_CHANGES)
    assert set(gui.LANGUAGE_HINTS) < set(gui.LANGUAGES)
    assert gui.language_name("it") == "Italian"


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


def test_one_file_waits_for_its_partner(tmp_path):
    from prosediff.gui import single_file, with_second_file

    sent = tmp_path / "draft.docx"
    sent.write_bytes(b"x")
    s, note = settings_from_args([str(sent)], Settings(mode="git"))
    assert note == "" and s.mode == "files" and (s.old, s.new) == (str(sent), "")
    assert s.single == str(sent)  # the One file tab too, to review it alone
    assert with_second_file(s, sent, sent).single == str(sent)
    assert single_file([str(sent)]) == sent
    assert single_file([str(tmp_path / "notes.txt")]) is None
    assert single_file([str(sent), str(sent)]) is None


def test_the_older_file_goes_on_the_left(tmp_path):
    import os

    from prosediff.gui import with_second_file

    sent, returned = tmp_path / "sent.docx", tmp_path / "returned.docx"
    sent.write_bytes(b"x")
    returned.write_bytes(b"y")
    os.utime(sent, (1_000_000, 1_000_000))
    os.utime(returned, (2_000_000, 2_000_000))
    for first, second in ((sent, returned), (returned, sent)):
        s = with_second_file(Settings(), first, second)
        assert (s.mode, s.old, s.new) == ("files", str(sent), str(returned))
        assert s.output == ""  # next to the newer, as the window fills it


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
        done = generate(Settings(repo=str(b.path), base=shas[0], target=target, output=str(out)))
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
    done = generate(s)
    path, c = done.path, done.comparison
    assert path == tmp_path / "a_vs_b.html" and len(c.files) == 1
    assert c.counts.moved == 0
    s.split = "sentence"
    assert generate(s).comparison.counts.moved == 1
    s.output_format = "diff"
    path = generate(s).path
    assert path.suffix == ".diff" and path.read_text().startswith("--- a/a.md\n+++ b/b.md\n")
    s.output = str(tmp_path / "a_vs_b.html")  # the format chosen wins over the suffix
    assert generate(s).path == tmp_path / "a_vs_b.diff"
    with pytest.raises(ValueError, match="old and the new file"):
        generate(Settings(mode="files"))
    with pytest.raises(ValueError, match="old and the new folder"):
        generate(Settings(mode="folders", old=s.old, new=s.new))


def test_settings_are_remembered(tmp_path):
    f = tmp_path / "gui.json"
    s = Settings(mode="files", old="a", new="b", align="left", paths=["p"])
    save_settings(s, f)
    assert load_settings(f) == s
    f.write_text("{ not json")
    assert load_settings(f) == Settings()


@pytest.fixture(scope="module")
def tk_root():
    """One Tk for the module: creating a second one in a process is
    unreliable on Windows (Tk intermittently fails to load its scripts)."""
    try:
        r = tk.Tk()
    except tk.TclError as e:  # no display
        pytest.skip(f"no display: {e}")
    r.withdraw()
    yield r
    r.destroy()


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


@pytest.fixture
def root(tk_root, monkeypatch):
    """A window of its own for each test; what the window looks for in the
    background (the models an AI reports, any-llm's providers) found at
    once, without starting Claude Code or Codex or asking Ollama; and no
    dialog waiting for a click: each records what it would have shown, in
    the window's shown."""
    monkeypatch.setattr(gui, "models_of", reported)
    monkeypatch.setattr(gui, "providers", lambda: ["anthropic", "ollama", "openai"])
    shown = []
    for name in ("showerror", "showwarning", "showinfo"):
        monkeypatch.setattr(gui.messagebox, name, lambda title, text, **kw: shown.append(text))
    w = tk.Toplevel(tk_root)
    w.withdraw()
    w.shown = shown
    yield w
    w.destroy()


def settle(root, app, ai: str | None = None) -> None:
    """Wait until the window has put what the background found in its
    lists: the providers, and the models of ai."""
    for _ in range(100):
        root.update()
        if not app.asking and (ai is None or ai in app.ai_models):
            return
        root.after(20)
    raise AssertionError("the lists were never filled")


def finish(root, app, seconds: float = 60) -> None:
    """Wait for the comparison's process to end (it starts a Python of its
    own: a few seconds)."""
    for _ in range(int(seconds * 10)):
        root.update()
        if app.job is None:
            return
        root.after(100)
    raise AssertionError("the comparison never ended")


def test_the_stages_are_told(tmp_path):
    """generate tells each stage as it starts: both comparisons, then the
    report."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    stages = []
    generate(Settings(mode="files", old=str(old), new=str(new), open_page=False), stages.append)
    assert stages == [
        "Comparing paragraph by paragraph…",
        "Comparing sentence by sentence…",
        "Writing the report…",
    ]
    stages.clear()
    s = Settings(mode="files", old=str(old), new=str(new), output_format="wdiff")
    generate(s, stages.append)
    assert stages == ["Comparing…", "Writing the diff…"]


def test_the_ai_reads_only_an_approved_preview(tmp_path, monkeypatch):
    """With an AI chosen, the report is first written without it and the
    preview shown; the AI is asked only once it is approved."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    asked = []
    monkeypatch.setattr(pipeline, "assess_comparison", lambda c, request: asked.append(request))
    s = Settings(mode="files", old=str(old), new=str(new), assess="claude", split="paragraph")
    previews = []

    def refuse(path):
        previews.append(path)
        assert path.is_file()  # written before the question
        return False

    done = generate(s, approve=refuse)
    path, assessment = done.path, done.assessment
    assert previews == [path] and asked == [] and assessment is None
    stages = []
    generate(s, stages.append, approve=lambda path: True)
    assert len(asked) == 1
    assert stages == [
        "Comparing…",
        "Writing the preview…",
        "Asking claude to assess the changes…",
        "Writing the report…",
    ]
    asked.clear()
    generate(replace(s, assess_preview=False), approve=refuse)  # switched off: no question
    assert len(asked) == 1


def test_the_preview_is_asked_about_in_the_window(root, tmp_path, monkeypatch):
    """The preview's question is a bar of the window, not a dialog the
    browser would cover: shown with the report opened, its answer sent to
    the comparison's process, gone once answered."""
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: pytest.fail("no dialog"))
    app = App(root, Settings(mode="files", assess="claude"))
    app.job, app.job_settings = object(), app.collect()
    app.messages, app.replies = queue.Queue(), queue.Queue()
    page = tmp_path / "page.html"
    page.write_text("")
    app.preview(page)
    root.update()
    assert app.preview_bar.winfo_manager() == "pack" and "page.html" in app.preview_text.get()
    assert opened == [page.resolve().as_uri()]
    app.answer_preview(True)
    app.job = None  # no process to poll
    root.update()
    assert app.replies.get_nowait() is True and not app.preview_bar.winfo_manager()


def test_a_comparison_runs_apart_and_can_be_cancelled(root, tmp_path):
    """Compare starts the comparison in a process of its own and becomes
    Cancel, which stops it and all it started, and becomes Compare again;
    the status line tells the stage."""
    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    app = App(root, Settings(mode="files", old=str(old), new=str(new), open_page=False))
    app.run()
    assert app.button["text"] == "Cancel" and app.job is not None
    pid = app.job.pid
    assert app.status.get() == "Starting…"
    app.cancel()
    assert app.button["text"] == "Compare" and app.job is None
    assert app.status.get() == "Cancelled."
    assert not gui.psutil.pid_exists(pid) or gui.psutil.Process(pid).status() == "zombie"
    # and a comparison let run tells its stages, then its result
    seen = set()
    app.run()
    for _ in range(600):
        root.update()
        seen.add(app.status.get().split("…")[0] + "…")  # the stage, its time aside
        if app.job is None:
            break
        root.after(100)
    assert "Writing the report…" in seen or "Comparing sentence by sentence…" in seen
    assert "changed" in app.status.get()


def test_the_model_list_says_loading_until_the_ai_answers(root, monkeypatch):
    """The AI, model and effort saved are shown at once, checked once the AI
    reports its models; an AI chosen then shows Loading in the model and
    effort fields, which cannot be used, until it does."""
    import threading

    answer = threading.Event()

    def slow(ai):
        answer.wait(10)
        return REPORTED[ai]

    monkeypatch.setattr(gui, "models_of", slow)
    app = App(root, Settings(mode="files", assess="claude/opus", assess_effort="max"))
    root.update()
    assert (app.assess_ai.get(), app.assess_model.get(), app.assess_effort.get()) == (
        "claude",
        "opus",
        "max",
    )
    s = app.collect()
    assert (s.assess, s.assess_effort) == ("claude/opus", "max")
    app.assess_ai.set("codex")
    assert app.assess_model.get() == "Loading…" and app.model_box.instate(["disabled"])
    assert app.assess_effort.get() == "Loading…" and app.effort_box.instate(["disabled"])
    answer.set()
    settle(root, app, "codex")
    assert app.assess_model.get() == "gpt-6-astra" and not app.model_box.instate(["disabled"])
    app.assess_ai.set("claude")
    assert app.assess_model.get() == "default" and root.shown == []  # opus was offered


def test_the_ai_list_says_loading_until_the_ais_are_known(root, monkeypatch):
    """With no AI saved, the AI list says Loading and cannot be used until
    the AIs are found in the background; then no AI is chosen."""
    import threading

    answer = threading.Event()

    def slow():
        answer.wait(10)
        return ["openai"]

    monkeypatch.setattr(gui, "providers", slow)
    app = App(root, Settings(mode="files"))
    root.update()
    assert app.assess_ai.get() == "Loading…" and app.ai_box.instate(["disabled"])
    assert app.collect().assess == "" and app.model_box.instate(["disabled"])
    answer.set()
    settle(root, app)
    assert app.assess_ai.get() == "none" and not app.ai_box.instate(["disabled"])
    assert list(app.ai_box["values"]) == [*gui.AIS, "openai"] and root.shown == []


def test_a_saved_ai_or_model_no_longer_offered_is_an_error(root):
    """A saved AI no longer offered (any-llm's provider gone), or a saved
    model its AI no longer reports, is said in an error, and replaced: by no
    AI, by the AI's default model."""
    app = App(root, Settings(mode="files", assess="mistral/large"))
    settle(root, app)
    assert "The AI saved, mistral, is not available" in root.shown[0]
    assert app.assess_ai.get() == "none" and app.collect().assess == ""
    root.shown.clear()
    app = App(root, Settings(mode="files", assess="claude/opus-3"))
    settle(root, app, "claude")
    assert root.shown == [
        "The model saved, opus-3, is not one claude offers any more: its default, "
        "default, is chosen instead."
    ]
    assert app.assess_model.get() == "default" and app.collect().assess == "claude"


def test_ai_model_and_effort_as_the_ai_reports(root):
    """The AI assessment: the AI, then its model and effort among those it
    reports, each AI's own default chosen (Claude's "default", Codex's
    default model and that model's default effort), making the --assess
    spec; a saved choice comes back as it was."""
    app = App(root, Settings(mode="files"))
    settle(root, app)
    ais = ["none", "claude", "codex", "ollama", "anthropic", "openai"]
    assert list(app.ai_box["values"]) == ais
    assert app.assess_spec() == "" and app.model_box.instate(["disabled"])
    # the switches of the assessment, greyed out without an AI; marking on
    assert all(s.instate(["disabled"]) for s in app.ai_switches)
    assert app.collect().assess_annotate is True
    app.assess_ai.set("claude")
    settle(root, app, "claude")
    assert not any(s.instate(["disabled"]) for s in app.ai_switches)
    assert list(app.model_box["values"]) == ["default", "opus", "haiku"]
    assert app.assess_model.get() == "default" and app.assess_spec() == "claude"
    # Claude says no default effort: "default", its own, shown, not an empty box
    assert list(app.effort_box["values"]) == ["default", "low", "max"]
    assert app.assess_effort.get() == "default" and app.collect().assess_effort == ""
    assert "own default" in app.effort_hint("default")
    app.assess_model.set("opus")
    assert app.assess_effort.get() == "default"
    app.assess_effort.set("max")
    s = app.collect()
    assert (s.assess, s.assess_effort) == ("claude/opus", "max")
    app.assess_model.set("haiku")  # it reports no effort
    assert list(app.effort_box["values"]) == [] and app.assess_effort.get() == ""
    app.assess_ai.set("codex")
    settle(root, app, "codex")
    assert list(app.model_box["values"]) == ["gpt-6-astra", "gpt-5.5"]
    assert (app.assess_model.get(), app.assess_effort.get()) == ("gpt-6-astra", "low")
    assert app.model_hint("gpt-6-astra") == "GPT-6-Astra, Codex's default"
    assert app.effort_hint("low") == "Fast (the model's default)"
    app.assess_model.set("gpt-5.5")
    assert app.assess_effort.get() == "medium"
    app.assess_ai.set("ollama")
    settle(root, app, "ollama")
    assert app.assess_spec() == "ollama/gemma3:270m"
    app.assess_ai.set("openai")  # its models unknown without a key: typed
    settle(root, app, "openai")
    assert "no API key" in app.status.get()
    app.assess_model.set("gpt-5")
    assert app.assess_spec() == "openai/gpt-5"
    app.assess_context.set("changes")
    app.assess_instructions.set("Be brief.")
    assert app.collect().assess_save_prompt is False  # off by default
    app.assess_save_prompt.set(True)
    s = app.collect()
    assert (s.assess_context, s.assess_instructions) == ("changes", "Be brief.")
    assert s.assess_save_prompt is True
    again = App(
        root,
        Settings(
            mode="files", assess="codex/gpt-5.5", assess_effort="low", assess_context="changes"
        ),
    )
    settle(root, again, "codex")
    assert (again.assess_ai.get(), again.assess_model.get()) == ("codex", "gpt-5.5")
    assert (again.assess_effort.get(), again.assess_context.get()) == ("low", "changes")
    again.reset_options()
    assert again.assess_spec() == "" and again.collect().assess_context == "document"


def test_window_loads_a_repository(root, history):
    b, shas = history
    app = App(root, Settings(repo=str(b.path)))
    assert app.target.get() == WORKTREE
    assert shas[4][:7] in app.base.get()
    assert list(app.target_box["values"])[:2] == [WORKTREE, INDEX]
    s = app.collect()
    assert (s.mode, s.base, s.target) == ("git", shas[4], "worktree")
    # a ref typed by hand goes through as it is
    app.base.set("HEAD~2")
    assert app.collect().base == "HEAD~2"
    # the move similarity, kept within 0.05 and 1
    app.move_similarity.set(0.6)
    assert app.collect().move_similarity == 0.6
    app.move_similarity.set(3)
    assert app.collect().move_similarity == 1.0
    # moved passages followed by default, and the switch turns them off
    assert app.collect().move_passages
    app.move_passages.set(False)
    assert not app.collect().move_passages
    # untracked files only make sense with the working tree
    app.target.set(INDEX)
    app.update_untracked()
    assert app.untracked_box.instate(["disabled"])


def test_window_rejects_a_folder_that_is_not_a_repository(root, tmp_path):
    app = App(root, Settings(repo=str(tmp_path)))
    assert "Not a git repository" in app.status.get()


def test_save_to_follows_the_new_side(root, tmp_path):
    """Save to is, by default, next to the new file, or in the new folder, and
    follows them as they change, swapped too; kept as "" so it follows next
    time; a file chosen stays where it is."""
    old, new = two_files(tmp_path, "a\n", "b\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    moved = elsewhere / "c.md"
    moved.write_text("c\n", encoding="utf-8")
    app = App(root, Settings(mode="files", old=str(old), new=str(new)))
    assert app.output.get() == str(tmp_path / "a_vs_b.html")
    assert app.collect().output == ""
    app.new.set(str(moved))
    assert app.output.get() == str(elsewhere / "a_vs_c.html")
    gui.swap(app.old, app.new)
    assert app.output.get() == str(tmp_path / "c_vs_a.html")
    app.output_format.set("diff")
    app.rename_output()
    assert app.output.get() == str(tmp_path / "c_vs_a.diff")
    app.mode.set("folders")
    app.old_folder.set(str(tmp_path))
    app.new_folder.set(str(elsewhere))
    assert app.output.get() == str(elsewhere / "prosediff.diff")
    chosen = str(tmp_path / "mine.diff")
    app.output.set(chosen)
    app.new_folder.set(str(tmp_path))
    assert app.output.get() == chosen and app.collect().output == chosen


def test_the_ai_card_is_greyed_out_but_for_the_html_report(root, monkeypatch):
    """The AI assessment goes in the HTML report only: for a .diff or a
    .wdiff its card is greyed out whole, and no AI is asked."""
    monkeypatch.setattr(gui, "models_of", lambda ai: [])
    app = App(root, Settings(mode="files", assess="codex/gpt-5.5"))
    assert not app.ai_box.instate(["disabled"])
    app.output_format.set("wdiff")
    assert app.ai_box.instate(["disabled"]) and app.model_box.instate(["disabled"])
    assert all(s.instate(["disabled"]) for s in app.ai_switches)
    assert app.collect().assess == "codex/gpt-5.5"  # kept for when HTML is back
    app.output_format.set("html")
    assert not app.ai_box.instate(["disabled"])
    assert all(not s.instate(["disabled"]) for s in app.ai_switches)


def test_format_renames_the_output(root):
    """Choosing a format gives the Save to file its extension; a name of
    another kind, or none, is left as it is."""
    app = App(root, Settings(output="C:/p/a_vs_b.html"))
    for fmt, expected in (
        ("diff", "a_vs_b.diff"),
        ("wdiff", "a_vs_b.wdiff"),
        ("html", "a_vs_b.html"),
    ):
        app.output_format.set(fmt)
        app.rename_output()
        assert Path(app.output.get()).name == expected
    for kept in ("changes.patch", "notes.txt", ""):
        app.output.set(kept)
        app.output_format.set("diff")
        app.rename_output()
        assert app.output.get() == kept


def test_tracked_formats_greyed_for_other_files(root):
    """Word, tracked and OpenDocument, tracked are greyed out when two files
    are compared that are not both of their kind; a repository or folders
    keep them, their files known only once compared."""
    app = App(root, Settings(mode="files", old="a.md", new="b.docx"))

    def enabled(fmt):
        return not app.format_buttons[fmt].instate(["disabled"])

    assert not enabled("docx") and not enabled("odt") and enabled("html")
    app.old.set("C:/p/a.DOCX")
    assert enabled("docx") and not enabled("odt")
    app.old.set("a.odt")
    app.new.set("b.odt")
    assert enabled("odt") and not enabled("docx")
    app.mode.set("git")
    assert enabled("docx") and enabled("odt")


def test_mode_switch(root):
    """The segmented button shows the fields of one source at a time, and
    what it shows is what is compared."""
    app = App(root, Settings(mode="files"))
    root.update_idletasks()
    assert app.sides["files"].winfo_manager() == "pack"
    assert not app.sides["folders"].winfo_manager()
    app.mode.set("folders")
    app.show_mode()
    assert app.sides["folders"].winfo_manager() == "pack"
    assert not app.sides["files"].winfo_manager()
    assert app.collect().mode == "folders"


def test_comments_choice(root, tmp_path):
    """One choice for the comments; comments without text are a choice of
    markers only."""

    app = App(root, Settings(comments="none"))
    assert app.collect().comments == "none"
    assert app.empty_comments_box.instate(["disabled"])
    app.comments.set("markers")
    assert not app.empty_comments_box.instate(["disabled"])


def test_invalid_arguments_show_an_error_and_exit(monkeypatch, tmp_path):
    from prosediff import gui

    shown = []

    class NoWindow:  # a second real Tk is unreliable on Windows (see tk_root)
        def withdraw(self):
            pass

        def destroy(self):
            shown.append("destroyed")

    monkeypatch.setattr(gui.tk, "Tk", NoWindow)
    monkeypatch.setattr(gui, "own_taskbar_button", lambda: None)
    monkeypatch.setattr(gui, "set_icon", lambda root: None)
    monkeypatch.setattr(gui, "load_settings", Settings)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg, **kw: shown.append(msg))
    monkeypatch.setattr(gui, "App", lambda *a: pytest.fail("the window must not open"))
    (tmp_path / "a b.docx").write_text("")
    with pytest.raises(SystemExit) as exited:
        gui.main([str(tmp_path / "notes.txt"), str(tmp_path / "a b.docx")])
    assert exited.value.code == 2
    assert shown[0].startswith("Two arguments must be two Markdown, Word or OpenDocument files")
    assert "Usage: prosediff-gui" in shown[0] and shown[1] == "destroyed"
    assert (
        f"Received 2 arguments:\n1. “{tmp_path / 'notes.txt'}”  (not found)\n"
        f"2. “{tmp_path / 'a b.docx'}”\n\n" in shown[0]
    )


def test_the_window_has_the_logo(tk_root):
    from pathlib import Path

    from prosediff import gui

    here = Path(gui.__file__).parent
    assert (here / "logo.ico").is_file() and (here / "logo.png").is_file()
    if sys.platform != "win32":
        gui.set_icon(tk_root)  # only that the .png loads without an error
        return
    # Tk does not report an .ico back: ask Windows for the window's icons
    import ctypes

    tk_root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(tk_root.winfo_id())

    def icons():
        send = ctypes.windll.user32.SendMessageW
        return [send(hwnd, 0x7F, which, 0) for which in (0, 1)]  # WM_GETICON small, big

    tk_feather = icons()
    gui.set_icon(tk_root)
    assert all(icons()) and all(a != b for a, b in zip(icons(), tk_feather, strict=True))


def test_linux_colour_scheme_from_the_portal(monkeypatch):
    """On Linux the desktop portal says light or dark, through ReadOne, or the
    deprecated Read on an older portal (its answer a variant within a
    variant); no portal, no answer."""
    import types

    class Refused(Exception):
        pass

    def fake_jeepney(replies):
        calls = []

        class Bus:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def send_and_get_reply(self, call, timeout=None):
                assert timeout == gui.SETTING_TIMEOUT
                calls.append(call)
                answer = replies[call]
                if answer is Refused:
                    raise Refused(call)
                return types.SimpleNamespace(body=(answer,))

        jeepney = types.ModuleType("jeepney")
        jeepney.DBusAddress = lambda *a, **k: None
        jeepney.DBusErrorResponse = Refused
        jeepney.new_method_call = lambda obj, method, sig, body: method
        blocking = types.ModuleType("jeepney.io.blocking")
        blocking.open_dbus_connection = lambda bus, auth_timeout: Bus()
        io = types.ModuleType("jeepney.io")
        for name, module in (
            ("jeepney", jeepney),
            ("jeepney.io", io),
            ("jeepney.io.blocking", blocking),
        ):
            monkeypatch.setitem(sys.modules, name, module)
        return calls

    fake_jeepney({"ReadOne": ("u", 1)})
    assert gui.portal_color_scheme() == gui.PREFER_DARK
    calls = fake_jeepney({"ReadOne": Refused, "Read": ("v", ("u", 2))})
    assert gui.portal_color_scheme() == 2 and calls == ["ReadOne", "Read"]
    monkeypatch.setattr(sys, "platform", "linux")
    fake_jeepney({"ReadOne": ("u", 1)})
    assert gui.system_dark()
    monkeypatch.setitem(sys.modules, "jeepney", None)  # not installed
    assert gui.portal_color_scheme() is None and not gui.system_dark()


def test_options_saved_only_when_asked_and_reset(root, tmp_path, monkeypatch):
    """Compare saves nothing; Save options writes the choices, Reset to
    defaults puts every option back, leaving what is compared alone."""
    from prosediff import gui

    f = tmp_path / "gui.json"
    monkeypatch.setattr(gui, "settings_file", lambda: f)
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(b"One.\n")
    new.write_bytes(b"Two.\n")
    app = App(root, Settings(mode="files", old=str(old), new=str(new), open_page=False))
    app.comments.set("none")
    app.move_algorithm.set("token-set")
    app.output_format.set("wdiff")
    app.run()
    finish(root, app)
    assert app.button["text"] == "Compare" and "changed" in app.status.get()
    assert not f.exists()
    app.save_options()
    saved = load_settings(f)
    assert (saved.comments, saved.move_algorithm) == ("none", "token-set")
    assert b"\r" not in f.read_bytes()
    app.reset_options()
    s = app.collect()
    assert (s.comments, s.move_algorithm, s.output_format) == ("markers", None, "html")
    assert s.old == str(old)


def test_a_failed_comparison_shows_an_error(root, tmp_path, monkeypatch):
    """An error in the comparison's process reaches the window: an error
    box, and the Compare button back, not a window waiting forever."""
    shown = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, text: shown.append(text))
    app = App(
        root,
        Settings(
            mode="files",
            old=str(tmp_path / "a.md"),
            new=str(tmp_path / "gone.md"),
            open_page=False,
        ),
    )
    app.run()
    finish(root, app)
    assert app.button["text"] == "Compare"
    assert app.status.get() == "Not compared."
    assert shown and "no such file or folder" in shown[0]


def test_move_settings_of_paragraphs_and_sentences(root, tmp_path):
    """Paragraphs and sentences have their own moved-line settings, shown at
    their defaults and remembered only when changed; comparing both ways
    writes an HTML report holding both, and a diff paragraph by paragraph."""
    from prosediff.diff import move_defaults

    app = App(root, Settings())
    shown = lambda sim, algo: (round(sim.get(), 6), algo.get())  # noqa: E731
    assert shown(app.move_similarity, app.move_algorithm) == move_defaults(False)
    assert shown(app.sentence_move_similarity, app.sentence_move_algorithm) == move_defaults(True)
    app.sentence_move_similarity.set(0.65)
    app.move_algorithm.set("token-set")
    app.split.set("both")
    s = app.collect()
    assert (s.move_similarity, s.sentence_move_similarity, s.split) == (None, 0.65, "both")
    assert (s.move_algorithm, s.sentence_move_algorithm) == ("token-set", None)
    old, new = tmp_path / "a.md", tmp_path / "b.md"
    old.write_bytes(b"One sentence here. Another one there.\n")
    new.write_bytes(b"Another one there. One sentence here.\n")
    s.mode, s.old, s.new, s.output = "files", str(old), str(new), str(tmp_path / "r.html")
    path = generate(s).path
    page = path.read_text(encoding="utf-8")
    assert 'data-split="paragraph"' in page and 'data-split="sentence"' in page
    # a diff holds one split: both compares paragraph by paragraph
    s.output_format, s.output = "diff", str(tmp_path / "r.diff")
    path = generate(s).path
    assert path.suffix == ".diff" and path.read_text(encoding="utf-8")


def test_advanced_moved_passage_settings(root, tmp_path):
    """The advanced settings are hidden until asked for; only the values
    changed from prosediff's defaults are kept, and saved."""
    app = App(root, Settings(mode="files"))
    root.update()
    assert app.advanced_window.state() == "withdrawn"
    app.toggle_advanced()
    root.update()
    assert app.advanced_window.state() != "withdrawn"  # "zoomed" on macOS
    app.toggle_advanced()
    root.update()
    assert app.advanced_window.state() == "withdrawn"
    # one field for each setting, at its default
    assert set(app.passage_vars) == {f.name for f in fields(MovedPassageSettings)}
    assert app.passage_vars["max_pairs"].get() == f"{MOVED_PASSAGE_DEFAULTS.max_pairs:,}"
    assert app.collect().moved_passages == {}
    app.passage_vars["min_words"].set("6")
    app.passage_vars["partial_share"].set("0.5")
    app.passage_vars["rounds"].set("not a number")  # keeps the default
    s = app.collect()
    assert s.moved_passages == {"min_words": 6, "partial_share": 0.5}
    assert MovedPassageSettings.from_choices(s.moved_passages).min_words == 6
    path = tmp_path / "gui.json"
    assert save_settings(s, path)
    assert load_settings(path).moved_passages == {"min_words": 6, "partial_share": 0.5}
    app.reset_options()  # "Reset to defaults"
    assert app.collect().moved_passages == {}


def test_moved_passage_settings_loaded_and_checked(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(
        json.dumps({"moved_passages": {"min_words": 5, "unknown": 3, "rounds": "x"}}),
        encoding="utf-8",
    )
    assert load_settings(path).moved_passages == {"min_words": 5}
    path.write_text(json.dumps({"moved_passages": [1, 2]}), encoding="utf-8")
    assert load_settings(path).moved_passages == {}
    with pytest.raises(ValueError, match="partial_share"):
        MovedPassageSettings.from_choices({"partial_share": 3})


def test_documents_to_download(tmp_path, monkeypatch, root):
    """The documents made of the AI's problems go in the report unless
    switched off; the switch is greyed out while the problems are not
    marked in the text."""
    from test_aidocs import FIXED, assessment
    from test_tracked import pair

    old, new = pair(tmp_path, "docx")
    monkeypatch.setattr(pipeline, "assess_comparison", lambda c, request: assessment([FIXED]))
    s = Settings(mode="files", old=str(old), new=str(new), assess="claude", assess_preview=False)
    path = generate(s).path
    assert path.read_text(encoding="utf-8").count('class="ai-document"') == 2
    path = generate(replace(s, assess_documents=False)).path
    assert 'class="ai-document"' not in path.read_text(encoding="utf-8")
    app = App(root, Settings(mode="files"))
    settle(root, app)
    assert app.collect().assess_documents is True
    app.assess_ai.set("claude")
    settle(root, app, "claude")
    assert not app.documents_switch.instate(["disabled"])
    app.assess_annotate.set(False)
    assert app.documents_switch.instate(["disabled"])
    app.assess_annotate.set(True)
    app.assess_documents.set(False)
    assert app.collect().assess_documents is False


def test_one_file_reviewed(root, tmp_path, monkeypatch):
    """The "One file" tab: one file, reviewed by the AI chosen, into an
    HTML report next to it; the options of a comparison greyed out, the
    other formats too; no AI, no review."""
    from test_aidocs import FIXED
    from test_tracked import NEW, word_file

    path = word_file(tmp_path / "paper.docx", NEW)
    asked = []

    def assessed(c, request):
        asked.append((c, request))
        return Assessment("claude", "## Verdict\n**Good**.", annotations=[FIXED], kind="review")

    monkeypatch.setattr(pipeline, "assess_comparison", assessed)
    s = Settings(mode="review", single=str(path), assess="claude", assess_preview=False)
    stages = []
    done = generate(s, stages.append)
    out, c, a = done.path, done.comparison, done.assessment
    assert out == tmp_path / "paper_review.html" and c.single and a.verdict == "good"
    assert stages == [
        "Reading the file…",
        "Asking claude to review the file…",
        "Writing the report…",
    ]
    assert out.read_text(encoding="utf-8").count('class="ai-document"') == 1
    with pytest.raises(ValueError, match="choose an AI"):
        generate(replace(s, assess=""))
    with pytest.raises(ValueError, match="choose the file"):
        generate(replace(s, single=""))
    app = App(root, Settings(mode="files", output_format="wdiff"))
    settle(root, app)
    app.mode.set("review")
    app.show_mode()
    assert app.sides["review"].winfo_manager() == "pack"
    assert app.output_format.get() == "html" and app.button.cget("text") == "Review"
    assert all(app.format_buttons[f].instate(["disabled"]) for f in ("diff", "docx", "odt"))
    assert all(w.instate(["disabled"]) for w in app.comparing_only)
    app.single.set(str(path))
    assert app.output.get() == str(tmp_path / "paper_review.html")
    app.assess_ai.set("claude")
    settle(root, app, "claude")
    assert all(w.instate(["disabled"]) for w in app.changes_only)
    assert not app.documents_switch.instate(["disabled"])
    got = app.collect()
    assert got.mode == "review" and got.single == str(path)
    app.assess_ai.set(gui.NO_ASSESSMENT)
    app.run()
    assert app.job is None and "Choose an AI" in root.shown[-1]
    # a negative number of context lines refused, as the command line does
    app.context.set("-3")
    app.run()
    assert app.job is None and "Context lines must be 0 or more" in root.shown[-1]
    app.context.set("auto")
    app.mode.set("files")
    app.show_mode()
    assert app.button.cget("text") == "Compare"
    assert not any(w.instate(["disabled"]) for w in app.comparing_only)


def test_the_window_has_the_options_of_the_command_line(root, tmp_path):
    """The Markdown filter, the hidden lines and the AI's timeout, as
    --md-filter, --max-hidden and --assess-timeout: kept with the other
    options, put in the run, refused out of range as the command line
    refuses them, and reset to their defaults."""
    from prosediff.assess import ASSESS_TIMEOUT
    from prosediff.diff import MAX_HIDDEN
    from prosediff.gui import run_of

    old, new = two_files(tmp_path, "One.\n", "Two.\n")
    s = Settings(mode="files", old=str(old), new=str(new), md_filter="cat", max_hidden=7)
    app = App(root, replace(s, assess_timeout=90))
    got = app.collect()
    assert (got.md_filter, got.max_hidden, got.assess_timeout) == ("cat", 7, 90)
    run = run_of(replace(got, assess="claude"))
    assert run.options.md_filter == "cat" and run.options.max_hidden == 7
    assert run.request.timeout == 90
    app.max_hidden.set("-1")
    app.run()
    assert app.job is None and "Hidden lines must be 0 or more" in root.shown[-1]
    app.max_hidden.set("7")
    app.assess_timeout.set("0")
    app.run()
    assert app.job is None and "timeout must be above 0" in root.shown[-1]
    app.reset_options()
    got = app.collect()
    assert (got.md_filter, got.max_hidden, got.assess_timeout) == ("", MAX_HIDDEN, ASSESS_TIMEOUT)


def test_a_failed_ai_writing_assessment_is_shown(root, tmp_path, monkeypatch):
    """The AI-writing assessment failing is said, as the command line says
    it: its error goes back with the result, and the window shows it."""
    from prosediff.gui import run_job

    def assessed(c, request, kind="value"):
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
    app = App(root, s)
    app.job, app.job_settings = object(), s
    app.handle(kind, result)
    assert "The AI-writing assessment failed: no login" in root.shown


def test_a_number_box_steps_the_number_it_shows(root):
    """A box showing 250,000 steps from 250,000, not from 250 (as Tk's own
    arrows read it), keeps its thousands separated, and stays in range."""
    from prosediff.gui import number_box

    var = tk.StringVar(value="250,000")
    box = number_box(root, var, 0, 1_000_000, 100)
    box.event_generate("<<Decrement>>")
    assert var.get() == "249,900"
    var.set("999,950")
    box.event_generate("<<Increment>>")
    assert var.get() == "1,000,000"
    var.set("none")
    box.event_generate("<<Increment>>")
    assert var.get() == "100"
