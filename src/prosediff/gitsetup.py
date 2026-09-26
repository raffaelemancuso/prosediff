"""prosediff --setup-git: git's own commands made to show documents as prose.

Two things are set, for one repository or (global) for every one:

- a textconv driver, "prosediff", with the attributes that give it the Word
  and OpenDocument files: git diff, git log -p and git show then show the
  Markdown prosediff reads a document into, instead of "Binary files
  differ" (the attributes of one repository go in .git/info/attributes,
  which is not committed; the global ones in git's global attributes file);
- a difftool, "prosediff": git difftool -t prosediff opens a prosediff page
  for each changed file, git difftool -d -t prosediff one page for them all.

Both run this Python with -m prosediff, so they work wherever prosediff was
installed, uv tool or virtual environment, whether its scripts are on PATH
or not.
"""

import os
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from prosediff.diff import run

# git config answers at once (seconds).
GIT_TIMEOUT = 60

DRIVER = "prosediff"
ATTRIBUTES = ("*.docx diff=prosediff", "*.odt diff=prosediff")


class SetupError(RuntimeError):
    """git could not be configured."""


def command() -> str:
    """This Python running prosediff, for git's shell: quoted, with forward
    slashes (git runs these commands with sh, on Windows too)."""
    return f'"{Path(sys.executable).as_posix()}" -m prosediff'


def git(*args: str, cwd: Path | None = None) -> str:
    try:
        done = run(["git", *args], cwd=cwd, text=True, encoding="utf-8", timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        raise SetupError("git is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise SetupError(f"git {' '.join(args)} took too long, and was stopped") from None
    if done.returncode not in (0, 1):  # 1: git config --get found nothing
        raise SetupError(done.stderr.strip() or f"git {' '.join(args)} failed")
    return done.stdout.strip()


def attributes_file(repo: Path | None) -> Path:
    """Where the attributes go: the repository's .git/info/attributes, or
    the global attributes file (core.attributesFile, else git's default)."""
    if repo is not None:
        path = git("rev-parse", "--path-format=absolute", "--git-path", "info/attributes", cwd=repo)
        return Path(path)
    configured = git("config", "--global", "--get", "core.attributesFile")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "git" / "attributes"


def setup_git(repo: Path | None) -> list[str]:
    """Set the textconv driver, its attributes and the difftool, for the
    repository repo belongs to, or for the user when repo is None. Returns
    what was set, one line each."""
    if repo is not None:
        top = git("rev-parse", "--show-toplevel", cwd=repo)
        if not top:
            raise SetupError(f"not a git repository: {repo}")
        repo = Path(top)
        scope = ["config", "--local"]
    else:
        scope = ["config", "--global"]
    run = command()
    settings = {
        f"diff.{DRIVER}.textconv": f"{run} --to-markdown",
        # two files, or with git difftool -d two folders
        f"difftool.{DRIVER}.cmd": (
            'if [ -d "$LOCAL" ]; then mode=--folders; else mode=--files; fi; '
            f'{run} "$mode" "$LOCAL" "$REMOTE" --open'
        ),
    }
    done = []
    for key, value in settings.items():
        git(*scope, key, value, cwd=repo)
        done.append(f"git {' '.join(scope)} {key} '{value}'")
    path = attributes_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [a for a in ATTRIBUTES if a not in lines]
    if missing:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if lines and path.read_bytes()[-1:] != b"\n":
                f.write("\n")
            f.write("\n".join(missing) + "\n")
    done += [f"{path}: {a}" for a in ATTRIBUTES]
    return done


def document_name(data: bytes, name: str) -> str:
    """The name to read a document under: its own when it says what it is,
    else .odt or .docx by its content (git may hand textconv a file whose
    name has lost its extension)."""
    if Path(name).suffix.lower() in (".docx", ".odt"):
        return name
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            if z.read("mimetype").startswith(b"application/vnd.oasis.opendocument.text"):
                return name + ".odt"
    except (zipfile.BadZipFile, KeyError):
        pass
    return name + ".docx"
