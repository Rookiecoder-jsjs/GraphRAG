"""Extract embedded images from PDF and DOCX for multimodal indexing.

Each supported format is wrapped in a try/except so a single corrupt file
never blocks ingestion — extraction is auxiliary, text is primary. Limits
(per-document count, minimum dimension, max byte size, intra-doc sha256
deduplication) live here so the calling pipeline can stay clean.

PyMuPDF is AGPL-3.0; pypdfium2 (BSD) is the documented fallback if
licensing becomes a concern — the public entry point is `extract_images_
from_document`, so swapping the engine only touches this module.
"""
from __future__ import annotations

import hashlib
import io
import logging
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

import fitz  # PyMuPDF — see module docstring for license note

logger = logging.getLogger(__name__)

# Same mapping documents.py uses for serving — keep in sync.
MEDIA_TYPE_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Extraction limits. Conservative defaults: filtering icons / decoration
# (very small images) and bounding the API spend per document.
MAX_IMAGES_PER_DOC = 20
MIN_DIMENSION = 120       # both axes; overrides via env if you raise it later
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # mirrors EMBEDDING_IMAGE_MAX_FILE_SIZE


@dataclass
class ExtractedImage:
    """One image harvested from a document.

    `data` is the raw image bytes (PNG/JPEG — converted if necessary during
    extraction so the embedding service can send a clean data URI). The
    sha256 is computed over these bytes and is used both for deduplication
    within a single document and as the deterministic input to the
    uuid5-based chunk id in the calling pipeline.
    """
    data: bytes
    ext: str
    media_type: str
    sha256: str
    width: int
    height: int
    page: Optional[int] = None
    heading_path: List[str] = field(default_factory=list)


def _hash(bytes_data: bytes) -> str:
    return hashlib.sha256(bytes_data).hexdigest()


def _png_size_via_ihdr(data: bytes) -> Optional[tuple[int, int]]:
    """Cheap width/height probe for PNG headers — used as a fast filter
    without decoding the full image.
    """
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        # IHDR is the first chunk, length 13, width@0, height@4 (big-endian uint32).
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    except Exception:
        return None


def _jpeg_size_via_soi(data: bytes) -> Optional[tuple[int, int]]:
    """Best-effort width/height parse from JPEG SOF markers — no PIL dep.

    JPEG dimensions live in SOF0/SOF2 markers (0xC0/0xC2). For the demo
    sizes we care about, this is enough to filter small icons before
    decoding — if it fails, the caller falls through to PyMuPDF's Pixmap.
    """
    try:
        if data[:2] != b"\xff\xd8":
            return None
        i = 2
        while i < len(data) - 8:
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            i += 2
            if marker == 0xD8 or marker == 0xD9:
                continue
            length = (data[i] << 8) | data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                # Skip precision byte, then height, width (big-endian uint16).
                _, h, w = struct.unpack(">BHH", data[i + 2:i + 7])
                return w, h
            i += length
        return None
    except Exception:
        return None


def _resize_dim_for_filter(data: bytes, ext: str) -> Optional[tuple[int, int]]:
    """Return (width, height) from PNG/JPEG headers, or None."""
    if ext == ".png":
        return _png_size_via_ihdr(data)
    if ext in (".jpg", ".jpeg"):
        return _jpeg_size_via_soi(data)
    return None


# -------------------- PDF (PyMuPDF) -------------------------------------------

def extract_images_from_pdf(
    path: str,
    *,
    max_images: int = MAX_IMAGES_PER_DOC,
    min_dimension: int = MIN_DIMENSION,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> List[ExtractedImage]:
    """Walk every page and pull each embedded image.

    Skips SMask-bearing entries (their base image comes through separately)
    and CMYK images (converted to RGB first — only RGB / Gray are useful
    for visual embeddings). Resizes are NOT performed; the size filter
    runs against the native pixels.
    """
    out: List[ExtractedImage] = []
    seen_hashes: set[str] = set()
    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.warning("extract_images_from_pdf: open failed (%s) — %s", path, e)
        return out

    try:
        for page_no, page in enumerate(doc, start=1):
            if len(out) >= max_images:
                break
            try:
                images = page.get_images(full=True)
            except Exception as e:
                logger.debug("PDF page %d get_images failed: %s", page_no, e)
                continue
            for img_info in images:
                if len(out) >= max_images:
                    break
                xref = img_info[0]
                smask = img_info[1]
                # Skip soft-mask entries — their base image follows
                # separately and we'd otherwise extract a meaningless gray
                # mask.
                if smask:
                    continue
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception as e:
                    logger.debug("xref %d Pixmap failed: %s", xref, e)
                    continue

                # CMYK / non-RGB → convert so embedding gets clean data.
                if pix.colorspace and pix.colorspace.name not in ("DeviceRGB", "DeviceGray"):
                    try:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    except Exception:
                        continue

                width, height = pix.width, pix.height
                if width < min_dimension or height < min_dimension:
                    continue

                # Always re-encode to PNG — uniform format, predictable
                # size, and avoids issues with exotic encodings.
                try:
                    data = pix.tobytes("png")
                except Exception:
                    continue
                if len(data) > max_bytes:
                    continue

                sha = _hash(data)
                if sha in seen_hashes:
                    continue
                seen_hashes.add(sha)

                out.append(ExtractedImage(
                    data=data, ext=".png", media_type="image/png",
                    sha256=sha, width=width, height=height, page=page_no,
                    heading_path=[f"Page {page_no}"],
                ))
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return out


# -------------------- DOCX (stdlib zipfile + xml) -----------------------------

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _heading_level(p_style_val: str) -> Optional[int]:
    """Extract a 1-based heading level from a pStyle value, or None."""
    if not p_style_val:
        return None
    if p_style_val.startswith("Heading"):
        tail = p_style_val[len("Heading"):]
        if tail.isdigit():
            return int(tail)
    # Also accept common translations (e.g. TOCHeading).
    return None


def _paragraph_text(p_elem: ET.Element) -> str:
    """Concatenate <w:t> texts within a <w:p>, ignoring other markup."""
    return "".join(t.text or "" for t in p_elem.iter(f"{_W_NS}t")).strip()


def extract_images_from_docx(
    path: str,
    *,
    max_images: int = MAX_IMAGES_PER_DOC,
    min_dimension: int = MIN_DIMENSION,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> List[ExtractedImage]:
    """Walk document.xml in order, snapshotting the heading stack at every
    ``a:blip`` we encounter. Relies ONLY on stdlib (zipfile + xml).
    """
    out: List[ExtractedImage] = []
    seen_hashes: set[str] = set()
    try:
        with zipfile.ZipFile(path, "r") as z:
            try:
                rels_xml = z.read("word/_rels/document.xml.rels")
            except KeyError:
                return out
            rels_root = ET.fromstring(rels_xml)
            rid_to_target = {}
            for rel in rels_root.iter("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
                rid_to_target[rel.get("Id")] = rel.get("Target")

            try:
                doc_xml = z.read("word/document.xml")
            except KeyError:
                return out
            doc_root = ET.fromstring(doc_xml)
    except Exception as e:
        logger.warning("extract_images_from_docx: open failed (%s) — %s", path, e)
        return out

    heading_stack: List[str] = []
    body = doc_root.find(f"{_W_NS}body")
    if body is None:
        return out

    for p in body.iter(f"{_W_NS}p"):
        if len(out) >= max_images:
            break
        # 1) Update heading stack with this paragraph's pStyle.
        p_style = p.find(f"{_W_NS}pPr/{_W_NS}pStyle")
        p_style_val = p_style.get(f"{_W_NS}val") if p_style is not None else None
        level = _heading_level(p_style_val or "")
        if level is not None:
            text = _paragraph_text(p)
            heading_stack = heading_stack[: max(level - 1, 0)] + [text or (p_style_val or "Heading")]

        # 2) Find every image embedded in this paragraph.
        for blip in p.iter(f"{_A_NS}blip"):
            if len(out) >= max_images:
                break
            embed_attr = f"{_R_NS}embed"
            r_id = blip.get(embed_attr) or blip.get("embed")
            if not r_id:
                continue
            target = rid_to_target.get(r_id)
            if not target:
                continue
            # Targets are relative to word/ (the rels base).
            media_path = target if target.startswith("word/") else f"word/{target.lstrip('/')}"
            try:
                with zipfile.ZipFile(path, "r") as z:
                    data = z.read(media_path)
            except (KeyError, zipfile.BadZipFile):
                continue
            if len(data) > max_bytes:
                continue
            ext = Path(media_path).suffix.lower()
            media_type = MEDIA_TYPE_BY_EXT.get(ext)
            if not media_type:
                continue  # skip emf/wmf/etc — the embedding service only takes PNG/JPEG

            # Pre-filter by cheap header dimensions; fall back to letting
            # the size constraint drop it.
            dims = _resize_dim_for_filter(data, ext)
            if dims:
                w, h = dims
                if w < min_dimension or h < min_dimension:
                    continue

            sha = _hash(data)
            if sha in seen_hashes:
                continue
            seen_hashes.add(sha)

            out.append(ExtractedImage(
                data=data, ext=ext, media_type=media_type,
                sha256=sha,
                width=dims[0] if dims else 0,
                height=dims[1] if dims else 0,
                page=None,
                heading_path=list(heading_stack),
            ))

    return out


# -------------------- Public entry --------------------------------------------

def extract_images_from_document(
    path: str, file_type: str
) -> List[ExtractedImage]:
    """Dispatch by file_type. Never raises — returns [] on any failure
    (with a warning logged). For unsupported types, returns [] quietly.
    """
    ext = (file_type or "").lower().lstrip(".")
    try:
        if ext == "pdf":
            return extract_images_from_pdf(path)
        if ext == "docx":
            return extract_images_from_docx(path)
        return []  # txt/md/markdown/image: nothing to extract
    except Exception as e:
        logger.warning(
            "extract_images_from_document(%s, %s): %s", path, file_type, e
        )
        return []