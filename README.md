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

![An HTML report made by prosediff: two versions of the opening of Alice's Adventures in Wonderland side by side, changed words highlighted, a comment's author, text and date in a tooltip, the comments panel above](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_page.png)

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
  where it sits, with its author and date, new comments marked 🆕, and all
  listed in a panel. Only new and removed comments are shown: those already
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
  with `--by-sentence`, a sentence) moved elsewhere is shown at both ends,
  tinted as a move, with the words edited on the way highlighted; how alike
  it must stay to count as moved is set with `--move-similarity` and
  `--move-algorithm` (see [Moved lines](#moved-lines-algorithm-and-threshold)). Code diff
  tools show the same text as one deletion and one unrelated insertion.
- **It is made for prose**: long lines wrap, changes are highlighted word by
  word and down to the letter within a word, each change is described in
  plain English on hover (`changed "repeat" to "repeated"`), words are
  counted as well as lines, and Markdown can be shown formatted.
- **It works from git or without it**: two commits, the staged or
  uncommitted changes, two files or two folders, from the command line,
  from a window, or from `git difftool`.
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
```

(installed: `uv tool install prosediff`; from a checkout: `uv run prosediff ...`)

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
| `-o`, `--output FILE`      | output file. Default: with `--open`, a new HTML report in the temporary folder; otherwise, comparing two folders, `prosediff.html` in the new one (never compared itself when the folders are compared again), else `diff.html` (`.diff` or `.wdiff` with `--format diff` or `wdiff`). The GUI puts the HTML report comparing two folders into the new one too |
| `--format html\|diff\|wdiff` | `html`: the HTML report (default); `diff`: a unified diff, with `-U` lines of context (default 3) or `--full`, its lines paired as the HTML report pairs them (an edited line's removal followed by its new text). A text file's diff is a patch `git apply` and `patch` can apply. For Markdown files and Word and OpenDocument documents, the lines are those the HTML report compares: one per paragraph (or sentence, with `--by-sentence`), blank lines left out, numbered as in the HTML report; a document's formatting is written in Markdown (`**bold**`), the comments added or removed in [CriticMarkup](https://github.com/CriticMarkup/CriticMarkup-toolkit) (`{>>Author (date): text<<}`; those both sides have are left out, as in the HTML report), and tracked changes kept with `--docx-changes all` as `{++inserted++}` and `{--deleted--}`: a diff to read, not to apply. `wdiff`: a word diff, as `git diff --word-diff` writes one, the same lines with the words changed within each marked `[-removed-]{+added+}` (paired as in the HTML report), a line removed or added whole marked whole. Moved lines are marked only in the HTML report. Default: `diff` when the output file ends in `.diff` or `.patch`, `wdiff` for `.wdiff`. In the GUI, "Format" |
| `-U`, `--context N`        | unchanged lines shown around each change, in every file; unset, 0 in Markdown files and Word documents (whose lines are whole paragraphs) and 3 in the others. In the GUI, the "Context lines" box: `auto` or a number |
| `--full`                   | show every line of each changed file                          |
| `--max-hidden N`           | unchanged lines embedded per gap for the HTML report to reveal (default 500); longer gaps are left out, to keep the HTML report light |
| `--align left\|justify`    | alignment of wrapped lines (default left)                     |
| `--comments markers\|text\|none` | the comments of Markdown files and Word and OpenDocument documents, in every format. `markers` (default): set apart from the text, only those added or removed since the base shown, the comments both sides have left out; in the HTML report each is a 💬 marker (🆕 when added), with the author, the comment and its date on hover, and listed in a panel; in the diffs it is written in CriticMarkup (`{>>Author (date): text<<}`). `text`: the comment markup compared as part of the text, as pandoc writes it. `none`: every comment left out, so a line whose only change was a comment is unchanged. `--no-fold-comments` is the older spelling of `text`. In the GUI, "Comments" |
| `--empty-comments`         | also show the comments that have no text, left out by default (listed as "(no text)" in the panel) |
| `--docx-changes accept\|reject\|all` | the tracked changes of Word and OpenDocument documents: accept them (default), reject them, or keep them all, shown as Word shows them (insertions underlined, deletions struck through, who made each and when on hover) |
| `--md-filter COMMAND`      | shell command (cmd.exe on Windows, sh elsewhere) both versions of every Markdown file (not Word or OpenDocument files, which are not read as Markdown) are piped through, stdin to stdout, before comparing; line numbers are then those of the filtered text |
| `--by-sentence`            | compare the prose of Markdown files and Word documents sentence by sentence instead of paragraph by paragraph: a sentence moved between paragraphs is recognised, and each sentence is labelled with its line and its place in it (`12.3`) |
| `--language CODE`          | the language of the prose: its rules split sentences with `--by-sentence` (about forty languages are known; others fall back to a simple rule), and the HTML report hyphenates wrapped lines by it. A code, e.g. `en`, `it`, `de`, `fr`, `pt-br`; `document`, the languages Word and OpenDocument files mark their text with, in the runs' and the styles' settings: each paragraph is split and hyphenated by its own, and the file's language is the one most of its letters are marked with, for the paragraphs that mark none (an error for Markdown and text files); or `guess`, guessed from each file's text (py3langid). Default: `document` for Word and OpenDocument files, `guess` for the others and for a document that marks no language. A file whose language is unknown (too short or too mixed to guess) is split by English rules and not hyphenated |
| `--encoding NAME`          | the encoding of text and Markdown files, e.g. `utf-8`, `cp1252`, `latin-1` (Word and OpenDocument files carry their own). Default `auto`: UTF-8, unless a file cannot be read as UTF-8 or reads with control characters; then the encoding is guessed with [cchardet](https://pypi.org/project/cchardet/) (Mozilla's uchardet, reliable even on a few words), or, when its guess cannot read the file, with [charset-normalizer](https://pypi.org/project/charset-normalizer/); Windows-1252 is preferred when it reads the text alike, and the file header says which was used. In the GUI, the "Text encoding" box |
| `--move-similarity X`      | how alike, above 0 and at most 1, an edited line must be to where it reappears to count as moved, by `--move-algorithm` (default 0.7; 1: only lines moved unchanged) |
| `--move-algorithm NAME`    | how that likeness is measured: `token-sort` (default), the words and punctuation two lines have in common whatever their order; `tokens`, in order; `chars`, their characters in common, in order; `levenshtein`, 1 − the words inserted, deleted or replaced over the longer line's; `token-set`, the words both share against the rest of each. See [Moved lines](#moved-lines-algorithm-and-threshold). In the GUI, the list next to "Moved-line similarity" |
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
  --comments none`: as a word diff, the comments left out.

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

![The prosediff window: a git repository with base and target commits chosen from lists, the options in two cards, and the output](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_window.png)

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

The options sit in two cards, each explained by a tooltip (rest the pointer
on it, or on its ⓘ):

- **What is compared**: Word and OpenDocument tracked changes, how alike a
  line must stay to count as moved and how that is measured, the document
  language (`default`: the one Word and OpenDocument files mark, the others
  guessed; `document`; `guess`, guessed from each file; or a code such as
  `it`), which splits sentences and hyphenates lines, the encoding of text
  files, and switches to compare sentence by sentence and to ignore
  whitespace.
- **How it is shown**: the comments (markers, text or none), comments
  without text, context lines or whole files, and the alignment of wrapped
  lines.

Under **Output**, the format (HTML report, unified diff or word diff, the
extension of the file following it) and where to save it (by default,
comparing two folders, `prosediff.html` in the new one; otherwise a new file
in the temporary folder). Compare (or Ctrl+Enter) writes it and opens it;
the comparison runs in the background, a progress bar running meanwhile, a
notification says when it is done. The window remembers its choices only
when asked: **Save options** writes them (to `%APPDATA%\prosediff\gui.json`)
for it to open with next time, and **Reset to defaults** puts every option
back to its default (what is compared and where the output goes stay).

## The HTML report

- The two sides (hash or file name, subject, author, date) and, for
  commits, the commits in between (reachable from the target, or from HEAD
  for the index and the working tree, and not from the base; the 50 newest
  are listed).
- A comments panel: every comment with its author and date, marked new,
  removed or unchanged, each linked to the line it sits in (unchanged
  comments are in a collapsed list). In the text, a comment is a 💬 marker,
  or 🆕 when it was added since the base; hovering or focusing it shows the
  author in bold, the comment below and its date in grey.
- The changed files with their counts of lines and words added and removed
  (and moved lines), linked to their tables; buttons expand or collapse
  every file at once.
- Each file as a collapsible four-column table, its header sticking to the
  top while it scrolls. Long lines wrap instead of scrolling sideways, so
  prose stays readable. Unchanged lines beyond the context are folded into a
  "show N unchanged lines" link that reveals them.
- Changed words highlighted within changed lines; a word changed into a
  similar one ("repeat" to "repeated") has only its changed letters
  highlighted.
- A removed line that reappears elsewhere in the file (at least 20 non-space
  characters) is shown as moved, in its own colour, with "moved to line N" /
  "moved from line N": as it was (spacing aside), or edited (at least 70%
  alike by default, its words compared whatever their order), in which case
  its edits are highlighted too.
- Changed images (PNG, JPEG, GIF, WebP, BMP, up to 5 MB) old and new side by
  side; other binary files are listed but not shown.
- A toolbar: the number of changes, with `n` and `p` (or its arrows) to jump
  to the next and previous change; five icon buttons (hovering any toolbar
  item shows its name, what it does and its key): `u` for one column instead
  of two (each changed line shows its old version above its new one); `f` for
  the text formatted, on by default, or plain (formatted: Markdown's syntax
  hidden, emphasis, headings, links and citations styled, a document's bold,
  italic, underline and the like shown, prose in a proportional font); `i` for comments inline, each written out after its marker (its
  author and text), as on paper; `t` to tint the whole
  of an edited line, as most diff tools do (by default only its changed
  words are coloured, and its gutter; the tint stops short of the space
  between paragraphs); `m` for the formatting changes, on by default: text
  of a Word or OpenDocument file whose words are the same but whose
  formatting changed (made bold or italic, underlined, struck through, made
  superscript, subscript, a link or a heading) is marked in amber, what
  changed shown on hover, the unchanged lines holding such changes
  unfolded, and each file's header says in how many lines; a spacing
  stepper, − and + either side of the value
  (or `[` and `]`, or the arrow keys on the value, an ARIA spinbutton), for
  less or more space between the paragraphs of Markdown and
  Word documents; and a checkbox for the comment tooltips (on by default).
  The browser remembers the views, the spacing and the checkbox.
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
  columns fit a portrait page. What the screen shows on hover is written
  out: each comment's author and text after its marker, and where a moved
  line went. A file running over
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
lines are compared again word by word, and a word replaced by a single word
is compared letter by letter when at least half its letters survive. Moved
lines are found among the lines left removed and added, identical ones
first, then the most similar pairs. The HTML report is rendered with Jinja2.

### Moved lines: algorithm and threshold

Which removed and added lines count as one line moved depends on how their
likeness is measured and on the threshold it must reach. Five measures are
offered, all computed by rapidfuzz on the words and punctuation of the two
lines (spacing aside): `tokens` (in common, in order: twice their longest
common subsequence over their total length), `chars` (the same on
characters), `levenshtein` (1 − words inserted, deleted or replaced over the
longer line's), `token-sort` (in common whatever their order: `tokens` on
the words sorted) and `token-set` (rapidfuzz's `token_set_ratio`: the words
both share against the rest of each).

They were compared on simulated revisions of five public-domain books from
Project Gutenberg (Austen, Darwin, Mill, Manzoni, Goethe: English, Italian
and German; a novel, science, an essay, drama), where where every paragraph
went is known: 1,000 stretches of 40 paragraphs, in each 4 paragraphs moved
and edited one way (words replaced, deleted or inserted at 0% to 50%, or
sentences reordered, with or without 10% of words edited), and 4 deleted
while 4 others were inserted, half of them the deleted paragraph's closest
look-alike from elsewhere in the book, which a threshold must turn down. A
move found is right when it pairs a paragraph with its own new version;
recall counts the moves a reader would still call moves (up to 30% of words
edited, or reordered). The time is that of scoring 250,000 pairs, the most
prosediff scores in one file (500 removed × 500 added paragraphs). The best
threshold of each measure:

| algorithm    | threshold | precision | recall | F1    | time, 250,000 pairs |
|--------------|----------:|----------:|-------:|------:|--------------------:|
| `token-sort` |      0.70 |     99.0% |  99.0% | 99.0% |              0.68 s |
| `token-set`  |      0.80 |     98.7% |  99.5% | 99.1% |              5.24 s |
| `chars`      |      0.50 |     94.4% |  98.7% | 96.5% |              1.23 s |
| `tokens`     |      0.40 |     93.4% |  98.2% | 95.7% |              0.97 s |
| `levenshtein`|      0.30 |     92.7% |  87.6% | 90.1% |              0.70 s |

`token-sort` and `token-set` are the only ones that follow a paragraph
whose sentences were reordered, and they keep false moves rare where the
order-bound measures need a low threshold to reach the same recall (and
then pair unrelated paragraphs). The two are tied on F1, and `token-sort` is
eight times faster, hence the default: `token-sort` at 0.70. The former
default, `tokens` at 0.80, had the same precision (99.1%) but found only
73.7% of the moves: 66% of those with 30% of their words edited and 29% of
the reordered ones, against 96% and 100% now. Lower `--move-similarity` to
follow heavier rewrites (at 0.60, `token-sort` finds 93% of paragraphs with
half their words changed, 2.1% of its moves then wrong), raise it to be
stricter.
The full tables, by threshold and kind of edit, are in
[docs/move_sensitivity.txt](docs/move_sensitivity.txt); `uv run python
docs/move_sensitivity.py` remakes them.

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
the sentences of paragraph 3 with `--by-sentence`); Markdown files keep
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
from prosediff import compare, compare_paths, render

html = render(compare("path/to/repo", "HEAD~1", "HEAD", context=3))
html = render(compare_paths("v1.docx", "v2.docx", fold_comments_md=True))
```

## Development

```
uv run pytest                            # the tests
uv run playwright install chromium       # once, for the browser tests
uv run ruff check && uv run ruff format --check
uv run --with pillow python docs/make_screenshots.py   # the README screenshots
uv run --with pillow python docs/make_icon.py          # the window icons, from docs/logo.svg
```

The tests build throwaway repositories with GitPython and need `git` on
`PATH`; the browser tests need Playwright's Chromium, and are skipped
without it. The Word tests write their documents with python-docx, or as raw
XML for what it cannot write (tracked changes, footnotes, equations), and
the OpenDocument tests as raw XML. The git tests keep git's global and
system settings out of the way. `.github/workflows/tests.yml` runs
the linter, and the tests on Windows, Linux and macOS with Python 3.11 and
3.14.
