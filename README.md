# prosediff

**Side-by-side comparison of prose, not code: Word documents (.docx) first,
Markdown too.**

Diff tools are made for code, where a line is a statement, a change is a
line and nobody comments inside the file. prosediff is made for prose, where
a line is a whole paragraph, a change is a few words inside it, paragraphs
move, and co-authors leave comments in the margin: papers, reports, books,
the drafts co-authors send back. It is optimised for Word files and works on
Markdown and any text file too; for code, a code diff tool serves better.

It writes a self-contained HTML page showing the differences between two
versions side by side: the older version on the left, the newer on the right,
each paragraph facing the paragraph it came from, changed words highlighted
inside it. Text that moved is followed to its new place **even when it was
edited on the way**. The versions are two Word documents, two Markdown or
text files, two folders, or commits of a git repository, its index (staged
changes) or working tree. Paragraphs wrap and are numbered, changes are
described in plain English, and the new and removed comments of Word
documents are shown and listed.

![A page made by prosediff: two versions of the opening of Alice's Adventures in Wonderland side by side, changed words highlighted, a comment's author, text and date in a tooltip, the comments panel above](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_page.png)

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
  in the old version are left out, even where they moved. The same works on the
  Markdown that `pandoc` makes of a Word file, and on Word files in git.
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
  it must stay to count as moved is set with `--move-similarity`. Code diff
  tools show the same text as one deletion and one unrelated insertion.
- **It is made for prose**: long lines wrap, changes are highlighted word by
  word and down to the letter within a word, each change is described in
  plain English on hover (`changed "repeat" to "repeated"`), words are
  counted as well as lines, and Markdown can be shown formatted.
- **It works from git or without it**: two commits, the staged or
  uncommitted changes, two files or two folders, from the command line or
  from a window.
- **The result is one self-contained HTML file**: no server, no network, no
  Word needed to read it. It can be attached to an e-mail, so a co-author
  sees what changed since they last read the paper, and printed or saved as
  PDF.

## Usage

```
prosediff REPO BASE [TARGET] [options]
prosediff --files OLD NEW [options]
```

(from a checkout: `uv run prosediff ...`; installed: `uv tool install .`)

| Argument / option          | Meaning                                                       |
|----------------------------|---------------------------------------------------------------|
| `REPO`                     | the repository, or any folder inside it                       |
| `BASE`                     | the older commit: hash, branch, tag, `HEAD~2`, ...            |
| `TARGET`                   | the newer commit; without it, BASE is compared with the working tree (tracked files), as `git diff BASE` does |
| `--files OLD NEW`          | compare two files (whatever their names) or two folders, outside git |
| `--cached`, `--staged`     | compare BASE with the index instead, as `git diff --cached BASE` does |
| `--untracked`              | with the working tree, also show the untracked files `.gitignore` does not exclude |
| `-w`, `--ignore-whitespace`| compare lines ignoring whitespace, as `git diff -w`           |
| `-p`, `--path PATH`        | restrict the diff to this file or folder (repeatable)         |
| `-o`, `--output FILE`      | output file (default `diff.html`)                             |
| `-U`, `--context N`        | unchanged lines shown around each change, in every file; unset, 0 in Markdown files and Word documents (whose lines are whole paragraphs) and 3 in the others. In the GUI, the "Context lines" box: `auto` or a number |
| `--full`                   | show every line of each changed file                          |
| `--max-hidden N`           | unchanged lines embedded per gap for the page to reveal (default 500); longer gaps are left out, to keep the page light |
| `--align left\|justify`    | alignment of wrapped lines (default left)                     |
| `--no-fold-comments`       | compare the comment markup of Markdown and Word documents as text; by default each comment added or removed since the base is shown as a 💬 marker (🆕 when added), with the author, the comment and its date on hover, and listed in a panel, while the comments both sides have are left out |
| `--empty-comments`         | also show the comments that have no text, left out by default (listed as "(no text)" in the panel) |
| `--docx-changes accept\|reject\|all` | the tracked changes of Word documents: accept them (default), reject them, or show them as markup |
| `--md-filter COMMAND`      | shell command (cmd.exe on Windows, sh elsewhere) both versions of every Markdown file are piped through, stdin to stdout, before comparing; line numbers are then those of the filtered text |
| `--by-sentence`            | compare the prose of Markdown files and Word documents sentence by sentence instead of paragraph by paragraph: a sentence moved between paragraphs is recognised, and each sentence is labelled with its line and its place in it (`12.3`) |
| `--sentence-language CODE` | the language whose rules split sentences (default `en`; about forty are known, e.g. `it`, `de`, `fr`; others fall back to a simple rule) |
| `--move-similarity X`      | how alike, above 0 and at most 1, an edited line must be to where it reappears to count as moved (default 0.8; 1: only lines moved unchanged) |
| `--version`                | print the version                                             |

Examples:

- `prosediff . HEAD~1 HEAD -o review.html`: the last commit;
- `prosediff . HEAD --untracked`: everything not yet committed;
- `prosediff . HEAD --cached`: what the next commit would record;
- `prosediff --files draft_v1.docx draft_v2_returned.docx`: what a co-author
  changed and commented, from the two Word files;
- `prosediff --files submitted/ revised/`: two folders, file by file.

## The window

`prosediff-gui` opens a window to choose what to compare.
`prosediff-gui REPOSITORY` opens it with a git repository filled in (or the
repository a folder belongs to); `prosediff-gui FILE.docx` first asks, in a
file dialog, for the file to compare it with, the older of the two going on
the left; `prosediff-gui OLD NEW` opens it with two Markdown or Word files.
The Files tab has a button to swap the two.

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

![The prosediff window: a git repository with base and target commits chosen from lists, and the options](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_window.png)

- **Git repository**: pick a folder; base and target are chosen among the
  working tree, the index and the latest 200 commits (hash, date, author,
  subject), or typed as any ref (`HEAD~15`, a tag). The window starts from
  the uncommitted changes when there are any, otherwise from the last
  commit. Optionally, untracked files and a list of paths (separated by `;`).
- **Files or folders**: two files (whatever their names, Word documents
  included) or two folders.
- **Options**: besides those below, comparing sentence by sentence (with the
  language of the text) and the similarity at which an edited line counts as
  moved.

Below, the options that matter when reading a diff (comment markers, Word
tracked changes, alignment, context lines or whole files, whitespace) and
where to save the page (by default a new page in the temporary folder).
Compare (or Ctrl+Enter) writes the page and opens it in the browser; the
comparison runs in the background, and the window remembers the choices for
the next time (`%APPDATA%\prosediff\gui.json`).

## The page

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
  highlighted. Hovering a change shows it in plain English (`changed
  "repeat" to "repeated"`, `added "Furthermore,"`), hovering a changed line
  lists all of its changes.
- A removed line that reappears elsewhere in the file (at least 20 non-space
  characters) is shown as moved, in its own colour, with "moved to line N" /
  "moved from line N": as it was (spacing aside), or lightly edited (at
  least 80% similar), in which case its edits are highlighted too.
- Changed images (PNG, JPEG, GIF, WebP, BMP, up to 5 MB) old and new side by
  side; other binary files are listed but not shown.
- A toolbar: the number of changes, with `n` and `p` (or its arrows) to jump
  to the next and previous change; four icon buttons (hovering any toolbar
  item shows its name, what it does and its key): `u` for one column instead
  of two (each changed line shows its old version above its new one); `f` for
  Markdown formatted, on by default, or raw (formatted: the syntax hidden,
  emphasis, headings, links and citations styled, prose in a proportional
  font); `c` for colour-blind
  colours (orange and blue instead of red and green); `t` to tint the whole
  of an edited line, as most diff tools do (by default only its changed
  words are coloured, and its gutter; the tint stops short of the space
  between paragraphs); a spacing stepper, − and + either side of the value
  (or `[` and `]`, or the arrow keys on the value, an ARIA spinbutton), for
  less or more space between the paragraphs of Markdown and
  Word documents; and two checkboxes for the comment tooltips (on by default)
  and the change tooltips (off by default; with both on, a comment inside a
  changed word shows the comment, and with only the change ones on, the change).
  The browser remembers the views, the spacing and the checkboxes.
- Printing (or saving as PDF from the browser) opens every file, drops the
  toolbar and buttons, keeps the colours and does not split a line across
  pages.

Changes are also marked without colour, by a sign in the line-number gutter
(`−` removed, `+` added, `~` changed, `→` `←` moved), and every changed row
tells screen readers what it is. The page follows the browser's light or dark
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
first, then the most similar pairs. The page is rendered with Jinja2.

A Word document is read into Markdown with python-docx, which opens the
document and resolves its styles, formatting, links and comments; its
paragraphs are then walked element by element, so everything lands where it
sits in the text: headings, list items, tables, footnotes and endnotes,
links, bold and italic, equations as linear text (`DV_(it) = β ⋅ (a)/(b)`),
and each comment, with its author and date, where it starts. Tracked changes
are settled during the walk (accepting keeps the inserted runs and drops the
deleted ones, rejecting the reverse, "all" keeps both as marked spans), so
the spaces at the edges of a change stay where Word had them, comments
anchored in deleted text are kept, and footnotes referenced only from
deleted text are dropped with it. Headers, footers and page layout are not
part of the comparison. Nothing outside Python is needed: no Word, no
pandoc. On a 12,000-word manuscript with 26 comments and tracked changes by
two co-authors, the accepted and rejected texts match pandoc's word for word.
Since nobody sees that Markdown, the rows of a Word document are numbered by
paragraph (1, 2, 3, and 3.1, 3.2 for the sentences of paragraph 3 with
`--by-sentence`) rather than by line of it; Markdown files keep their line
numbers.

Folding comments replaces each comment span, before any filter runs, with
one character of the Unicode private use area standing for its author and
text: a comment is then compared like a word, the same comment matches on
both sides even when its `id` was renumbered by a new conversion, and a
filter cannot cut it in two. The characters become markers when the page is
built. The comments present on both sides are then taken out of the text,
with the spaces around them (one is left where a comment stood between two
words), before the lines are lined up: they are never shown, and a
paragraph that only gained a comment moved over from its neighbour is not a
change. A paragraph with a new or removed comment is shown.

The formatted view recognises the common inline Markdown (headings, block
quotes, code, strong, emphasis, links, images, citations, pandoc spans with
attributes) with regular expressions, since no Markdown parser reports where
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
```

The tests build throwaway repositories with GitPython and need `git` on
`PATH`; the browser tests need Playwright's Chromium, and are skipped
without it. The Word tests write their documents with python-docx, or as raw
XML for what it cannot write (tracked changes, footnotes, equations). `.github/workflows/tests.yml` runs
the linter, and the tests on Windows, Linux and macOS with Python 3.11 and
3.14.
