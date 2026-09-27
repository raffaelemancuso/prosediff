# Benchmark data

Kept in the repository so that the benchmarks in `docs/` need neither the
network nor the time to make their trials again.

- `gutenberg/<id>.txt`: the public-domain books of `move_sensitivity.py`
  (`BOOKS`), exactly as Project Gutenberg serves them at
  `https://www.gutenberg.org/cache/epub/<id>/pg<id>.txt`, licence text
  included; `.gitattributes` keeps git from changing their line endings.
- `passage_benchmark_cases.json.gz`: the trials of `passage_benchmark.py`
  (the two versions, the true moves, and the lines' alignment by git), also
  read by `word_matcher_benchmark.py`. It records the parameters that made
  it; when they change, the next run makes it again and overwrites it.
