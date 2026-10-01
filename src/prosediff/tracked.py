"""A Comparison as a document of tracked changes: one Word document compared
with another, written as a .docx, or one OpenDocument text with another,
written as an .odt. The output is a copy of the new file with each change
since the old one marked in it as the word processor's own tracked change
(a revision), all else kept, which Word or LibreOffice then accepts or
rejects one by one (prosediff.redline).

Nothing else can be written so: text or Markdown, several files at once, a
Word document against an OpenDocument text, or a document written in the
other format, are refused (check_paths, check_tracked).
"""

import datetime as dt
import warnings
from pathlib import Path

from prosediff.diff import Comparison, FileDiff
from prosediff.sources import FILE_STAMP, suffix_of

# The documents of tracked changes, by the name --format gives them, and the
# suffix of the files each is made from.
TRACKED_FORMATS = {"docx": ".docx", "odt": ".odt"}
# Who the changes are by, when the new side names no author.
AUTHOR = "prosediff"
NAMES = {"docx": "a Word document", "odt": "an OpenDocument text"}


def refusal(fmt: str, why: str) -> ValueError:
    suffix = TRACKED_FORMATS[fmt]
    return ValueError(
        f"tracked changes as {suffix} compare {NAMES[fmt]} with {NAMES[fmt]} "
        f"({suffix} with {suffix}): {why}"
    )


def check_paths(old: str | Path, new: str | Path, fmt: str) -> None:
    """Refuse two files not both of fmt's kind, before comparing them
    (ValueError)."""
    suffix = TRACKED_FORMATS[fmt]
    for side, path in (("old", old), ("new", new)):
        if suffix_of(path) != suffix:
            raise refusal(fmt, f"the {side} one is {Path(path).name}")


def check_tracked(comparison: Comparison, fmt: str) -> FileDiff:
    """The one file of a comparison that can be written as fmt: a document of
    that kind on both sides; ValueError for anything else."""
    suffix = TRACKED_FORMATS[fmt]
    files = [f for f in comparison.files if not f.binary]
    if len(files) != 1:
        raise refusal(fmt, f"{len(files):,} files changed")
    f = files[0]
    for side, data, path in (("old", f.old_data, f.old_path), ("new", f.new_data, f.new_path)):
        if not data or suffix_of(path) != suffix:
            raise refusal(fmt, f"the {side} side is {path or 'missing'}")
    return f


def settled(f: FileDiff, fmt: str) -> bool:
    """Whether f can be written as fmt: an .odt only with the tracked
    changes the files have accepted."""
    return fmt != "odt" or f.document_changes == "accept-all"


def author_of(comparison: Comparison) -> str:
    return comparison.target.author or AUTHOR


def save(write, path: Path) -> Path:
    """Write the document with write(path); returns where it went. When
    path is open in Word or LibreOffice, which lock it, beside it instead,
    as NAME__locked_YYYYMMDD_HHMMSS.docx (or .odt), with a warning: one
    open document must not cost the comparison."""
    try:
        write(str(path))
        return path
    except PermissionError:
        stamp = dt.datetime.now().strftime(FILE_STAMP)
        other = path.with_name(f"{path.stem}__locked_{stamp}{path.suffix}")
        write(str(other))
        warnings.warn(f"{path} is open and locked: written to {other} instead", stacklevel=2)
        return other


def write_tracked(comparison: Comparison, path: Path, fmt: str) -> Path:
    """Write the document of tracked changes as fmt, one of TRACKED_FORMATS;
    returns where it went (save). ValueError when the comparison is not of
    one such document with another (check_tracked)."""
    if fmt not in TRACKED_FORMATS:
        raise ValueError(f"format must be one of {tuple(TRACKED_FORMATS)}, not {fmt!r}")
    f = check_tracked(comparison, fmt)
    if not settled(f, fmt):
        raise ValueError(
            "tracked changes as .odt accept the tracked changes the files have: "
            "--docx-changes must be accept-all"
        )
    from prosediff.redline import redline

    return save(lambda target: redline(f, target, author_of(comparison), fmt), path)
