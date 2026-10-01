# AI assessment

[Back to the README](../README.md).

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

## The AI's problems in Word and LibreOffice documents

When a Word document is compared with another (or an OpenDocument text with
another) and the AI marked problems, the assessment's drawer, and a button
for each in the top bar, offer the documents to download, made from the new
version:

- **The changes, tracked, with the AI's comments**: the document of
  [tracked changes](how_it_works.md#tracked-changes), each problem a comment of the AI's
  on its passage, what is wrong and the change it proposes. Only when the
  new version has no tracked changes of its own: those already show what
  the co-authors changed, under their names, and would be marked again,
  under another;
- **The new version with the AI's fixes, tracked**: where the AI wrote out
  how the passage should read, its fix is a tracked change of its own, to
  accept or reject in Word or LibreOffice, what is wrong a comment on it;
  a problem it gave no such fix for (text to add elsewhere, a reviewer to
  answer, a passage over several paragraphs) is a comment, the change
  proposed in it. So is a fix whose words are not the document's own text,
  word for word: one in an equation, a field or a note reference, whose
  place could only be guessed. A fix changes whole words, and keeps the
  document's curly quotes and dashes where the AI wrote plain ones.
  It is the new file itself, its own tracked changes and comments (the
  co-authors') kept as they are, the AI's added on top. A fix of words a
  co-author put in is a change of the AI's beside theirs: in Word, its
  deletion within their insertion, as Word marks one; in LibreOffice, its
  deletion stacked on their insertion, as LibreOffice writes one (checked
  with LibreOffice 26.8). Rejected, the words come back as the
  co-author's. (Read with `--docx-changes reject-all` or `show`, a Word
  document's own changes are settled first, as the text was compared.)

Each opens with a comment on the first paragraph giving the AI's verdict;
the comments and the changes are the AI's, under its name ("Claude Code
(claude-opus-5-5)"). Review mode (`r`) has a box beside each problem:
unticked, the problem is left out of the documents when they are saved, its
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

## Reviewing one file

`--review FILE --assess AI` (in the window, the **One file** tab) skips the
comparison: there is no other version, and the AI reads the file itself,
whole, its comments included. It answers as for a revision, with a verdict
(*Good*, *Fair* or *Poor*), a summary, the strengths and the problems to
fix, and, with `--assess-annotate` (on by default), marks each problem in
the text, a badge before its passage and a card in the margin. The report
shows the file in one column, paragraph by paragraph. A Word document (or
an OpenDocument text) comes back as **the document with the AI's fixes,
tracked**: the file itself, the co-authors' tracked changes and comments
in it kept, the AI's added; to download from the top bar or the
assessment's drawer: each problem a comment of the AI's on its passage,
each fix it wrote out a tracked change
of its own, as described above; review mode's boxes choose which problems
it holds. There are no changes to track, so the other document is not
made, and the options of a comparison do not apply: `--split` is paragraph,
the output an HTML report, `--assess-context` has nothing to choose, and
`--assess-ai-writing`, which asks about the text the changes added, is
refused. Markdown and text files are reviewed too, without a document to
download.
