"""Side-by-side HTML diff between two commits of a git repository."""

from prosediff.diff import Comparison, FileDiff, Options, Row, compare, compare_paths
from prosediff.render import render

__all__ = ["Comparison", "FileDiff", "Options", "Row", "compare", "compare_paths", "render"]
