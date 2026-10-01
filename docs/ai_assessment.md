# AI assessment

[Back to the README](../README.md).

`--assess AI` (in the window, "AI assessment") has an AI read the changes
and judge them as a whole:

- **Verdict**: *Improves*, *Mixed* or *Worsens*, and why;
- **What changed**, grouped by section;
- **Improvements**;
- **Problems to fix**, most serious first, each quoting the words
  concerned: unsupported claims, misstated results, broken sentences,
  placeholders, dangling citations, inconsistent terms, and reviewers'
  comments left unanswered.

The verdict is a badge in the HTML report's top bar; clicking it opens the
assessment in a drawer. The assessment comes with the HTML report only.

![The AI assessment of a prosediff report, opened in its drawer from the verdict in the top bar: a verdict badge, what changed, the improvements and the problems to fix](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_assessment.png)

With `--assess-annotate` (on by default; in the window, "Mark problems in
the text") the AI also marks each problem: a numbered ⚠ badge before its
passage and a card in the margin with the fix proposed. A passage that
cannot be found is listed as such in review mode. Clicking a problem pins
it until clicked again; the top bar's ◀ ⚠ ▶
(`a`, Shift+`a`) steps through them; see [report.md](report.md).

![A paragraph of a prosediff report with the problems the AI marked: a numbered badge before each passage in the text, and in the margin beside it a card for each, what is wrong and the change proposed; the first pinned, its passage highlighted and its card ringed](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_marks.png)

## The AI's problems in Word and LibreOffice documents

When two Word documents (or two OpenDocument texts) are compared and the
AI marked problems, the drawer and the top bar offer two documents to
download, made from the new version:

- **The changes, tracked, with the AI's comments**: the
  [tracked-changes document](how_it_works.md#tracked-changes), each
  problem an AI comment. It is offered only when the new version has no
  tracked changes of its own.
- **The new version with the AI's fixes, tracked**: the new file itself,
  its own tracked changes and comments kept, with the AI's added on top.
  Each fix the AI wrote out is a tracked change to accept or reject, with
  a comment saying what is wrong. Problems without a fix, or whose place
  would be a guess (an equation, a field, a note reference), are comments
  only. A fix inside a co-author's insertion is the AI's own change;
  rejected, the words return to the co-author.

Each document opens with the verdict as a comment, and the AI's comments
and changes carry its name, such as "Claude Code (claude-opus-5-5)". In
review mode (`r`), unticking a problem leaves it out. Word allows no
comments in notes, so a note's problem is commented on its number in the
text. The documents add about 2.7 times the document's size to the
report; `--no-assess-documents` (in the window, "Documents to download"
off) leaves them out.

The AI is one of three kinds, each an optional extra:

| `--assess`       | What answers | Install | Sign-in |
|------------------|--------------|---------|---------|
| `claude`, `claude/MODEL` | Claude Code, through the [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/); MODEL one of those Claude Code reports (`opus`, `sonnet`, a full name such as `claude-opus-5-5`, ...) | `uv tool install "prosediff[claude]"` | your Claude login (the one Claude Code uses); billed to your Claude plan |
| `codex`, `codex/MODEL`   | ChatGPT, through OpenAI's [Codex SDK](https://pypi.org/project/openai-codex/) (the Codex program comes with it); MODEL one of those Codex reports (`gpt-5.5`, ...) | `uv tool install "prosediff[codex]"` | your ChatGPT login: `prosediff --login-codex` once; billed to your ChatGPT plan |
| `PROVIDER/MODEL` | any model of the fifty-odd [providers any-llm supports](https://mozilla-ai.github.io/any-llm/providers/): a local model of [Ollama](https://ollama.com/) (`ollama/qwen3`), LM Studio, llama.cpp or vLLM, or an API (`openai/gpt-5`, `anthropic/claude-sonnet-5`, `gemini/...`, `mistral/...`, `deepseek/...`, `groq/...`, `openrouter/...`, Azure, Bedrock, ...) | `uv tool install "prosediff[models]"` | none for a local model; an API's key in its environment variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, ...) |

Extras combine: `"prosediff[claude,codex,models]"`; a missing one is
named. `prosediff --list-models claude` (or `codex`, `ollama`, `openai`,
...) lists the models the AI reports, the default first and the default
effort starred, as the window does. Three more settings:

- **Effort** (`--assess-effort`): more reads more closely, but takes
  longer and costs more.
- **What it reads** (`--assess-context`; in the window, **Changes + new
  version** or **Changes only**): by default the changes and the whole new
  version, to check them against the rest; or the changes only.
- **Your instructions** (`--assess-instructions`): the journal, what to
  look at, as a sentence or a text file, added to the prompt.
- **prosediff's prompt** (`--assess-prompt`, `--assess-writing-prompt`; in
  the window, the pencil button beside **Instructions**): replaced by one
  of your own, as text or a text file. Keep its `## Verdict` section, with
  the first word in bold one of the verdicts it names: the report reads
  it. prosediff still adds after it the rules for Word and OpenDocument
  files and for marking problems in the text. `--assess-save-prompt` shows
  the prompt sent.
- **Who the comments are by** (`--assess-author`; in the window,
  **Author**): the name on the AI's comments and tracked-change fixes in the Word
  and OpenDocument documents; by default the AI and its model.

**Privacy.** The AI is sent the word diff of the changed paragraphs
(comments included, except those marked resolved, unless
`--no-skip-resolved`), the whole new version (unless `--assess-context
changes`) and your instructions; no files, and no tools to run. With
`claude`, `codex` or an API, this text goes to Anthropic's, OpenAI's
or the API's servers under your account; with Ollama it stays on the
computer. The HTML report needs no network to read. `--assess-save-prompt`
(in the window, "Save AI prompt") puts the text, exactly as sent, at the
end of the report. A long revision is cut at about 100,000 tokens.

In the window, "Preview before sending" (on by default) first opens the
report without the assessment, then asks whether to send the changes. A
file reviewed alone has no preview.

Check the assessment against the text. A local model needs a few billion
parameters to assess a long diff, and is fast only if it fits the graphics
card's memory.

## Reviewing one file

`--review FILE --assess AI` (in the window, the **One file** tab) skips
the comparison: the AI reads the file whole, comments included. It gives a
verdict (*Good*, *Fair* or *Poor*), a summary, the strengths and the
problems, marked in the text with `--assess-annotate`. A Word or
OpenDocument file also comes back as **the document with the AI's fixes,
tracked**, as above; Markdown and text files have no document to
download. `--split` is paragraph, the output is HTML, `--assess-context`
does not apply, and `--comments` is `markers` (the AI is sent the file's
comments) or `none` (it is not). `--assess-ai-writing` asks whether the
file reads as AI-written; with no earlier version to weigh it against, the
AI can only compare its parts with each other, so this verdict is weaker
still than for changes.
