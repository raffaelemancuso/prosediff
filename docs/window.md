# The window

[Back to the README](../README.md).

`prosediff-gui` opens a window to choose what to compare, its Open screen;
each report it makes opens in a window of its own. Both are web views of
the system's (Edge WebView2 on Windows, WebKit on macOS; on Linux, Qt's,
installed with prosediff), and follow its light or dark mode.

- `prosediff-gui REPOSITORY` opens it with a git repository filled in (or
  the repository a folder belongs to).
- `prosediff-gui FILE.docx` (or `.odt`, `.md`) opens it on the **One file**
  tab, the file filled in, to review it alone.
- `prosediff-gui NAME.ai.json` (any `.json` file) opens it on the
  **Rebuild** tab, the AI's saved answers filled in, to make the report
  again.
- `prosediff-gui OLD NEW` opens it with two Markdown, Word or OpenDocument
  files, or two folders.
- `prosediff-gui NAME.prosediff` opens a project (see below).

Any other arguments show an error window, and the program exits once it
is closed.

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
  are compared, and with **Only the files in both folders**
  (`--common-only`) only those at the same path in both.
- **One file**: a file reviewed whole by the AI (see
  [Reviewing one file](ai_assessment.md#reviewing-one-file)). Compare
  becomes **Review**, the output is `NAME_review.html` next to the file,
  and the options only a comparison has are left out.
- **Rebuild**: a report made again from the AI's answers saved beside it
  (`NAME.ai.json`), without asking the AI (`--rebuild`): the Comparison
  and AI cards are left out, the run's own settings used; the report is
  written over the old one unless **Save to** says otherwise.

The Files and Folders views have a button to swap the two. A file
dropped on a field puts its path in it.

The **Comparison** card holds the options that change what is found; each
field has a tooltip. They are: Word and OpenDocument tracked changes; the
comments (markers, text or none); the document language, which splits
sentences and hyphenates lines; ignoring whitespace; skipping resolved
comments (on by default: comments marked resolved in Word or LibreOffice,
and their replies, are neither shown nor sent to the AI); and "Compare by"
(paragraphs, sentences, or both, the default). A split the output cannot
hold is greyed out: Both for a diff, Sentences for a tracked-changes
document. It gives way to Paragraphs until the output can hold it again.

**Advanced settings**, in the bottom bar, opens the rest in a dialog over
the window (Close or Escape closes it): context lines, line alignment, hidden
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
name can be typed, and the button beside each lists them all); what it
**Reads**; your **Instructions**, typed, written in a large box in a dialog
of their own (the pencil button), or from a text file; in that dialog,
prosediff's own prompts too, to edit (**Restore
default** puts prosediff's back): for one file, the review's; for two
versions, the assessment's and the AI-writing check's; **Other files** to
send as context (a switch, and **Files…**, their list in a dialog of its
own, sorted by **File** or **Folder** when a heading is clicked, with
**Add…** for several at once: PDF, Word, OpenDocument, Markdown or text;
a file analysed, one of the two compared, the one reviewed, or one in a
folder or repository compared, is refused); who its comments in the Word and OpenDocument documents are by
(**Author**, of its comments and tracked changes; empty: the AI and its model); whether it marks problems in the text
(**Mark problems in the text**, on by default); whether it may
edit the text (**Allow text edits**, on by default: its fixes as
rewordings, tracked changes in the documents); and
whether the prompt is saved in the report. The switches are greyed out
while no AI is chosen, and the whole card unless the output is the HTML
report.

**Compare** (Ctrl+Enter) writes the output and opens it: the HTML report
in its window (the same one for the next report), a diff or a document of
tracked changes in the program that opens it. The status line shows the
current stage and how long it has taken, and **Progress**, above it, logs
each stage and what the AI does, a line each; drag the log's corner to
make it taller or shorter. Meanwhile Compare becomes **Cancel** (Esc),
which stops everything, the AI included. A notification says when it is
done. With **Preview before sending**, the report without the assessment
opens first, and the Open screen asks whether to send it to the AI.

In a report's window, the documents to download (**With AI fixes**,
**Tracked changes**) are saved where a dialog says, beside the report at
first, rather than in the Downloads folder. **View > Zoom** (or Ctrl+`+`,
Ctrl+`−`, Ctrl+`0`, Ctrl with the mouse wheel) zooms the report as a browser
would, from 25% to 500%; the window remembers the zoom for the next report.

The window remembers its choices only when asked. **Options > Save options** writes
them to `%APPDATA%\prosediff\gui.json` (elsewhere, under
`$XDG_CONFIG_HOME` or `~/.config`). **Options > Reset to defaults** puts every
option back, except what is compared and where the output goes. Of the
moved-passage values, only those changed from the defaults are saved.

## Projects

The **File** menu saves and opens projects (`NAME.prosediff`, a JSON
file). **Save project** (Ctrl+S; **Save project as…** for another file)
keeps every setting shown: what is compared (the repository, the files,
the folders, the file reviewed), the other files sent to the AI as
context, the comparison and output options, the AI and its prompts. It
also keeps the AI's answers of the last report it assessed in the window,
with the checksum (SHA-256) of every file the AI read: the files compared
and the context files, taken as the run began.

It keeps, too, the choices made in that report's window: which problems
are **In the download**, which **Resolved**, which fixes undone (**Undo
fix**). Made in the report of the project open, they are written into the
project as they are made, nothing else of it changed: settings changed in
the window since stay unsaved until Save project. Made in a report the
project does not hold yet (one made since it was saved), they are kept
until **Save project**, which saves them with that report's answers. A
report opened as a file, in a browser, keeps its choices in the browser
instead, for that report on that computer.

**Open project…** (Ctrl+O, or `prosediff-gui NAME.prosediff`) shows all
those settings again, and the window's title names the project. When the
project holds a report, its **Rebuild** tab is set to it: **Rebuild**
makes that report again without asking the AI, over the report it was
made as, unless Save to says otherwise (`prosediff --rebuild
NAME.prosediff` does the same). It is refused if any file the AI read,
input or context file, changed since or is gone: the AI's marks would not
fit the new text, so the AI must be asked again. A git repository's
commits do not change and are not checked. The report made again opens
with the choices the project keeps.
