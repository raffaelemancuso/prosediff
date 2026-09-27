"""Benchmark of the moved-passage matching (diff.mark_moves): speed and accuracy.

    uv run python docs/passage_benchmark.py [--quick]

Revisions are simulated on the public-domain books of docs/move_sensitivity.py
(kept in docs/benchmark_data/gutenberg), so that where every passage went is known. Two
kinds of line, as prosediff compares prose:

- paragraphs (--split paragraph): a sentence moved from one paragraph into
  another is a moved passage;
- sentences (--split sentence): a clause moved from one sentence into another.

Each trial takes a stretch of consecutive paragraphs and makes a new version:
MOVES passages moved elsewhere in the stretch (each untouched, or with RATES
of its words replaced), DECOYS passages deleted and as many from far away in
the book inserted (removed and added text that is not a move), and a few
lines edited in place. diff.align then compares the two versions, and each
moved passage it reports is checked against the truth:

- right: its old end lies in the old line the passage left and its new end in
  the new line it went to, each overlapping the true passage by at least
  OVERLAP of the shorter;
- boundary: for a right one, how much of the true passage its ends cover and
  how much of them is the passage (intersection over union of the
  characters, both ends averaged).

A true move also counts as found when the diff shows it another right way:
as a whole line moved (the passage was all of its line), or with the line it
left paired with the line it went to (the passage then stands unchanged
between them). A passage reported with the same text at the same place, in
a line of each side at the same position (the lines keep their number in a
trial), is text the line pairing set apart and the matching joined again: it
is counted apart, as "rejoined", and is neither right nor wrong.

Precision is the share of the passages reported that are right (rejoined
ones aside), recall the share of the true moves found. The time is that of diff.align with and
without moved passages, over all the trials, and of a stress case: STRESS
paragraphs all edited, the largest job mark_moves meets. The report
is printed and written next to this script as passage_benchmark.txt.

The trials (the two versions, the true moves, and the lines' alignment by git)
are made once and saved in CASES (in docs/benchmark_data, in the repository),
which later runs read instead of making
them again, so that every run, and docs/word_matcher_benchmark.py, compares
the same revisions. The file keeps the parameters that made it (and
CASES_VERSION, to bump when trial() changes): when they differ it is made
again, and the run says so. --quick takes the first quarter of the trials of
each book.
"""

import gzip
import json
import random
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from move_sensitivity import BOOKS, DATA, book_text, paragraphs

from prosediff import diff
from prosediff.diff import align, git_opcodes
from prosediff.sentences import sentences

DOCS = Path(__file__).parent
REPORT = DOCS / "passage_benchmark.txt"
# The trials, saved once next to the books (both kept in the repository), and
# the version of the way they are made: bump it when trial() or the drawing
# of the trials changes.
CASES = DATA / "passage_benchmark_cases.json.gz"
CASES_VERSION = 1
UNITS = ("paragraph", "sentence")
SEED = 20260927
TRIALS = 60  # per book and kind of line
STRETCH = {"paragraph": 30, "sentence": 60}
MOVES = 3
DECOYS = 3
EDITED = 4  # lines edited in place, 5% of their words
RATES = (0.0, 0.0, 0.1, 0.2)  # share of a moved passage's words replaced
MIN_WORDS = 6  # the shortest passage moved or used as a decoy
OVERLAP = 0.5
STRESS = 400  # paragraphs, every one edited, for the stress case
STRESS_MOVES = 30  # sentences moved in it, and as many decoys
WORD = re.compile(r"\w+", re.UNICODE)
CLAUSE = re.compile(r"(?<=[,;:])\s+")


def header(title: str) -> list[str]:
    """The first lines of a report: its title, when it was made, a blank."""
    return [title, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""]


def write_report(path: Path, text: str) -> None:
    """The report printed, and written to path (UTF-8, LF)."""
    print(text)
    path.write_bytes((text + "\n").encode("utf-8"))


def timed_align(old: list[str], new: list[str], ops, **options) -> tuple[list, float]:
    """diff.align's rows, and how long it took, its caches emptied first."""
    clear_caches()
    t0 = time.perf_counter()
    rows, _, _ = align(old, new, None, ops, **options)
    return rows, time.perf_counter() - t0


def clear_caches() -> None:
    """Forget the word comparisons cached by a previous run, so that each run
    is timed from scratch."""
    cache = getattr(diff, "_word_ops", None)
    if cache is not None:
        cache.cache_clear()


def words(text: str) -> int:
    return len(WORD.findall(text))


def edit(text: str, rate: float, rng: random.Random, pool: list[str]) -> str:
    """text with a share rate of its words replaced by words from pool."""
    toks = text.split(" ")
    for k in rng.sample(range(len(toks)), round(rate * len(toks))):
        toks[k] = rng.choice(pool)
    return " ".join(toks)


def pieces_of(line: str, unit: str, language: str) -> list[str]:
    """What a line is made of, for moving a passage: a paragraph's sentences,
    a sentence's clauses."""
    if unit == "paragraph":
        return sentences(line, language)
    return CLAUSE.split(line)


def join(pieces: list[str]) -> str:
    return " ".join(p for p in pieces if p)


def trial(
    lines: list[str],
    far: list[str],
    unit: str,
    language: str,
    rng: random.Random,
    moves_made: int = MOVES,
    decoys_made: int = DECOYS,
    edited: int = EDITED,
    edit_rate: float = 0.05,
):
    """A new version of lines: (old, new, truth), truth a list of (old line,
    old passage, new line, new passage) of the moves made. moves_made
    passages moved, decoys_made decoys, and edited other lines with a share
    edit_rate of their words replaced."""
    parts = [pieces_of(x, unit, language) for x in lines]
    pool = [w for x in lines for w in x.split(" ")]
    truth, taken = [], set()

    def pick_piece(exclude: set[int]) -> tuple[int, int] | None:
        spots = [
            (i, k)
            for i, ps in enumerate(parts)
            if i not in exclude and len(ps) >= 2
            for k, p in enumerate(ps)
            if words(p) >= MIN_WORDS and (i, k) not in taken
        ]
        return rng.choice(spots) if spots else None

    edited_lines: set[int] = set()
    moves = []
    for _ in range(moves_made):
        spot = pick_piece(edited_lines)
        if not spot:
            break
        i, k = spot
        taken.add(spot)
        edited_lines.add(i)
        moves.append((i, k))
    decoys = []
    for _ in range(decoys_made):
        spot = pick_piece(edited_lines)
        if not spot:
            break
        taken.add(spot)
        edited_lines.add(spot[0])
        decoys.append(spot)
    # remove the moved and decoy pieces, remembering the moved ones
    moved = {}
    for i, k in moves:
        rate = rng.choice(RATES)
        moved[(i, k)] = (parts[i][k], edit(parts[i][k], rate, rng, pool))
    for i, k in [*moves, *decoys]:
        parts[i][k] = ""
    # insert each moved piece into another line, at a piece boundary
    targets = [t for t in range(len(parts)) if t not in edited_lines and len(parts[t]) >= 1]
    inserts: dict[int, list[tuple[int, str, int | None]]] = {}
    for n, ((i, _k), (old_p, new_p)) in enumerate(moved.items()):
        if not targets:
            break
        t = rng.choice(targets)
        targets.remove(t)
        inserts.setdefault(t, []).append((rng.randint(0, len(parts[t])), new_p, n))
        truth.append([i, old_p, t, new_p])
    for _ in decoys:
        if not targets:
            break
        t = rng.choice(targets)
        targets.remove(t)
        cand = [p for x in far for p in pieces_of(x, unit, language) if words(p) >= MIN_WORDS]
        inserts.setdefault(t, []).append((rng.randint(0, len(parts[t])), rng.choice(cand), None))
    for t, items in inserts.items():
        for at, text, _ in sorted(items, reverse=True):
            parts[t].insert(at, text)
    # edits in place elsewhere
    free = [t for t in range(len(parts)) if t not in edited_lines and t not in inserts]
    for t in rng.sample(free, min(edited, len(free))):
        parts[t] = [edit(p, edit_rate, rng, pool) for p in parts[t]]
    new = [join(ps) for ps in parts]
    return lines, new, truth


def spans_found(rows, old: list[str], new: list[str]):
    """The moved passages align reported: (old line, start, end, new line,
    start, end), 0-based lines."""
    return [
        (a.left_no - 1, ma.start, ma.end, b.right_no - 1, mb.start, mb.end)
        for a, ma, b, mb in diff.moved_passages(rows)
    ]


def locate(line: str, text: str) -> tuple[int, int] | None:
    k = line.find(text)
    return (k, k + len(text)) if k >= 0 else None


def overlap(a: tuple[int, int], b: tuple[int, int]) -> tuple[float, float]:
    """(share of the shorter covered, intersection over union)."""
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0]) or 1
    return inter / shorter, inter / union if union else 0.0


def shown_otherwise(rows, ti: int, tt: int) -> bool:
    """Whether the diff shows old line ti and new line tt as one line moved,
    or side by side (the passage between them then unchanged)."""
    for r in rows:
        if r.left_no == ti + 1 and r.right_no == tt + 1:
            return True
    outs = {r.move_pair for r in rows if r.kind == "moved-out" and r.left_no == ti + 1}
    return any(r.kind == "moved-in" and r.right_no == tt + 1 and r.move_pair in outs for r in rows)


def score(rows, found, truth, old, new):
    """(right, rejoined, reported, true moves found, true moves, IoUs)."""
    right = rejoined = 0
    ious, hit = [], set()
    for oi, os_, oe, ni, ns, ne in found:
        if oi == ni and old[oi][os_:oe] == new[ni][ns:ne]:
            rejoined += 1
            continue
        for n, (ti, t_old, tt, t_new) in enumerate(truth):
            if n in hit or oi != ti or ni != tt:
                continue
            a, b = locate(old[ti], t_old), locate(new[tt], t_new)
            if not a or not b:
                continue
            (ca, ia), (cb, ib) = overlap((os_, oe), a), overlap((ns, ne), b)
            if ca >= OVERLAP and cb >= OVERLAP:
                right += 1
                hit.add(n)
                ious.append((ia + ib) / 2)
                break
    for n, (ti, _, tt, _) in enumerate(truth):
        if n not in hit and shown_otherwise(rows, ti, tt):
            hit.add(n)
    return right, rejoined, len(found), len(hit), len(truth), ious


def case_params() -> dict:
    """What the trials are made from: a saved set made from other values is
    made again."""
    return {
        "version": CASES_VERSION,
        "seed": SEED,
        "trials": TRIALS,
        "stretch": STRETCH,
        "moves": MOVES,
        "decoys": DECOYS,
        "edited": EDITED,
        "rates": list(RATES),
        "min_words": MIN_WORDS,
        "books": [number for number, _, _ in BOOKS],
        "stress": STRESS,
        "stress_moves": STRESS_MOVES,
    }


def make_cases() -> dict:
    """Every trial, for each kind of line and book in turn (the order of the
    random draws), and the stress case: the two versions, the true moves,
    and the lines' alignment by git."""
    rng = random.Random(SEED)
    cases: dict = {"params": case_params()}
    for unit in UNITS:
        cases[unit] = []
        for number, language, _title in BOOKS:
            paras = [p for p in paragraphs(book_text(number)) if words(p) >= 5]
            if unit == "sentence":
                lines_all = [s for p in paras for s in sentences(p, language)]
            else:
                lines_all = paras
            size = STRETCH[unit]
            for _ in range(TRIALS):
                start = rng.randrange(0, len(lines_all) - 3 * size)
                lines = lines_all[start : start + size]
                far_at = (start + len(lines_all) // 2) % (len(lines_all) - size)
                far = lines_all[far_at : far_at + size]
                old, new, truth = trial(lines, far, unit, language, rng)
                (ops,) = git_opcodes([(old, new)])
                cases[unit].append(
                    {"book": number, "old": old, "new": new, "truth": truth, "ops": ops}
                )
    # stress: a long document revised throughout, every paragraph edited, so
    # that removed and added passages are many on both sides
    number, language, _ = BOOKS[1]
    paras = [p for p in paragraphs(book_text(number)) if words(p) >= 20]
    lines, far = paras[:STRESS], paras[STRESS : 2 * STRESS]
    old, new, truth = trial(
        lines,
        far,
        "paragraph",
        language,
        random.Random(SEED),
        moves_made=STRESS_MOVES,
        decoys_made=STRESS_MOVES,
        edited=STRESS,
        edit_rate=0.15,
    )
    (ops,) = git_opcodes([(old, new)])
    cases["stress"] = {"book": number, "old": old, "new": new, "truth": truth, "ops": ops}
    return cases


def load_cases() -> dict:
    """The saved trials, or, when there are none or their parameters differ,
    new ones, saved."""
    wanted = json.loads(json.dumps(case_params()))
    if CASES.exists():
        try:
            data = json.loads(gzip.decompress(CASES.read_bytes()))
        except (OSError, ValueError):
            data = None
        if data and data.get("params") == wanted:
            return data
        print(f"{CASES.name}: made with other parameters, made again", file=sys.stderr)
    data = make_cases()
    CASES.parent.mkdir(parents=True, exist_ok=True)
    CASES.write_bytes(gzip.compress(json.dumps(data).encode("utf-8")))
    return json.loads(json.dumps(data))


def trials_of(data: dict, unit: str, per_book: int) -> list[dict]:
    """The first per_book trials of each book, for one kind of line."""
    taken: dict[int, int] = {}
    out = []
    for case in data[unit]:
        if taken.get(case["book"], 0) < per_book:
            taken[case["book"]] = taken.get(case["book"], 0) + 1
            out.append(case)
    return out


def run(quick: bool) -> str:
    data = load_cases()
    trials = TRIALS // 4 if quick else TRIALS
    out = [
        *header("Moved passages: speed and accuracy of diff.mark_moves"),
        f"{trials} trials per book and kind of line; {MOVES} moves and {DECOYS} decoys "
        f"each; moved passages with {', '.join(f'{int(r * 100)}%' for r in RATES)} of "
        f"their words replaced; a passage found is right when each end overlaps the "
        f"true one by {int(OVERLAP * 100)}% of the shorter. Trials read from "
        f"{CASES.name} (made once, see the docstring).",
        "",
    ]
    for unit in UNITS:
        similarity, algorithm = diff.move_defaults(unit == "sentence")
        tot = {
            "right": 0,
            "rejoined": 0,
            "found": 0,
            "hit": 0,
            "true": 0,
            "ious": [],
            "t_on": 0.0,
            "t_off": 0.0,
        }
        for case in trials_of(data, unit, trials):
            old, new, truth, ops = case["old"], case["new"], case["truth"], case["ops"]
            moves = {"move_similarity": similarity, "move_algorithm": algorithm}
            rows, t_on = timed_align(old, new, ops, **moves)
            _, t_off = timed_align(old, new, ops, move_passages=False, **moves)
            tot["t_on"] += t_on
            tot["t_off"] += t_off
            r, j, f, h, t, ious = score(rows, spans_found(rows, old, new), truth, old, new)
            tot["right"] += r
            tot["rejoined"] += j
            tot["found"] += f
            tot["hit"] += h
            tot["true"] += t
            tot["ious"] += ious
        judged = tot["found"] - tot["rejoined"]
        p = tot["right"] / judged if judged else 0.0
        rc = tot["hit"] / tot["true"] if tot["true"] else 0.0
        f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
        iou = statistics.mean(tot["ious"]) if tot["ious"] else 0.0
        extra = tot["t_on"] - tot["t_off"]
        out += [
            f"Lines are {unit}s (similarity {similarity}, {algorithm})",
            f"  true moves      : {tot['true']:>7,}",
            f"  reported        : {tot['found']:>7,}",
            f"  rejoined        : {tot['rejoined']:>7,}",
            f"  right           : {tot['right']:>7,}",
            f"  wrong           : {judged - tot['right']:>7,}",
            f"  moves found     : {tot['hit']:>7,}",
            f"  precision       : {p * 100:>6.1f}%",
            f"  recall          : {rc * 100:>6.1f}%",
            f"  F1              : {f1 * 100:>6.1f}%",
            f"  boundary (IoU)  : {iou * 100:>6.1f}%",
            f"  align, passages : {tot['t_on']:>9,.2f} s",
            f"  align, without  : {tot['t_off']:>9,.2f} s",
            f"  passages cost   : {extra:>9,.2f} s",
            "",
        ]
    stress = data["stress"]
    old, new, truth, ops = stress["old"], stress["new"], stress["truth"], stress["ops"]
    rows, t_on = timed_align(old, new, ops)
    _, t_off = timed_align(old, new, ops, move_passages=False)
    r, j, f, h, t, _ = score(rows, spans_found(rows, old, new), truth, old, new)
    out += [
        f"Stress: {len(old):,} paragraphs, {t:,} sentences moved, {STRESS_MOVES:,} decoys, "
        "every other paragraph with 15% of its words replaced",
        f"  reported        : {f:>7,}",
        f"  right           : {r:>7,}",
        f"  wrong           : {f - j - r:>7,}",
        f"  moves found     : {h:>7,} of {t:,}",
        f"  align, passages : {t_on:>9,.2f} s",
        f"  align, without  : {t_off:>9,.2f} s",
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    write_report(REPORT, run("--quick" in sys.argv))
