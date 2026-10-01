"""Sides outside git: two files or two folders, and word-processor documents.

A .docx is read by prosediff.word (python-docx), an .odt by prosediff.odt
(odfdo), into paragraphs of styled
text (prosediff.document), their tracked changes settled and their comments
kept; Markdown is only written from them, for git's own commands.
"""

from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path

from prosediff.document import CHANGES as DOCX_CHANGES
from prosediff.document import Document, to_markdown
from prosediff.odt import OdtError, read_odt
from prosediff.word import WordError, read_docx

__all__ = [
    "DOCUMENT_SUFFIXES",
    "DOCX_CHANGES",
    "FOLDER_FILES",
    "FOLDER_PAGE",
    "SourceError",
    "default_page",
    "describe_side",
    "document_to_markdown",
    "is_document",
    "page_of",
    "patterns",
    "read_document",
    "read_side",
    "review_page",
]

# The word-processor documents prosediff reads, and what the HTML report calls them.
DOCUMENT_SUFFIXES = {".docx": "Word", ".odt": "OpenDocument"}
# The files of two folders compared by default: prose, not code or data.
FOLDER_FILES = "*.docx|*.odt|*.md|*.typ|*.txt"
# The lock files word processors leave next to an open document: Word's
# ~$name.docx, LibreOffice's .~lock.name.odt#. Never a side's content.
LOCK_FILES = ("~$*", ".~lock.*#")
# The HTML report comparing two folders goes into the new one, by default, under
# this name; at the top of a folder, it is never one of the files compared.
FOLDER_PAGE = "prosediff.html"


class SourceError(RuntimeError):
    """A side could not be read or converted."""


def is_document(path: str | None) -> bool:
    """Whether a path names a Word or OpenDocument text."""
    return bool(path) and Path(path).suffix.lower() in DOCUMENT_SUFFIXES


def document_to_markdown(data: bytes, name: str, changes: str = "accept-all") -> bytes:
    """A Word document or an OpenDocument text as Markdown, by its name's
    extension, comments kept. changes is "accept-all" or "reject-all" (the tracked
    changes), or "show" to keep them as insertion and deletion spans."""
    return to_markdown(read_document(data, name, changes)).encode("utf-8")


def read_document(data: bytes, name: str, changes: str = "accept-all") -> Document:
    """A Word document or an OpenDocument text as prosediff reads it
    (prosediff.document), by its name's extension; changes as in
    document_to_markdown."""
    if Path(name).suffix.lower() == ".odt":
        try:
            return read_odt(data, changes)
        except OdtError as e:
            raise SourceError(f"{name} is not a readable OpenDocument text: {e}") from None
    try:
        return read_docx(data, changes)
    except WordError as e:
        raise SourceError(f"{name} is not a readable Word document: {e}") from None


def patterns(include: str | None) -> list[str]:
    """The glob patterns of an include option, separated by "|"; none (an
    empty option) takes every file."""
    return [p.strip() for p in (include or "").split("|") if p.strip()]


def included(rel: str, globs: list[str]) -> bool:
    """Whether a path relative to its folder ("/"-separated) matches one of
    the patterns, ignoring case: by its name, or by its whole path for a
    pattern with a "/" in it."""
    if not globs:
        return True
    name = rel.rsplit("/", 1)[-1].lower()
    return any(fnmatchcase(rel.lower() if "/" in g else name, g.lower()) for g in globs)


def read_side(path: Path, include: str | None = None) -> dict[str, bytes]:
    """The files of a folder compared, by path relative to it
    ("/"-separated): every file below it that the include patterns match
    (patterns()), .git folders, the lock files of open documents and an
    HTML report of prosediff's own (FOLDER_PAGE) at its top excepted.
    """
    globs = patterns(include)
    files = {}
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        if ".git" in rel.parts or not p.is_file() or not included(rel.as_posix(), globs):
            continue
        if any(fnmatchcase(p.name, lock) for lock in LOCK_FILES) or rel.as_posix() == FOLDER_PAGE:
            continue
        files[rel.as_posix()] = p.read_bytes()
    return files


def default_page(old: Path, new: Path, suffix: str = ".html") -> Path | None:
    """Where the output (the HTML report, or what suffix names) goes when
    none is given: comparing two folders, into the new one, as FOLDER_PAGE;
    comparing two files, next to the new one, named after both, so reports
    of different pairs do not overwrite each other. None for anything else."""
    if old.is_dir() and new.is_dir():
        return (new / FOLDER_PAGE).with_suffix(suffix)
    if old.is_file() and new.is_file():
        return new.parent / f"{old.stem}_vs_{new.stem}{suffix}"
    return None


def page_of(mode: str, old: str, new: str, suffix: str = ".html") -> Path | None:
    """Where the output of mode ("git", "files", "folders" or "review",
    old being the file reviewed) goes when none is given: review_page,
    default_page; None comparing git versions, or a side not given."""
    if mode == "review":
        return review_page(Path(old)) if old else None
    if mode == "git" or not old or not new:
        return None
    return default_page(Path(old), Path(new), suffix)


def review_page(path: Path) -> Path:
    """Where the HTML report reviewing a file alone goes when no output is
    given: next to it, as NAME_review.html."""
    return path.parent / f"{path.stem}_review.html"


def describe_side(path: Path) -> tuple[str, str, str, str]:
    """(full name, short name, kind, date) of a side, for the HTML report header."""
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return str(path.resolve()), path.name, "folder" if path.is_dir() else "file", stamp
