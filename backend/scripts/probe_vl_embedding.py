"""Phase 0 probe: verify qwen3-vl-embedding on DashScope before migrating.

Throwaway diagnostic — NOT part of the app, safe to delete after use.

Answers the pre-migration questions for the multimodal knowledge base:
  T1  plain-string input on the OpenAI-compatible endpoint
  T2  content-item input [{"text": ...}]
  T3  image input via public URL [{"image": <url>}]
  T4  image input via base64 data URI [{"image": "data:image/png;base64,..."}]
  T5  batched text input
  T6  `dimensions` parameter (MRL) — can we request 1024 to match the
      existing EMBEDDING_DIM without recreating the Chroma collection?
  T7  native DashScope multimodal endpoint — text embedding (the shape
      this model actually supports)
  T8  native image via public URL
  T9  native image via base64 data URI
  T10 native batched text (two content items in one call)
  T11 native dimension control (MRL) — request 1024 to match the
      current EMBEDDING_DIM

Reads BAILIAN_API_KEY from backend/.env (value is NEVER printed).
All inputs are synthetic. Run from anywhere:

    .venv/Scripts/python backend/scripts/probe_vl_embedding.py
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL = "qwen3-vl-embedding"
COMPAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
NATIVE_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
    "multimodal-embedding/multimodal-embedding"
)
# Public sample image from Alibaba DashScope docs (stable, small).
PUBLIC_IMAGE_URL = (
    "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
)
TEXT_A = "探针文本:知识图谱跨模态检索"
TEXT_B = "probe text: cross-modal retrieval"
TIMEOUT_SECONDS = 90


def _load_api_key() -> str:
    """Read BAILIAN_API_KEY from backend/.env without printing it."""
    env_path = BACKEND_DIR / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "BAILIAN_API_KEY":
                return value.strip().strip("'\"")
    return os.environ.get("BAILIAN_API_KEY", "")


def _synthetic_png_b64(size: int = 64) -> str:
    """Build a size x size solid-red PNG with the stdlib only (synthetic)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + b"\xff\x00\x00" * size  # filter byte + red pixels
    idat = zlib.compress(row * size)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def _post(url: str, api_key: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")[:300]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw_error": raw}
        return e.code, parsed
    except urllib.error.URLError as e:
        return 0, {"transport_error": str(e.reason)}


def _dims_from_compat(data: Dict[str, Any]) -> List[int]:
    dims: List[int] = []
    for item in data.get("data") or []:
        emb = item.get("embedding")
        if isinstance(emb, list):
            dims.append(len(emb))
    return dims


def _dims_from_native(data: Dict[str, Any]) -> List[int]:
    dims: List[int] = []
    for item in (data.get("output") or {}).get("embeddings") or []:
        emb = item.get("embedding")
        if isinstance(emb, list):
            dims.append(len(emb))
    return dims


def _err_detail(code: int, data: Dict[str, Any]) -> str:
    return f"HTTP {code}, body={json.dumps(data, ensure_ascii=False)[:220]}"


def main() -> int:
    api_key = _load_api_key()
    if not api_key:
        print("[FAIL] BAILIAN_API_KEY not found in backend/.env or environment")
        return 1
    print(f"[info] API key loaded (len={len(api_key)}; value never printed)")
    print(f"[info] model={MODEL}")

    results: List[Tuple[str, bool, str]] = []
    all_dims: Dict[str, int] = {}

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    # T1 — plain string input (classic OpenAI embeddings shape)
    code, data = _post(COMPAT_URL, api_key, {"model": MODEL, "input": TEXT_A})
    dims = _dims_from_compat(data)
    if code == 200 and dims:
        all_dims["T1-text"] = dims[0]
    record(
        "T1 compat plain-string input",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}, usage={data.get('usage')}"
        if code == 200 else _err_detail(code, data),
    )

    # T2 — content-item input [{"text": ...}]
    code, data = _post(
        COMPAT_URL, api_key, {"model": MODEL, "input": [{"text": TEXT_A}]}
    )
    dims = _dims_from_compat(data)
    if code == 200 and dims:
        all_dims["T2-text-item"] = dims[0]
    record(
        "T2 compat content-item [{'text': ...}]",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}" if code == 200 else _err_detail(code, data),
    )

    # T3 — image via public URL
    code, data = _post(
        COMPAT_URL, api_key, {"model": MODEL, "input": [{"image": PUBLIC_IMAGE_URL}]}
    )
    dims = _dims_from_compat(data)
    if code == 200 and dims:
        all_dims["T3-image-url"] = dims[0]
    record(
        "T3 compat image via public URL",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}, usage={data.get('usage')}"
        if code == 200 else _err_detail(code, data),
    )

    # T4 — image via base64 data URI (synthetic 64x64 red PNG)
    data_uri = f"data:image/png;base64,{_synthetic_png_b64(64)}"
    code, data = _post(
        COMPAT_URL, api_key, {"model": MODEL, "input": [{"image": data_uri}]}
    )
    dims = _dims_from_compat(data)
    if code == 200 and dims:
        all_dims["T4-image-b64"] = dims[0]
    record(
        "T4 compat image via base64 data URI",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}" if code == 200 else _err_detail(code, data),
    )

    # T5 — batched text input
    code, data = _post(
        COMPAT_URL, api_key, {"model": MODEL, "input": [TEXT_A, TEXT_B]}
    )
    dims = _dims_from_compat(data)
    record(
        "T5 compat batched text (2 items)",
        code == 200 and len(dims) == 2,
        f"HTTP {code}, got {len(dims)} embeddings, dims={dims}"
        if code == 200 else _err_detail(code, data),
    )

    # T6 — dimensions parameter (MRL): request 1024 to match EMBEDDING_DIM
    code, data = _post(
        COMPAT_URL,
        api_key,
        {"model": MODEL, "input": TEXT_A, "dimensions": 1024},
    )
    dims = _dims_from_compat(data)
    t6_ok = code == 200 and dims == [1024]
    if t6_ok:
        all_dims["T6-dimensions-1024"] = dims[0]
    record(
        "T6 compat dimensions=1024 (match current EMBEDDING_DIM)",
        t6_ok,
        f"HTTP {code}, dims={dims}" if code == 200 else _err_detail(code, data),
    )

    # T7 — native DashScope multimodal endpoint, text (the shape this
    # model actually accepts — compat mode returned 404 for it).
    code, data = _post(
        NATIVE_URL,
        api_key,
        {"model": MODEL, "input": {"contents": [{"text": TEXT_A}]}},
    )
    dims = _dims_from_native(data)
    if code == 200 and dims:
        all_dims["T7-native-text"] = dims[0]
    record(
        "T7 native text embedding",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}, usage={data.get('usage')}"
        if code == 200 else _err_detail(code, data),
    )

    # T8 — native image via public URL
    code, data = _post(
        NATIVE_URL,
        api_key,
        {"model": MODEL, "input": {"contents": [{"image": PUBLIC_IMAGE_URL}]}},
    )
    dims = _dims_from_native(data)
    if code == 200 and dims:
        all_dims["T8-native-image-url"] = dims[0]
    record(
        "T8 native image via public URL",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}, usage={data.get('usage')}"
        if code == 200 else _err_detail(code, data),
    )

    # T9 — native image via base64 data URI (synthetic 64x64 red PNG)
    data_uri = f"data:image/png;base64,{_synthetic_png_b64(64)}"
    code, data = _post(
        NATIVE_URL,
        api_key,
        {"model": MODEL, "input": {"contents": [{"image": data_uri}]}},
    )
    dims = _dims_from_native(data)
    if code == 200 and dims:
        all_dims["T9-native-image-b64"] = dims[0]
    record(
        "T9 native image via base64 data URI",
        code == 200 and bool(dims),
        f"HTTP {code}, dims={dims}" if code == 200 else _err_detail(code, data),
    )

    # T10 — native batched text (two content items in one call)
    code, data = _post(
        NATIVE_URL,
        api_key,
        {"model": MODEL, "input": {"contents": [{"text": TEXT_A}, {"text": TEXT_B}]}},
    )
    dims = _dims_from_native(data)
    record(
        "T10 native batched text (2 items)",
        code == 200 and len(dims) == 2,
        f"HTTP {code}, got {len(dims)} embeddings, dims={dims}"
        if code == 200 else _err_detail(code, data),
    )

    # T11 — native dimension control (MRL): request 1024 to match the
    # current EMBEDDING_DIM. DashScope native APIs are inconsistent about
    # the parameter spelling, so try both.
    t11_dims: List[int] = []
    t11_detail = ""
    for param_name in ("dimension", "dimensions"):
        code, data = _post(
            NATIVE_URL,
            api_key,
            {
                "model": MODEL,
                "input": {"contents": [{"text": TEXT_A}]},
                "parameters": {param_name: 1024},
            },
        )
        dims = _dims_from_native(data)
        if code == 200 and dims:
            t11_dims = dims
            t11_detail = f"parameters.{param_name}=1024 -> HTTP {code}, dims={dims}"
            break
        t11_detail += f"parameters.{param_name}: {_err_detail(code, data)}; "
    t11_ok = t11_dims == [1024]
    if t11_ok:
        all_dims["T11-dimension-1024"] = 1024
    record(
        "T11 native dimension=1024 (match EMBEDDING_DIM)",
        t11_ok,
        t11_detail or "no response",
    )

    # --- verdict ---
    by_name = {name: ok for name, ok, _ in results}
    passed = sum(1 for ok in by_name.values() if ok)
    print(f"\n=== {passed}/{len(results)} checks passed ===")
    default_dims = {v for k, v in all_dims.items() if not k.startswith("T11")}
    if default_dims:
        print(
            f"dims by test: {all_dims} -> "
            + ("UNIFIED vector space confirmed (text == image dims)"
               if len(default_dims) == 1
               else "WARNING: dims differ between modalities — check above")
        )
    native_ok = by_name.get("T7 native text embedding", False)
    print("verdict:")
    print("  - request shape: " + (
        "native endpoint (compat mode does not support this model)"
        if native_ok else "NONE WORKING — investigate errors above"))
    print(f"  - image via URL: {'yes' if by_name.get('T8 native image via public URL') else 'no'}; "
          f"image via base64: {'yes' if by_name.get('T9 native image via base64 data URI') else 'no'}")
    print(f"  - batch text: {'yes' if by_name.get('T10 native batched text (2 items)') else 'no'}; "
          f"dimension=1024: "
          f"{'yes — collection dim unchanged' if by_name.get('T11 native dimension=1024 (match EMBEDDING_DIM)') else 'no — recreate collection at native dim'}")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
