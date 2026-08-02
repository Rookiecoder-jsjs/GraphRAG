"""Tests for app/utils/image_extractor.py (PDF + DOCX embedded images).

Covers limits (size filter, dedup), heading capture (DOCX), page labels
(PDF), corrupt-byte tolerance, and the dispatch by file_type.

Run: cd backend && ../.venv/Scripts/python.exe tests/test_image_extractor.py
Exit code 0 = all passed, 1 = any failed. Also pytest-discoverable via the
sync mirror at the bottom.
"""
import io
import os
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, ".")

import fitz  # PyMuPDF — already a project dep
from app.utils.image_extractor import (  # noqa: E402
    extract_images_from_document,
    extract_images_from_docx,
    extract_images_from_pdf,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def _png_bytes(size: int = 200) -> bytes:
    """Solid red PNG; size is the square side length."""
    def chunk(t, p):
        return (
            struct.pack(">I", len(p)) + t + p
            + struct.pack(">I", zlib.crc32(t + p) & 0xFFFFFFFF)
        )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\xff\x00\x00" * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


# -------------------- PDF -----------------------------------------------------

def _make_pdf(inserts: list[tuple[int, bytes]]) -> str:
    """Build a single-page PDF embedding the given (side, png_bytes) tuples.

    Each insert goes onto a fresh page so we can verify page labelling.
    """
    doc = fitz.open()
    for side, png in inserts:
        page = doc.new_page()
        # Place the image at top-left, side x side.
        page.insert_image(fitz.Rect(0, 0, side, side), stream=png)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    doc.save(tmp.name)
    doc.close()
    return tmp.name


def _case_pdf_basic():
    big = _png_bytes(200)
    small = _png_bytes(50)  # below MIN_DIMENSION=120 → filtered
    path = _make_pdf([(200, big), (260, small), (200, big)])  # last = dup of first
    try:
        out = extract_images_from_pdf(path)
        check(
            "PDF: dedups same image, filters below min-dim, labels page",
            len(out) == 1
            and out[0].width == 200 and out[0].height == 200
            and out[0].page == 1  # duplicates + small icons are all on page 1
            and out[0].heading_path == ["Page 1"],
            f"got {[(e.width, e.height, e.page, e.heading_path) for e in out]}",
        )
    finally:
        os.unlink(path)


def _case_pdf_multiple_pages():
    img1 = _png_bytes(200)
    img2 = _png_bytes(180)  # DIFFERENT bytes → not deduped
    p1 = _make_pdf([(200, img1)])
    p2 = _make_pdf([(200, img1), (180, img2)])  # one insert per page
    try:
        out1 = extract_images_from_pdf(p1)
        out2 = extract_images_from_pdf(p2)
        check(
            "PDF: per-page page labels survive (1 vs 1,2)",
            out1[0].page == 1 and len(out1) == 1
            and [e.page for e in out2] == [1, 2] and len(out2) == 2,
            f"out1={[e.page for e in out1]} out2={[e.page for e in out2]}",
        )
    finally:
        os.unlink(p1); os.unlink(p2)


def _case_pdf_corrupt_path():
    check(
        "PDF: nonexistent / corrupt file returns [] without raising",
        extract_images_from_pdf("/no/such/file.pdf") == []
        and extract_images_from_pdf(__file__) == [],  # a Python file is not a PDF
    )


# -------------------- DOCX ---------------------------------------------------

def _make_docx(media: list[tuple[str, str, bytes]], include_headings: bool = True) -> str:
    """Build a minimal DOCX.

    `media` is a list of (rId, path inside word/, png bytes) triples.
    When include_headings is True, each blip paragraph is preceded by a
    Heading1 carrying a unique title so we can assert the heading_stack.
    """
    paras = []
    rels = [
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{path}"/>'
        for rid, path, _ in media
    ]
    rid_to_data = {rid: data for rid, _, data in media}

    body_paras = []
    for i, (rid, path, _) in enumerate(media, start=1):
        if include_headings:
            body_paras.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading{i}"/></w:pPr>'
                f'<w:r><w:t>Section {i}</w:t></w:r></w:p>'
            )
        body_paras.append(
            '<w:p><w:r><w:drawing>'
            '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData>'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rid}"/></pic:blipFill>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        )

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<w:body>{"".join(body_paras)}</w:body></w:document>'
    ).encode()
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels) + '</Relationships>'
    ).encode()
    ct_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '</Types>'
    ).encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct_xml)
        z.writestr("word/_rels/document.xml.rels", rels_xml)
        z.writestr("word/document.xml", doc_xml)
        for rid, path, data in media:
            # path is e.g. "media/foo.png" → zip path "word/media/foo.png"
            zip_path = path if path.startswith("word/") else f"word/{path}"
            z.writestr(zip_path, data)
    return buf.getvalue()


def _case_docx_heading_stack():
    big = _png_bytes(150)
    small = _png_bytes(40)  # filtered by min-dimension
    docx = _make_docx([
        ("rId1", "media/a.png", big),
        ("rId2", "media/b.png", small),
        ("rId3", "media/c.png", big),  # dedup with rId1
    ])
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False); tmp.close()
    Path(tmp.name).write_bytes(docx)
    try:
        out = extract_images_from_docx(tmp.name)
        # Heading1 "Section 1" precedes rId1, "Section 2" precedes rId2,
        # "Section 3" precedes rId3. Only rId1 should survive (small is
        # filtered, rId3 is a duplicate of rId1).
        check(
            "DOCX: heading_stack per image, dedup + size filter",
            len(out) == 1
            and out[0].heading_path == ["Section 1"]
            and out[0].width == 150,
            f"got {[(e.width, e.heading_path) for e in out]}",
        )
    finally:
        os.unlink(tmp.name)


def _case_docx_no_headings():
    big = _png_bytes(150)
    docx = _make_docx([("rId1", "media/a.png", big)], include_headings=False)
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False); tmp.close()
    Path(tmp.name).write_bytes(docx)
    try:
        out = extract_images_from_docx(tmp.name)
        check(
            "DOCX: no Heading → empty heading_path (not a fake one)",
            len(out) == 1 and out[0].heading_path == [],
        )
    finally:
        os.unlink(tmp.name)


def _case_docx_corrupt_zip():
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False); tmp.close()
    Path(tmp.name).write_bytes(b"not a real zip")
    try:
        check(
            "DOCX: corrupt bytes return [] without raising",
            extract_images_from_docx(tmp.name) == [],
        )
    finally:
        os.unlink(tmp.name)


def _case_dispatch():
    big = _png_bytes(200)
    pdf = _make_pdf([(200, big)])
    docx = _make_docx([("rId1", "media/a.png", big)], include_headings=False)
    tmp_docx = tempfile.NamedTemporaryFile(suffix=".docx", delete=False); tmp_docx.close()
    Path(tmp_docx.name).write_bytes(docx)
    try:
        check(
            "dispatch: pdf / docx extract; txt / md / image return []",
            len(extract_images_from_document(pdf, "pdf")) == 1
            and len(extract_images_from_document(tmp_docx.name, "docx")) == 1
            and extract_images_from_document(tmp_docx.name, "txt") == []
            and extract_images_from_document(tmp_docx.name, "md") == []
            and extract_images_from_document(tmp_docx.name, "image") == [],
        )
    finally:
        os.unlink(pdf); os.unlink(tmp_docx.name)


print("PDF")
_case_pdf_basic()
_case_pdf_multiple_pages()
_case_pdf_corrupt_path()
print("\nDOCX")
_case_docx_heading_stack()
_case_docx_no_headings()
_case_docx_corrupt_zip()
print("\nDispatch")
_case_dispatch()


def test_all_image_extractor_checks_passed():
    """pytest mirror — all cases above already ran at import time."""
    assert not _failures, (
        f"{len(_failures)} image extractor checks failed: {', '.join(_failures)}"
    )


print()
if _failures:
    print(f"\033[91m{len(_failures)} FAILED:\033[0m " + ", ".join(_failures))
    sys.exit(1)
print(f"\033[92mAll checks passed.\033[0m")