"""Sensitivity analysis of the moved-line matching: algorithms and thresholds.

    uv run python docs/move_sensitivity.py

Revisions are simulated on public-domain books from Project Gutenberg (in
English, Italian and German; fiction, drama, science and an essay), so that
where every line went is known. The analysis is run twice, on the two kinds
of line prosediff compares: paragraphs (by default) and sentences (with
--split sentence, split by prosediff.sentences in each book's language), each
with its own suggested default. Each trial takes a stretch of STRETCH
consecutive lines (short lines of dialogue included, as they come) and
makes a new version of it:

- MOVES lines are moved elsewhere in the stretch, each edited one way: its
  words untouched, or replaced, deleted or inserted at one of RATES, or
  reordered (a paragraph's sentences, a sentence's clauses; with or without
  10% of its words edited);
- DECOYS other lines are deleted, and as many lines from elsewhere in the
  book inserted: removed and added lines that are not moves, which a
  threshold must keep apart. Half are any paragraph from far away; half are
  hard, a deleted paragraph's closest look-alike (the one sharing the most
  words, of LOOKALIKE_SAMPLE drawn from the rest of the book), as reference
  entries or stock phrases resemble each other.

prosediff aligns the two versions (diff.align, as for a Markdown file):
the lines are paired (diff.line_pairs), a weak pair giving way to a move
(diff.unpair_moved), and the moved-line matching pairs the removed and added
lines left, the identical ones first, then those at least as alike as the
threshold by the algorithm, the most alike first (diff.mark_moves). Those
steps are replayed here for each algorithm of diff.MOVE_ALGORITHMS and each
threshold of THRESHOLDS (and checked against diff.align itself on a
sample).

A move found is right when it pairs a paragraph with its own new version
(a paragraph the diff shows as moved while another is the one moved is a
right move too: both say the same); wrong when it pairs two different
paragraphs. Recall is measured on the moves the matching can find: a
paragraph whose old and new versions the alignment does not show face to
face (each removed, added, or weakly paired with another line).
Precision is the share of the moves found that are right, recall the share
of the moves to find that are found, F1 their harmonic mean, on the moves a
reader would still call moves (RECOGNISABLE); recall is also given by kind
of edit.

Each algorithm is also timed as diff.mark_moves runs it, on its largest
job: BENCH x BENCH removed and added paragraphs (diff.MOVE_MAX_CELLS pairs),
half of the added ones edited versions of removed ones; the best of REPEATS
runs, timed alone, once the trials (scored in parallel, in WORKERS
processes) are done. The suggested default is the fastest of the algorithms whose best F1
is within F1_TIE points of the best. The report is written next to this
script, as move_sensitivity.txt, with a part for each kind of line.
"""

import os
import random
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

from prosediff.diff import (
    MOVE_ALGORITHMS,
    MOVE_MARGIN,
    MOVE_MAX_CELLS,
    MOVE_TOLERANCE,
    PAIRING_THRESHOLD,
    align,
    difflib_opcodes,
    line_pairs,
    move_key,
    similarity_tokens,
    token_similarity,
    unpair_moved,
)
from prosediff.sentences import split_sentences

DOCS = Path(__file__).parent
CACHE = DOCS / "__pycache__" / "gutenberg"
# (id, language, what it is), each fetched once into CACHE.
BOOKS = [
    (1342, "en", "Austen, Pride and Prejudice (en, novel)"),
    (1228, "en", "Darwin, On the Origin of Species (en, science)"),
    (5669, "en", "Mill, Considerations on Representative Government (en, essay)"),
    (45334, "it", "Manzoni, I promessi sposi (it, novel)"),
    (2229, "de", "Goethe, Faust I (de, drama)"),
]
# The kinds of line compared: paragraphs, and sentences (--split sentence).
UNITS = ("paragraph", "sentence")
SEED = 20260926
TRIALS_PER_BOOK = 200
STRETCH = {"paragraph": 40, "sentence": 60}  # lines per trial
MOVES = 4
DECOYS = 4
RATES = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5)
EDITS = [f"words {int(r * 100)}%" for r in RATES] + ["reordered", "reordered + 10%"]
# The moves a reader would still call moves: what F1 is computed on.
RECOGNISABLE = {
    "words 0%",
    "words 5%",
    "words 10%",
    "words 20%",
    "words 30%",
    "reordered",
    "reordered + 10%",
}
THRESHOLDS = [round(0.3 + 0.05 * k, 2) for k in range(14)]  # 0.30 ... 0.95
LOOKALIKE_SAMPLE = 300
BENCH = int(MOVE_MAX_CELLS**0.5)  # 500 x 500 pairs
REPEATS = 3
F1_TIE = 0.005  # best F1s this close are a tie, broken by speed
CHECKED_TRIALS = 20  # trials whose replayed matching is checked against diff.align
WORKERS = os.cpu_count() or 1
SCORE_TIMEOUT = 1800  # seconds, for all the trials of one kind of line
FETCH_TIMEOUT = 60  # seconds, per book
WORD = re.compile(r"\w+", re.UNICODE)
SENTENCE = re.compile(r"(?<=[.!?;:])\s+")
CLAUSE = re.compile(r"(?<=[,;:])\s+")
MOVED_TO = re.compile(r"moved to line ([\d,]+)")


def book_text(number: int) -> str:
    """A book's text, its Project Gutenberg header and footer left out."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{number}.txt"
    if not path.exists():
        url = f"https://www.gutenberg.org/cache/epub/{number}/pg{number}.txt"
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as r:
            path.write_bytes(r.read())
    text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    start = re.search(r"\*\*\* ?START OF TH(E|IS) PROJECT GUTENBERG EBOOK.*\n", text)
    end = re.search(r"\*\*\* ?END OF TH(E|IS) PROJECT GUTENBERG EBOOK", text)
    return text[start.end() if start else 0 : end.start() if end else len(text)]


def paragraphs(text: str) -> list[str]:
    """The paragraphs of a text: blocks between blank lines, each one line."""
    return [" ".join(b.split()) for b in re.split(r"\n\s*\n", text) if b.strip()]


def edited(
    paragraph: str, kind: str, vocabulary: list[str], rng: random.Random, unit: str = "paragraph"
) -> str:
    """A line as a reviser might leave it: a paragraph's sentences, or a
    sentence's clauses, reordered; its words edited."""
    if kind.startswith("reordered"):
        sentences = (SENTENCE if unit == "paragraph" else CLAUSE).split(paragraph)
        if len(sentences) > 1:
            rng.shuffle(sentences)
            if " ".join(sentences) == paragraph:  # a real reordering
                sentences.reverse()
        paragraph = " ".join(sentences)
        rate = 0.1 if kind.endswith("10%") else 0.0
    else:
        rate = int(kind.split()[1].rstrip("%")) / 100
    if not rate:
        return paragraph
    out = []
    for word in paragraph.split(" "):
        if rng.random() >= rate:
            out.append(word)
            continue
        op = rng.choice(("replace", "delete", "insert"))
        if op == "replace":
            out.append(rng.choice(vocabulary))
        elif op == "insert":
            out += [word, rng.choice(vocabulary)]
    return " ".join(out)


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def trial(book: list[str], vocabulary: list[str], rng: random.Random, unit: str):
    """Two versions of a stretch of the book; for each new line, the old line
    it comes from (0-based, None for an inserted one); the kind of edit of
    each old line (moved ones as edited, the others "words 0%")."""
    stretch = STRETCH[unit]
    start = rng.randrange(0, len(book) - stretch)
    old = book[start : start + stretch]
    movable = [k for k, p in enumerate(old) if move_key(p)]
    chosen = rng.sample(movable, min(MOVES + DECOYS, len(movable)))
    moved, deleted = chosen[:MOVES], chosen[MOVES:]
    kinds = {k: "words 0%" for k in range(len(old))}
    kinds.update({k: rng.choice(EDITS) for k in moved})
    new: list[tuple[str, int | None]] = [
        (p, k) for k, p in enumerate(old) if k not in moved and k not in deleted
    ]
    for k in moved:
        new.insert(
            rng.randrange(0, len(new) + 1), (edited(old[k], kinds[k], vocabulary, rng, unit), k)
        )

    def far() -> int:
        k = rng.randrange(0, len(book))
        while start - stretch <= k < start + 2 * stretch:
            k = rng.randrange(0, len(book))
        return k

    for n, k in enumerate(deleted):
        if n % 2:
            # hard: the deleted paragraph's closest look-alike (Jaccard of
            # word sets, neutral among the algorithms compared)
            words = set(WORD.findall(old[k].lower()))
            pool = [far() for _ in range(LOOKALIKE_SAMPLE)]
            pick = max(pool, key=lambda j: jaccard(words, set(WORD.findall(book[j].lower()))))
        else:
            pick = far()
        new.insert(rng.randrange(0, len(new) + 1), (book[pick], None))
    return old, [p for p, _ in new], [k for _, k in new], kinds


def greedy(candidates: list[tuple[float, int, int]], threshold: float) -> list[tuple[int, int]]:
    """diff.mark_moves' fuzzy stage: the pairs at least threshold alike, the
    most alike first, each line in one pair at most."""
    used_out, used_in, out = set(), set(), []
    for _, a, b in sorted(
        (c for c in candidates if c[0] >= threshold - MOVE_TOLERANCE),
        key=lambda c: (-c[0], c[1], c[2]),
    ):
        if a not in used_out and b not in used_in:
            used_out.add(a)
            used_in.add(b)
            out.append((a, b))
    return out


def replay(pairs, old, new, threshold, score, prepared_old, prepared_new) -> set:
    """The moves diff.mark_moves finds on a pairing: (old line, new line),
    1-based. The identical lines first (spacing aside), each removed one
    with the first identical added one; then the most alike of the rest."""
    removed = [i for _, i, j in pairs if j is None and move_key(old[i])]
    added = [j for _, i, j in pairs if i is None and move_key(new[j])]
    by_key: dict = defaultdict(list)
    for i in removed:
        by_key[move_key(old[i])].append(i)
    found, moved_old, moved_new = set(), set(), set()
    for j in added:
        if by_key.get(key := move_key(new[j])):
            i = by_key[key].pop(0)
            found.add((i + 1, j + 1))
            moved_old.add(i)
            moved_new.add(j)
    outs = [i for i in removed if i not in moved_old]
    ins = [j for j in added if j not in moved_new]
    if threshold >= 1 or not outs or not ins or len(outs) * len(ins) > MOVE_MAX_CELLS:
        return found
    cutoff = max(0.0, threshold - MOVE_MARGIN)
    candidates = [
        (sc, a, b)
        for a, i in enumerate(outs)
        for b, j in enumerate(ins)
        if (sc := score(prepared_old[i], prepared_new[j], cutoff))
    ]
    return found | {(outs[a] + 1, ins[b] + 1) for a, b in greedy(candidates, threshold)}


def moves_of(rows) -> set[tuple[int, int]]:
    """The moves diff.align marked: (old line, new line), 1-based."""
    return {
        (r.left_no, int(MOVED_TO.match(r.changes[0])[1].replace(",", "")))
        for r in rows
        if r.kind == "moved-out"
    }


def bench_lines(
    books: list[list[str]], vocabularies: list[list[str]], rng: random.Random, unit: str
):
    """BENCH removed lines, and BENCH added ones: half of them edited
    versions of removed ones, half others."""
    pool = [(b, p) for b, book in enumerate(books) for p in book if move_key(p)]
    picked = rng.sample(pool, 2 * BENCH)
    removed = [p for _, p in picked[:BENCH]]
    added = [
        edited(p, rng.choice(EDITS), vocabularies[b], rng, unit) for b, p in picked[: BENCH // 2]
    ] + [p for _, p in picked[BENCH : BENCH + BENCH - BENCH // 2]]
    rng.shuffle(added)
    return removed, added


def seconds(algorithm: str, removed: list[str], added: list[str], threshold: float) -> float:
    """How long diff.mark_moves' fuzzy stage takes to score every pair, best
    of REPEATS runs."""
    prepare, score = MOVE_ALGORITHMS[algorithm]
    cutoff = max(0.0, threshold - MOVE_MARGIN)
    best = float("inf")
    for _ in range(REPEATS):
        t = time.perf_counter()
        out_items = [prepare(x) for x in removed]
        in_items = [prepare(x) for x in added]
        for ta in out_items:
            for tb in in_items:
                score(ta, tb, cutoff)
        best = min(best, time.perf_counter() - t)
    return best


def column(values: list[str], width: int) -> str:
    return "".join(f"{v:>{width}}" for v in values)


def score_trials(share: list[tuple[int, tuple]]) -> tuple[Counter, Counter, Counter, int]:
    """Score a share of the trials, (number, trial): the right moves found,
    by algorithm, threshold and kind of edit; the wrong ones, by algorithm
    and threshold; the moves to find, by kind; and how many runs were
    checked against diff.align (those of the first CHECKED_TRIALS)."""
    right: Counter = Counter()  # (algorithm, threshold, kind)
    wrong: Counter = Counter()  # (algorithm, threshold)
    to_find: Counter = Counter()  # kind
    checked = 0
    for n, (old, new, source, kinds) in share:
        # the pairing before any move gives way: the same at every threshold
        ops = difflib_opcodes(old, new)
        base = line_pairs(ops, old, new, move_similarity=2.0)
        facing = {
            (i, j)
            for _, i, j in base
            if i is not None
            and j is not None
            and token_similarity(
                similarity_tokens(old[i]), similarity_tokens(new[j]), PAIRING_THRESHOLD
            )
        }
        # the moves to find: a paragraph's two versions not shown face to face
        for j, i in enumerate(source):
            if i is not None and (i, j) not in facing and move_key(old[i]):
                paired_i = any(a == i for a, _ in facing)
                paired_j = any(b == j for _, b in facing)
                if not paired_i and not paired_j:
                    to_find[kinds[i]] += 1
        for algorithm, (prepare, score) in MOVE_ALGORITHMS.items():
            prepared_old = {i: prepare(x) for i, x in enumerate(old)}
            prepared_new = {j: prepare(x) for j, x in enumerate(new)}
            for threshold in THRESHOLDS:
                found = replay(
                    unpair_moved(base, old, new, threshold, algorithm),
                    old,
                    new,
                    threshold,
                    score,
                    prepared_old,
                    prepared_new,
                )
                if n < CHECKED_TRIALS and threshold in (0.5, 0.8):
                    rows_t, _, _ = align(
                        old,
                        new,
                        context=None,
                        opcodes=ops,
                        move_similarity=threshold,
                        move_algorithm=algorithm,
                    )
                    assert moves_of(rows_t) == found, (n, algorithm, threshold)
                    checked += 1
                for i, j in found:
                    if source[j - 1] == i - 1:
                        right[(algorithm, threshold, kinds[i - 1])] += 1
                    else:
                        wrong[(algorithm, threshold)] += 1

    return right, wrong, to_find, checked


def lines_of(number: int, language: str, unit: str) -> list[str]:
    """A book's lines of a kind: its paragraphs, or their sentences."""
    book = paragraphs(book_text(number))
    return book if unit == "paragraph" else split_sentences(book, language)[0]


def analyse(unit: str) -> tuple[list[str], tuple]:
    """The part of the report on one kind of line, and its suggested default
    (algorithm, threshold, precision, recall, F1, wrong moves)."""
    rng = random.Random(SEED)
    trials, corpus, books, vocabularies = [], [], [], []
    for number, language, title in BOOKS:
        book = lines_of(number, language, unit)
        vocabulary = [w for p in book for w in p.split(" ") if WORD.fullmatch(w)]
        corpus.append((title, len(book)))
        books.append(book)
        vocabularies.append(vocabulary)
        trials += [trial(book, vocabulary, rng, unit) for _ in range(TRIALS_PER_BOOK)]

    # the trials are scored in parallel, WORKERS processes each taking a
    # share (the alignment is pure Python: threads would wait on the GIL)
    shares = [list(enumerate(trials))[k::WORKERS] for k in range(WORKERS)]
    right: Counter = Counter()  # (algorithm, threshold, kind)
    wrong: Counter = Counter()  # (algorithm, threshold)
    to_find: Counter = Counter()  # kind
    checked = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        for r, w, f, c in pool.map(score_trials, shares, timeout=SCORE_TIMEOUT):
            right += r
            wrong += w
            to_find += f
            checked += c

    n_find = sum(to_find[k] for k in RECOGNISABLE)
    results = []
    for algorithm in MOVE_ALGORITHMS:
        for t in THRESHOLDS:
            good = sum(right[(algorithm, t, k)] for k in EDITS)
            good_recognisable = sum(right[(algorithm, t, k)] for k in RECOGNISABLE)
            found = good + wrong[(algorithm, t)]
            precision = good / found if found else 1.0
            recall = good_recognisable / n_find
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            results.append((algorithm, t, precision, recall, f1, wrong[(algorithm, t)]))
    best = {
        a: max((r for r in results if r[0] == a), key=lambda r: (r[4], r[1]))
        for a in MOVE_ALGORITHMS
    }
    top = max(r[4] for r in best.values())
    removed, added = bench_lines(books, vocabularies, rng, unit)
    timing = {
        a: (seconds(a, removed, added, best[a][1]), seconds(a, removed, added, 0.8))
        for a in MOVE_ALGORITHMS
    }
    tied = [a for a in MOVE_ALGORITHMS if best[a][4] >= top - F1_TIE]
    winner = best[min(tied, key=lambda a: timing[a][0])]

    title = f"{unit.capitalize()}s" + (" (--split sentence)" if unit == "sentence" else "")
    lines = [
        title,
        "=" * len(title),
        "",
        "Corpus (Project Gutenberg; paragraphs: blocks between blank lines"
        + ("; sentences: split by prosediff.sentences" if unit == "sentence" else "")
        + "):",
    ]
    width = max(len(t) for t, _ in corpus)
    lines += [f"  {t:<{width}}  {f'{n:,}':>7} {unit}s" for t, n in corpus]
    n_all = sum(to_find.values())
    lines += [
        "",
        f"Trials: {len(trials):,} ({TRIALS_PER_BOOK:,} per book, seed {SEED}), each a "
        f"stretch of {STRETCH[unit]:,} {unit}s:",
        f"  {MOVES:,} moved, each edited one way; {DECOYS:,} deleted and {DECOYS:,} "
        "inserted from elsewhere in the book.",
        f"Replayed matching checked against diff.align: {checked:,} runs, all equal.",
        "",
        f"Moves to find (a {unit}'s two versions not shown face to face): {n_all:,}",
    ]
    kw = max(len(k) for k in EDITS)
    lines += [
        f"  {k:<{kw}}  {f'{to_find[k]:,}':>6} ({to_find[k] / n_all * 100:5.1f}%)"
        + ("  recognisable" if k in RECOGNISABLE else "")
        for k in EDITS
    ]
    lines += [
        f"  (words 0% includes {unit}s left in place that the diff shows as moved",
        "  because others moved past them.)",
        "",
        "Best threshold of each algorithm, by F1 (precision on every move found,",
        "recall on the recognisable ones):",
        "  "
        + f"{'algorithm':<12}"
        + column(["threshold", "precision", "recall", "F1", "wrong"], 11),
    ]
    for a in MOVE_ALGORITHMS:
        _, t, p, r, f, n_wrong = best[a]
        lines.append(
            f"  {a:<12}"
            + column(
                [
                    f"{t:.2f}",
                    f"{p * 100:.1f}%",
                    f"{r * 100:.1f}%",
                    f"{f * 100:.1f}%",
                    f"{n_wrong:,}",
                ],
                11,
            )
        )
    cells = BENCH * BENCH
    lines += [
        "",
        f"Time to score {cells:,} pairs (diff.MOVE_MAX_CELLS, the most diff.mark_moves",
        f"scores; {BENCH:,} removed x {BENCH:,} added {unit}s), best of {REPEATS:,} runs:",
        "  " + f"{'algorithm':<12}" + column(["at best", "per pair", "at 0.80", "per pair"], 11),
    ]
    for a in MOVE_ALGORITHMS:
        t_best, t_80 = timing[a]
        lines.append(
            f"  {a:<12}"
            + column(
                [
                    f"{t_best:.2f} s",
                    f"{t_best / cells * 1e6:.1f} us",
                    f"{t_80:.2f} s",
                    f"{t_80 / cells * 1e6:.1f} us",
                ],
                11,
            )
        )
    lines += [
        "",
        f"Within {F1_TIE * 100:.1f} points of the best F1: {', '.join(tied)}.",
        f"Suggested default: {winner[0]} at {winner[1]:.2f} (F1 {winner[4] * 100:.1f}%, "
        f"{timing[winner[0]][0]:.2f} s for {cells:,} pairs), the fastest of those.",
    ]
    short = {
        k: k.replace("words ", "w").replace("reordered", "reord").replace(" + ", "+") for k in EDITS
    }
    for a in MOVE_ALGORITHMS:
        lines += [
            "",
            f"{a}, by threshold (recall by kind of edit, %):",
            "  "
            + f"{'thr':<5}"
            + column(["prec.", "recall", "F1", "wrong"], 8)
            + "  "
            + column([short[k] for k in EDITS], 10),
        ]
        for algorithm, t, p, r, f, n_wrong in results:
            if algorithm != a:
                continue
            by_kind = [
                f"{right[(a, t, k)] / to_find[k] * 100:5.1f}" if to_find[k] else "-" for k in EDITS
            ]
            lines.append(
                f"  {t:<5.2f}"
                + column([f"{p * 100:.1f}", f"{r * 100:.1f}", f"{f * 100:.1f}", f"{n_wrong:,}"], 8)
                + "  "
                + column(by_kind, 10)
            )
    return lines, winner


def main() -> None:
    t0 = time.monotonic()
    lines = [
        "prosediff: sensitivity of the moved-line matching to algorithm and threshold",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]
    winners = {}
    for unit in UNITS:
        part, winners[unit] = analyse(unit)
        lines += [*part, "", ""]
    lines += ["Suggested defaults:"]
    lines += [
        f"  {unit + 's':<11} {w[0]} at {w[1]:.2f} (F1 {w[4] * 100:.1f}%)"
        for unit, w in winners.items()
    ]
    lines += ["", f"Run time: {time.monotonic() - t0:,.0f} s."]
    report = "\n".join(lines) + "\n"
    (DOCS / "move_sensitivity.txt").write_bytes(report.encode("utf-8"))
    print(report)


if __name__ == "__main__":
    main()
