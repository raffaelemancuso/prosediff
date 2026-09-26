"""A Comparison as text: a unified diff (.diff), or a word diff (.wdiff),
for when an HTML report is not wanted (to read in an editor, to mail, to keep beside
the files).

Both are made from the HTML report's own line pairing (FileDiff.pairs, from
diff.line_pairs): the lines the HTML report compares, paired as its rows pair them.
In the unified diff an edited line is written as its removal followed by its
new text, where the HTML report shows the two face to face; in the word diff, as
git diff --word-diff=plain writes it, as one line, the words changed marked
[-removed-]{+added+} (paired as in the HTML report, diff.word_ops), a line removed
or added whole marked as a whole, and unchanged lines as they are, without
the unified diff's leading space. The hunk headers are the same.

A text file's lines are its own, and its unified diff a patch git and patch
can apply. The lines of Markdown files and Word and OpenDocument documents
are those the HTML report compares: one per paragraph (or sentence, with
--by-sentence), blank lines left out, a document's formatting in Markdown,
comments in CriticMarkup ({>>Author (date): text<<}, only those added or
removed; none at all with diff.compare's drop_comments), tracked changes too
({++inserted++}, {--deleted--}); their diff is for reading, not for
applying.
"""

from prosediff.diff import CONTEXT, Comparison, FileDiff, comment_text, word_ops

Pair = tuple[int | None, int | None]
# The text formats, by the name --format gives them.
TEXT_FORMATS = ("diff", "wdiff")


def _hunks(pairs: list[Pair], changed: list[bool], context: int | None) -> list[range]:
    """The stretches of pairs each hunk covers: the changed pairs, with up to
    context unchanged ones around them; those that touch or overlap merge.
    context None: the whole file, in one hunk."""
    if not any(changed):
        return []
    if context is None:
        return [range(len(pairs))]
    out: list[range] = []
    for k in (k for k, c in enumerate(changed) if c):
        start, end = max(0, k - context), min(len(pairs), k + context + 1)
        if out and start <= out[-1].stop:
            out[-1] = range(out[-1].start, max(end, out[-1].stop))
        else:
            out.append(range(start, end))
    return out


def _side_range(before: int, count: int) -> str:
    """A side of a hunk header: its first line (1-based) and count; an empty
    side names the line after which the other's lines go."""
    start = before + 1 if count else before
    return f"{start}" if count == 1 else f"{start},{count}"


def word_line(old: str, new: str) -> str:
    """An edited line as git's word diff writes it: [-removed-]{+added+}."""
    out = []
    for op, o1, o2, n1, n2 in word_ops(old, new):
        if op == "equal":
            out.append(new[n1:n2])
            continue
        if o2 > o1:
            out.append(f"[-{old[o1:o2]}-]")
        if n2 > n1:
            out.append(f"{{+{new[n1:n2]}+}}")
    return "".join(out)


def file_diff(f: FileDiff, context: int | None = CONTEXT, fmt: str = "diff") -> list[str]:
    """One file's part of the diff (fmt "diff") or word diff ("wdiff"), its
    lines without line ends; context None for every line of the file."""
    old = f"a/{f.old_path}" if f.old_path else "/dev/null"
    new = f"b/{f.new_path}" if f.new_path else "/dev/null"
    if f.binary:
        note = f" ({f.note})" if f.note else ""
        return [f"Binary files {old} and {new} differ{note}"]
    a = [comment_text(x, f.comments) for x in f.old_lines]
    b = [comment_text(x, f.comments) for x in f.new_lines]
    changed = [i is None or j is None or a[i] != b[j] for i, j in f.pairs]
    hunks = _hunks(f.pairs, changed, context)
    if not hunks:
        return []
    out = [f"--- {old}", f"+++ {new}"]
    # the old and new lines before each pair
    seen_old = seen_new = 0
    before = []
    for i, j in f.pairs:
        before.append((seen_old, seen_new))
        seen_old += i is not None
        seen_new += j is not None
    for h in hunks:
        n_old = sum(f.pairs[k][0] is not None for k in h)
        n_new = sum(f.pairs[k][1] is not None for k in h)
        old_before, new_before = before[h.start]
        out.append(f"@@ -{_side_range(old_before, n_old)} +{_side_range(new_before, n_new)} @@")
        for k in h:
            i, j = f.pairs[k]
            if fmt == "wdiff":
                if not changed[k]:
                    out.append(b[j])
                elif i is None:
                    out.append(f"{{+{b[j]}+}}")
                elif j is None:
                    out.append(f"[-{a[i]}-]")
                else:
                    out.append(word_line(a[i], b[j]))
            elif not changed[k]:
                out.append(f" {b[j]}")
            else:
                if i is not None:
                    out.append(f"-{a[i]}")
                if j is not None:
                    out.append(f"+{b[j]}")
    return out


def unified(comparison: Comparison, context: int | None = CONTEXT, fmt: str = "diff") -> str:
    """The unified diff (fmt "diff") or word diff ("wdiff") of every changed
    file."""
    if fmt not in TEXT_FORMATS:
        raise ValueError(f"format must be one of {TEXT_FORMATS}, not {fmt!r}")
    lines = [line for f in comparison.files for line in file_diff(f, context, fmt)]
    return "\n".join(lines) + "\n" if lines else ""
