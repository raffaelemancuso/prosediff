"""Other files sent to the AI as context: read to text, whatever their
format; a file without text refused."""

import pytest
from helpers import ANSWER, docx, fake

from prosediff.assess import AssessRequest, assess
from prosediff.cli import main
from prosediff.gui import Settings
from prosediff.pipeline import files_of
from prosediff.references import UnreadableFile, read_references, reference_text


def pdf(path, *pages):
    """A minimal PDF, each page one line of text in Helvetica."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * k} 0 R" for k in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font = 3 + 2 * len(pages)
    for k, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * k} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(out)
    return path


def test_each_format_read_to_text(tmp_path):
    guide = pdf(tmp_path / "guide.pdf", "Abstracts of 150 words at most.", "No footnotes.")
    report = tmp_path / "report.docx"
    docx(report, [[("run", "Reviewer 2 asks for robustness checks.")]])
    notes = tmp_path / "notes.md"
    notes.write_text("Cut the introduction by a fifth.\n", encoding="utf-8")
    pages = reference_text(guide)
    assert "Abstracts of 150 words at most." in pages and "No footnotes." in pages
    assert reference_text(report) == "Reviewer 2 asks for robustness checks."
    refs = read_references([guide, report, notes, " "])
    assert [r.name for r in refs] == ["guide.pdf", "report.docx", "notes.md"]
    assert refs[2].text == "Cut the introduction by a fifth."


def test_a_file_without_text_is_refused(tmp_path):
    with pytest.raises(UnreadableFile, match="no text"):
        reference_text(pdf(tmp_path / "scan.pdf", ""))
    with pytest.raises(UnreadableFile, match=r"missing\.pdf"):
        reference_text(tmp_path / "missing.pdf")


def test_the_files_sent_with_the_changes_and_with_a_review(tmp_path):
    """The other files reach the model in the message, each a <reference>
    named by its file, before the instructions, in an assessment and in a
    review alike; one that cannot be read is the assessment's error."""
    notes = tmp_path / "guide.md"
    notes.write_text("Abstracts of 150 words at most.\n", encoding="utf-8")
    request = AssessRequest("claude", instructions="Be brief.", files=(str(notes),))
    for kind, diff, document in (("value", "[-a-]{+b+}", ""), ("review", "", "b")):
        runner = fake(ANSWER)
        assess(diff, "x", request, document=document, runner=runner, kind=kind)
        prompt = runner.asked[0][2]
        block = '<reference name="guide.md">\nAbstracts of 150 words at most.\n</reference>'
        assert block in prompt and prompt.index(block) < prompt.index("<instructions>")
    missing = AssessRequest("claude", files=(str(tmp_path / "gone.pdf"),))
    made = assess("[-a-]{+b+}", "x", missing, runner=fake(ANSWER))
    assert "gone.pdf" in made.error


def test_the_files_given_on_the_command_line_and_in_the_window(tmp_path, capsys):
    """--assess-file, repeated, goes with --assess; the window sends its
    files only while its switch is on."""
    s = Settings(assess="claude", assess_files="a.pdf; b.docx;", assess_send_files=False)
    assert files_of(s) == ()
    s.assess_send_files = True
    assert files_of(s) == ("a.pdf", "b.docx")
    with pytest.raises(SystemExit):
        main(["--files", "a.md", "b.md", "--assess-file", "x.pdf"])
    assert "--assess-file" in capsys.readouterr().err
