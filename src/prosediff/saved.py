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


def _fingerprints(run: Run) -> dict[str, str]:
    """The SHA-256 of each file compared or reviewed (not of folders or a
    repository), to tell whether they changed since."""
    out = {}
    if run.mode in ("files", "review"):
        for p in filter(None, (run.old, run.new)):
            with contextlib.suppress(OSError):
                out[str(Path(p).resolve())] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return out


def save(run: Run, report: Path, assessment: Assessment, writing: Assessment | None) -> Path:
    """Keep the answers beside report (saved_path); its path. OSError when
    it cannot be written."""
    absolute = {k: str(Path(getattr(run, k)).resolve()) for k in ("old", "new") if getattr(run, k)}
    data = {
        "prosediff": package_version(),
        "saved": datetime.now().isoformat(timespec="seconds"),
        "run": {**asdict(run), **absolute, "output": str(report.resolve())},
        "inputs": _fingerprints(run),
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


def load(path: str | Path) -> tuple[Run, Assessment, Assessment | None, list[str]]:
    """The run and the answers kept in path, and what to warn of: the
    files compared that changed since (the AI's marks may no longer fit
    them). SavedError for a file that is not one of these."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        run = _run(data["run"])
        assessment = _assessment(data["assessment"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SavedError(f"{path.name}: not answers prosediff saved ({e})") from e
    if assessment is None:
        raise SavedError(f"{path.name}: it holds no assessment")
    warnings = []
    for file, digest in (data.get("inputs") or {}).items():
        try:
            now = hashlib.sha256(Path(file).read_bytes()).hexdigest()
        except OSError:
            warnings.append(f"{file} is gone")
            continue
        if now != digest:
            warnings.append(f"{file} changed since the AI read it: its marks may not fit")
    return run, assessment, _assessment(data.get("writing")), warnings
