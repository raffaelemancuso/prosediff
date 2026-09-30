# The rich test pair of the AI's fixes

`v1.md` and `v2.md` are a short text on mass and energy with what a paper
has besides plain paragraphs: an equation in a paragraph and one of its own,
curly quotes and en dashes (pandoc's smart punctuation), a footnote, a list
with bold and italic words, a table and a link. `v2` brings in errors, for
`tests/test_aidocs.py` to fix as a model would.

The Word and OpenDocument files are made from them with pandoc (3.10):

    pandoc v1.md -o v1.docx
    pandoc v2.md -o v2.docx
    pandoc v1.md -o v1.odt
    pandoc v2.md -o v2.odt
