# Moved text

[Back to the README](../README.md).

## How moved text is found

Moved lines are found among the lines left removed and added: identical
ones first, then the most similar pairs. Moved passages are then found among
the words still removed and added. Each line's runs of changes (joined
across gaps of up to two unchanged words) are scored against the other
side, a short passage also against the best-matching window of a longer
one. Each match is trimmed to the runs of at least two shared tokens.

A match needs at least four words, 15 characters, and two shared words of
four letters or more. The most similar pairs are taken first. A removed and
an added passage from the same line are an edit in place, never a move.
Past 250,000 pairs of passages, only pairs sharing a rare word are tried.
These limits can be changed (`--passage-*`, or the GUI's advanced
settings); their defaults come from `docs/passage_benchmark.py`, which
simulates revisions of Project Gutenberg books.

## Moved lines: algorithm and threshold

Two likeness measures are offered, both by rapidfuzz on words and
punctuation: `token-sort`, the words in common whatever their order, and
`token-set`, the words both share against the rest of each. Three
order-bound measures were dropped: `tokens`, `chars` and `levenshtein`.

The benchmark simulated revisions of five Project Gutenberg books in
English, Italian and German. In each of 1,000 stretches of 40 paragraphs
(or 60 sentences), 4 lines were moved and edited or reordered, and 4 others
were replaced, half by a close look-alike that a threshold must reject.
Time is for scoring 250,000 pairs, the most prosediff scores in one file.
The best threshold of each measure:

| algorithm    | paragraphs: threshold | F1    | time   | sentences: threshold | F1    | time   |
|--------------|----------------------:|------:|-------:|---------------------:|------:|-------:|
| `token-sort` |                  0.70 | 98.6% | 0.95 s |                 0.55 | 99.2% | 0.32 s |
| `token-set`  |                  0.80 | 98.8% | 8.88 s |                 0.70 | 99.3% | 1.91 s |
| `chars` (dropped) |             0.50 | 96.3% | 1.22 s |                 0.50 | 97.4% | 0.25 s |
| `tokens` (dropped) |            0.40 | 95.6% | 0.97 s |                 0.40 | 98.0% | 0.34 s |
| `levenshtein` (dropped) |       0.30 | 90.1% | 0.83 s |                 0.30 | 92.0% | 0.30 s |

Only the two token measures follow reordered sentences or clauses, and
they tie on F1, but `token-sort` is six to nine times faster. Hence the
defaults: **`token-sort` at 0.70 by paragraph, `token-sort` at 0.55
sentence by sentence**, since an edited word costs a short sentence more.
`--move-similarity` and `--move-algorithm` override both. Lower the
threshold to follow heavier rewrites (at 0.60 by paragraph, `token-sort`
finds 90% of paragraphs with half their words changed, 1.9% of its moves
then wrong); raise it to be stricter.

The full tables are in [docs/move_sensitivity.txt](move_sensitivity.txt).
`uv run python docs/move_sensitivity.py` remakes them, and `--check`
measures only the defaults, as a check after a code change.
