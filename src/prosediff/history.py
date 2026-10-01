"""How long the AI took, run by run (a line of JSON each, in prosediff's
settings folder), for an estimate of the next run before it is asked:
"usually 2-4 minutes". The same AI, model, effort and kind of assessment
only; none until a few runs are known."""

import json
import os
from datetime import datetime
from pathlib import Path

from prosediff.assess import Assessment

# The runs kept, the newest; the runs an estimate needs, and those it uses.
HISTORY_KEEP = 200
MIN_RUNS = 3
RECENT_RUNS = 20


def config_dir() -> Path:
    """prosediff's settings folder: %APPDATA%\\prosediff on Windows,
    $XDG_CONFIG_HOME/prosediff or ~/.config/prosediff elsewhere."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "prosediff"


def history_file() -> Path:
    return config_dir() / "assess_history.jsonl"


def record(a: Assessment, prompt_chars: int) -> None:
    """Note how long an assessment took; a failed one is not noted, nor
    anything when the file cannot be written."""
    if a.error or a.seconds <= 0:
        return
    run = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "ai": a.backend,
        "effort": a.effort,
        "kind": a.kind,
        "prompt_chars": prompt_chars,
        "seconds": round(a.seconds, 1),
        "output_tokens": a.output_tokens,
    }
    path = history_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        lines = [*lines, json.dumps(run)][-HISTORY_KEEP:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass


def estimate(ai: str, effort: str, kind: str) -> tuple[float, float] | None:
    """How many seconds the next such assessment usually takes: the middle
    half of the recent runs alike; None with fewer than MIN_RUNS."""
    try:
        lines = history_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    seconds = []
    for line in lines:
        try:
            run = json.loads(line)
        except ValueError:
            continue
        if (run.get("ai"), run.get("effort", ""), run.get("kind")) == (ai, effort, kind):
            seconds.append(float(run["seconds"]))
    seconds = sorted(seconds[-RECENT_RUNS:])
    if len(seconds) < MIN_RUNS:
        return None
    n = len(seconds)
    return seconds[n // 4], seconds[(3 * n) // 4 if n > 3 else n - 1]


def usually(span: tuple[float, float] | None) -> str:
    """An estimate in words: "usually 40-70 seconds", "usually 2-4
    minutes", "usually about 3 minutes"; "" without one."""
    if span is None:
        return ""
    low, high = span
    if high < 90:
        low, high, unit = max(1, round(low / 5) * 5), max(5, round(high / 5) * 5), "seconds"
    else:
        low, high, unit = max(1, round(low / 60)), max(1, round(high / 60)), "minutes"
    if low >= high:
        return f"usually about {high} {unit}"
    return f"usually {low}–{high} {unit}"
