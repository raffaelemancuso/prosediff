# Moved text

[Back to the README](../README.md).

## How moved text is found

Moved
lines are found among the lines left removed and added, identical ones
first, then the most similar pairs. Moved passages are then found among the
words still removed and added: each changed line's runs of changes (joined
across gaps of up to two unchanged words), and each line removed or added
whole, are scored against those of the other side by the moved-line
measure, a shorter passage also against the best-matching window of a
longer one (rapidfuzz's partial alignment), each match trimmed to the runs
of at least two tokens both share and widened over a word edited next to
its edge. A match needs at least four words and 15 characters, and two
words of four letters or more in common (passages alike in their articles
and prepositions alone are chance); the most similar pairs are taken first,
and what is left of a passage around a match is matched again. A removed
and an added passage that are the same change of one line are an edit in
place, never a move. Past 250,000 pairs of passages (a long document revised
throughout), only the pairs sharing a rare word are tried. All of these
limits can be changed (`--passage-*`, or the GUI's advanced settings);
their defaults were chosen with `docs/passage_benchmark.py`, which simulates
revisions of books from Project Gutenberg and measures the moved passages
found, their precision, recall and bounds, and their cost in time.

## Moved lines: algorithm and threshold

Which removed and added lines count as one line moved depends on how their
likeness is measured and on the threshold it must reach. Two measures are
offered, both computed by rapidfuzz on the words and punctuation of the two
lines (spacing aside): `token-sort` (in common whatever their order: twice
the longest common subsequence of the sorted words over their total length)
and `token-set` (rapidfuzz's `token_set_ratio`: the words both share against
the rest of each). Three order-bound measures were compared too and
dropped: `tokens` (the words in common, in order), `chars` (the same on
characters) and `levenshtein` (1 − words inserted, deleted or replaced over
the longer line's).

They were compared on simulated revisions of five public-domain books from
Project Gutenberg (Austen, Darwin, Mill, Manzoni, Goethe: English, Italian
and German; a novel, science, an essay, drama), where where every line went
is known, once for each kind of line prosediff compares: paragraphs, and
sentences (`--split sentence`, split by prosediff's own sentence splitter).
Each of 1,000 stretches of 40 paragraphs (or 60 sentences) had 4 lines
moved and edited one way (words replaced, deleted or inserted at 0% to 50%,
or reordered: a paragraph's sentences, a sentence's clauses; with or without
10% of words edited), and 4 deleted while 4 others were inserted, half of
them the deleted line's closest look-alike from elsewhere in the book,
which a threshold must turn down. A move found is right when it pairs a
line with its own new version; recall counts the moves a reader would
still call moves (up to 30% of words edited, or reordered). The time is
that of scoring 250,000 pairs, the most prosediff scores in one file (500
removed × 500 added lines). The best threshold of each measure:

| algorithm    | paragraphs: threshold | F1    | time   | sentences: threshold | F1    | time   |
|--------------|----------------------:|------:|-------:|---------------------:|------:|-------:|
| `token-sort` |                  0.70 | 98.6% | 0.95 s |                 0.55 | 99.2% | 0.32 s |
| `token-set`  |                  0.80 | 98.8% | 8.88 s |                 0.70 | 99.3% | 1.91 s |
| `chars` (dropped) |             0.50 | 96.3% | 1.22 s |                 0.50 | 97.4% | 0.25 s |
| `tokens` (dropped) |            0.40 | 95.6% | 0.97 s |                 0.40 | 98.0% | 0.34 s |
| `levenshtein` (dropped) |       0.30 | 90.1% | 0.83 s |                 0.30 | 92.0% | 0.30 s |

`token-sort` and `token-set` are the only ones that follow a line whose
sentences or clauses were reordered, and they keep false moves rare where
the order-bound measures need a low threshold to reach the same recall
(and then pair unrelated lines). The two are tied on F1 for both kinds of
line, and `token-sort` is six to nine times faster, hence the defaults, one
for each kind of line: **`token-sort` at 0.70 by paragraph, `token-sort` at
0.55 sentence by sentence** (a sentence is short, so each word edited costs
it more likeness). `--move-similarity` and `--move-algorithm` override
both; the window shows the default of the way chosen, and switches it when
"Sentence by sentence" is switched, unless another value was chosen. The
former default, `tokens` at 0.80, was as precise (99.3%) but found only
73.8% of the paragraphs moved: 67% of those with 30% of their words edited
and 31% of the reordered ones, against 97.5% and 99.8% now (with
`token-sort` at 0.70). Lower `--move-similarity` to follow heavier rewrites
(by paragraph at 0.60, `token-sort` finds 90% of paragraphs with half their
words changed, 1.9% of its moves then wrong), raise it to be stricter.
The full tables, by threshold and kind of edit, are in
[docs/move_sensitivity.txt](move_sensitivity.txt); `uv run python
docs/move_sensitivity.py` remakes them for the two measures offered, and
`--check` measures only the defaults, as a check after a change of the code.
