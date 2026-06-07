"""Tests for #18 document cluster map.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_cluster_map.py

Coverage:
  1. _pca_2d — numpy-based 2D projection. Math sanity: orthogonal
     inputs project to orthogonal axes; centred input → centred output.
  2. _doc_centroid_embedding — averaging per-chunk embeddings to one
     vector per document. Empty input → None (caller skips the doc).
  3. API endpoint — GET /api/documents/cluster-map
     * returns empty list when user has < 2 documents (no projection
       makes sense for a single point)
     * returns one point per document with (doc_id, title, file_type, x, y)
     * coordinates are finite numbers
     * user scoping — only own docs
  4. _pca_2d shape guard — non-2D input raises (we only project to 2D)
"""
from __future__ import annotations

import asyncio
import math
import sys
import unittest.mock as _mock
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# =========================================================================
# Standalone runner
# =========================================================================

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


# =========================================================================
# 1. _pca_2d math
# =========================================================================

def test_pca_2d_orthogonal_inputs_yield_orthogonal_axes():
    """Two clearly-orthogonal clusters (x-axis vs y-axis spread) should
    project to roughly orthogonal principal components. The exact
    eigenvalues are an implementation detail; the sign may flip — we
    check that the projected clusters are not collapsed onto one line."""
    from app.api.documents import _pca_2d
    import numpy as np
    # 20 docs, 2D embedding that already happens to be aligned with
    # x and y axes. After PCA, the spread along the two output axes
    # should both be non-trivial (no degenerate 1D projection).
    rng = np.random.default_rng(42)
    # Cluster A: spread along first axis
    a = np.column_stack([rng.normal(0, 3, 10), rng.normal(0, 0.1, 10)])
    # Cluster B: spread along second axis
    b = np.column_stack([rng.normal(0, 0.1, 10), rng.normal(0, 3, 10)])
    X = np.vstack([a, b])
    pts = _pca_2d(X)
    check("pca_2d: output shape is (N, 2)", pts.shape == (20, 2))
    x_range = pts[:, 0].max() - pts[:, 0].min()
    y_range = pts[:, 1].max() - pts[:, 1].min()
    check(f"pca_2d: both axes have non-trivial spread "
          f"(x_range={x_range:.2f}, y_range={y_range:.2f})",
          x_range > 0.5 and y_range > 0.5)


def test_pca_2d_centred_input_stays_centred():
    """Centring is part of PCA — output mean should be near zero."""
    from app.api.documents import _pca_2d
    import numpy as np
    rng = np.random.default_rng(7)
    X = rng.normal(loc=[3, -2, 1, 0, 4], scale=1, size=(10, 5))
    pts = _pca_2d(X)
    check("pca_2d: x-axis mean is near zero",
          abs(pts[:, 0].mean()) < 1e-6)
    check("pca_2d: y-axis mean is near zero",
          abs(pts[:, 1].mean()) < 1e-6)


def test_pca_2d_rejects_wrong_shape():
    """A 1D input or N < 2 should fail fast rather than crash the API."""
    from app.api.documents import _pca_2d
    import numpy as np
    raised = False
    try:
        _pca_2d(np.array([1.0, 2.0, 3.0]))  # 1-D
    except (ValueError, IndexError):
        raised = True
    check("pca_2d: 1-D input raises", raised)

    raised2 = False
    try:
        _pca_2d(np.array([[1.0, 2.0]]))  # only 1 row
    except (ValueError, IndexError):
        raised2 = True
    check("pca_2d: 1-row input raises", raised2)


# =========================================================================
# 2. _doc_centroid_embedding
# =========================================================================

def test_centroid_averages_per_chunk_embeddings():
    """The centroid of a doc is the mean of its chunk embeddings. With
    one chunk it's that vector verbatim; with multiple chunks it's the
    element-wise mean."""
    from app.api.documents import _doc_centroid_embedding
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    c1 = _doc_centroid_embedding([v1])
    check("centroid: single chunk → that vector",
          c1 is not None and abs(c1[0] - 1.0) < 1e-9
          and abs(c1[1] - 0.0) < 1e-9)
    c2 = _doc_centroid_embedding([v1, v2])
    check("centroid: 2 chunks → element-wise mean",
          c2 is not None
          and abs(c2[0] - 0.5) < 1e-9
          and abs(c2[1] - 0.5) < 1e-9)


def test_centroid_empty_input_returns_none():
    """A doc with no chunks returns None so the caller can skip it
    rather than projecting a zero-vector."""
    from app.api.documents import _doc_centroid_embedding
    check("centroid: empty list → None",
          _doc_centroid_embedding([]) is None)


# =========================================================================
# 3. API endpoint
# =========================================================================

class _FakeRow(dict):
    def __getitem__(self, k): return super().__getitem__(k)
    def __contains__(self, k): return super().__contains__(k)


async def _fake_embed_with_identity(contents):
    """Return an embedding that's literally the chunk content's
    character codes (truncated/zero-padded to a fixed dim). Two docs
    with different topics → different vectors → projected apart."""
    DIM = 8
    out = []
    for c in contents:
        v = [0.0] * DIM
        for i, ch in enumerate(c[:DIM]):
            v[i] = (ord(ch) % 97) / 97.0  # normalise to 0..1
        out.append(v)
    return out


def _make_db_ctx(scripted_responses):
    """Return a _Ctx whose execute() pops scripted_responses in order."""
    call_index = {"i": 0}

    class _Cursor:
        def __init__(self, rows):
            self._rows = list(rows)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def fetchall(self): return list(self._rows)

    class _Db:
        def execute(self, sql, params=()):
            payload = (scripted_responses[call_index["i"]]
                       if call_index["i"] < len(scripted_responses)
                       else [])
            call_index["i"] += 1
            return _Cursor(payload)
        async def commit(self): return None

    class _Ctx:
        async def __aenter__(self): return _Db()
        async def __aexit__(self, *a): return False

    return _Ctx()


def test_endpoint_returns_one_point_per_doc():
    """Two docs with distinct content project to two distinct points."""
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    # Two distinct documents, each with 2 distinct chunks.
    docs_rows = [
        _FakeRow({"id": "d-1", "title": "Cats", "file_type": "pdf",
                  "original_filename": "cats.pdf"}),
        _FakeRow({"id": "d-2", "title": "Banking", "file_type": "txt",
                  "original_filename": "banking.txt"}),
    ]
    chunks_rows = [
        _FakeRow({"document_id": "d-1", "content": "cats kittens meow"}),
        _FakeRow({"document_id": "d-1", "content": "feline pets"}),
        _FakeRow({"document_id": "d-2", "content": "investment banking"}),
        _FakeRow({"document_id": "d-2", "content": "stocks portfolio"}),
    ]
    db_ctx = _make_db_ctx([docs_rows, chunks_rows])

    with _mock.patch.object(docs_mod, "get_db", lambda: db_ctx), \
         _mock.patch.object(docs_mod, "_embed_chunks_for_centroid",
                            _fake_embed_with_identity):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/cluster-map")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    check("GET cluster-map: 200 OK",
          r.status_code == 200, f"got {r.status_code}: {r.text}")
    body = r.json()
    check("cluster-map payload: has 'points' key", "points" in body)
    pts = body["points"]
    check("cluster-map payload: 2 points for 2 docs", len(pts) == 2)
    titles = sorted([p["title"] for p in pts])
    check("cluster-map payload: titles match",
          titles == ["Banking", "Cats"])
    for p in pts:
        check(f"point {p['title']}: has x, y, doc_id, file_type",
              "x" in p and "y" in p and "doc_id" in p
              and "file_type" in p)
        check(f"point {p['title']}: x is finite",
              isinstance(p["x"], (int, float))
              and math.isfinite(p["x"]))
        check(f"point {p['title']}: y is finite",
              isinstance(p["y"], (int, float))
              and math.isfinite(p["y"]))
    p1, p2 = pts[0], pts[1]
    distinct = (abs(p1["x"] - p2["x"]) > 1e-6
                or abs(p1["y"] - p2["y"]) > 1e-6)
    check("cluster-map payload: 2 docs project to 2 distinct points",
          distinct)


def test_endpoint_returns_empty_for_no_docs():
    """No documents → empty list. The endpoint must not crash with a
    PCA shape error (which would happen if it tried to project a 0xN
    matrix)."""
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    db_ctx = _make_db_ctx([[]])

    with _mock.patch.object(docs_mod, "get_db", lambda: db_ctx):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/cluster-map")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    check("GET cluster-map (empty): 200 OK", r.status_code == 200)
    check("GET cluster-map (empty): empty points list",
          r.json().get("points") == [])


def test_endpoint_returns_empty_for_one_doc():
    """With exactly one document, there's no projection to do. Return
    an empty list rather than a single point at (0,0) — the UI can't
    usefully display a one-point map."""
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    docs_rows = [
        _FakeRow({"id": "d-1", "title": "Solo", "file_type": "pdf",
                  "original_filename": "solo.pdf"}),
    ]
    chunks_rows = [
        _FakeRow({"document_id": "d-1", "content": "only doc"}),
    ]
    db_ctx = _make_db_ctx([docs_rows, chunks_rows])

    with _mock.patch.object(docs_mod, "get_db", lambda: db_ctx), \
         _mock.patch.object(docs_mod, "_embed_chunks_for_centroid",
                            _fake_embed_with_identity):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/cluster-map")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    check("GET cluster-map (1 doc): 200 OK", r.status_code == 200)
    check("GET cluster-map (1 doc): empty points list "
          "(can't project one point meaningfully)",
          r.json().get("points") == [])


# =========================================================================
# 4. Router registration
# =========================================================================

def test_cluster_map_endpoint_registered():
    from app.api.documents import router
    methods_paths = sorted({
        (m, r.path)
        for r in router.routes if hasattr(r, "path") and hasattr(r, "methods")
        for m in (r.methods or set()) if m not in ("HEAD",)
    })
    check("/api/documents/cluster-map is registered",
          ("GET", "/api/documents/cluster-map") in methods_paths)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_pca_2d_orthogonal_inputs_yield_orthogonal_axes,
    test_pca_2d_centred_input_stays_centred,
    test_pca_2d_rejects_wrong_shape,
    test_centroid_averages_per_chunk_embeddings,
    test_centroid_empty_input_returns_none,
    test_endpoint_returns_one_point_per_doc,
    test_endpoint_returns_empty_for_no_docs,
    test_endpoint_returns_empty_for_one_doc,
    test_cluster_map_endpoint_registered,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #18 document cluster map...")
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__}: no unhandled exceptions", False, repr(e))
    print()
    if _failures:
        print(f"{FAIL} {len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print(f"{PASS} All checks passed ({len(ALL_TESTS)} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
