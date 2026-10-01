# The window

[Back to the README](../README.md).

`prosediff-gui` opens a window to choose what to compare, drawn with
[ttkbootstrap](https://pypi.org/project/ttkbootstrap/) in its Bootstrap theme, light
or dark as the system is set (Windows' app mode, macOS's appearance; light
elsewhere), the title bar too on Windows.
`prosediff-gui REPOSITORY` opens it with a git repository filled in (or the
repository a folder belongs to); `prosediff-gui FILE.docx` (or `.odt`, `.md`) first asks, in a
file dialog, for the file to compare it with, the older of the two going on
the left, and fills in the **One file** tab with it too, to review it alone
instead; `prosediff-gui OLD NEW` opens it with two Markdown, Word or
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
shows the fields of one of three sources, or of one file to review:

- **Git repository**: pick a folder; base and target are chosen among the
  working tree, the index and the latest 200 commits (hash, date, author,
  subject), or typed as any ref (`HEAD~15`, a tag). The window starts from
  the uncommitted changes when there are any, otherwise from the last
  commit. Optionally, untracked files and a list of paths (separated by `;`).
- **Files**: two files, whatever their names, Word documents included.
- **Folders**: two folders, of which only the files matching the patterns
  of "Only" (`--include`) are compared.
- **One file**: a file alone, reviewed whole by the AI chosen under **AI
  assessment** (see [Reviewing one file](ai_assessment.md#reviewing-one-file)); Compare
  becomes **Review**, the output is the HTML report (`NAME_review.html`
  next to the file), and the options that only a comparison has ("Compare
  by", "Ignore whitespace", what the AI reads, the AI-writing check) are
  greyed out.

The options that change what the comparison finds sit in one card,
**Comparison**, each explained by a tooltip (rest the pointer on it, or on
its ⓘ; in a drop-down list, on an item to learn what it means): Word and
OpenDocument tracked changes, the comments (markers, text or none), the
document language (`default`: the one Word and OpenDocument files mark, the
others guessed; `document`; `guess`, guessed from each file; or a code such
as `it`), which splits sentences and hyphenates lines, a switch to ignore
whitespace, and how prose is compared ("Compare by": paragraphs, sentences
or both, the default). A split the output cannot hold is greyed out: Both
for a diff, which holds one, Sentences for a Word or OpenDocument document
of tracked changes; one chosen gives way to Paragraphs, and comes back when
the output can hold it again.

The **Advanced settings** button, in the bar at the bottom, opens the rest
in a window of its own, beside the main one (Close, or Escape, hides it
again; what it holds applies to the next comparison). Its **Report** card:
context lines or whole files, the alignment of wrapped lines, the
unchanged lines embedded per gap ("Hidden lines", as `--max-hidden`), whether
passages moved within or between paragraphs are followed too ("Moved
passages"), and comments without text. Then the settings few need to change: how alike a paragraph and a sentence must stay to
count as moved and how that is measured (a threshold and an algorithm for
each, "Moved paragraphs" and "Moved sentences"); how moved passages are
told from chance likeness, the same as the `--passage-*` options, each
explained by its tooltip; the encoding of text files ("Text
encoding"); the command Markdown files are piped through ("Markdown
filter", as `--md-filter`); and how long the AI may take ("Timeout", as
`--assess-timeout`). A negative number of context or hidden lines, or a
timeout of none, is refused, as on the command line. Only the moved-passage values changed from the defaults are
saved, so the others follow prosediff's defaults; "Reset to defaults"
puts every option back.

Under **Output**, the format (HTML report, unified diff or word diff, the
extension of the file following it), where to save it (by default next to the new
file, as `OLD_vs_NEW.html`, or into the new folder, as `prosediff.html`,
following the files or folders as they change; comparing git versions, a new
file in the temporary folder; a file you choose stays).

Under **AI assessment** (see [AI assessment](ai_assessment.md)), the **AI**
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
