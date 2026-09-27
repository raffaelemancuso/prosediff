"""Which sequence matcher prosediff should compare words with: difflib's (the
standard library's, in use); patiencediff's (Breezy's patience diff, in C);
rapidfuzz's (the Indel distance's alignment, in C); histodiff's histogram,
patience and Myers diffs (pure Python); similar-rs's Myers, patience and
histogram diffs (the Rust crate similar); cydifflib's and cdifflib's
difflib (the same algorithm in C++ and C: built from source where no wheel
fits, as for Python 3.14 on Windows); Levenshtein's alignment (the
Levenshtein package, in C++: edits including substitutions, where rapidfuzz's
Indel has insertions and deletions only); myers's and pymyers's Myers
diffs, patience-diff-algo's patience diff, lcs2's most contiguous longest
common subsequence, fastdiff's differ and edit-distance's alignment (all pure
Python); myers-diff's Myers diff (in C, through Cython) and dissimilar's
diff with semantic cleanup (the Rust crate dissimilar, which diffs characters:
each distinct word is given a character of its own), each turned into opcodes.
Neither of the last two has a wheel for Python 3.14 on Windows: myers-diff
builds from source in a Visual Studio developer prompt, dissimilar only after
its pyproject.toml is given a version and its binding is moved from pyo3 0.18
(which cannot link against 3.14) to 0.25; on Windows both need a short
build path, as the linker fails past 260 characters.

Run it with some matchers only by naming them: python
docs/word_matcher_benchmark.py lcs2 "similar-rs histogram".

    uv run --with patiencediff --with histodiff --with similar-rs --with Levenshtein \
        --with cydifflib --with cdifflib python docs/word_matcher_benchmark.py

Each matcher replaces difflib in diff.word_ops, the word pairing of every
changed line. On the changed lines of the trials of docs/passage_benchmark.py
(both kinds of line), it is timed (the best of up to REPEATS passes) and its
output measured: how many separate
changes a changed line is cut into (fewer pieces read better), and how many
of them are a single word or punctuation sign left unchanged between two
changes (the "the" and "," a matcher anchors on by chance), and on how many
lines its pairing differs from difflib's. Then
docs/passage_benchmark.py is run with it, for the moved passages found. The
report is printed and written next to this script as word_matcher_benchmark.txt.

Each matcher runs in a process of its own, within MATCHER_TIMEOUT: one that
raises, crashes or hangs is reported as failed (with the error's last line)
and the others still run.
"""

import contextlib
import io
import os
import re
import statistics
import subprocess
import sys
import time
from functools import lru_cache, partial
from pathlib import Path

import passage_benchmark as pb
from rapidfuzz.distance import Indel

from prosediff import diff

DOCS = Path(__file__).parent
REPORT = DOCS / "word_matcher_benchmark.txt"
TRIALS = 20  # per book and kind of line, for the timing and the pieces
# seconds for one matcher (about a minute for most, edit-distance's about
# three): past it the matcher is reported as failed
MATCHER_TIMEOUT = 600
REPEATS = 3  # timed passes of the word pairing, the best kept
REPEAT_BUDGET = 60  # seconds: no further pass once the passes took this long
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rapidfuzz_opcodes(a: list[str], b: list[str]):
    return [tuple(op) for op in Indel.opcodes(a, b)]


def cydifflib_opcodes(a: list[str], b: list[str]):
    import cydifflib

    return cydifflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


def cdifflib_opcodes(a: list[str], b: list[str]):
    import cdifflib

    return cdifflib.CSequenceMatcher(None, a, b, autojunk=False).get_opcodes()


def levenshtein_opcodes(a: list[str], b: list[str]):
    import Levenshtein

    return [tuple(op) for op in Levenshtein.opcodes(a, b)]


def from_pairs(pairs, n: int, m: int):
    """Opcodes from the matched items of two sequences of n and m items,
    (i, j) pairs in increasing order."""
    out, i, j = [], 0, 0
    for x, y in [*pairs, (n, m)]:
        if x > i or y > j:
            out.append((diff.change_tag(i, x, j, y), i, x, j, y))
        if (x, y) == (n, m):
            break
        if out and out[-1][0] == "equal" and out[-1][2] == x and out[-1][4] == y:
            out[-1] = ("equal", out[-1][1], x + 1, out[-1][3], y + 1)
        else:
            out.append(("equal", x, x + 1, y, y + 1))
        i, j = x + 1, y + 1
    return out


def from_steps(steps, n: int, m: int):
    """Opcodes from a walk over two sequences: "=" both, "-" the first, "+"
    the second."""
    pairs, i, j = [], 0, 0
    for step in steps:
        if step == "=":
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif step == "-":
            i += 1
        elif step == "+":
            j += 1
    return from_pairs(pairs, n, m)


def myers_opcodes(a: list[str], b: list[str]):
    import myers

    kinds = {"k": "=", "r": "-", "o": "-", "i": "+"}
    return from_steps([kinds[k] for k, _ in myers.diff(a, b)], len(a), len(b))


def pymyers_opcodes(a: list[str], b: list[str]):
    import pymyers

    result = pymyers.MyersBase(a, b).diff()
    pairs = sorted((m.x, m.y) if hasattr(m, "x") else tuple(m) for m in result.matches)
    return from_pairs(pairs, len(a), len(b))


def patience_algo_opcodes(a: list[str], b: list[str]):
    import bram_diff

    return from_pairs(sorted(bram_diff.matches(a, b)), len(a), len(b))


def lcs2_opcodes(a: list[str], b: list[str]):
    import lcs2

    return from_pairs(lcs2.lcs_indices(a, b), len(a), len(b))


def fastdiff_opcodes(a: list[str], b: list[str]):
    import fastdiff

    # it compares lines: one token a line (a token holds no line break)
    kinds = {"  ": "=", "- ": "-", "+ ": "+"}
    lines = fastdiff.compare("\n".join(a), "\n".join(b))
    return from_steps([kinds[x[:2]] for x in lines if x[:2] in kinds], len(a), len(b))


def edit_distance_opcodes(a: list[str], b: list[str]):
    import edit_distance

    ops = edit_distance.SequenceMatcher(a, b).get_opcodes()
    steps = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            steps += ["="] * (i2 - i1)
        else:
            steps += ["-"] * (i2 - i1) + ["+"] * (j2 - j1)
    return from_steps(steps, len(a), len(b))


def myers_diff_opcodes(a: list[str], b: list[str]):
    import myers_diff

    # the deleted items of a and the inserted ones of b: the rest pair in order
    gone = {"DELETE": set(), "INSERT": set()}
    for op in myers_diff.diff(a, b):
        gone[op["type"]].add(op["index"])
    kept_a = [i for i in range(len(a)) if i not in gone["DELETE"]]
    kept_b = [j for j in range(len(b)) if j not in gone["INSERT"]]
    return from_pairs(list(zip(kept_a, kept_b, strict=True)), len(a), len(b))


def dissimilar_opcodes(a: list[str], b: list[str]):
    import dissimilar

    # it diffs characters: each distinct token becomes one private-use one
    codes: dict[str, str] = {}
    for token in (*a, *b):
        if token not in codes:
            k = len(codes)
            codes[token] = chr(0xE000 + k if k < 6400 else 0xF0000 + k - 6400)
    kinds = {"Equal": "=", "Delete": "-", "Insert": "+"}
    steps = []
    for chunk in dissimilar.diff("".join(codes[t] for t in a), "".join(codes[t] for t in b)):
        steps += [kinds[type(chunk).__name__]] * len(str(chunk))
    return from_steps(steps, len(a), len(b))


def histodiff_opcodes(algorithm: str):
    def opcodes(a: list[str], b: list[str]):
        import histodiff

        return [
            (op.tag, op.a_start, op.a_end, op.b_start, op.b_end)
            for op in histodiff.diff(a, b, algorithm)
        ]

    return opcodes


def similar_opcodes(algorithm: str):
    def opcodes(a: list[str], b: list[str]):
        from similar import difflib as similar_difflib

        return similar_difflib.SequenceMatcher(None, a, b, algorithm=algorithm).get_opcodes()

    return opcodes


MATCHERS = {
    "difflib": diff.difflib_opcodes,
    "cydifflib": cydifflib_opcodes,
    "cdifflib": cdifflib_opcodes,
    "patiencediff": diff.patience_opcodes,
    "rapidfuzz": rapidfuzz_opcodes,
    "Levenshtein": levenshtein_opcodes,
    "histodiff histogram": histodiff_opcodes("histogram"),
    "histodiff patience": histodiff_opcodes("patience"),
    "histodiff myers": histodiff_opcodes("myers"),
    "similar-rs myers": similar_opcodes("myers"),
    "similar-rs patience": similar_opcodes("patience"),
    "similar-rs histogram": similar_opcodes("histogram"),
    "myers": myers_opcodes,
    "pymyers": pymyers_opcodes,
    "patience-diff-algo": patience_algo_opcodes,
    "lcs2": lcs2_opcodes,
    "fastdiff": fastdiff_opcodes,
    "edit-distance": edit_distance_opcodes,
    "myers-diff": myers_diff_opcodes,
    "dissimilar": dissimilar_opcodes,
}


def use(matcher) -> None:
    """Make diff.word_ops pair words with matcher (cached, like the original)."""
    diff._word_ops = lru_cache(maxsize=diff.WORD_OPS_CACHE)(
        partial(diff.word_opcodes, pair=matcher)
    )


def line_pairs() -> list[tuple[str, str]]:
    """The changed lines of the first TRIALS trials of each book and kind of
    line, as docs/passage_benchmark.py saved them."""
    data = pb.load_cases()
    return [
        (o, n)
        for unit in pb.UNITS
        for case in pb.trials_of(data, unit, TRIALS)
        for o, n in zip(case["old"], case["new"], strict=True)
        if o != n
    ]


def measure(name: str) -> list[str]:
    """The report's part for one matcher (run in a process of its own)."""
    matcher = MATCHERS[name]
    try:
        matcher(["a"], ["b"])
    except ImportError:
        return [f"{name}: not installed", ""]
    pairs = line_pairs()
    # difflib's pairing, the reference (diff._word_ops pairs with
    # patiencediff when it is installed)
    use(diff.difflib_opcodes)
    reference = [tuple(diff.word_ops(o, n)) for o, n in pairs]
    use(matcher)
    # the best of up to REPEATS passes (each with an empty cache), fewer when
    # they would pass REPEAT_BUDGET: a busy machine slows one pass, not all
    times: list[float] = []
    while len(times) < REPEATS and sum(times) < REPEAT_BUDGET:
        diff._word_ops.cache_clear()
        t0 = time.perf_counter()
        all_ops = [diff.word_ops(o, n) for o, n in pairs]
        times.append(time.perf_counter() - t0)
    elapsed = min(times)
    pieces, lone, differ = [], 0, 0
    for (o, _n), ref, ops in zip(pairs, reference, all_ops, strict=True):
        differ += tuple(ops) != ref
        changes = [op for op in ops if op[0] != "equal"]
        pieces.append(len(changes))
        # an unchanged run of one word or sign between two changes
        for k in range(1, len(ops) - 1):
            tag, o1, o2, *_ = ops[k]
            if tag == "equal" and ops[k - 1][0] != "equal" and ops[k + 1][0] != "equal":
                lone += len(diff.similarity_tokens(o[o1:o2])) == 1
    diff._word_ops.cache_clear()
    with contextlib.redirect_stdout(io.StringIO()):
        report = pb.run(quick=True)
    wanted = ("precision", "recall", "boundary", "passages cost", "Lines are")
    kept = [line for line in report.splitlines() if line.strip().startswith(wanted)]
    return [
        name,
        f"  word pairing time   : {elapsed:>9,.2f} s",
        f"  changes per line    : {statistics.mean(pieces):>9,.2f}",
        f"  lone words anchored : {lone:>9,}",
        f"  lines unlike difflib: {differ:>9,}",
        *[f"  {line.strip()}" for line in kept],
        "",
    ]


def failure(name: str, why: str) -> list[str]:
    print(f"{name}: failed, {why}", file=sys.stderr)
    return [name, f"  failed: {why}", ""]


def main(only: list[str]) -> str:
    """Each matcher is measured in a process of its own, within
    MATCHER_TIMEOUT: one that raises, crashes the interpreter (as a compiled
    extension can) or hangs is reported as failed, and the others still run."""
    names = [name for name in MATCHERS if not only or name in only]
    out = [
        *pb.header("Word matchers for diff.word_ops: speed, pieces, and moved passages"),
        f"{len(line_pairs()):,} changed lines of the trials of docs/passage_benchmark.py.",
        "",
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHON_COLORS": "0"}
    for name in names:
        try:
            done = subprocess.run(
                [sys.executable, __file__, "--one", name],
                capture_output=True,
                encoding="utf-8",
                env=env,
                timeout=MATCHER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            out += failure(name, f"no result within {MATCHER_TIMEOUT:,} s")
            continue
        if done.returncode == 0:
            out += [*done.stdout.rstrip("\n").split("\n"), ""]
            print(f"{name}: done", file=sys.stderr)
            continue
        # the exception's last line, or the exit code of a crash
        last = [ANSI.sub("", line) for line in done.stderr.splitlines() if line.strip()]
        out += failure(name, last[-1].strip() if last else f"exit code {done.returncode}")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--one"]:
        print("\n".join(measure(sys.argv[2])).rstrip("\n"))
        sys.exit(0)
    text = main(sys.argv[1:])
    # a run of some matchers only has a report of its own
    pb.write_report(
        REPORT if len(sys.argv) == 1 else REPORT.with_name(REPORT.stem + "_partial.txt"), text
    )
    sys.exit(0)
