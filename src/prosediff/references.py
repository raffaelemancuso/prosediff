"""Other files sent to the AI as context (--assess-file; in the window,
"Send other files"): a journal's guidelines, a reviewer's report, a cited
paper. Each is read to plain text: a PDF's text layer (pypdfium2, Google's
PDFium), a Word or OpenDocument document as prosediff's Markdown with its
tracked changes accepted, any other file as text."""

from dataclasses import dataclass
from pathlib import Path

from prosediff.diff import AUTO_ENCODING, decode_text
from prosediff.document import to_markdown
from prosediff.sources import is_document, read_document, suffix_of

# How much of them the AI is sent, in all (characters): the rest cut, the
# cut said.
MAX_REFERENCE_CHARS = 200_000


class UnreadableFile(ValueError):
    """A file to send that cannot be read."""


@dataclass(frozen=True)
class Reference:
    name: str
    text: str


def pdf_text(data: bytes) -> str:
    """The text of a PDF, page by page; "" for one with no text layer (a scan)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return "\n\n".join(pages)
    finally:
        pdf.close()


def reference_text(path: str | Path) -> str:
    """A file's text as the AI is sent it; UnreadableFile when it cannot be
    read, or holds no text."""
    path = Path(path).expanduser()
    try:
        data = path.read_bytes()
        if suffix_of(path) == ".pdf":
            text = pdf_text(data)
        elif is_document(path.name):
            text = to_markdown(read_document(data, path.name))
        else:
            text = decode_text(data, AUTO_ENCODING)[0]
    except Exception as e:  # missing, unreadable, a broken PDF or document
        raise UnreadableFile(f"{path.name}: {e}") from e
    if not text.strip():
        raise UnreadableFile(f"{path.name}: no text in it (a scanned PDF needs OCR first)")
    return text.replace("\r\n", "\n").strip()


def read_references(paths) -> list[Reference]:
    """The files of paths, read, each named by its file name."""
    return [Reference(Path(p).name, reference_text(p)) for p in paths if str(p).strip()]
