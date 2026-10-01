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

Requirements: Python 3.11 or later, and [git](https://git-scm.com/) for
every comparison, `--files` and `--folders` included: prosediff aligns the
lines of the two versions with `git diff --no-index` (see [How it
works](how_it_works.md)). git must be on `PATH`, or named by the
`GIT_PYTHON_GIT_EXECUTABLE` environment variable (e.g.
`C:\Program Files\Git\cmd\git.exe`); without it prosediff does not start.

## Options

| Argument / option          | Meaning                                                       |
|----------------------------|---------------------------------------------------------------|
| `--git REPO BASE [TARGET]` | compare commits of a git repository. REPO: the repository, or any folder inside it; BASE: the older commit (hash, branch, tag, `HEAD~2`, ...); TARGET: the newer commit; without it, BASE is compared with the working tree (tracked files), as `git diff BASE` does |
| `--files OLD NEW`          | compare two files, whatever their names, outside git |
| `--folders OLD NEW`        | compare two folders, file by file, outside git |
| `--review FILE`            | no comparison: one file alone, reviewed whole by the AI `--assess` names (required), in an HTML report of its assessment and the problems it marked in the text, with, for a Word or OpenDocument file, the file with the AI's comments and fixes as tracked changes to download (see [Reviewing one file](ai_assessment.md#reviewing-one-file)). Written next to the file as `NAME_review.html` by default |
| `--include PATTERNS`       | with `--folders`, compare only the files matching these glob patterns, separated by `\|` (quote them), e.g. `"*.docx\|*.md"`; a pattern is matched against each file's name, or its path within the folder when it has a `/`, ignoring case. Default `*.docx\|*.odt\|*.md\|*.typ\|*.txt`; `""` compares every file. The lock files an open document leaves beside it (Word's `~$name.docx`, LibreOffice's `.~lock.name.odt#`) are always left out. In the GUI, the "Folders: only" box |
| `--cached`                 | with `--git`, compare BASE with the index instead, as `git diff --cached BASE` does |
| `--untracked`              | with `--git` and the working tree, also show the untracked files `.gitignore` does not exclude |
| `-w`, `--ignore-whitespace`| compare lines ignoring whitespace, as `git diff -w`           |
| `-p`, `--path PATH`        | with `--git` or `--folders`, restrict the diff to this file or folder (repeatable) |
| `-o`, `--output FILE`      | output file. Default: with `--open`, a new HTML report in the temporary folder; otherwise, comparing two folders, `prosediff.html` in the new one (never compared itself when the folders are compared again); comparing two files, `OLD_vs_NEW.html` next to the new one; else `diff.html` (`.diff`, `.wdiff`, `.docx` or `.odt` with `--format diff`, `wdiff`, `docx` or `odt`). The GUI does the same |
| `--format html\|diff\|wdiff\|docx\|odt` | `html`: the HTML report (default); `diff`: a unified diff, with `-U` lines of context (default 3) or `--full`, its lines paired as the HTML report pairs them (an edited line's removal followed by its new text). A text file's diff is a patch `git apply` and `patch` can apply. For Markdown files and Word and OpenDocument documents, the lines are those the HTML report compares: one per paragraph (or sentence, with `--by-sentence`), blank lines left out, numbered as in the HTML report; a document's formatting is written in Markdown (`**bold**`), the comments added or removed in [CriticMarkup](https://github.com/CriticMarkup/CriticMarkup-toolkit) (`{>>Author (date): text<<}`; those both sides have are left out, as in the HTML report), and tracked changes kept with `--docx-changes show` as `{++inserted++}` and `{--deleted--}`: a diff to read, not to apply. `wdiff`: a word diff, as `git diff --word-diff` writes one, the same lines with the words changed within each marked `[-removed-]{+added+}` (paired as in the HTML report), a line removed or added whole marked whole. `docx`, `odt`: the new version as a Word document or an OpenDocument text, each change since the old one a tracked change to accept or reject in Word or LibreOffice (see [Tracked changes](how_it_works.md#tracked-changes)); paragraph by paragraph only. Moved lines are marked only in the HTML report. Default: `diff` when the output file ends in `.diff` or `.patch`, `wdiff` for `.wdiff`, `docx` for `.docx`, `odt` for `.odt`. In the GUI, "Format" |
| `-U`, `--context N`        | unchanged lines shown around each change, in every file; unset, 0 in Markdown files and Word documents (whose lines are whole paragraphs) and 3 in the others. In the GUI, the "Context lines" box: `auto` or a number |
| `--full`                   | show every line of each changed file                          |
| `--max-hidden N`           | unchanged lines embedded per gap for the HTML report to reveal (default 500); longer gaps are left out, to keep the HTML report light. In the GUI, "Hidden lines" |
| `--align left\|justify`    | alignment of wrapped lines (default left)                     |
| `--comments markers\|text\|none` | the comments of Markdown files and Word and OpenDocument documents, in every format. `markers` (default): set apart from the text, only those added or removed since the base shown, the comments both sides have left out; in the HTML report each is a 💬 marker (a green balloon holding a + when added; a red one holding a − when removed), with a card in the margin beside it (the author, the date and the comment); in the diffs it is written in CriticMarkup (`{>>Author (date): text<<}`). `text`: the comment markup compared as part of the text, as pandoc writes it. `none`: every comment left out, so a line whose only change was a comment is unchanged. In the GUI, "Comments" |
| `--empty-comments`         | also show the comments that have no text, left out by default (a card saying "(no text)") |
| `--docx-changes accept-all\|reject-all\|show` | the tracked changes of Word and OpenDocument documents: accept them all (`accept-all`, the default), reject them all (`reject-all`), or `show` them as Word shows them (insertions underlined, deletions struck through, who made each and when on hover) |
| `--md-filter COMMAND`      | shell command (cmd.exe on Windows, sh elsewhere) both versions of every Markdown file (not Word or OpenDocument files, which are not read as Markdown) are piped through, stdin to stdout, before comparing; line numbers are then those of the filtered text. In the GUI, "Markdown filter" |
| `--split paragraph\|sentence\|both` | how the prose of Markdown files and Word and OpenDocument documents is compared: paragraph by paragraph; sentence by sentence, where a sentence moved between paragraphs is recognised and each sentence is labelled with its line and its place in it (`12.3`); or both, in one HTML report whose toolbar switches between the two (`s`), a diff holding one only. Default: both for the HTML report, paragraph by paragraph for a diff. In the GUI, "Compare by" |
| `--language CODE`          | the language of the prose: its rules split sentences with `--split sentence` (about forty languages are known; others fall back to a simple rule), and the HTML report hyphenates wrapped lines by it. A code, e.g. `en`, `it`, `de`, `fr`, `pt-br`; `document`, the languages Word and OpenDocument files mark their text with, in the runs' and the styles' settings: each paragraph is split and hyphenated by its own, and the file's language is the one most of its letters are marked with, for the paragraphs that mark none (an error for Markdown and text files); or `guess`, guessed from each file's text (py3langid). Default: `document` for Word and OpenDocument files, `guess` for the others and for a document that marks no language. A file whose language is unknown (too short or too mixed to guess) is split by English rules and not hyphenated |
| `--encoding NAME`          | the encoding of text and Markdown files, e.g. `utf-8`, `cp1252`, `latin-1` (Word and OpenDocument files carry their own). Default `auto`: UTF-8, unless a file cannot be read as UTF-8 or reads with control characters; then the encoding is guessed with [cchardet](https://pypi.org/project/cchardet/) (Mozilla's uchardet, reliable even on a few words), or, when its guess cannot read the file, with [charset-normalizer](https://pypi.org/project/charset-normalizer/); Windows-1252 is preferred when it reads the text alike, and the file header says which was used. In the GUI, the "Text encoding" box |
| `--move-similarity X`      | how alike, above 0 and at most 1, an edited paragraph (a line of other files) must be to where it reappears to count as moved, by `--move-algorithm` (default 0.7; 1: only lines moved unchanged). Since 0.5.0 it no longer applies to sentences, which have their own setting below |
| `--sentence-move-similarity X`, `--sentence-move-algorithm NAME` | the same for sentences, when prose is compared sentence by sentence (default 0.55, `token-sort`). In the GUI, "Moved paragraphs" and "Moved sentences", each a threshold and an algorithm |
| `--move-passages`, `--no-move-passages` | also follow the passages moved within a paragraph (a line) or between two (default: on): a run of words removed in one place and added in another, gaps of up to two unchanged words allowed, at least four words (and 15 non-space characters) long, as alike as `--move-similarity` (or `--sentence-move-similarity`) and its algorithm say, is shown as moved rather than as a deletion and an unrelated insertion; a passage is also looked for inside a longer one (a sentence moved out of a paragraph deleted or rewritten). In the GUI, "Moved passages" |
| `--passage-min-words N`, `--passage-min-chars N`, `--passage-max-gap N`, `--passage-shared-words N`, `--passage-content-letters N`, `--passage-edge-run N`, `--passage-partial-share X`, `--passage-rounds N`, `--passage-max-pairs N`, `--passage-rare-share X`, `--passage-rare-min N` | how moved passages are told from chance likeness (advanced; `prosediff --help` says what each does, and its default): the shortest passage (4 words, 15 characters), the unchanged words allowed inside one (2), the words of meaning two passages must share (2, of 4 letters or more), the words in common that can start or end one (2), when a passage is looked for inside a longer one (0.8), the rounds of matching (4), and past how many pairs only those sharing a rare word are tried (250,000; rare: in 1% of the passages, or 20). In the GUI, the fields of "Advanced settings" |
| `--move-algorithm NAME`    | how that likeness is measured: `token-sort` (default), the words and punctuation two lines have in common whatever their order; `token-set`, the words both share against the rest of each. See [Moved lines](moves.md#moved-lines-algorithm-and-threshold). In the GUI, the list next to "Moved-line similarity" |
| `--assess AI`              | have an AI assess the value of the changes as a whole, at the top of the HTML report, closed until opened (not in a `.diff` or `.wdiff`, which refuse it; see [AI assessment](ai_assessment.md)): `claude`, `codex`, or `PROVIDER/MODEL` (e.g. `ollama/qwen3`, `openai/gpt-5`); `claude/MODEL` and `codex/MODEL` choose their model among those they report (`--list-models`), else the one the login uses. In the GUI, the "AI assessment" card |
| `--assess-effort LEVEL`    | how hard the model thinks: one of the levels it reports it supports (`--list-models`; e.g. `low`, `medium`, `high`, `xhigh`, `max`). Default: the model's own |
| `--assess-context document\|changes` | what the model reads: `document` (default), the changes and the whole new version, to check them against the rest of the document (citations, cross-references, terms); `changes`, the changes only. The old version is never sent apart: its unchanged paragraphs are in the new one, and what changed is in the changes. In the GUI, "Changes + new version" and "Changes only" |
| `--assess-instructions TEXT` | your own instructions, added to the prompt (e.g. `"the journal is Research Policy; Laura asked to shorten the introduction"`), or a text file holding them |
| `--assess-annotate`, `--no-assess-annotate` | have the AI mark each problem in the text (default: on): from its first words to its last, with what is wrong and the change it proposes; a numbered ⚠ badge before each in the HTML report, its passage highlighted when clicked, a card in the margin beside it, and stepped through from the top bar. In the GUI, "Mark individual changes" |
| `--assess-documents`, `--no-assess-documents` | with problems marked in a Word document or an OpenDocument text compared with another, put in the HTML report the tracked changes with the AI's comments and the new version with its fixes, to download (default: on; see [The AI's problems in Word and LibreOffice documents](ai_assessment.md#the-ais-problems-in-word-and-libreoffice-documents)). In the GUI, "Documents to download" |
| `--assess-save-prompt`     | also put the exact text sent to the model, its system prompt and its message, in the HTML report, in a closed panel at its end (off by default): to see what it read. In the GUI, "Save AI prompt" |
| `--assess-ai-writing`      | also ask the AI, apart, whether the text the changes added reads as written by an AI (off by default): a second assessment, its verdict (likely, possibly or unlikely) in the HTML report's top bar, opening its own drawer, with the signs for and against. An indication, not a proof: careful writers show the same signs, and writers in a second language are often taken for an AI wrongly. In the GUI, "Check for AI writing" |
| `--assess-timeout SECONDS` | give up on the assessment after this long (default 900); the report is written all the same, saying why there is none. In the GUI, "Timeout (seconds)" |
| `--list-models AI`         | list the models an AI reports it offers (`claude`, `codex`, `ollama`, or any provider any-llm reaches), its default first, and the efforts each supports |
| `--login-codex`            | log in to ChatGPT, in the browser, for `--assess codex` (once) |
| `--open`                   | open the HTML report in the browser once it is written |
| `--setup-git`              | set git up to show Word and OpenDocument files as text and to open prosediff HTML reports from `git difftool` (see below), for the repository REPO (default: the current folder) |
| `--global`                 | with `--setup-git`, for every repository of the user instead |
| `--to-markdown FILE`       | print a Word or OpenDocument file as Markdown (pandoc's: formatting, comments and tracked changes included), tracked changes as `--docx-changes` says: what `git diff` shows once set up |
| `--version`                | print the version                                             |

## Examples

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
  ollama/qwen3`: the same by a local model, nothing leaving the computer;
- `prosediff --review paper.docx --assess claude --open`: no comparison,
  one paper reviewed whole by Claude Code, its problems marked in the text
  and the paper offered back with the AI's comments and fixes, tracked.

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
