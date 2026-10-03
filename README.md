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

**See what changed between two drafts, and have an AI tell you whether the
revision is better: Word documents (.docx) first, OpenDocument (.odt) and
Markdown too.**

- **An AI reviews the revision**: Claude Code, ChatGPT through Codex, a
  local Ollama model (nothing leaves your computer) or any API model reads
  the changes and gives a verdict (*Improves*, *Mixed* or *Worsens*), what
  changed, what improved and what to fix.
- **It marks each problem in the text**: a numbered badge on the passage
  and a card in the margin saying what is wrong and the fix it proposes.
- **It hands you back the document**: the Word or LibreOffice file with the
  AI's comments, and its fixes as tracked changes to accept or reject one by
  one.
- **It reviews a single file too**, with no older version to compare: the
  report is then a diff of the file and the file with the AI's fixes
  applied. It can also check whether the text reads as AI-written.
- **It finds moved paragraphs even when they were edited**: a paragraph,
  sentence or passage moved elsewhere is shown at both ends as a move, the
  words changed on the way highlighted, where code diffs see an unrelated
  deletion and insertion ([how](https://github.com/raffaelemancuso/prosediff/blob/master/docs/moves.md)).
- **It is a git diff tool for Word files**: after `prosediff --setup-git`,
  `git diff`, `git log -p` and `git show` print .docx and .odt files as
  text instead of "Binary files differ", and `git difftool -t prosediff`
  opens the side-by-side report ([git integration](https://github.com/raffaelemancuso/prosediff/blob/master/docs/cli.md#with-gits-own-commands)).

**What the AI reads.** Comparing two versions, the AI is sent prosediff's
own word diff between them (`[-removed-]`, `{+added+}`), computed from the
two texts and not from the files' Track Changes, and, by default, the whole
new version. Reviewing one file, it is sent the file. Other files can go with it as
context, to draw on, not to assess (`--assess-file`, or the window's
**Other files** switch): a journal's guidelines, a reviewer's report, a
cited paper, as PDF, Word, OpenDocument, Markdown or text. Either way, a Word or
OpenDocument file is read with its tracked changes accepted, so the AI sees
the final text, not who changed what: `--docx-changes reject-all` reads the
text before them, and `--docx-changes show` keeps them in the text as
marked insertions and deletions, with author and date, for the AI to see
(in the window, **Tracked changes**). Comments marked resolved are never
sent unless `--no-skip-resolved`. The AI's answers are saved beside the
report (`NAME.ai.json`), and `--rebuild` (the window's **Rebuild** tab)
remakes the report from them without asking the AI again.

Underneath is a diff made for prose, not code: a self-contained HTML report
of two versions side by side, each paragraph facing the one it came from,
changed words highlighted inside it. The versions are two Word or
OpenDocument documents, two Markdown or text files, two folders, or commits
of a git repository.

![An HTML report made by prosediff: the AI's verdict in the top bar; below, two versions of a short text on free fall side by side, the second a revision with errors in it, changed words highlighted, and beside them a margin of cards for the comments and the problems the AI marked, one of them pinned, its passage highlighted in the text](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_page.png)

## Why prosediff

- **Code diff tools** (`git diff`, GitHub, diff2html, Meld) show a Word
  document as a binary file and a paragraph as one long changed line.
  **Word's Compare Documents** gives a third Word file full of revision
  marks, and cannot compare folders, git history or Markdown.
- **prosediff reads Word and OpenDocument files directly**, whether or not
  Track Changes was on, and shows only the comments added or removed.
  Comments marked resolved in Word or LibreOffice, and their replies, are
  skipped by default: hidden from the report and never sent to the AI,
  whether two versions are compared or one file is reviewed
  (`--no-skip-resolved`, or the window's **Skip resolved comments**
  switch, keeps them).
- **It lines the versions up correctly**: an edited paragraph faces the one
  it came from, however rewritten; an inserted or split paragraph does not
  shift the ones below.
- **It can put tracked changes back**: from a draft and a version returned
  without them, it writes the returned document with each change a tracked
  change, to accept or reject in Word or LibreOffice.
- **The result is one HTML file**: no server, no network, no Word needed to
  read it; attach it to an e-mail, or print it.

## Install

```
uv tool install prosediff
```

This installs `prosediff-gui` (the window) and `prosediff` (the command line).
It needs Python 3.11 or later and [git](https://git-scm.com/) on `PATH` for
every comparison, files and folders included. The AI assessment is an
optional extra: `"prosediff[claude]"`, `"prosediff[codex]"` or
`"prosediff[models]"` (Ollama and API models).

## Usage

Run `prosediff-gui`, or double-click it (on Windows it opens no console).
In the window:

1. Choose what to compare: two **Files**, two **Folders**, two versions in a
   **Git repository**, or **One file** for the AI to review alone.
2. Optionally, pick an **AI** (Claude Code, Codex, Ollama or an API model),
   its model and effort.
3. Click **Compare** (Ctrl+Enter). The report opens in a window of its own,
   where the choices made of the AI's problems are kept in the project.

![The prosediff window: a git repository with base and target commits chosen from lists, the comparison options in one card, the output, and the AI assessment card with its AI, model and effort](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_window.png)

`prosediff-gui OLD.docx NEW.docx` opens the window with two files filled
in. Every option has a tooltip; [The window](https://github.com/raffaelemancuso/prosediff/blob/master/docs/window.md) describes them
all. Everything the window does is also on the command line, for scripts
and git: see [Command line](https://github.com/raffaelemancuso/prosediff/blob/master/docs/cli.md).

## Documentation

- [The window](https://github.com/raffaelemancuso/prosediff/blob/master/docs/window.md): every field and option of `prosediff-gui`.
- [Command line](https://github.com/raffaelemancuso/prosediff/blob/master/docs/cli.md): every option of `prosediff`, examples, and git integration.
- [The HTML report](https://github.com/raffaelemancuso/prosediff/blob/master/docs/report.md): what it shows, and its keys.
- [AI assessment](https://github.com/raffaelemancuso/prosediff/blob/master/docs/ai_assessment.md): the AIs supported, what they are
  sent, the documents with the AI's comments and fixes.
- [How it works](https://github.com/raffaelemancuso/prosediff/blob/master/docs/how_it_works.md): tracked-changes output, alignment,
  reading Word and OpenDocument files, and the Python API.
- [Moved text](https://github.com/raffaelemancuso/prosediff/blob/master/docs/moves.md): how moves are found, and the benchmark behind
  the default algorithms and thresholds.

## Development

```
uv run pytest                            # the tests
uv run playwright install chromium       # once, for the browser tests
uv run ruff check && uv run ruff format --check
uv run python docs/make_screenshots.py --assess claude   # the README screenshots
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
