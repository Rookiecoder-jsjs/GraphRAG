"""Tests for LLMService multimodal image attachment loading.

Covers the IMAGE_DIR realpath fence and the legacy "images/" path-prefix
strip: SQLite stores image chunk paths as "images/<doc_id>/<file>"
(predating IMAGE_DIR, when they lived under UPLOAD_DIR/images), while
IMAGE_DIR is already the images root — naive concatenation would resolve
to IMAGE_DIR/images/<doc_id>/<file> and every attachment would silently
"miss on disk", leaving the LLM unable to see retrieved images.
"""
import asyncio
import struct
import zlib

import pytest

from app.services.llm import LLMService


def _png_bytes(size: int = 16) -> bytes:
    """Synthetic size×size solid-red PNG (stdlib only)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload))
        )

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))
    idat = chunk(b"IDAT", zlib.compress(raw))
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + chunk(b"IEND", b"")


def _make_service(image_root) -> LLMService:
    """LLMService with IMAGE_DIR pinned to a temp root (no env gymnastics)."""
    service = LLMService()
    service.settings.IMAGE_DIR = str(image_root)
    return service


@pytest.mark.asyncio
async def test_load_image_data_uri_strips_images_prefix(tmp_path):
    """'images/<doc_id>/<file>' must resolve against IMAGE_DIR, not IMAGE_DIR/images."""
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "abc.png").write_bytes(_png_bytes())
    service = _make_service(tmp_path)

    uri = await service._load_image_data_uri("images/doc-1/abc.png")
    assert uri is not None
    assert uri.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_load_image_data_uri_prefixless_path(tmp_path):
    """Prefix-less '<doc_id>/<file>' storage also resolves (future format)."""
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "abc.png").write_bytes(_png_bytes())
    service = _make_service(tmp_path)

    uri = await service._load_image_data_uri("doc-1/abc.png")
    assert uri is not None


@pytest.mark.asyncio
async def test_load_image_data_uri_traversal_rejected(tmp_path):
    """Path traversal cannot escape the IMAGE_DIR fence."""
    service = _make_service(tmp_path)
    assert await service._load_image_data_uri("images/../../secret.png") is None
    assert await service._load_image_data_uri("images/doc-1/missing.png") is None


@pytest.mark.asyncio
async def test_load_image_data_uri_bad_extension(tmp_path):
    """Non-PNG/JPEG attachments are dropped, not sent to the model."""
    (tmp_path / "doc-1").mkdir(parents=True)
    (tmp_path / "doc-1" / "evil.txt").write_text("hello")
    service = _make_service(tmp_path)
    assert await service._load_image_data_uri("images/doc-1/evil.txt") is None


@pytest.mark.asyncio
async def test_load_image_data_uri_oversized_dropped(tmp_path):
    """Files above LLM_IMAGE_MAX_BYTES are dropped instead of inflating the prompt."""
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "big.png").write_bytes(b"\x00" * (2 * 1024 * 1024 + 1))
    service = _make_service(tmp_path)
    assert await service._load_image_data_uri("images/doc-1/big.png") is None
