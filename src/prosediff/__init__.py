"""Side-by-side HTML diff between two commits of a git repository."""

from prosediff.diff import Comparison, FileDiff, MoveSettings, Options, Row, compare, compare_paths
from prosediff.render import render

__all__ = [
    "Comparison",
    "FileDiff",
    "MoveSettings",
    "Options",
    "Row",
    "compare",
    "compare_paths",
    "render",
]
