"""Helpers shared by several test files."""

import re
import zipfile
from xml.sax.saxutils import escape

END = '[]{.comment-end id="3"}'


NOTE = '[Too long.]{.comment-start id="3" author="Anna" date="2026-09-23T23:40:00Z"}'


def docx(path, paragraphs, comment=None):
    """A minimal Word document; paragraphs are lists of (kind, text) runs,
    kind being "run", "ins" or "del"; comment adds one comment on the first
    paragraph."""
    auth = 'w:author="A" w:date="2026-01-01T00:00:00Z"'

    def run(kind, text):
        t = escape(text)
        if kind == "del":
            return (
                f'<w:del w:id="9" {auth}><w:r><w:delText xml:space="preserve">'
                f"{t}</w:delText></w:r></w:del>"
            )
        r = f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'
        return f'<w:ins w:id="8" {auth}>{r}</w:ins>' if kind == "ins" else r

    body = ""
    for k, p in enumerate(paragraphs):
        runs = "".join(run(kind, text) for kind, text in p)
        if comment and k == 0:
            runs = (
                '<w:commentRangeStart w:id="0"/>' + runs + '<w:commentRangeEnd w:id="0"/>'
                '<w:r><w:commentReference w:id="0"/></w:r>'
            )
        body += f"<w:p>{runs}</w:p>"
    comments = None
    if comment:
        comments = (
            '<w:comment w:id="0" w:author="Anna" w:date="2026-01-01T00:00:00Z">'
            f"<w:p><w:r><w:t>{escape(comment)}</w:t></w:r></w:p></w:comment>"
        )
    return docx_xml(path, body, comments=comments)


def docx_xml(path, body, footnotes=None, comments=None, styles=None):
    """A Word document from raw XML: body is the content of w:body, footnotes
    the w:footnote elements, comments the w:comment elements, styles the
    w:style elements (for what python-docx cannot write: tracked changes,
    footnotes, equations)."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    m = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    ns = f'xmlns:w="{w}" xmlns:m="{m}"'
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    parts = {"document": (f"{ct}.document.main+xml", None)}
    if footnotes is not None:
        parts["footnotes"] = (f"{ct}.footnotes+xml", f"{rel}/footnotes")
    if comments is not None:
        parts["comments"] = (f"{ct}.comments+xml", f"{rel}/comments")
    if styles is not None:
        parts["styles"] = (f"{ct}.styles+xml", f"{rel}/styles")
    types = "".join(
        f'<Override PartName="/word/{name}.xml" ContentType="{t}"/>'
        for name, (t, _) in parts.items()
    )
    doc_rels = "".join(
        f'<Relationship Id="rId{k}" Type="{r}" Target="{name}.xml"/>'
        for k, (name, (_, r)) in enumerate(parts.items())
        if r
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/'
            'vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{types}</Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{doc_rels}</Relationships>",
        )
        z.writestr("word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>")
        if footnotes is not None:
            z.writestr("word/footnotes.xml", f"<w:footnotes {ns}>{footnotes}</w:footnotes>")
        if comments is not None:
            z.writestr("word/comments.xml", f"<w:comments {ns}>{comments}</w:comments>")
        if styles is not None:
            z.writestr("word/styles.xml", f"<w:styles {ns}>{styles}</w:styles>")
    return path


def kinds(rows):
    return [r.kind for r in rows]


def odt_xml(path, body, styles=""):
    """An OpenDocument text from raw XML: body is the content of office:text,
    styles the automatic styles (what odfdo does not write directly:
    tracked changes, comments with dates)."""
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:loext="urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0" '
        'office:version="1.3"'
    )
    mime = "application/vnd.oasis.opendocument.text"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", mime, compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/manifest.xml",
            '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:'
            'manifest:1.0" manifest:version="1.3">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>'
            '<manifest:file-entry manifest:full-path="content.xml" '
            'manifest:media-type="text/xml"/></manifest:manifest>',
        )
        z.writestr(
            "content.xml",
            f"<office:document-content {ns}><office:automatic-styles>{styles}"
            f"</office:automatic-styles><office:body><office:text>{body}</office:text>"
            "</office:body></office:document-content>",
        )
    return path


def run(text, props=""):
    """A Word run of text, props its w:rPr."""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def strip_tags(html):
    """The text of some HTML, its tags taken out."""
    return re.sub(r"<[^>]+>", "", html)


def two_files(tmp_path, old, new, suffix=".md"):
    """Two versions of a text file, a and b, written as UTF-8."""
    a, b = tmp_path / f"a{suffix}", tmp_path / f"b{suffix}"
    a.write_text(old, encoding="utf-8")
    b.write_text(new, encoding="utf-8")
    return a, b


def two_folders(tmp_path, name, old, new):
    """Two folders, old and new, each holding its version of the file name."""
    for side, text in (("old", old), ("new", new)):
        (tmp_path / side).mkdir()
        (tmp_path / side / name).write_text(text, encoding="utf-8")
    return tmp_path / "old", tmp_path / "new"


def markdown_of(data: bytes, name: str = "a.docx", changes: str = "accept") -> str:
    """A Word document's (or, named .odt, an OpenDocument text's) body as
    Markdown, as prosediff reads it."""
    from prosediff.document import to_markdown
    from prosediff.sources import read_document

    return to_markdown(read_document(data, name, changes))
