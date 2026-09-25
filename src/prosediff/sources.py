"""Sides outside git: two files or two folders, and Word documents.

A .docx is read into Markdown by prosediff.word (python-docx), its tracked
changes settled and its comments kept, so they can be folded and listed like
those of a Markdown file.
"""

from datetime import datetime
from pathlib import Path

from prosediff.word import CHANGES as DOCX_CHANGES
from prosediff.word import WordError
from prosediff.word import docx_to_markdown as _docx_to_markdown

__all__ = ["DOCX_CHANGES", "SourceError", "describe_side", "docx_to_markdown", "read_side"]


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
