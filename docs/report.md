# The HTML report

[Back to the README](../README.md).

## What it shows

- **Header**: the title and repository path, a line for each side (hash or
  file name, subject, author, date, or the path outside git) and, for
  commits, the 50 newest commits in between.
- **AI assessment** (with `--assess`): the verdict as a badge in the top
  bar, opening a drawer with what changed, the improvements and the problems
  to fix. See [AI assessment](ai_assessment.md).
- **Summary line**: the view and the counts of changes.
- **File list** (several files): each file's counts, linked to its table.
- **File tables**: old beside new, collapsible, the header sticking under
  the top bar. Long lines wrap. Unchanged lines beyond the context fold into
  a "show N unchanged lines" link.
- **Changed words** are highlighted; a word changed into a similar one
  ("repeat" to "repeated") only in its changed letters.
- **Images** (PNG, JPEG, GIF, WebP, BMP, up to 5 MB) are shown side by
  side. Other binary files are only listed.
- **Columns** resize by dragging the line between them or the margin's
  edge; a double-click restores the default.

## Comments and problems

The margin holds a card for each comment and each problem the AI marked,
beside its paragraph. A comment's card gives its status (new or removed),
author, date and formatted text; a problem's card, what is wrong and the
fix proposed. A paragraph with a card is never folded away. Comments
marked resolved in Word or LibreOffice, and their replies, have no card
and no marker: by default they are left out of the report, and the AI is
never sent them, both when comparing and when reviewing one file
(`--no-skip-resolved` keeps them; see [How it works](how_it_works.md)).

In the text, a comment is a 💬 balloon, green with + when added, red with −
when removed; a problem is a numbered ⚠ badge. Clicking a card or its mark
pins it, highlighting its words until it is clicked again or Esc is pressed.
On a narrow window, and in one column, the cards sit under their paragraph.

## Moved text

A removed line that reappears elsewhere (at least 20 non-space characters)
is shown in its own colour, with "moved to line N" or "moved from line N".
It may be edited on the way: at least 70% alike by default, 55% sentence by
sentence, its words compared in any order, its edits highlighted. Hovering
its number says where it went and whether it was edited.

A passage of at least four words moved within or between paragraphs is
tinted the same way and counted as moved, not as words removed and added.
Moved passages are always found but shown only with the "Moved passages"
switch (`v`), off by default; then each is removed in one place and added
in the other. See [Moved text](moves.md).

## Top bar and keys

The top bar stays in view; hovering an item shows its key. The browser
remembers the views, the spacing and the column widths.

| Key | Action | Default |
|---|---|---|
| `n`, `p` | Next, previous change | |
| `c`, Shift+`c` | Next, previous comment | |
| `a`, Shift+`a` | Next, previous AI problem | |
| `r` | Review mode | off |
| `j`, `k` | Next, previous item in review mode | |
| `s` | Paragraph or sentence view (`--split both`) | |
| `l` | Lines joining each move's two places | off |
| `v` | Moved passages | off |
| `u` | One column, old above new | off |
| `g` | Margin | shown |
| `h` | Change highlights (View menu) | on |
| `f` | Formatted text (View menu) | on |
| `m` | Formatting changes (View menu) | on |
| `[`, `]` | Less, more space between paragraphs | |
| Esc | Unpin a comment or problem | |

- **Review mode** lists the assessment, changes, comments and problems on
  the left, and shows only the chosen item's paragraph, with **Previous**
  and **Next**. **Problems ↓** at the top of the list jumps to the
  problems; dragging the list's right edge widens or narrows it
  (remembered; a double-click puts it back).
- **The documents to download:** a button in the top bar for each
  (**⤓ Tracked changes**, **⤓ With AI fixes**) saves it at once; its
  tooltip says how many of the problems it holds.
- **Which problems go into them:** each problem's card has two boxes:
  **In the download**, unticked, leaves the problem's comment out of the
  Word or OpenDocument file and rejects its fix; **Resolved** marks its
  comment resolved in the file (Word's Resolve, LibreOffice's Resolved),
  greyed out while the problem is left out. **In the download** is also
  on the problem's line in Review mode's list.
  **✓ Resolve fixes applied** in the top bar marks resolved the comments
  of the problems whose fix the version with the AI's fixes holds.
- **Filter** in the top bar shows only the problems in the download or
  left out of it, resolved or not: the others lose their card and their
  badge and leave Review's list, and the problems' ◀ ▶ skip them. A
  problem that stops matching, a box clicked, goes at once.
- **A fix already applied:** in the report of one file's fixes, a
  problem whose fix the version on the right holds has a green card that
  says so, its **Resolved** box ticked from the start; in the documents, its comment starts with
  "✓ Fix already applied" in bold and ends saying that the
  tracked change on the passage is the fix: accept it to keep it, reject
  it to undo it.
- **Highlights off** removes the colour of changed words and lines; the
  gutters stay tinted and signed.
- **Formatted** hides Markdown syntax and shows emphasis, headings, links
  and a document's bold, italic and the like, in a proportional font.
- **Formatting changes** marks in amber the words of a Word or OpenDocument
  file whose formatting alone changed, with what changed on hover.
- **Spacing** (− and +, an ARIA spinbutton) applies to Markdown and Word
  documents.

## Language and hyphenation

Prose in Markdown and Word documents is hyphenated for its language: the
one given with `--language`, else the one the document marks, else one
guessed from the text. [pyphen](https://pypi.org/project/Pyphen/) places
soft hyphens, so every browser breaks words alike. A flag shows the
language, in the file header or before a paragraph in another language; its
tooltip says how the language was found. The flags are inline SVGs from
[flag-icons](https://github.com/lipis/flag-icons) (MIT), since Windows has
no flag emoji.

## Printing

Printing, or saving as PDF, opens every file, drops the toolbar and keeps
the light colours, even in dark mode. Paragraphs break across pages without
leaving a lone line, and the gutters narrow to fit a portrait page. The
assessment comes first, each card is printed whole, and where moved text
went is written out. Column headings repeat on every page, and folded lines
print as "⋯ N unchanged lines".

## Accessibility

Changes are also marked without colour, by a sign in the gutter (`−`
removed, `+` added, `~` changed, `→` `←` moved), and each changed row tells
screen readers what it is. The report follows the browser's light or dark
mode and needs no network: CSS, JavaScript and images are inline.
