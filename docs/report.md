# The HTML report

[Back to the README](../README.md).

- A compact header: the title (and the repository's path), then a line
  for each side (hash or file name, subject, author, date, and the file's
  or folder's path outside git) and, for commits, the commits in between
  (reachable from the target, or from HEAD for the index and the working
  tree, and not from the base; the 50 newest are listed).
- With `--assess`, the AI's assessment of the changes: its verdict as a
  badge, what changed, the improvements and the problems to fix (see [AI
  assessment](ai_assessment.md)), in a drawer opened from its verdict in the
  top bar.
- A summary line: which view it is (paragraph or sentence, with a button to
  the other when the report holds both) and how many paragraphs (or
  sentences; lines, for files other than prose) were changed, inserted,
  deleted and moved.
- The margin, beside the two versions: a card for each comment and each
  problem the AI marked, next to the paragraph it is in, in the order they
  come in its text. A comment's card says whether it is new or removed, who
  wrote it and when, and what it says, with the paragraphs and the
  formatting it has in Word or LibreOffice (a long one shows its first
  lines, the rest on "Show all"); a problem's card, "Problem 3", what is
  wrong and the change proposed. The cards of a paragraph taller than it run
  on down the margin, pushing the next ones down, so the paragraphs keep the
  height of their text; a paragraph with a card is never folded away with
  the unchanged ones. In the text, a comment is a 💬 marker, a green balloon
  holding a + when it was added since the base, or a red one holding a −
  when it was removed;
  a problem, a numbered ⚠ badge. Clicking a card, or its mark in the text,
  pins it: the words it is anchored to stay highlighted, across paragraphs
  too (only its marker ringed when those words were deleted with a tracked
  change; its whole paragraph when a Markdown file does not say where its
  text ends), and its card ringed, until it is clicked again (or Esc). A
  pinned comment highlights its words only, not a mark among them (another
  comment's, an AI badge). The top bar steps through the comments (◀ ▶, `c`
  and Shift+`c`) and the problems (◀ ⚠ ▶, `a` and Shift+`a`), each counter
  saying which. On a narrow window, and in one column, the cards sit under
  their paragraph. **Margin** in the top bar (`g`) hides the margin, the two
  versions taking its width, and brings it back.
- The columns resize with the mouse: drag the line between the old and the
  new version to share their width otherwise, or the margin's edge to widen
  or narrow it; double-click either for the default. The browser remembers
  the widths.
- Review mode (`r`, or **Review** in the top bar), to go through a revision
  item by item: down the left, what there is to review, the AI assessment,
  the changes (each with its first words removed and added), the comments
  and the problems (one the AI marked but that the report could not find
  listed as such); beside it, only the paragraph the chosen one is in, with
  its cards, a line saying which it is ("Problem 4 of 17 · paragraph 33"),
  and **Previous** and **Next** (`k`, `j`) through them all. `r` again, or
  the button, ends it.
- With more than one file, the changed files with their counts of lines
  and words added and removed (and moved lines), linked to their tables;
  buttons expand or collapse every file at once. A single file needs
  neither: its own header gives its counts.
- Each file as a collapsible table, the old version beside the new (and
  the margin, when there are comments or problems), its header sticking
  under the top bar while it scrolls. Long lines wrap instead of scrolling sideways, so
  prose stays readable. Unchanged lines beyond the context are folded into a
  "show N unchanged lines" link that reveals them.
- Changed words highlighted within changed lines; a word changed into a
  similar one ("repeat" to "repeated") has only its changed letters
  highlighted.
- A removed line that reappears elsewhere in the file (at least 20 non-space
  characters) is shown as moved, in its own colour, with "moved to line N" /
  "moved from line N": as it was (spacing aside), or edited (at least 70%
  alike by default, 55% sentence by sentence, its words compared whatever
  their order), in which case its edits are highlighted too. Hovering its
  number tells where it went ("Moved to line 137.3") or where it came from,
  and whether it was edited on the way; with `l` (off by default), a line
  joins its two places (hovering either end lights up both).
- A passage moved within a paragraph or between two (a sentence moved into
  another paragraph, two sentences swapped, a clause moved inside its
  sentence: at least four words, as alike as a moved line must be) is drawn
  as a moved line is: tinted in the colour of moves at both ends, its edits
  on the way highlighted, counted as a moved passage, not as words removed
  and added. Hovering it tells where it went or came from, a line joins its
  two places, and on paper where it went is written after it. A paragraph
  whose words all moved elsewhere counts as neither removed nor added, and
  its gutter is that of a moved line (`→`, `←`). They are found always,
  but shown only when the toolbar's "Moved passages" switch (`v`, shown when
  there are any) is on; it is off by default, each passage then removed in
  one place and added in the other, as the plain word diff shows it, and the
  counts following.
- With `--split both`, the comparison paragraph by paragraph and the one
  sentence by sentence in one report, each with its counts, a toolbar button
  (`s`) switching between them.
- Changed images (PNG, JPEG, GIF, WebP, BMP, up to 5 MB) old and new side by
  side; other binary files are listed but not shown.
- A top bar, staying at the top as the page scrolls: what is compared; the
  AI's verdict, opening its assessment; the number of changes, with `n` and
  `p` (or its arrows) to jump to the next and previous change; the comments'
  and the problems' arrows; **Review**; the view switch (`s`, with both
  views); `l` for the lines between moves and `v` for the moved passages
  (both off by default), and `u` for one column; and a **View** menu for
  the switches few use, each named with its key (hovering any item of the
  bar shows its name, what it does and its key). `u` shows one column instead of two (each changed line shows its
  old version above its new one). In the View menu: `h` for the change
  highlights, on by default (off: the background colour of changed words
  and lines gone, the text plain, the gutters of line numbers still tinted
  and signed where it changed); `f` for
  the text formatted, on by default, or plain (formatted: Markdown's syntax
  hidden, emphasis, headings, links and citations styled, a document's bold,
  italic, underline and the like shown, prose in a proportional font); `m`
  for the formatting changes, on by default: text
  of a Word or OpenDocument file whose words are the same but whose
  formatting changed (made bold or italic, underlined, struck through, made
  superscript, subscript, a link or a heading) is marked in amber, what
  changed shown on hover, the unchanged lines holding such changes
  unfolded, and each file's header says in how many lines; a spacing
  stepper, − and + either side of the value
  (or `[` and `]`, or the arrow keys on the value, an ARIA spinbutton), for
  less or more space between the paragraphs of Markdown and
  Word documents. The browser remembers the views and the spacing.
- The prose of Markdown files and Word documents is hyphenated by the rules
  of its language (`--language`: by default the one a Word or OpenDocument
  file marks each paragraph with, otherwise guessed from the file's text):
  soft hyphens placed by [pyphen](https://pypi.org/project/Pyphen/), so every
  browser breaks words the same way, on screen and on paper, without
  dictionaries of its own; copying text leaves them behind.
- A flag shows the language: one in the file header when all of a file's
  paragraphs are in the same language, or one before each paragraph's
  number when a Word or OpenDocument file marks some paragraphs with another
  language. Its tooltip names the language and how it was found: marked in
  the document, guessed from the text, or given with `--language`. The
  flags are SVGs of [flag-icons](https://github.com/lipis/flag-icons) (MIT),
  embedded in the HTML report, so they show on Windows too, which has no flag emoji.
- Printing (or saving as PDF from the browser's Print dialog) opens every
  file, drops the toolbar and buttons, keeps the colours (the light ones,
  even from a browser in dark mode), lets a long paragraph continue on the
  next page (never leaving a lone line either side) rather than leave the
  rest of a page blank, and narrows the line-number gutters so the text
  columns fit a portrait page. The AI assessment heads the report, every
  card is printed whole beside its paragraph, and where a moved line went
  is written out. A file running over
  several pages repeats its column headings (its name, old and new) at the
  top of each, and folded unchanged lines print as a quiet "⋯ N unchanged
  lines".

Changes are also marked without colour, by a sign in the line-number gutter
(`−` removed, `+` added, `~` changed, `→` `←` moved), and every changed row
tells screen readers what it is. The HTML report follows the browser's light or dark
mode and needs no network: the CSS, the JavaScript and the images are inline.
