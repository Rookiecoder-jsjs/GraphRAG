"""Tests for app.utils.md_parser — anydoc-based document conversion (ADR-008).

Regression coverage for the markitdown 0.0.1a3 → anydoc swap:

- docx must render document text, never dump raw OOXML out of the zip
  (markitdown silently did this for ~36% of sampled docx files);
- converter failures must degrade to empty content (the upload endpoint
  answers 400) and never escape as raises — markitdown's exception classes
  inherited BaseException, so `except Exception` did not catch them;
- hostile inputs must fail fast instead of hanging the to_thread worker
  (markitdown hung >25s on a zip bomb; anydoc enforces resource limits).

Test documents are assembled in-code (minimal OOXML zip / single-page PDF
with computed xref) so no binary fixtures are committed — except
``handmade-scanned.pdf`` (1KB, image-only, from firecrawl/anydoc's MIT
test suite) which locks the NeedsOcrError branch.
"""
import random
import time
import zipfile
from pathlib import Path

import pytest

from app.utils.md_parser import (
    convert_document_to_markdown,
    extract_text_fallback,
    extract_title_from_markdown,
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ----------------------------------------------------------------- builders

def _make_docx(path: Path, body_xml: str) -> None:
    """Assemble a minimal OOXML docx (zip container) with the given body XML."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships'
            '/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{_W_NS}"><w:body>{body_xml}</w:body></w:document>',
        )


def _make_pdf(path: Path, text: str) -> None:
    """Assemble a minimal single-page PDF with a correct xref table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_pos,
    )
    path.write_bytes(bytes(out))


def _docx_paragraph(text: str) -> str:
    return f'<w:p xmlns:w="{_W_NS}"><w:r><w:t>{text}</w:t></w:r></w:p>'


# -------------------------------------------------------------- happy paths

def test_markdown_passthrough_returns_content_and_h1_title(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# My Title\n\nbody text\n", encoding="utf-8")
    content, title = convert_document_to_markdown(str(f), "md")
    assert "body text" in content
    assert title == "My Title"


def test_txt_returns_content_without_title(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("plain notes\nsecond line\n", encoding="utf-8")
    content, title = convert_document_to_markdown(str(f), "txt")
    assert "plain notes" in content
    assert title is None


def test_extract_text_fallback_txt_reads_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("fallback content", encoding="utf-8")
    content, title = extract_text_fallback(str(f), "txt")
    assert content == "fallback content"
    assert title is None


def test_extract_text_fallback_binary_type_returns_empty(tmp_path):
    content, title = extract_text_fallback(str(tmp_path / "x.docx"), "docx")
    assert content == ""
    assert title is None


def test_docx_renders_text_not_raw_ooxml(tmp_path):
    """markitdown 0.0.1a3 silently dumped raw OOXML for some docx files; the
    converted output must be document text only (ADR-008 bug ①)."""
    f = tmp_path / "doc.docx"
    _make_docx(f, _docx_paragraph("Hello knowledge graph from DOCX."))
    content, _ = convert_document_to_markdown(str(f), "docx")
    assert "Hello knowledge graph from DOCX." in content
    assert "<w:" not in content
    assert "Content from the zip file" not in content


def test_pdf_renders_text(tmp_path):
    f = tmp_path / "doc.pdf"
    _make_pdf(f, "Hello knowledge graph from PDF.")
    content, _ = convert_document_to_markdown(str(f), "pdf")
    assert "Hello knowledge graph from PDF." in content


# ------------------------------------------------- failure contract (ADR-008)

@pytest.mark.parametrize(
    "kind",
    ["corrupt-pdf", "empty-docx", "random-bytes-docx", "deep-xml-docx"],
)
def test_hostile_inputs_return_empty_and_never_raise(tmp_path, kind):
    """Every conversion failure must degrade to ("", None) — the upload
    endpoint's 400 path — and fail fast. Under markitdown 0.0.1a3 the corrupt
    PDF raised a BaseException subclass straight through `except Exception`
    (bug ②), and the deep-XML/zip-bomb class could hang the worker (bug ③)."""
    f = tmp_path / {"corrupt-pdf": "f.pdf", "empty-docx": "f.docx",
                    "random-bytes-docx": "f.docx", "deep-xml-docx": "f.docx"}[kind]
    ext = f.suffix.lstrip(".")
    if kind == "corrupt-pdf":
        f.write_bytes(b"%PDF-1.4\ntrailing garbage, not a real pdf\n")
    elif kind == "empty-docx":
        f.write_bytes(b"")
    elif kind == "random-bytes-docx":
        f.write_bytes(random.Random(42).randbytes(4096))  # deterministic
    else:  # deep-xml-docx: XML nesting past anydoc's 256-level resource limit
        _make_docx(f, _docx_paragraph("<a>" * 400 + "x" + "</a>" * 400))

    t0 = time.perf_counter()
    content, title = convert_document_to_markdown(str(f), ext)
    elapsed = time.perf_counter() - t0

    assert content == ""
    assert title is None
    assert elapsed < 10  # seconds; hang regression would blow way past this


def test_scanned_pdf_returns_empty():
    """Image-only pages raise NeedsOcrError inside md_parser, which degrades
    to ("", None) — same 400 routing as other failures (fixture: image-only
    PDF from firecrawl/anydoc's MIT test suite)."""
    fixture = Path(__file__).parent / "fixtures" / "handmade-scanned.pdf"
    content, title = convert_document_to_markdown(str(fixture), "pdf")
    assert content == ""
    assert title is None


# ------------------------------------------------------------------- titles

def test_extract_title_from_markdown_h1():
    assert extract_title_from_markdown("intro\n\n# The Title\n\nmore") == "The Title"


def test_extract_title_from_markdown_none_without_h1():
    assert extract_title_from_markdown("## only h2\nplain") is None
