# prosediff on the command line

[Back to the README](../README.md).

```
prosediff --git REPO BASE [TARGET] [options]
prosediff --files OLD NEW [options]
prosediff --folders OLD NEW [options]
prosediff --review FILE --assess AI [options]
prosediff --setup-git [REPO | --global]
prosediff --to-markdown FILE
prosediff --list-models AI
prosediff --login-codex
```

(installed: `uv tool install prosediff`; from a checkout: `uv run prosediff ...`)

Requirements: Python 3.11 or later and [git](https://git-scm.com/), even for
`--files` and `--folders` ([why](how_it_works.md)). git must be on `PATH`
or named by `GIT_PYTHON_GIT_EXECUTABLE`.

## Options

| Argument / option          | Meaning                                                       |
|----------------------------|---------------------------------------------------------------|
| `--git REPO BASE [TARGET]` | compare commits of a git repository. REPO: the repository or any folder inside it; BASE: the older commit (hash, branch, tag, `HEAD~2`, ...); TARGET: the newer commit. Without TARGET, BASE is compared with the working tree (tracked files), as `git diff BASE` does |
| `--files OLD NEW`          | compare two files, outside git |
| `--folders OLD NEW`        | compare two folders, file by file, outside git |
| `--review FILE`            | review one file whole, without comparing, by the AI `--assess` names (required). Writes `NAME_review.html` next to it by default (see [Reviewing one file](ai_assessment.md#reviewing-one-file)) |
| `--include PATTERNS`       | with `--folders`, compare only files matching these glob patterns, quoted and separated by `\|`. A pattern matches the name, or the relative path if it has a `/`, ignoring case. Default `"*.docx\|*.odt\|*.md\|*.typ\|*.txt"`; `""` compares every file. Office lock files are always skipped |
| `--common-only`            | with `--folders`, compare only the files found at the same path in both folders: those only in one (added, deleted or renamed) are left out |
| `--cached`                 | with `--git`, compare BASE with the index, as `git diff --cached BASE` does |
| `--untracked`              | with `--git` and the working tree, also show untracked files that `.gitignore` does not exclude |
| `-w`, `--ignore-whitespace`| ignore whitespace, as `git diff -w` does |
| `-p`, `--path PATH`        | with `--git` or `--folders`, restrict the diff to this file or folder (repeatable) |
| `-o`, `--output FILE`      | output file. Default: with `--open`, a temporary file; for two folders, `prosediff.html` in the new one; for two files, `OLD_vs_NEW.html` next to the new one; otherwise `diff.html` (or the extension of `--format`) |
| `--format html\|diff\|wdiff\|docx\|odt` | `html` (default): the HTML report. `diff`: a unified diff; a patch for text files, but for documents one line per paragraph (or sentence), to read rather than apply. `wdiff`: a word diff, as `git diff --word-diff`. `docx`, `odt`: the new version with each change tracked, by paragraph only (see [Tracked changes](how_it_works.md#tracked-changes)). Moves are marked only in the HTML report. Default: from the output extension (`.diff`/`.patch`, `.wdiff`, `.docx`, `.odt`) |
| `-U`, `--context N`        | unchanged lines shown around each change, in the HTML report and `--format diff`. Default: 0 in Markdown and Word files (whose lines are whole paragraphs), 3 in the others |
| `--full`                   | show every line of each changed file, in the HTML report and `--format diff` |
| `--max-hidden N`           | unchanged lines per gap that the HTML report embeds to reveal on demand (default 500); longer gaps are left out |
| `--align left\|justify`    | alignment of wrapped lines (default left) |
| `--comments markers\|text\|none` | comments in documents. `markers` (default): only those added or removed, shown apart from the text. `text`: compared as part of the text. `none`: left out |
| `--empty-comments`         | also show comments with no text (left out by default) |
| `--skip-resolved`, `--no-skip-resolved` | leave out the comments of Word and OpenDocument files marked resolved, and their replies: not shown, not sent to the AI (default: on) |
| `--docx-changes accept-all\|reject-all\|show` | tracked changes in Word and OpenDocument files: accept them all (default), reject them all, or show them as Word does |
| `--md-filter COMMAND`      | shell command (cmd.exe on Windows, sh elsewhere) that both versions of each Markdown file are piped through before comparing; not applied to Word or OpenDocument files |
| `--split paragraph\|sentence\|both` | compare the prose of Markdown, Word and OpenDocument files by paragraph, by sentence, or both (switched with `s` in the report). Default: `both` for HTML, `paragraph` for a diff |
| `--language CODE`          | language for sentence splitting and hyphenation: a code (`en`, `it`, `pt-br`, ...); `document`, as Word and OpenDocument files mark their text (an error for other files); or `guess`. Default: `document` for documents, `guess` otherwise. An unknown language is split by English rules and not hyphenated |
| `--encoding NAME`          | encoding of text and Markdown files, e.g. `cp1252`. Default `auto`: UTF-8, else guessed; the report says which |
| `--move-similarity X`      | how alike (above 0, at most 1) an edited paragraph or line must be to where it reappears to count as moved (default 0.7; 1: only unchanged moves). See [Moved lines](moves.md#moved-lines-algorithm-and-threshold) |
| `--move-algorithm NAME`    | how that likeness is measured: `token-sort` (default) or `token-set`. See [Moved lines](moves.md#moved-lines-algorithm-and-threshold) |
| `--sentence-move-similarity X`, `--sentence-move-algorithm NAME` | the same for sentences, when comparing by sentence (default 0.55, `token-sort`) |
| `--move-passages`, `--no-move-passages` | also show passages of at least four words moved within or between paragraphs as moved, rather than as a deletion and an unrelated insertion (default: on). See [moves.md](moves.md) |
| `--passage-min-words N`, `--passage-min-chars N`, `--passage-max-gap N`, `--passage-shared-words N`, `--passage-content-letters N`, `--passage-edge-run N`, `--passage-partial-share X`, `--passage-rounds N`, `--passage-max-pairs N`, `--passage-rare-share X`, `--passage-rare-min N` | advanced tuning of moved-passage detection; `prosediff --help` gives each one's meaning and default |
| `--assess AI`              | add an AI assessment to the HTML report (refused with `diff` and `wdiff` output; see [AI assessment](ai_assessment.md)): `claude`, `codex`, or `PROVIDER/MODEL` (e.g. `ollama/qwen3`, `openai/gpt-5`). `claude/MODEL` and `codex/MODEL` pick one of the models `--list-models` reports; without MODEL, the login's default is used |
| `--assess-effort LEVEL`    | how hard the model thinks: a level it supports (see `--list-models`; e.g. `low`, `high`, `max`). Default: the model's own |
| `--assess-context document\|changes` | what the model reads: the changes and the whole new version (`document`, default), or the changes only |
| `--assess-instructions TEXT` | your own instructions added to the prompt, or a text file holding them |
| `--rebuild FILE` | no AI asked: the HTML report made again from the AI's answers saved beside an earlier one (`NAME.ai.json`), or a project holding them (`NAME.prosediff`), the files compared again as then, and refused if a file the AI read, context files included, changed since (its SHA-256 checksum, kept in the answers, another) or is gone; `-o` to write it elsewhere than over that report |
| `--assess-file FILE` | another file sent to the AI as context, to draw on, not to assess (PDF, Word, OpenDocument, Markdown or text); repeat it for several |
| `--assess-prompt TEXT` | a prompt in place of prosediff's own for the assessment (or the review of one file), or a text file holding it; keep its `## Verdict` section |
| `--assess-writing-prompt TEXT` | the same for `--assess-ai-writing` |
| `--assess-author NAME` | who the AI's comments and fixes in the Word and OpenDocument documents are by (default: the AI and its model) |
| `--assess-edits`, `--no-assess-edits` | let the AI edit the text: each fix a rewording of the passage, a tracked change in the documents and, reviewing one file, shown as a diff (default: on); off, it only marks the problems and says what to do |
| `--assess-annotate`, `--no-assess-annotate` | have the AI mark each problem in the text, with what is wrong and a proposed change (default: on) |
| `--assess-documents`, `--no-assess-documents` | offer Word or OpenDocument files with the AI's comments and fixes for download (default: on; see [details](ai_assessment.md#the-ais-problems-in-word-and-libreoffice-documents)) |
| `--assess-save-prompt`     | also include the exact prompt sent to the model in the report (off by default) |
| `--assess-ai-writing`      | also ask whether the added text (with `--review`, the file) reads as AI-written (off by default); an indication, not proof |
| `--assess-mark-ai-writing` | with `--assess-ai-writing`, that same assessment also marks each passage that reads as AI-written, shown with the problems and as comments in the documents: comparing, only text the changes added; with `--review`, any passage |
| `--assess-timeout SECONDS` | give up on the assessment after this long (default 900); the report is still written |
| `--list-models AI`         | list the models an AI offers (`claude`, `codex`, `ollama`, or any provider any-llm reaches), default first, with the efforts each supports |
| `--login-codex`            | log in to ChatGPT in the browser for `--assess codex` (once) |
| `--open`                   | open the HTML report in the browser once written |
| `--setup-git`              | set git up for documents (see below), for REPO (default: the current folder) |
| `--global`                 | with `--setup-git`, for every repository of the user |
| `--to-markdown FILE`       | print a Word or OpenDocument file as Markdown, with tracked changes handled as `--docx-changes` says |
| `--version`                | print the version |

## Examples

- `prosediff --git . HEAD~1 HEAD -o review.html`: the last commit;
- `prosediff --git . HEAD --cached`: what the next commit would record;
- `prosediff --files draft_v1.docx draft_v2_returned.docx`: what a co-author
  changed and commented;
- `prosediff --folders submitted/ revised/`: two folders, file by file;
- `prosediff --files draft_v1.docx draft_v2.docx -o changes.diff`: a unified
  diff;
- `prosediff --files draft_v1.docx draft_v2.docx -o redline.docx`: the new
  version with every change tracked (`-o redline.odt` for LibreOffice);
- `prosediff --files draft_v1.docx draft_v2_returned.docx --assess claude
  --open`: the report, headed by Claude Code's assessment;
- `prosediff --files draft_v1.docx draft_v2_returned.docx --assess
  ollama/qwen3`: the same by a local model, nothing leaving the computer;
- `prosediff --review paper.docx --assess claude --open`: one paper reviewed
  whole by Claude Code.

## With git's own commands

`prosediff --setup-git` sets up the repository it is run in (or REPO;
`--global`: every repository of the user) so that git understands documents:

- `git diff`, `git log -p` and `git show` show Word and OpenDocument files
  as Markdown instead of "Binary files differ". The `*.docx` and `*.odt`
  attributes go in `.git/info/attributes` (not committed), or git's global
  attributes file with `--global`.
- `git difftool -t prosediff` opens a report for each changed file, and
  `git difftool -d -t prosediff` one report for them all (on Windows, if git
  says it "could not symlink", add `--no-symlinks`). Repositories set up by
  prosediff 0.3.1 or earlier need `prosediff --setup-git` again for
  `git difftool -d`.

Both run `python -m prosediff` with the Python prosediff was installed with,
so they work whether or not its scripts are on `PATH`. Running the setup
again changes nothing; `git config --unset` and the attributes file undo it.
