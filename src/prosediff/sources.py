"""Sides outside git: two files or two folders, and word-processor documents.

A .docx is read into Markdown by prosediff.word (python-docx), an .odt by
prosediff.odt (odfdo), their tracked changes settled and their comments kept,
so they can be folded and listed like those of a Markdown file.
"""

from datetime import datetime
from pathlib import Path

from prosediff.odt import OdtError, odt_to_markdown
from prosediff.word import CHANGES as DOCX_CHANGES
from prosediff.word import WordError
from prosediff.word import docx_to_markdown as _docx_to_markdown

__all__ = [
    "DOCUMENT_SUFFIXES",
    "DOCX_CHANGES",
    "SourceError",
    "describe_side",
    "document_to_markdown",
    "docx_to_markdown",
    "is_document",
    "read_side",
]

# The word-processor documents read into Markdown, and what the page calls them.
DOCUMENT_SUFFIXES = {".docx": "Word", ".odt": "OpenDocument"}


class SourceError(RuntimeError):
    """A side could not be read or converted."""


def docx_to_markdown(data: bytes, name: str, changes: str = "accept") -> bytes:
    """A Word document as Markdown, its tracked changes settled, comments kept.

    changes is "accept" or "reject" (the tracked changes), or "all" to keep
    them as insertion and deletion spans.
    """
    try:
        return _docx_to_markdown(data, changes).encode("utf-8")
    except WordError as e:
        raise SourceError(f"{name} is not a readable Word document: {e}") from None


def is_document(path: str | None) -> bool:
    """Whether a path names a Word or OpenDocument text, read into Markdown."""
    return bool(path) and Path(path).suffix.lower() in DOCUMENT_SUFFIXES


def document_to_markdown(data: bytes, name: str, changes: str = "accept") -> bytes:
    """A Word document or an OpenDocument text as Markdown, by its name's
    extension; changes as in docx_to_markdown."""
    if Path(name).suffix.lower() == ".odt":
        try:
            return odt_to_markdown(data, changes).encode("utf-8")
        except OdtError as e:
            raise SourceError(f"{name} is not a readable OpenDocument text: {e}") from None
    return docx_to_markdown(data, name, changes)


def read_side(path: Path) -> dict[str, bytes]:
    """The files of one side, by path relative to it ("/"-separated).

    A file is a side of one file, under its own name; a folder contributes
    every file below it, .git folders excepted.
    """
    if path.is_file():
        return {path.name: path.read_bytes()}
    if not path.is_dir():
        raise SourceError(f"no such file or folder: {path}")
    files = {}
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        if ".git" in rel.parts or not p.is_file():
            continue
        files[rel.as_posix()] = p.read_bytes()
    return files


def describe_side(path: Path) -> tuple[str, str, str, str]:
    """(full name, short name, kind, date) of a side, for the page header."""
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return str(path.resolve()), path.name, "folder" if path.is_dir() else "file", stamp
