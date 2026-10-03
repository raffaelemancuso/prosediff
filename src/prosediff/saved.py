"""The AI's answers kept beside the HTML report that holds them, as
NAME.ai.json: the run that made the report (what was compared, and how)
and each assessment, as the AI made it. The report can then be made again
from them (--rebuild; in the window, the tab Rebuild), when
prosediff's report changes, without asking the AI a second time: the files
are compared again, the answers are not asked for again."""

import contextlib
import hashlib
import json
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from prosediff.assess import Annotation, Assessment, AssessRequest
from prosediff.diff import MovedPassageSettings, MoveSettings, Options
from prosediff.pipeline import Run
from prosediff.render import package_version

SAVED_SUFFIX = ".ai.json"
# The format of the answers kept; those of another (an earlier prosediff,
# whose fingerprints left out the context files and the files of two
# folders) are refused: the report cannot be shown to fit the text.
SAVED_FORMAT = 2


class SavedError(ValueError):
    """A file of saved answers that cannot be read."""


def saved_path(report: Path) -> Path:
    """Where the answers of the report at report are kept: NAME.ai.json."""
    return report.with_name(report.stem + SAVED_SUFFIX)


def _known(cls, values: dict) -> dict:
    """values without the keys cls has no field for (a file saved by
    another version of prosediff)."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in values.items() if k in names}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints(run: Run) -> dict[str, str]:
    """The SHA-256 of each file the run reads, by its absolute path, to tell
    whether it changed since: the two files compared or the one reviewed;
    each file of two folders compared (as include picks them); and the
    other files sent to the AI as context. Not a repository's: a commit
    does not change."""
    files: list[Path] = []
    if run.mode in ("files", "review"):
        files += [Path(p) for p in (run.old, run.new) if p]
    elif run.mode == "folders":
        from prosediff.sources import read_side

        for side in (run.old, run.new):
            with contextlib.suppress(OSError):
                files += [Path(side) / rel for rel in read_side(Path(side), run.include)]
    if run.request is not None:
        files += [Path(p) for p in run.request.files]
    out = {}
    for p in files:
        with contextlib.suppress(OSError):
            out[str(p.resolve())] = _sha256(p)
    return out


def save(
    run: Run,
    report: Path,
    assessment: Assessment,
    writing: Assessment | None,
    inputs: dict[str, str] | None = None,
) -> Path:
    """Keep the answers beside report (saved_path); its path. inputs: the
    fingerprints of the files the run read, taken as it read them (else
    now). OSError when it cannot be written."""
    absolute = {k: str(Path(getattr(run, k)).resolve()) for k in ("old", "new") if getattr(run, k)}
    data = {
        "format": SAVED_FORMAT,
        "prosediff": package_version(),
        "saved": datetime.now().isoformat(timespec="seconds"),
        "run": {**asdict(run), **absolute, "output": str(report.resolve())},
        "inputs": fingerprints(run) if inputs is None else inputs,
        "assessment": asdict(assessment),
        "writing": asdict(writing) if writing is not None else None,
    }
    path = saved_path(report)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return path


def _assessment(values: dict | None) -> Assessment | None:
    if not values:
        return None
    a = Assessment(**_known(Assessment, values))
    a.annotations = [Annotation(**_known(Annotation, n)) for n in values.get("annotations", [])]
    return a


def _run(values: dict) -> Run:
    o = values.get("options") or {}
    options = Options(
        **{
            **_known(Options, o),
            "paragraph_moves": MoveSettings(**o.get("paragraph_moves", {})),
            "sentence_moves": MoveSettings(**o.get("sentence_moves", {})),
            "moved_passage_settings": MovedPassageSettings(
                **_known(MovedPassageSettings, o.get("moved_passage_settings", {}))
            ),
        }
    )
    r = values.get("request")
    request = (
        AssessRequest(**{**_known(AssessRequest, r), "files": tuple(r.get("files") or ())})
        if r
        else None
    )
    return Run(
        **{
            **_known(Run, values),
            "output": Path(values["output"]),
            "options": options,
            "request": request,
        }
    )


def load(path: str | Path) -> tuple[Run, Assessment, Assessment | None]:
    """The run and the answers kept in path (NAME.ai.json, or a project
    holding them). SavedError for a file that is not one of these, and for
    one whose files are gone or changed since the AI read them (their
    SHA-256 another), the context files sent to it too: the AI's marks are
    of the text it read, and put on another they may fit it wrongly."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SavedError(f"{path.name}: not answers prosediff saved ({e})") from e
    if isinstance(data, dict) and data.get("kind") == PROJECT_KIND:
        if not data.get("report"):
            raise SavedError(f"{path.name}: the project holds no report to make again")
        data = data["report"]
    current(data, path)
    try:
        run = _run(data["run"])
        assessment = _assessment(data["assessment"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SavedError(f"{path.name}: not answers prosediff saved ({e})") from e
    if assessment is None:
        raise SavedError(f"{path.name}: it holds no assessment")
    for file, digest in (data.get("inputs") or {}).items():
        try:
            now = hashlib.sha256(Path(file).read_bytes()).hexdigest()
        except OSError:
            raise SavedError(f"{file} is gone: the report cannot be made again") from None
        if now != digest:
            raise SavedError(
                f"{file} changed since the AI read it (its checksum is another): the "
                "report is not made again, its marks would not fit the text; ask the AI again"
            )
    return run, assessment, _assessment(data.get("writing"))


# A project (NAME.prosediff, JSON): the window's settings, every one (what
# is compared, the context files, the options, the AI), and the answers of
# the last report the AI assessed with them (as NAME.ai.json holds them,
# the fingerprints of the files read included), to make it again while
# those files are unchanged.
PROJECT_SUFFIX = ".prosediff"
PROJECT_KIND = "prosediff project"


def answers(path: str | Path) -> dict:
    """The answers kept in NAME.ai.json, as they are, for a project to hold.
    SavedError when it cannot be read."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SavedError(f"{Path(path).name}: not answers prosediff saved ({e})") from e
    if not isinstance(data, dict) or "run" not in data or "assessment" not in data:
        raise SavedError(f"{Path(path).name}: not answers prosediff saved")
    current(data, Path(path))
    return data


def current(data: object, path: Path) -> None:
    """SavedError for answers kept in a format other than SAVED_FORMAT."""
    if not isinstance(data, dict) or data.get("format") != SAVED_FORMAT:
        raise SavedError(
            f"{path.name}: saved by an earlier prosediff, whose answers do not hold the "
            "checksums of every file the AI read: the report cannot be made again from "
            "them; ask the AI again"
        )


def save_project(
    path: str | Path, settings: dict, report: dict | None, choices: dict | None = None
) -> None:
    """Write a project: settings (the window's, as gui.Settings holds them),
    report (answers, or None), and the choices made in that report's page
    (which problems are in the documents to download, resolved, their fix
    undone; None: none made). OSError when it cannot be written."""
    data = {
        "kind": PROJECT_KIND,
        "prosediff": package_version(),
        "saved": datetime.now().isoformat(timespec="seconds"),
        "settings": settings,
        "report": report,
        "choices": choices if report else None,
    }
    _write(Path(path), data)


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def _project(path: Path) -> dict:
    """A project as saved; SavedError for a file that is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SavedError(f"{path.name}: not a prosediff project ({e})") from e
    if not isinstance(data, dict) or data.get("kind") != PROJECT_KIND:
        raise SavedError(f"{path.name}: not a prosediff project")
    return data


def load_project(path: str | Path) -> tuple[dict, dict | None]:
    """A project's settings and report (None when it holds none).
    SavedError for a file that is not a project."""
    data = _project(Path(path))
    return data.get("settings") or {}, data.get("report")


def project_choices(path: str | Path) -> dict | None:
    """The choices made in a project's report (save_project); None when it
    holds none. SavedError for a file that is not a project."""
    choices = _project(Path(path)).get("choices")
    return choices if isinstance(choices, dict) else None


def save_choices(path: str | Path, choices: dict) -> None:
    """The choices made in a project's report written into it, the rest as
    it was saved: settings changed in the window since are not saved with
    them. SavedError for a file that is not a project, OSError when it
    cannot be written."""
    data = _project(Path(path))
    data["choices"] = choices
    _write(Path(path), data)
