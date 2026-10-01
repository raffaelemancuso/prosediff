# The window

[Back to the README](../README.md).

`prosediff-gui` opens a window to choose what to compare. It follows the
system's light or dark mode on Windows and macOS, and is light elsewhere.

- `prosediff-gui REPOSITORY` opens it with a git repository filled in (or
  the repository a folder belongs to).
- `prosediff-gui FILE.docx` (or `.odt`, `.md`) opens it on the **One file**
  tab, the file filled in, to review it alone.
- `prosediff-gui OLD NEW` opens it with two Markdown, Word or OpenDocument
  files, or two folders.

Any other arguments show an error box, and the program exits once it is
dismissed.

To have it at hand, install it once:

```
uv tool install --editable C:\path\to\prosediff
```

This puts `prosediff` and `prosediff-gui` on `PATH`; on Windows,
`prosediff-gui.exe` opens no console. `scripts/prosediff_gui.bat` and
`scripts/prosediff_gui.sh` also start the window.

![The prosediff window: a git repository with base and target commits chosen from lists, the comparison options in one card, the output, and the AI assessment card with its AI, model and effort](https://raw.githubusercontent.com/raffaelemancuso/prosediff/master/docs/screenshot_window.png)

The segmented button at the top chooses what is compared:

- **Git repository**: pick a folder. Base and target are chosen among the
  working tree, the index and the latest 200 commits, or typed as any ref
  (`HEAD~15`, a tag). It starts from the uncommitted changes, if any,
  else the last commit. Optionally, untracked files and paths (`;`
  between them).
- **Files**: two files, Word documents included.
- **Folders**: two folders; only the files matching "Only" (`--include`)
  are compared.
- **One file**: a file reviewed whole by the AI (see
  [Reviewing one file](ai_assessment.md#reviewing-one-file)). Compare
  becomes **Review**, the output is `NAME_review.html` next to the file,
  and the options only a comparison has are greyed out.

The Files and Folders views have a button to swap the two.

The **Comparison** card holds the options that change what is found; each
field has a tooltip. They are: Word and OpenDocument tracked changes; the
comments (markers, text or none); the document language, which splits
sentences and hyphenates lines; ignoring whitespace; skipping resolved
comments (on by default: comments marked resolved in Word or LibreOffice,
and their replies, are neither shown nor sent to the AI); and "Compare by"
(paragraphs, sentences, or both, the default). A split the output cannot
hold is greyed out: Both for a diff, Sentences for a tracked-changes
document. It gives way to Paragraphs until the output can hold it again.

**Advanced settings**, in the bottom bar, opens the rest in a separate
window (Close or Escape hides it): context lines, line alignment, hidden
lines, moved passages, comments without text, moved paragraphs and
sentences, the `--passage-*` settings, text encoding, the Markdown filter
and the AI timeout. [cli.md](cli.md) explains each. A negative number of
lines, or no timeout, is refused.

Under **Output**: the format (HTML report, unified diff or word diff) and
where to save it. By default it goes next to the new file as
`OLD_vs_NEW.html`, or into the new folder as `prosediff.html`; for git
versions, into the temporary folder. A file you choose stays.

Under **AI assessment** (see [AI assessment](ai_assessment.md)): the
**AI**, its **Model** and **Effort**, listed as the AI reports them (any
name can be typed); what it **Reads**; your **Instructions**, typed, written
in a large box in a window of their own (the pencil button), or from a text
file; in that window, prosediff's own prompts too, to edit (**Restore
default** puts prosediff's back): for one file, the review's; for two
versions, the assessment's and the AI-writing check's; **Other files** to
send as context (a switch, the files separated by ";", and a button that
adds several at once: PDF, Word, OpenDocument, Markdown or text); who its comments in the Word and OpenDocument documents are by
(**Author**, of its comments and tracked changes; empty: the AI and its model); whether it marks problems in the text
(**Mark problems in the text**, on by default); whether it may
edit the text (**Allow text edits**, on by default: its fixes as
rewordings, tracked changes in the documents); and
whether the prompt is saved in the report. The switches are greyed out
while no AI is chosen, and the whole card unless the output is the HTML
report.

**Compare** (Ctrl+Enter) writes the output and opens it. The status line
shows the current stage and how long it has taken. Meanwhile Compare
becomes **Cancel** (Esc), which stops everything, the AI included. A
notification says when it is done.

The window remembers its choices only when asked. **Save options** writes
them to `%APPDATA%\prosediff\gui.json` (elsewhere, under
`$XDG_CONFIG_HOME` or `~/.config`). **Reset to defaults** puts every
option back, except what is compared and where the output goes. Of the
moved-passage values, only those changed from the defaults are saved.
