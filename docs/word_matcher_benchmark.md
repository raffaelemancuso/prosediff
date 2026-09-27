# Word matchers: benchmark and recommendation

prosediff pairs the words of each changed line with `difflib.SequenceMatcher`
(in `diff._word_ops`). `docs/word_matcher_benchmark.py` puts other sequence
matchers in its place and measures, on the changed lines of the simulated
revisions of `docs/passage_benchmark.py` (saved in `docs/benchmark_data/`):

- **speed**: the word pairing time, the best of up to three passes;
- **readability**: how many separate changes a changed line is cut into
  (fewer read better), and how many single words or signs are left unchanged
  between two changes (the "the" or "," a matcher anchors on by chance);
- **faithfulness**: on how many lines prosediff shows something different
  from what it shows with difflib (after its own slide and space merging);
- **moved passages**: precision, recall and boundary accuracy (IoU) of
  `docs/passage_benchmark.py --quick` with the matcher in place.

Each matcher runs in a process of its own, with a 10-minute limit, so that
one that raises, crashes or hangs is reported as failed and the others still
run. The full output is `docs/word_matcher_benchmark.txt`.

## Results

2,776 changed lines (run of 2026-09-27 12:56). The speed-up is against
difflib in the same run: absolute times on this laptop varied fourfold
between runs of the same day, the ratios much less. Differences of a second
or so among the fast matchers are within that noise (histodiff histogram
and similar-rs myers swapped places between runs).

| Matcher | Core | Speed-up | Changes per line | Lone words | Lines unlike difflib | Paragraphs P / R / IoU | Sentences P / R / IoU |
|---|---|---:|---:|---:|---:|---|---|
| difflib (in use) | Python | 1.0× | 1.57 | 34 | 0 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| rapidfuzz (Indel) | C++ | 46.9× | **2.32** | 35 | **487** | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| histodiff histogram | Python | 28.7× | 1.57 | 34 | 5 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| Levenshtein | C++ | 28.2× | **1.76** | 33 | **277** | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| similar-rs myers | Rust | 18.1× | 1.57 | 34 | 3 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| similar-rs patience | Rust | 15.5× | 1.57 | 35 | 4 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| similar-rs histogram | Rust | 15.2× | 1.57 | 34 | 3 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| patiencediff | Rust | 14.0× | 1.57 | 35 | 4 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| histodiff patience | Python | 13.8× | 1.57 | 34 | 5 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| histodiff myers | Python | 12.3× | 1.57 | 34 | 5 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| myers-diff | C | 12.2× | 1.57 | 35 | 4 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| dissimilar | Rust | 12.0× | 1.57 | 32 | 4 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| cydifflib | C++ | 11.1× | 1.57 | 34 | **0** | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| fastdiff | Python | 5.3× | 1.63 | **328** | 78 | **94.9** / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| pymyers | Python | 2.3× | 1.60 | 110 | 84 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| cdifflib | C | 1.8× | 1.57 | 34 | **0** | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| myers | Python | 1.2× | 1.60 | 110 | 84 | 100.0 / 100.0 / 98.9 | 100.0 / 98.7 / 97.5 |
| edit-distance | Python | failed | | | | no result within 600 s (about 0.06× in an earlier run) | |
| lcs2 | Python | failed | | | | no result within 600 s | |
| patience-diff-algo | Python | failed | | | | `IndexError` (a bug of the package, below) | |

The moved passages are found equally well by every matcher but fastdiff:
they depend on which words are paired only at the edges, which prosediff's
slide settles anyway. What tells the matchers apart is how a changed line
reads, and whether the package is fit to depend on.

## What the differences look like

**Levenshtein and rapidfuzz cut a replaced word around the characters it
shares with its replacement.** They minimise an edit distance, so a quote
mark or a comma common to the two words becomes a match of its own and the
change is split in two. Of the 277 lines Levenshtein shows differently, 273
are cut into more pieces:

> difflib: [-“She-]{+it+} is a great fool for going away, if she liked him.”
>
> Levenshtein: [-“-]{+it+}[-She-] is a great fool for going away, if she liked
> him.”

> difflib: You [-will-]{+Elizabeth,+} not find him more favourably spoken of
> by anyone.”
>
> Levenshtein: You {+Elizabeth+}[-will-]{+,+} not find him more favourably
> spoken of by anyone.”

**similar-rs, patiencediff and histodiff differ from difflib mostly where
the text repeats itself**, on which copy of a repeated phrase was removed:
both readings are right. similar-rs's 3 lines are all of this kind;
patiencediff's 4 are the same 3 and a long line of 10 changes it cuts into
11; histodiff's 5 are the same 3 and two long lines, one cut into a piece
more, one into a piece fewer. (The lines myers-diff and dissimilar show
differently were not inspected.)

> difflib: … come allora; [-eppure aveva sonno. I rimorsi … più assolute;
> -]eppure aveva sonno. L'ordine …
>
> similar-rs myers: … come allora; eppure aveva sonno. [-I rimorsi … più
> assolute; eppure aveva sonno. -]L'ordine …

**fastdiff and the two pure-Python Myers diffs** (myers, pymyers) anchor
three to ten times as often on a lone word, and fastdiff's paragraphs lose
five points of precision in the moved passages.

## Fitness as a dependency

Checked on PyPI and GitHub on 2026-09-27.

| Package | Latest release | Repository | Wheels for Python 3.14 | Notes |
|---|---|---|---|---|
| patiencediff | 0.2.19, 2026-06-15 | breezy-team/patiencediff, since 2018, 24 releases, 7 contributors | Windows, Linux (also free-threaded) | Breezy's; Rust core with a pure-Python fallback |
| similar-rs | 0.1.1, 2026-08-28 | Maksim-Burtsev/similar-rs, created 2026-08-27, 2 contributors, 1 star | abi3 (3.9+): Windows, Linux, macOS | a thin binding of the Rust crate similar (mitsuhiko/similar: 1,331 stars, 15 contributors, last commit 2026-09-16) |
| histodiff | 0.2.0, 2026-09-24 | rmnvg/histodiff, created 2026-09-13, one main author | pure Python, any platform | MIT, no dependencies; `histodiff.compat.SequenceMatcher` is a drop-in |
| cydifflib | 1.2.0, 2025-04-11 | rapidfuzz/CyDifflib, last commit 2025-04-11 | none: built from source | the only fast matcher identical to difflib |
| Levenshtein | 0.27.5, 2026-09-12 | rapidfuzz/Levenshtein, 22 contributors | yes | excluded: cuts lines into more pieces |
| rapidfuzz | 3.14.6, 2026-08-30 | rapidfuzz/RapidFuzz | yes | excluded: cuts lines into more pieces |
| myers-diff | 0.1.0 | template metadata ("Your Name") | none: built in a Visual Studio prompt | not fit to depend on |
| dissimilar | 0.1.1 | messense/py-dissimilar, pyo3 0.18 | none: builds only after a version is added to its pyproject.toml and pyo3 is moved to 0.25 (0.18 cannot link against 3.14) | not fit to depend on |
| patience-diff-algo | 1.0.3, 2026-04-03 | joery0x3b800001/patience_diff | pure Python | `_matches_recursive` gets its bounds in the wrong order: `IndexError`, and common words silently left unmatched; bug report drafted |

## Recommendation

**Pair words with patiencediff, keeping difflib as the fallback when it is
not installed.** Done: `diff._word_matcher`, with patiencediff a dependency.
The other uses of difflib in `diff.py` stay: the letters of one changed word
(`char_marks`), the trimming of a moved passage's edges (`_core`), and the
line alignment of `align()` when it is given none (`difflib_opcodes`; a
comparison aligns lines with git).

- It is among the fast matchers (14× difflib here), and those cannot be told
  apart on speed within this machine's noise.
- It shows the same thing as difflib on all but 4 of 2,776 changed lines:
  three equally right readings of repeated text, and one long line of 10
  changes cut into 11; it finds the moved passages exactly as difflib does.
- Of the fast matchers it is the one with a track record: part of Breezy,
  on PyPI since 2018, 24 releases, the latest in June 2026, wheels for
  Python 3.14 on Windows and Linux, and a pure-Python fallback where no wheel
  fits, so installing prosediff can never fail on it.

The alternatives, in order:

- **similar-rs** (myers): slightly closer to difflib (3 lines, all repeated
  text, none cut into more pieces) and a single abi3 wheel for every platform and future Python; its diff engine is
  mature, but the Python binding is a month old, with one release day and
  one author. The better choice if it proves maintained.
- **histodiff** (histogram): pure Python and a drop-in, as good on output
  (5 lines); but two weeks old.
- **cydifflib**: the only fast matcher identical to difflib, but without
  wheels for Python 3.14 and without a release since April 2025.

Whichever is chosen, only the word pairing gets faster; how much of a whole
comparison that is was not measured here.

Two weaknesses this benchmark brought out were prosediff's own, not a
matcher's, and are fixed in its slide (`_edge_score`): where a removal
could equally start at the line's start or after a full stop, the tie kept
the matcher's choice, so a passage starting and ending with "Mr." showed as
`Mr. [-Collins … Mr. -]Collins` (the line's edges now score above a full
stop, which may be an abbreviation's); and a full stop followed by a
footnote reference or a closing quote or bracket (`A.[^1]`, `yes.”`) did
not count as the end of a sentence. The results above were measured before
these fixes; with them, patiencediff differs from difflib on 3 lines, not 4.

## Reproducing

The matchers that are not project dependencies are added for the run
(myers-diff and dissimilar from wheels built as the script's docstring
describes):

```sh
cd docs
uv run --with patiencediff --with histodiff --with similar-rs \
    --with Levenshtein --with cydifflib --with cdifflib --with myers \
    --with pymyers --with patience-diff-algo --with lcs2 --with fastdiff \
    --with edit-distance --with <myers_diff wheel> --with <dissimilar wheel> \
    python word_matcher_benchmark.py
```

Naming matchers runs only those, into `word_matcher_benchmark_partial.txt`:
`python word_matcher_benchmark.py difflib "similar-rs myers"`. A full run
takes about 35 minutes, lcs2's ten of them waiting for its time limit.
