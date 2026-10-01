# How it works

[Back to the README](../README.md).

## Tracked changes

`--format docx` (in the window, "Word, tracked") writes a copy of the new
Word document with each change since the old one as a Word tracked change,
by the target commit's author (else `prosediff`). `--format odt`
("OpenDocument, tracked") does the same for two OpenDocument texts. Neither
file needs tracked changes of its own, so a version edited with Track
Changes off gets them back from its draft.

Everything in the new file is kept: styles, page setup, headers and
footers, tables, images, fields, footnotes and comments. Accepting every
change gives the new file; rejecting every one gives the old. A paragraph
added or removed whole is inserted or deleted with its paragraph mark (a
deleted one copied from the old file with its style), so no empty
paragraph is left; a table row likewise. What a deleted paragraph pointed
at (an image, a link, a comment, a footnote) does not come with it. ODF tracks
no table rows, so an `.odt` marks them with LibreOffice's extension
`loext:text-changes-only` (since 7.2). Formatting-only changes are a
formatting change in Word; an OpenDocument text shows the new formatting.
The new file's own tracked changes are accepted first; a paragraph or row
it deleted that way is left empty, not merged away.

Only these two pairs are supported, one document at a time, paragraph by
paragraph. Other inputs (Markdown or text files, Word against OpenDocument,
a `.docx` from two OpenDocument texts or the reverse, several files) are
refused with a message saying why. Word does not read tracked changes from
an `.odt`: open it in LibreOffice. If the output is open and locked in Word or LibreOffice, it is written beside it as
`NAME__locked_YYYYMMDD_HHMMSS.docx`, with a warning.

## Reading and aligning

GitPython resolves the sides and lists the changed files, with rename
detection. Lines are aligned by `git diff --no-index --histogram`, in one
process for all files.

Within a block of replaced lines, each old line is paired with its most
similar new line (twice their longest common subsequence of words over
their total length, by rapidfuzz). Lines at least half similar are paired
without crossing, the rest in order, so an inserted line stands alone.
Paired lines are compared word by word with patiencediff (see
`docs/word_matcher_benchmark.md`), and a word replaced by a similar word
letter by letter. Removed or added words slide to where they read best, so
a removed sentence takes its own full stop. Moved lines and passages are
then looked for (see [Moved text](moves.md)).

**Word and OpenDocument files are read directly, not converted to
Markdown**: Word with python-docx, OpenDocument with odfdo. Headings,
lists, tables, footnotes, links, character formatting, each paragraph's
language, equations and comments land where they sit in the text. Tracked
changes are settled while reading: accepted, rejected, or all kept as
marked spans. Headers, footers and page layout are not compared. Neither
Word nor pandoc is needed. Each paragraph is compared as plain text with
each character's formatting beside it, so a change of formatting alone is
told apart from a change of words. Rows are numbered by paragraph (3.1,
3.2 for sentences with `--split sentence`); Markdown keeps line numbers.
Markdown is written from a document only for git's own commands
(`--to-markdown`, `git diff` once set up).

Each comment (in Markdown, before any filter runs) is folded into one
Unicode private-use character, so it is compared like a word and matches on both sides even when renumbered.
Comments present on both sides are removed before alignment and never
shown; a paragraph with a new or removed comment is shown.

Comments marked resolved are skipped by default (`--skip-resolved`, the
window's **Skip resolved comments**): when a Word or OpenDocument file is
read, each comment marked resolved, and each reply to one, is dropped
before anything else sees it. A skipped comment is therefore never shown
in the HTML report (neither as a marker nor as a card in the margin),
never written into the diffs, and never sent to the AI, whether two
versions are compared or one file is reviewed. Word records the mark in
the `word/commentsExtended.xml` part (`w15:done` on the comment's
`w15:commentEx`, a reply linked by `w15:paraIdParent`, as [MS-DOCX]
specifies); LibreOffice in its ODF extension, `loext:resolved` and
`loext:parent-name` on `office:annotation`. A comment open in the old
version and resolved in the new one is shown as removed.
`--no-skip-resolved` keeps every comment. Markdown comments have no
resolved state and are always kept.

In a Markdown file, the formatted view recognises common inline Markdown
with regular expressions, since no Markdown parser reports where each
inline element sits in the source.

Files are read as UTF-8, undecodable bytes replaced, and split into lines
as git does (CRLF counts as LF). A file is binary if its first 8,000
bytes contain a NUL byte.

From Python:

```python
from prosediff import Options, compare, compare_paths, render

html = render(compare("path/to/repo", "HEAD~1", "HEAD", Options(context=3)))
html = render(compare_paths("v1.docx", "v2.docx", Options(by_sentence=True, language="it")))
```

`Options` holds every comparison option (its docstring lists them).
`compare` also takes `paths`, `cached` and `untracked`; `compare_paths`
takes `paths` and `include`.
