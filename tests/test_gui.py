"""The window: choosing the sides, generating the page, remembering choices."""

import sys
import tkinter as tk

import pytest

from prosediff import gui
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


def test_arguments_prefill_two_files(tmp_path):
    a, d = tmp_path / "v1.md", tmp_path / "v2.DOCX"
    a.write_text("x")
    d.write_bytes(b"x")
    s, note = settings_from_args([str(a), str(d)], Settings(output="elsewhere.html"))
    assert note == "" and s.mode == "files" and (s.old, s.new) == (str(a), str(d))
    # the page goes next to the last file, named after both
    assert s.output == str(tmp_path / "v1_vs_v2.html")


def test_arguments_prefill_two_folders(tmp_path):
    a, b = tmp_path / "submitted", tmp_path / "revised"
    a.mkdir()
    b.mkdir()
    s, note = settings_from_args([str(a), str(b)], Settings(mode="git"))
    assert note == "" and s.mode == "files" and (s.old, s.new) == (str(a), str(b))
    assert s.output == str(b / "prosediff.html")
    # a folder and a file do not make a pair
    (tmp_path / "v1.md").write_text("x")
    _, note = settings_from_args([str(a), str(tmp_path / "v1.md")], Settings())
    assert "or two folders" in note


@pytest.mark.parametrize(
    "args,message",
    [
        (["plain"], "Not a folder"),
        (["x.txt", "y.txt"], "two Markdown, Word or OpenDocument files"),
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
    from prosediff.gui import single_file

    sent = tmp_path / "draft.docx"
    sent.write_bytes(b"x")
    s, note = settings_from_args([str(sent)], Settings(mode="git"))
    assert note == "" and s.mode == "files" and (s.old, s.new) == (str(sent), "")
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
        assert s.output == str(tmp_path / "sent_vs_returned.html")


def test_context_lines_box():
    from prosediff.gui import context_of

    assert context_of(Settings()) == "auto"
    assert context_of(Settings(context_lines="2")) == 2
    assert context_of(Settings(context_lines="nonsense")) == "auto"
    assert context_of(Settings(context_lines="2", full=True)) is None


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
        path, c = generate(Settings(repo=str(b.path), base=shas[0], target=target, output=str(out)))
        assert path == out and out.read_bytes().startswith(b"<!DOCTYPE html>")
        short = {"worktree": "working tree", "index": "index"}.get(target, shas[4][:7])
        assert c.target.short == short


def test_generate_files_and_default_output(tmp_path):
    """Two files make a page in the temporary folder by default, compared
    sentence by sentence when asked; without both files, an error."""
    moved = "Firms that adopted the new technology are compared with the others."
    (tmp_path / "a.md").write_text(f"First paragraph here. {moved}\n\nSecond paragraph.\n")
    (tmp_path / "b.md").write_text(f"First paragraph here.\n\nSecond paragraph. {moved}\n")
    s = Settings(mode="files", old=str(tmp_path / "a.md"), new=str(tmp_path / "b.md"))
    path, c = generate(s)
    assert path.parent.name == "prosediff" and path.suffix == ".html" and len(c.files) == 1
    assert c.moved == 0
    s.by_sentence = True
    assert generate(s)[1].moved == 1
    with pytest.raises(ValueError, match="old and the new"):
        generate(Settings(mode="files"))


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


@pytest.fixture
def root(tk_root):
    """A window of its own for each test."""
    w = tk.Toplevel(tk_root)
    w.withdraw()
    yield w
    w.destroy()


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
    # untracked files only make sense with the working tree
    app.target.set(INDEX)
    app.update_untracked()
    assert app.untracked_box.instate(["disabled"])


def test_window_rejects_a_folder_that_is_not_a_repository(root, tmp_path):
    app = App(root, Settings(repo=str(tmp_path)))
    assert "Not a git repository" in app.status.get()


def test_swap_files(root):
    app = App(root, Settings(mode="files", old="sent.docx", new="returned.docx"))
    app.swap_files()
    s = app.collect()
    assert (s.old, s.new) == ("returned.docx", "sent.docx")


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
    with pytest.raises(SystemExit) as exited:
        gui.main([str(tmp_path / "notes.txt")])
    assert exited.value.code == 2
    assert shown[0].startswith("Not a folder, a Markdown, Word or OpenDocument file")
    assert "Usage: prosediff-gui" in shown[0] and shown[1] == "destroyed"
    assert f"Received 1 argument:\n1. “{tmp_path / 'notes.txt'}”  (not found)" in shown[0]


def test_received_lists_the_arguments(tmp_path):
    from prosediff.gui import received

    (tmp_path / "a b.docx").write_text("")
    assert received([str(tmp_path / "a"), str(tmp_path / "a b.docx")]).splitlines() == [
        "Received 2 arguments:",
        f"1. “{tmp_path / 'a'}”  (not found)",
        f"2. “{tmp_path / 'a b.docx'}”",
    ]


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
