<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/logo_wordmark_dark.svg">
  <img src="https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/logo_wordmark.svg" alt="prosediff logo: a pilcrow with a red stem and a green stem, the old and new versions of a paragraph side by side" width="292" height="64">
</picture>

# prosediff

[![PyPI](https://img.shields.io/pypi/v/prosediff)](https://pypi.org/project/prosediff/)
[![Python](https://img.shields.io/pypi/pyversions/prosediff)](https://pypi.org/project/prosediff/)
[![Tests](https://github.com/raffaelemancuso/prosediff/actions/workflows/tests.yml/badge.svg)](https://github.com/raffaelemancuso/prosediff/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/pypi/l/prosediff)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/prosediff)](https://pypistats.org/packages/prosediff)
[![Stars](https://img.shields.io/github/stars/raffaelemancuso/prosediff)](https://github.com/raffaelemancuso/prosediff/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/raffaelemancuso/prosediff)](https://github.com/raffaelemancuso/prosediff/commits/master)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

**Side-by-side comparison of prose, not code: Word documents (.docx) first,
OpenDocument (.odt) and Markdown too.**

Diff tools are made for code, where a line is a statement, a change is a
line and nobody comments inside the file. prosediff is made for prose, where
a line is a whole paragraph, a change is a few words inside it, paragraphs
move, and co-authors leave comments in the margin: papers, reports, books,
the drafts co-authors send back. It is optimised for Word files, reads the
OpenDocument texts of LibreOffice (.odt) as well, and works on Markdown and
any text file too; for code, a code diff tool serves better.

It writes a self-contained HTML report showing the differences between two
versions side by side: the older version on the left, the newer on the right,
each paragraph facing the paragraph it came from, changed words highlighted
inside it. Text that moved is followed to its new place **even when it was
edited on the way**. The versions are two Word or OpenDocument documents,
two Markdown or text files, two folders, or commits of a git repository, its index (staged
changes) or working tree. Paragraphs wrap and are numbered, changes are
described in plain English, and the new and removed comments of Word
documents are shown and listed.

**Track Changes need not be turned on**, in Word or in LibreOffice: prosediff
compares the two saved versions themselves, so it shows what changed between
a draft and the one returned whether or not the co-author tracked their
edits. When they did, the tracked changes are accepted (or rejected, or kept
as markup) as `--docx-changes` says. More than that, **prosediff can put the
tracked changes back**: from a draft and a version returned without them, it
writes a copy of the returned Word document (or OpenDocument text) with each
change a tracked change, to accept or reject one by one in Word or
LibreOffice, as if Track Changes had been on all along ([Tracked
changes](#tracked-changes)).

![An HTML report made by prosediff: the AI's verdict in the top bar; below, two versions of a short text on free fall side by side, the second a revision with errors in it, changed words highlighted, and beside them a margin of cards for the comments and the problems the AI marked, one of them pinned, its passage highlighted in the text](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_page.png)

## Why prosediff

Tools for comparing versions of a text fall into two camps, and neither
serves a paper written with co-authors well:

- **Code diff tools** (`git diff`, GitHub and GitLab, diff2html, delta,
  Meld and the like) compare plain text line by line. They show a Word
  document as a binary file, and a paragraph kept on one long line as one
  line that changed, often without wrapping it.
- **Word's own Compare Documents** reads Word files, but its result is a
  third Word document full of revision marks, to review in Word: it cannot
  compare a folder, a git history or a Markdown file, and it leaves you to
  work out which comments are new.

prosediff sits between the two:

- **It compares Word documents directly**: `prosediff --files draft.docx
  draft_returned.docx` shows what a co-author changed, whatever they tracked
  or did not. Their tracked changes are accepted (or rejected) exactly as
  Word would, spaces included, and **their comments are kept**: each shown
  where it sits, new comments marked with a green balloon holding a +,
  removed ones a red one holding a −, each with a card in the margin beside its paragraph
  (its author, date and text). Only new and removed comments are shown: those already
  in the old version are left out, even where they moved. The same works on
  OpenDocument texts (.odt) from LibreOffice, both read directly (no
  conversion to Markdown: formatting, languages and comments are kept as
  they are), on the Markdown that `pandoc` makes of a Word file, and on
  Word files in git, where `prosediff
  --setup-git` also has `git diff` itself show them as text.
- **It lines the two versions up correctly**: each edited paragraph faces
  the paragraph it came from, however much it was rewritten, and a paragraph
  inserted, deleted or split does not shift the ones below it onto the wrong
  partners. A paragraph unrelated to anything on the other side is shown as
  removed or added rather than forced against a stranger. Comments and
  footnote numbers do not disturb the alignment: a renumbered footnote or a
  comment that moved is no change.
- **It follows text that moved, even when it changed**: a paragraph (or,
  with `--split sentence`, a sentence) moved elsewhere is shown at both ends,
  tinted as a move, with the words edited on the way highlighted; how alike
  it must stay to count as moved is set with `--move-similarity` and
  `--move-algorithm` (see [Moved lines](#moved-lines-algorithm-and-threshold)). So is a
  passage moved within a paragraph or between two, a sentence taken out of
  one paragraph and put in another (`--no-move-passages` turns this off).
  Code diff tools show the same text as one deletion and one unrelated
  insertion.
- **It is made for prose**: long lines wrap, changes are highlighted word by
  word and down to the letter within a word, each change is described in
  plain English on hover (`changed "repeat" to "repeated"`), words are
  counted as well as lines, and Markdown can be shown formatted.
- **It works in a git repository or outside one**: two commits, the staged
  or uncommitted changes, two files or two folders, from the command line,
  from a window, or from `git difftool`. The git program itself is needed
  either way: it aligns the lines of every comparison (see
  [Usage](#usage)).
- **It can have an AI judge the revision as a whole** (optional): Claude
  Code, ChatGPT through Codex, a local Ollama model or any API model reads
  the changes and says whether the new version is better, what changed,
  what improved and what to fix, at the top of the report (see [AI
  assessment](#ai-assessment)).
- **The result is one self-contained HTML file**: no server, no network, no
  Word needed to read it. It can be attached to an e-mail, so a co-author
  sees what changed since they last read the paper, and printed or saved as
  PDF.

## Usage

```
prosediff --git REPO BASE [TARGET] [options]
prosediff --files OLD NEW [options]
prosediff --folders OLD NEW [options]
prosediff --setup-git [REPO | --global]
prosediff --to-markdown FILE
prosediff --list-models AI
prosediff --login-codex
```

(installed: `uv tool install prosediff`; from a checkout: `uv run prosediff ...`)

Requirements: Python 3.11 or later, and [git](https://git-scm.com/) for
every comparison, `--files` and `--folders` included: prosediff aligns the
lines of the two versions with `git diff --no-index` (see [How it
works](#how-it-works)). git must be on `PATH`, or named by the
`GIT_PYTHON_GIT_EXECUTABLE` environment variable (e.g.
`C:\Program Files\Git\cmd\git.exe`); without it prosediff does not start.

| Argument / option          | Meaning                                                       |
|----------------------------|---------------------------------------------------------------|
| `--git REPO BASE [TARGET]` | compare commits of a git repository. REPO: the repository, or any folder inside it; BASE: the older commit (hash, branch, tag, `HEAD~2`, ...); TARGET: the newer commit; without it, BASE is compared with the working tree (tracked files), as `git diff BASE` does |
| `--files OLD NEW`          | compare two files, whatever their names, outside git |
| `--folders OLD NEW`        | compare two folders, file by file, outside git |
| `--include PATTERNS`       | with `--folders`, compare only the files matching these glob patterns, separated by `\|` (quote them), e.g. `"*.docx\|*.md"`; a pattern is matched against each file's name, or its path within the folder when it has a `/`, ignoring case. Default `*.docx\|*.odt\|*.md\|*.typ\|*.txt`; `""` compares every file. The lock files an open document leaves beside it (Word's `~$name.docx`, LibreOffice's `.~lock.name.odt#`) are always left out. In the GUI, the "Folders: only" box |
| `--cached`                 | with `--git`, compare BASE with the index instead, as `git diff --cached BASE` does |
| `--untracked`              | with `--git` and the working tree, also show the untracked files `.gitignore` does not exclude |
| `-w`, `--ignore-whitespace`| compare lines ignoring whitespace, as `git diff -w`           |
| `-p`, `--path PATH`        | with `--git` or `--folders`, restrict the diff to this file or folder (repeatable) |
| `-o`, `--output FILE`      | output file. Default: with `--open`, a new HTML report in the temporary folder; otherwise, comparing two folders, `prosediff.html` in the new one (never compared itself when the folders are compared again); comparing two files, `OLD_vs_NEW.html` next to the new one; else `diff.html` (`.diff`, `.wdiff`, `.docx` or `.odt` with `--format diff`, `wdiff`, `docx` or `odt`). The GUI does the same |
| `--format html\|diff\|wdiff\|docx\|odt` | `html`: the HTML report (default); `diff`: a unified diff, with `-U` lines of context (default 3) or `--full`, its lines paired as the HTML report pairs them (an edited line's removal followed by its new text). A text file's diff is a patch `git apply` and `patch` can apply. For Markdown files and Word and OpenDocument documents, the lines are those the HTML report compares: one per paragraph (or sentence, with `--by-sentence`), blank lines left out, numbered as in the HTML report; a document's formatting is written in Markdown (`**bold**`), the comments added or removed in [CriticMarkup](https://github.com/CriticMarkup/CriticMarkup-toolkit) (`{>>Author (date): text<<}`; those both sides have are left out, as in the HTML report), and tracked changes kept with `--docx-changes show` as `{++inserted++}` and `{--deleted--}`: a diff to read, not to apply. `wdiff`: a word diff, as `git diff --word-diff` writes one, the same lines with the words changed within each marked `[-removed-]{+added+}` (paired as in the HTML report), a line removed or added whole marked whole. `docx`, `odt`: the new version as a Word document or an OpenDocument text, each change since the old one a tracked change to accept or reject in Word or LibreOffice (see [Tracked changes](#tracked-changes)); paragraph by paragraph only. Moved lines are marked only in the HTML report. Default: `diff` when the output file ends in `.diff` or `.patch`, `wdiff` for `.wdiff`, `docx` for `.docx`, `odt` for `.odt`. In the GUI, "Format" |
| `-U`, `--context N`        | unchanged lines shown around each change, in every file; unset, 0 in Markdown files and Word documents (whose lines are whole paragraphs) and 3 in the others. In the GUI, the "Context lines" box: `auto` or a number |
| `--full`                   | show every line of each changed file                          |
| `--max-hidden N`           | unchanged lines embedded per gap for the HTML report to reveal (default 500); longer gaps are left out, to keep the HTML report light |
| `--align left\|justify`    | alignment of wrapped lines (default left)                     |
| `--comments markers\|text\|none` | the comments of Markdown files and Word and OpenDocument documents, in every format. `markers` (default): set apart from the text, only those added or removed since the base shown, the comments both sides have left out; in the HTML report each is a 💬 marker (a green balloon holding a + when added; a red one holding a − when removed), with a card in the margin beside it (the author, the date and the comment); in the diffs it is written in CriticMarkup (`{>>Author (date): text<<}`). `text`: the comment markup compared as part of the text, as pandoc writes it. `none`: every comment left out, so a line whose only change was a comment is unchanged. In the GUI, "Comments" |
| `--empty-comments`         | also show the comments that have no text, left out by default (a card saying "(no text)") |
| `--docx-changes accept-all\|reject-all\|show` | the tracked changes of Word and OpenDocument documents: accept them all (`accept-all`, the default), reject them all (`reject-all`), or `show` them as Word shows them (insertions underlined, deletions struck through, who made each and when on hover) |
| `--md-filter COMMAND`      | shell command (cmd.exe on Windows, sh elsewhere) both versions of every Markdown file (not Word or OpenDocument files, which are not read as Markdown) are piped through, stdin to stdout, before comparing; line numbers are then those of the filtered text |
| `--split paragraph\|sentence\|both` | how the prose of Markdown files and Word and OpenDocument documents is compared: paragraph by paragraph; sentence by sentence, where a sentence moved between paragraphs is recognised and each sentence is labelled with its line and its place in it (`12.3`); or both, in one HTML report whose toolbar switches between the two (`s`), a diff holding one only. Default: both for the HTML report, paragraph by paragraph for a diff. In the GUI, "Compare by" |
| `--language CODE`          | the language of the prose: its rules split sentences with `--split sentence` (about forty languages are known; others fall back to a simple rule), and the HTML report hyphenates wrapped lines by it. A code, e.g. `en`, `it`, `de`, `fr`, `pt-br`; `document`, the languages Word and OpenDocument files mark their text with, in the runs' and the styles' settings: each paragraph is split and hyphenated by its own, and the file's language is the one most of its letters are marked with, for the paragraphs that mark none (an error for Markdown and text files); or `guess`, guessed from each file's text (py3langid). Default: `document` for Word and OpenDocument files, `guess` for the others and for a document that marks no language. A file whose language is unknown (too short or too mixed to guess) is split by English rules and not hyphenated |
| `--encoding NAME`          | the encoding of text and Markdown files, e.g. `utf-8`, `cp1252`, `latin-1` (Word and OpenDocument files carry their own). Default `auto`: UTF-8, unless a file cannot be read as UTF-8 or reads with control characters; then the encoding is guessed with [cchardet](https://pypi.org/project/cchardet/) (Mozilla's uchardet, reliable even on a few words), or, when its guess cannot read the file, with [charset-normalizer](https://pypi.org/project/charset-normalizer/); Windows-1252 is preferred when it reads the text alike, and the file header says which was used. In the GUI, the "Text encoding" box |
| `--move-similarity X`      | how alike, above 0 and at most 1, an edited paragraph (a line of other files) must be to where it reappears to count as moved, by `--move-algorithm` (default 0.7; 1: only lines moved unchanged). Since 0.5.0 it no longer applies to sentences, which have their own setting below |
| `--sentence-move-similarity X`, `--sentence-move-algorithm NAME` | the same for sentences, when prose is compared sentence by sentence (default 0.55, `token-sort`). In the GUI, "Moved paragraphs" and "Moved sentences", each a threshold and an algorithm |
| `--move-passages`, `--no-move-passages` | also follow the passages moved within a paragraph (a line) or between two (default: on): a run of words removed in one place and added in another, gaps of up to two unchanged words allowed, at least four words (and 15 non-space characters) long, as alike as `--move-similarity` (or `--sentence-move-similarity`) and its algorithm say, is shown as moved rather than as a deletion and an unrelated insertion; a passage is also looked for inside a longer one (a sentence moved out of a paragraph deleted or rewritten). In the GUI, "Moved passages" |
| `--passage-min-words N`, `--passage-min-chars N`, `--passage-max-gap N`, `--passage-shared-words N`, `--passage-content-letters N`, `--passage-edge-run N`, `--passage-partial-share X`, `--passage-rounds N`, `--passage-max-pairs N`, `--passage-rare-share X`, `--passage-rare-min N` | how moved passages are told from chance likeness (advanced; `prosediff --help` says what each does, and its default): the shortest passage (4 words, 15 characters), the unchanged words allowed inside one (2), the words of meaning two passages must share (2, of 4 letters or more), the words in common that can start or end one (2), when a passage is looked for inside a longer one (0.8), the rounds of matching (4), and past how many pairs only those sharing a rare word are tried (250,000; rare: in 1% of the passages, or 20). In the GUI, the fields of "Advanced settings" |
| `--move-algorithm NAME`    | how that likeness is measured: `token-sort` (default), the words and punctuation two lines have in common whatever their order; `token-set`, the words both share against the rest of each. See [Moved lines](#moved-lines-algorithm-and-threshold). In the GUI, the list next to "Moved-line similarity" |
| `--assess AI`              | have an AI assess the value of the changes as a whole, at the top of the HTML report, closed until opened (not in a `.diff` or `.wdiff`, which refuse it; see [AI assessment](#ai-assessment)): `claude`, `codex`, or `PROVIDER/MODEL` (e.g. `ollama/qwen3`, `openai/gpt-5`); `claude/MODEL` and `codex/MODEL` choose their model among those they report (`--list-models`), else the one the login uses. In the GUI, the "AI assessment" card |
| `--assess-effort LEVEL`    | how hard the model thinks: one of the levels it reports it supports (`--list-models`; e.g. `low`, `medium`, `high`, `xhigh`, `max`). Default: the model's own |
| `--assess-context document\|changes` | what the model reads: `document` (default), the changes and the whole new version, to check them against the rest of the document (citations, cross-references, terms); `changes`, the changes only. The old version is never sent apart: its unchanged paragraphs are in the new one, and what changed is in the changes. In the GUI, "Changes + new version" and "Changes only" |
| `--assess-instructions TEXT` | your own instructions, added to the prompt (e.g. `"the journal is Research Policy; Laura asked to shorten the introduction"`), or a text file holding them |
| `--assess-annotate`, `--no-assess-annotate` | have the AI mark each problem in the text (default: on): from its first words to its last, with what is wrong and the change it proposes; a numbered ⚠ badge before each in the HTML report, its passage highlighted when clicked, a card in the margin beside it, and stepped through from the top bar. In the GUI, "Mark individual changes" |
| `--assess-documents`, `--no-assess-documents` | with problems marked in a Word document or an OpenDocument text compared with another, put in the HTML report the tracked changes with the AI's comments and the new version with its fixes, to download (default: on; see [The AI's problems in Word and LibreOffice documents](#the-ais-problems-in-word-and-libreoffice-documents)). In the GUI, "Documents to download" |
| `--assess-save-prompt`     | also put the exact text sent to the model, its system prompt and its message, in the HTML report, in a closed panel at its end (off by default): to see what it read. In the GUI, "Save AI prompt" |
| `--assess-ai-writing`      | also ask the AI, apart, whether the text the changes added reads as written by an AI (off by default): a second assessment, its verdict (likely, possibly or unlikely) in the HTML report's top bar, opening its own drawer, with the signs for and against. An indication, not a proof: careful writers show the same signs, and writers in a second language are often taken for an AI wrongly. In the GUI, "Check for AI writing" |
| `--assess-timeout SECONDS` | give up on the assessment after this long (default 900); the report is written all the same, saying why there is none |
| `--list-models AI`         | list the models an AI reports it offers (`claude`, `codex`, `ollama`, or any provider any-llm reaches), its default first, and the efforts each supports |
| `--login-codex`            | log in to ChatGPT, in the browser, for `--assess codex` (once) |
| `--open`                   | open the HTML report in the browser once it is written |
| `--setup-git`              | set git up to show Word and OpenDocument files as text and to open prosediff HTML reports from `git difftool` (see below), for the repository REPO (default: the current folder) |
| `--global`                 | with `--setup-git`, for every repository of the user instead |
| `--to-markdown FILE`       | print a Word or OpenDocument file as Markdown (pandoc's: formatting, comments and tracked changes included), tracked changes as `--docx-changes` says: what `git diff` shows once set up |
| `--version`                | print the version                                             |

Examples:

- `prosediff --git . HEAD~1 HEAD -o review.html`: the last commit;
- `prosediff --git . HEAD --untracked`: everything not yet committed;
- `prosediff --git . HEAD --cached`: what the next commit would record;
- `prosediff --files draft_v1.docx draft_v2_returned.docx`: what a co-author
  changed and commented, from the two Word files;
- `prosediff --folders submitted/ revised/`: two folders, file by file (their
  Word, OpenDocument, Markdown, Typst and text files; `--include` picks
  others);
- `prosediff --files draft.odt draft_returned.odt --open`: two LibreOffice
  documents, the HTML report opened in the browser;
- `prosediff --files draft_v1.docx draft_v2.docx -o changes.diff`: the same
  comparison as a unified diff, to read in an editor or send as a patch;
- `prosediff --files draft_v1.docx draft_v2.docx -o changes.wdiff
  --comments none`: as a word diff, the comments left out;
- `prosediff --files draft_v1.docx draft_v2.docx -o redline.docx`: the
  new version with every change a tracked change, to accept or reject in
  Word (`-o redline.odt` for LibreOffice);
- `prosediff --files draft_v1.docx draft_v2_returned.docx --assess claude
  --open`: the report, headed by Claude Code's assessment of the revision;
- `prosediff --files draft_v1.docx draft_v2_returned.docx --assess
  ollama/qwen3`: the same by a local model, nothing leaving the computer.

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

## AI assessment

`--assess AI` (in the window, "AI assessment") has an AI read the changes
and judge them as a whole, as a co-author reading the returned draft would:

- **Verdict**: *Improves*, *Mixed* or *Worsens*, and why;
- **What changed**, grouped by section;
- **Improvements**;
- **Problems to fix**, most serious first, each quoting the words concerned:
  claims no longer supported, results misstated, broken sentences,
  placeholders, dangling citations, inconsistent terms or spelling, and the
  reviewers' comments left unanswered.

Its verdict sits in the HTML report's top bar, a coloured badge;
clicking it opens the whole assessment in a drawer over the right of the
page, the text scrolling under it (on paper, it heads the report). A
unified or word diff has no room for it, so the AI assessment goes with the
HTML report only (in the window its card is greyed out for the other
formats).

![The AI assessment of a prosediff report, opened in its drawer from the verdict in the top bar: a verdict badge, what changed, the improvements and the problems to fix](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_assessment.png)

With `--assess-annotate` (on by default; in the window, "Mark problems in
the text") the AI also marks each problem in the text: the first and last
words of the passage, copied from the text, what is wrong and the change it
proposes. The report finds each passage (whatever the hyphenation, the
spacing, straight or curly quotes, and the comments among its words),
puts a numbered ⚠ badge before it, and a card in the margin beside its
paragraph: "Problem 3", what is wrong and the change proposed. One it cannot
find has neither, and review mode lists it as not found. Clicking a
problem, its badge or its card, pins it: its passage highlighted and its
card ringed, until clicked again. The top bar's ◀ ⚠ ▶ (`a`, Shift+`a`) steps
through them.

![A paragraph of a prosediff report with the problems the AI marked: a numbered badge before each passage in the text, and in the margin beside it a card for each, what is wrong and the change proposed; the first pinned, its passage highlighted and its card ringed](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_marks.png)

### The AI's problems in Word and LibreOffice documents

When a Word document is compared with another (or an OpenDocument text with
another) and the AI marked problems, the assessment's drawer offers two
documents to download, made from the new version:

- **The changes, tracked, with the AI's comments**: the document of
  [tracked changes](#tracked-changes), each problem a comment of the AI's
  on its passage, what is wrong and the change it proposes;
- **The new version with the AI's fixes, tracked**: where the AI wrote out
  how the passage should read, its fix is a tracked change of its own, to
  accept or reject in Word or LibreOffice, what is wrong a comment on it;
  a problem it gave no such fix for (text to add elsewhere, a reviewer to
  answer, a passage over several paragraphs) is a comment, the change
  proposed in it. So is a fix whose words are not the document's own text,
  word for word: one in an equation, a field or a note reference, whose
  place could only be guessed. A fix changes whole words, and keeps the
  document's curly quotes and dashes where the AI wrote plain ones.

Each opens with a comment on the first paragraph giving the AI's verdict;
the comments and the changes are the AI's, under its name ("Claude Code
(claude-opus-5-5)"). Review mode (`r`) has a box beside each problem:
unticked, the problem is left out of both documents when they are saved, its
comment taken out and its fix rejected. A problem in text only the old
version has stays in the report. Word takes no comment in a footnote or an
endnote, so a problem in one has its comment on the note's number in the
text ("In the footnote: ..."); its fix is in the note itself. The documents are made when the report is,
paragraph by paragraph, and held in it as text: the report grows by about
2.7 times the document's size (two copies, a third larger as text);
`--no-assess-documents` (in the window, "Documents to download" off)
leaves them out.

The AI is one of three kinds, each an optional extra of prosediff, so the
plain install stays small:

| `--assess`       | What answers | Install | Sign-in |
|------------------|--------------|---------|---------|
| `claude`, `claude/MODEL` | Claude Code, through the [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/); MODEL one of those Claude Code reports (`opus`, `sonnet`, a full name such as `claude-opus-5-5`, ...) | `uv tool install "prosediff[claude]"` | your Claude login (the one Claude Code uses); billed to your Claude plan |
| `codex`, `codex/MODEL`   | ChatGPT, through OpenAI's [Codex SDK](https://pypi.org/project/openai-codex/) (the Codex program comes with it); MODEL one of those Codex reports (`gpt-5.5`, ...) | `uv tool install "prosediff[codex]"` | your ChatGPT login: `prosediff --login-codex` once; billed to your ChatGPT plan |
| `PROVIDER/MODEL` | any model of the fifty-odd [providers any-llm supports](https://mozilla-ai.github.io/any-llm/providers/): a local model of [Ollama](https://ollama.com/) (`ollama/qwen3`), LM Studio, llama.cpp or vLLM, or an API (`openai/gpt-5`, `anthropic/claude-sonnet-5`, `gemini/...`, `mistral/...`, `deepseek/...`, `groq/...`, `openrouter/...`, Azure, Bedrock, ...) | `uv tool install "prosediff[models]"` | none for a local model; an API's key in its environment variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, ...) |

Several extras combine: `"prosediff[claude,codex,models]"`. Asked for an AI
whose extra is not installed, prosediff says which to add.

The models, and the efforts each supports, are always those the AI itself
reports, never a list kept in prosediff: `prosediff --list-models claude`
(or `codex`, `ollama`, `openai`, ...) prints them, its default first, the
model's default effort starred; the window offers the same lists. How the
assessment is made is chosen with three more settings:

- **Effort** (`--assess-effort`): how hard the model thinks, among the
  levels it supports; more effort reads more closely, but takes longer and
  costs more.
- **What it reads** (`--assess-context`; in the window, **Changes + new
  version** or **Changes only**): by default the changes and the whole new
  version, so the model can check them against the rest of the document (a
  citation left unused, a claim contradicting another section, a placeholder
  in an untouched paragraph); or the changes only, less to read. The old
  version is not sent apart, and need not be: its unchanged paragraphs are
  in the new one, and the changes hold its old wording beside the new.
- **Your instructions** (`--assess-instructions`): the journal, what a
  co-author asked for, what to look at, in a sentence or a text file;
  they are added to the prompt, the assessment's format kept.

What the AI is sent: the word diff of the changed paragraphs, comments
included, the whole new version (unless `--assess-context changes`), and
the instructions; no files, and no tools to run. Word and OpenDocument
files are read directly, but text carries no styles: their formatting is
written in prosediff's notation (`##` for a Heading 2 paragraph, `**` for
bold), and the model is told so, and to report what changed in the word
processor's terms (a paragraph that lost its Heading 2 style), never the
notation itself. `--assess-save-prompt`
(in the window, "Save AI prompt") puts that text, exactly as
sent, at the end of the HTML report, in a panel closed until opened. With `claude`, `codex` or an API, that text goes
to Anthropic's or OpenAI's (or the API's) servers under your account; with
Ollama it stays on the computer. The HTML report itself still needs no
network to read. A long revision is cut at about 100,000 tokens, the model
told; a local model is given a context window to fit the diff.

In the window, "Preview before sending" (on by default) first writes the
report without the assessment and opens it, then asks whether to send the
changes to the AI: yes, and the report is written again with the
assessment; no, and it stays as it is.

The assessment is an AI's reading: check it against the text. Its worth is
the model's: a local model needs to be large enough to follow the
instructions over a long diff (a few billion parameters at least), and
fast only when it fits the graphics card's memory; a very small one (under
1 billion) answers, but does not assess.

## With git's own commands

`prosediff --setup-git` sets up the repository it is run in (or REPO;
`--global`: every repository of the user) so that git's own commands
understand documents:

- `git diff`, `git log -p` and `git show` show a Word or OpenDocument file
  as Markdown, instead of "Binary files
  differ": a textconv driver running `prosediff --to-markdown`, given the
  `*.docx` and `*.odt` files in `.git/info/attributes` (not committed; with
  `--global`, git's global attributes file);
- `git difftool -t prosediff` opens a prosediff HTML report for each changed file,
  and `git difftool -d -t prosediff` one HTML report for them all (on Windows, if
  git says it "could not symlink", add `--no-symlinks`). A repository set up
  by prosediff 0.3.1 or earlier needs `prosediff --setup-git` again for
  `git difftool -d`, which the `--files` it was given no longer accepts.

Both call the Python prosediff was installed with (`python -m prosediff`),
so they keep working whether or not its scripts are on `PATH`. Running it
again changes nothing; `git config --unset` and the attributes file undo it.

## The window

`prosediff-gui` opens a window to choose what to compare, drawn with
[ttkbootstrap](https://pypi.org/project/ttkbootstrap/) in its Bootstrap theme, light
or dark as the system is set (Windows' app mode, macOS's appearance; light
elsewhere), the title bar too on Windows.
`prosediff-gui REPOSITORY` opens it with a git repository filled in (or the
repository a folder belongs to); `prosediff-gui FILE.docx` (or `.odt`, `.md`) first asks, in a
file dialog, for the file to compare it with, the older of the two going on
the left; `prosediff-gui OLD NEW` opens it with two Markdown, Word or
OpenDocument files, or two folders. Any other arguments (a folder outside git, a `.txt` file,
three files) show an error box listing the arguments received, and the
program exits once it is dismissed.
The Files and Folders views have a button to swap the two.

To have it at hand, install it once:

```
uv tool install --editable C:\path\to\prosediff
```

This puts `prosediff` and `prosediff-gui` on `PATH` (editable: they always run
the project's current code; `uv tool uninstall prosediff` removes them). On
Windows, `prosediff-gui.exe` is a windowed program: double-clicked, pinned,
behind a shortcut or with files dropped on it, it opens no console.
`scripts/prosediff_gui.bat` (Windows) and `scripts/prosediff_gui.sh` (bash:
Cygwin, Git Bash, Linux, macOS) start the same window, the installed one when
there is one, else from the project; a batch file itself always shows a
console for a moment.

![The prosediff window: a git repository with base and target commits chosen from lists, the comparison options in one card, the output, and the AI assessment card with its AI, model and effort](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_window.png)

What is compared is chosen with the segmented button at the top, which
shows the fields of one of three sources:

- **Git repository**: pick a folder; base and target are chosen among the
  working tree, the index and the latest 200 commits (hash, date, author,
  subject), or typed as any ref (`HEAD~15`, a tag). The window starts from
  the uncommitted changes when there are any, otherwise from the last
  commit. Optionally, untracked files and a list of paths (separated by `;`).
- **Files**: two files, whatever their names, Word documents included.
- **Folders**: two folders, of which only the files matching the patterns
  of "Only" (`--include`) are compared.

The options that change what the comparison finds sit in one card,
**Comparison**, each explained by a tooltip (rest the pointer on it, or on
its ⓘ; in a drop-down list, on an item to learn what it means): Word and
OpenDocument tracked changes, the comments (markers, text or none), the
document language (`default`: the one Word and OpenDocument files mark, the
others guessed; `document`; `guess`, guessed from each file; or a code such
as `it`), which splits sentences and hyphenates lines, a switch to ignore
whitespace, and how prose is compared ("Compare by": paragraphs, sentences
or both, the default).

The **Advanced settings** button, in the bar at the bottom, opens the rest
in a window of its own, beside the main one (Close, or Escape, hides it
again; what it holds applies to the next comparison). Its **Report** card:
context lines or whole files, the alignment of wrapped lines, whether
passages moved within or between paragraphs are followed too ("Moved
passages"), and comments without text. Then the settings few need to change: how alike a paragraph and a sentence must stay to
count as moved and how that is measured (a threshold and an algorithm for
each, "Moved paragraphs" and "Moved sentences"); how moved passages are
told from chance likeness, the same as the `--passage-*` options, each
explained by its tooltip; and the encoding of text files ("Text
encoding"). Only the moved-passage values changed from the defaults are
saved, so the others follow prosediff's defaults; "Reset to defaults"
puts every option back.

Under **Output**, the format (HTML report, unified diff or word diff, the
extension of the file following it), where to save it (by default next to the new
file, as `OLD_vs_NEW.html`, or into the new folder, as `prosediff.html`,
following the files or folders as they change; comparing git versions, a new
file in the temporary folder; a file you choose stays).

Under **AI assessment** (see [AI assessment](#ai-assessment)), the **AI**
(none, `claude`, `codex`, `ollama`, or another provider any-llm reaches),
its **Model** and its **Effort**, each list as the AI reports it and its
own defaults chosen (any name can be typed; a model that names no default
effort shows `default`, its own); what it **Reads**, the changes
and the new version or the changes only; your **Instructions**, typed or
from a text file; whether the AI marks the problems in the text (on by
default); and whether the text sent to the AI is put at the end of the
HTML report. The switches are greyed out while no AI is chosen, and the
whole card unless the output is the HTML report.

Compare (or Ctrl+Enter) writes it and opens it.
The comparison runs in a process of its own, the status line saying the
stage it is at and for how long (comparing paragraph by paragraph, then
sentence by sentence, asking the AI, writing the report); meanwhile Compare
becomes **Cancel** (or Esc), which stops it and all it started, the AI
included. A notification says when it is done. The window remembers its choices only
when asked: **Save options** writes them (to `%APPDATA%\prosediff\gui.json`)
for it to open with next time, and **Reset to defaults** puts every option
back to its default (what is compared and where the output goes stay).

## The HTML report

- A compact header: the title (and the repository's path), then a line
  for each side (hash or file name, subject, author, date, and the file's
  or folder's path outside git) and, for commits, the commits in between
  (reachable from the target, or from HEAD for the index and the working
  tree, and not from the base; the 50 newest are listed).
- With `--assess`, the AI's assessment of the changes: its verdict as a
  badge, what changed, the improvements and the problems to fix (see [AI
  assessment](#ai-assessment)), in a drawer opened from its verdict in the
  top bar.
- A summary line: which view it is (paragraph or sentence, with a button to
  the other when the report holds both) and how many paragraphs (or
  sentences; lines, for files other than prose) were changed, inserted,
  deleted and moved.
- The margin, beside the two versions: a card for each comment and each
  problem the AI marked, next to the paragraph it is in, in the order they
  come in its text. A comment's card says whether it is new or removed, who
  wrote it and when, and what it says, with the paragraphs and the
  formatting it has in Word or LibreOffice (a long one shows its first
  lines, the rest on "Show all"); a problem's card, "Problem 3", what is
  wrong and the change proposed. The cards of a paragraph taller than it run
  on down the margin, pushing the next ones down, so the paragraphs keep the
  height of their text; a paragraph with a card is never folded away with
  the unchanged ones. In the text, a comment is a 💬 marker, a green balloon
  holding a + when it was added since the base, or a red one holding a −
  when it was removed;
  a problem, a numbered ⚠ badge. Clicking a card, or its mark in the text,
  pins it: the words it is anchored to stay highlighted, across paragraphs
  too (only its marker ringed when those words were deleted with a tracked
  change; its whole paragraph when a Markdown file does not say where its
  text ends), and its card ringed, until it is clicked again (or Esc). A
  pinned comment highlights its words only, not a mark among them (another
  comment's, an AI badge). The top bar steps through the comments (◀ ▶, `c`
  and Shift+`c`) and the problems (◀ ⚠ ▶, `a` and Shift+`a`), each counter
  saying which. On a narrow window, and in one column, the cards sit under
  their paragraph. **Margin** in the top bar (`g`) hides the margin, the two
  versions taking its width, and brings it back.
- The columns resize with the mouse: drag the line between the old and the
  new version to share their width otherwise, or the margin's edge to widen
  or narrow it; double-click either for the default. The browser remembers
  the widths.
- Review mode (`r`, or **Review** in the top bar), to go through a revision
  item by item: down the left, what there is to review, the AI assessment,
  the changes (each with its first words removed and added), the comments
  and the problems (one the AI marked but that the report could not find
  listed as such); beside it, only the paragraph the chosen one is in, with
  its cards, a line saying which it is ("Problem 4 of 17 · paragraph 33"),
  and **Previous** and **Next** (`k`, `j`) through them all. `r` again, or
  the button, ends it.
- With more than one file, the changed files with their counts of lines
  and words added and removed (and moved lines), linked to their tables;
  buttons expand or collapse every file at once. A single file needs
  neither: its own header gives its counts.
- Each file as a collapsible table, the old version beside the new (and
  the margin, when there are comments or problems), its header sticking
  under the top bar while it scrolls. Long lines wrap instead of scrolling sideways, so
  prose stays readable. Unchanged lines beyond the context are folded into a
  "show N unchanged lines" link that reveals them.
- Changed words highlighted within changed lines; a word changed into a
  similar one ("repeat" to "repeated") has only its changed letters
  highlighted.
- A removed line that reappears elsewhere in the file (at least 20 non-space
  characters) is shown as moved, in its own colour, with "moved to line N" /
  "moved from line N": as it was (spacing aside), or edited (at least 70%
  alike by default, 55% sentence by sentence, its words compared whatever
  their order), in which case its edits are highlighted too. Hovering its
  number tells where it went ("Moved to line 137.3") or where it came from,
  and whether it was edited on the way; with `l` (off by default), a line
  joins its two places (hovering either end lights up both).
- A passage moved within a paragraph or between two (a sentence moved into
  another paragraph, two sentences swapped, a clause moved inside its
  sentence: at least four words, as alike as a moved line must be) is drawn
  as a moved line is: tinted in the colour of moves at both ends, its edits
  on the way highlighted, counted as a moved passage, not as words removed
  and added. Hovering it tells where it went or came from, a line joins its
  two places, and on paper where it went is written after it. A paragraph
  whose words all moved elsewhere counts as neither removed nor added, and
  its gutter is that of a moved line (`→`, `←`). They are found always,
  but shown only when the toolbar's "Moved passages" switch (`v`, shown when
  there are any) is on; it is off by default, each passage then removed in
  one place and added in the other, as the plain word diff shows it, and the
  counts following.
- With `--split both`, the comparison paragraph by paragraph and the one
  sentence by sentence in one report, each with its counts, a toolbar button
  (`s`) switching between them.
- Changed images (PNG, JPEG, GIF, WebP, BMP, up to 5 MB) old and new side by
  side; other binary files are listed but not shown.
- A top bar, staying at the top as the page scrolls: what is compared; the
  AI's verdict, opening its assessment; the number of changes, with `n` and
  `p` (or its arrows) to jump to the next and previous change; the comments'
  and the problems' arrows; **Review**; the view switch (`s`, with both
  views); `l` for the lines between moves and `v` for the moved passages
  (both off by default), and `u` for one column; and a **View** menu for
  the switches few use, each named with its key (hovering any item of the
  bar shows its name, what it does and its key). `u` shows one column instead of two (each changed line shows its
  old version above its new one). In the View menu: `h` for the change
  highlights, on by default (off: the background colour of changed words
  and lines gone, the text plain, the gutters of line numbers still tinted
  and signed where it changed); `f` for
  the text formatted, on by default, or plain (formatted: Markdown's syntax
  hidden, emphasis, headings, links and citations styled, a document's bold,
  italic, underline and the like shown, prose in a proportional font); `m`
  for the formatting changes, on by default: text
  of a Word or OpenDocument file whose words are the same but whose
  formatting changed (made bold or italic, underlined, struck through, made
  superscript, subscript, a link or a heading) is marked in amber, what
  changed shown on hover, the unchanged lines holding such changes
  unfolded, and each file's header says in how many lines; a spacing
  stepper, − and + either side of the value
  (or `[` and `]`, or the arrow keys on the value, an ARIA spinbutton), for
  less or more space between the paragraphs of Markdown and
  Word documents. The browser remembers the views and the spacing.
- The prose of Markdown files and Word documents is hyphenated by the rules
  of its language (`--language`: by default the one a Word or OpenDocument
  file marks each paragraph with, otherwise guessed from the file's text):
  soft hyphens placed by [pyphen](https://pypi.org/project/Pyphen/), so every
  browser breaks words the same way, on screen and on paper, without
  dictionaries of its own; copying text leaves them behind.
- A flag shows the language: one in the file header when all of a file's
  paragraphs are in the same language, or one before each paragraph's
  number when a Word or OpenDocument file marks some paragraphs with another
  language. Its tooltip names the language and how it was found: marked in
  the document, guessed from the text, or given with `--language`. The
  flags are SVGs of [flag-icons](https://github.com/lipis/flag-icons) (MIT),
  embedded in the HTML report, so they show on Windows too, which has no flag emoji.
- Printing (or saving as PDF from the browser's Print dialog) opens every
  file, drops the toolbar and buttons, keeps the colours (the light ones,
  even from a browser in dark mode), lets a long paragraph continue on the
  next page (never leaving a lone line either side) rather than leave the
  rest of a page blank, and narrows the line-number gutters so the text
  columns fit a portrait page. The AI assessment heads the report, every
  card is printed whole beside its paragraph, and where a moved line went
  is written out. A file running over
  several pages repeats its column headings (its name, old and new) at the
  top of each, and folded unchanged lines print as a quiet "⋯ N unchanged
  lines".

Changes are also marked without colour, by a sign in the line-number gutter
(`−` removed, `+` added, `~` changed, `→` `←` moved), and every changed row
tells screen readers what it is. The HTML report follows the browser's light or dark
mode and needs no network: the CSS, the JavaScript and the images are inline.

## How it works

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
sentence). Moved
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
found, their precision, recall and bounds, and their cost in time. The HTML
report is rendered with Jinja2.

### Moved lines: algorithm and threshold

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
[docs/move_sensitivity.txt](docs/move_sensitivity.txt); `uv run python
docs/move_sensitivity.py` remakes them for the two measures offered, and
`--check` measures only the defaults, as a check after a change of the code.

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

## Development

```
uv run pytest                            # the tests
uv run playwright install chromium       # once, for the browser tests
uv run ruff check && uv run ruff format --check
uv run --with pillow python docs/make_screenshots.py --assess claude   # the README screenshots
uv run --with pillow python docs/make_icon.py          # the window icons, from docs/logo.svg
```

The tests build throwaway repositories with GitPython and need `git` on
`PATH`; the browser tests need Playwright's Chromium, and are skipped
without it. The AI assessment is tested with a stand-in for the models, and
once with a real local one, the smallest there is (`ollama pull
gemma3:270m`, 292 MB), skipped when Ollama or that model is missing; no
test calls Claude, Codex or a paid API. The Word tests write their documents with python-docx, or as raw
XML for what it cannot write (tracked changes, footnotes, equations), and
the OpenDocument tests as raw XML. The git tests keep git's global and
system settings out of the way. `.github/workflows/tests.yml` runs
the linter, and the tests on Windows, Linux and macOS with Python 3.11 and
3.14.

Two benchmarks guide the defaults: `docs/passage_benchmark.py` (moved
passages, report `docs/passage_benchmark.txt`) and
`docs/word_matcher_benchmark.py` (the word matchers, report and
recommendation in `docs/word_matcher_benchmark.md`). Their test set is kept
in `docs/benchmark_data/`: the Project Gutenberg books, as served, and the
simulated revisions made from them, so no run downloads or rebuilds them.

## Acknowledgements

prosediff was inspired by [diff2html](https://diff2html.xyz/)
([rtfpessoa/diff2html](https://github.com/rtfpessoa/diff2html)), whose
side-by-side HTML view of a diff it carries over from code to prose.

The HTML report reads and writes the zips of the Word and LibreOffice
documents it offers with [fflate](https://github.com/101arrowz/fflate)
(0.8.3, MIT licence), whose browser build ships with prosediff
(`src/prosediff/vendor/`, its licence beside it).

prosediff was written with the help of [Claude Code](https://claude.com/claude-code),
Anthropic's AI coding assistant.
