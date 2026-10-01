# How it works

[Back to the README](../README.md).

## Tracked changes

`--format docx` (in the window, "Word, tracked") compares one Word
document with another and writes a copy of the new one with each change
since the old one marked in it as Word's own tracked change, by the target
commit's author (else `prosediff`); `--format odt` ("OpenDocument,
tracked") does the same for two OpenDocument texts, for LibreOffice
Writer. It is what Word's *Compare* and LibreOffice's *Compare Document*
make, from prosediff's own pairing of paragraphs and words; neither file
needs tracked changes of its own. So a version edited with Track Changes
off gets its tracked changes back, the draft it came from being all that is
needed. In the window, comparing two files, the two
formats are greyed out unless both files are of their kind.

Everything the new file holds is kept as it is: its styles, page setup,
sections, headers and footers, tables, images, fields, footnotes and
comments. Accepting every change gives the new file, rejecting every one
the old: words deleted come back in the formatting they had in the old
file, a paragraph added or removed whole is inserted or deleted with its
paragraph mark (a deleted one copied from the old file with its style),
so no empty paragraph is left behind, and a table row added or removed
whole is a row inserted or deleted. (ODF itself tracks no table rows,
only their cells' text: an `.odt` marks the row as LibreOffice does since
7.2, with its extension `loext:text-changes-only`; another program sees
the cells' words inserted or deleted, the row in place.) Words whose formatting alone changed
are a formatting change in Word; an OpenDocument text shows them in their
new formatting. What a deleted paragraph pointed at in the old file (an
image, a link, a comment, a footnote reference) does not come with it. The
new file's own tracked changes are accepted first; a paragraph or row it
deleted as a tracked change is left empty, not merged away (docx-plus,
which accepts them, does not merge paragraphs yet).

Only these two pairs are written so, one document at a time, paragraph by
paragraph: two Markdown or text files, a Word document against an
OpenDocument text, a `.docx` asked of two OpenDocument texts (or the other
way round) or several files are refused, with a message saying why. Word
does not read tracked changes from an `.odt`: open it in LibreOffice. A
document open, and so locked, in Word or LibreOffice is left be, the
output written beside it as `NAME__locked_YYYYMMDD_HHMMSS.docx`, with a
warning.

## Reading and aligning

GitPython resolves the sides and lists the changed files, with rename
detection (`git diff -M`); two folders are compared by path, a file that
disappears and reappears identical elsewhere counting as renamed. The lines
of every changed file are aligned by git itself, `git diff --no-index
--histogram --unified=0` on temporary copies, one process for all files, of
which only the hunk headers are read.

Within a block of replaced lines, each old line is paired with its most
similar new line. Similarity is the share of words and punctuation two lines
have in common, in order (twice their longest common subsequence over their
total length, computed by rapidfuzz), lines at least half similar are paired
so that the total similarity is highest without crossing, and the lines left
in between are paired in order. A line inserted in the middle of an edited
paragraph thus stands alone instead of shifting every pair below it. Paired
lines are compared again word by word, with patiencediff's patience diff
(difflib's result on all but a few lines in thousands, fourteen times as
fast; the matchers compared are in `docs/word_matcher_benchmark.md`), and a
word replaced by a single word is compared letter by letter when at least
half its letters survive.
Words removed or added between unchanged text slide, as git slides its
hunks, to where they read best: a removed sentence takes its own full stop,
not the one of the sentence before it, and starts where the sentence does,
or where the line does (a full stop may be an abbreviation's, as in "Mr.";
one followed by a footnote reference or a closing quote still ends its
sentence). Moved lines and passages are then looked for among the lines
and words left removed and added (see [Moved text](moves.md)). The HTML
report is rendered with Jinja2.

**Word and OpenDocument files are read directly, not converted to
Markdown.** A Word document is read with python-docx, which opens the
document and resolves its styles, formatting, links and comments; its
paragraphs are then walked element by element into paragraphs of styled
text, so everything lands where it sits in the text: headings, list items,
tables, footnotes and endnotes, links, bold, italic, underline,
strikethrough, superscript and subscript, the language each paragraph is
marked with, equations as linear text (`DV_(it) = β ⋅ (a)/(b)`), and each
comment, with its author and date, where it starts. Tracked changes
are settled during the walk (accepting keeps the inserted runs and drops the
deleted ones, rejecting the reverse, "all" keeps both as marked spans), so
the spaces at the edges of a change stay where Word had them, comments
anchored in deleted text are kept, and footnotes referenced only from
deleted text are dropped with it. Headers, footers and page layout are not
part of the comparison. Nothing outside Python is needed: no Word, no
pandoc. On a 12,000-word manuscript with 26 comments and tracked changes by
two co-authors, the accepted and rejected texts match pandoc's word for word.
Each paragraph is then compared as its text alone, with the formatting of
each of its characters beside it: no Markdown syntax in the text, so an
asterisk or a bracket typed in the document is just text, and a change of
formatting alone (a word made bold) is told apart from a change of words.
The rows of a document are numbered by paragraph (1, 2, 3, and 3.1, 3.2 for
the sentences of paragraph 3 with `--split sentence`); Markdown files keep
their line numbers. Markdown is only written from a document for git's own
commands (`--to-markdown`, `git diff` once set up), in pandoc's syntax.

An OpenDocument text is read the same way with odfdo, which opens the
package and resolves the styles that make a span bold, italic, underlined
and so on, into the same paragraphs: headings, lists, tables, footnotes,
links, and each comment (`office:annotation`) where it starts. Its tracked changes are settled as
well: an insertion is the text between two markers, kept or dropped, and a
deletion a marker whose text LibreOffice keeps apart, put back when the
changes are rejected.

Folding comments replaces each comment (in a Markdown file, each comment
span, before any filter runs) with one character of the Unicode private use
area standing for its author and text: a comment is then compared like a
word, the same comment matches on both sides even when its `id` was
renumbered, and a filter cannot cut it in two. The characters become markers when the HTML report is
built. The comments present on both sides are then taken out of the text,
with the spaces around them (one is left where a comment stood between two
words), before the lines are lined up: they are never shown, and a
paragraph that only gained a comment moved over from its neighbour is not a
change. A paragraph with a new or removed comment is shown.

In a Markdown file, the formatted view recognises the common inline Markdown
(headings, block quotes, code, strong, emphasis, links, images, citations,
pandoc spans with attributes) with regular expressions, since no Markdown parser reports where
each inline element starts and ends in the source. Each character gets its
style classes, and every piece of text the diff emits is cut into runs of
equal style, so formatting and change markup never have to nest.

Files are read as UTF-8, with undecodable bytes replaced, and split into
lines as git does (on LF; CRLF counts as LF). A file is binary if its first
8,000 bytes contain a NUL byte, as git decides.

From Python:

```python
from prosediff import Options, compare, compare_paths, render

html = render(compare("path/to/repo", "HEAD~1", "HEAD", Options(context=3)))
html = render(compare_paths("v1.docx", "v2.docx", Options(by_sentence=True, language="it")))
```

`Options` holds every option of a comparison but what is compared (its
docstring lists them); `compare` also takes `paths`, `cached` and
`untracked`, and `compare_paths` `paths` and `include`.
