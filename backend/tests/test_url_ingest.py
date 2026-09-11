"""Tests for URL ingestion (FEAT-019): services/url_fetcher.py + api/url_ingest.py.

SSRF guards are the critical surface: scheme whitelist, userinfo rejection,
private/loopback/link-local address rejection across ALL resolved addresses,
and per-hop revalidation on manual redirects. Fetch behavior is exercised via
``httpx.MockTransport`` injected into the fetcher's client factory — no real
network anywhere in this suite.
"""
import asyncio
import socket

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException

from app.database import get_db, init_db
from app.services.url_fetcher import (
    FetchedContent,
    UrlFetchFailed,
    UrlNotAllowed,
    UnsupportedContentType,
    _assert_host_resolves_public,
    fetch_url_document,
    validate_public_http_url,
)


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "url_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_dns(monkeypatch):
    """Host -> address(es) map for socket.getaddrinfo (global patch: the
    MockTransport fetches never open a real connection, so nothing else
    needs DNS)."""
    table: dict = {}

    def _getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror(f"no fake DNS entry for {host!r}")
        entries = []
        for ip in table[host]:
            if ":" in ip:
                entries.append((socket.AF_INET6, 1, 6, "", (ip, 0, 0, 0)))
            else:
                entries.append((socket.AF_INET, 1, 6, "", (ip, 0)))
        return entries

    monkeypatch.setattr("app.services.url_fetcher.socket.getaddrinfo", _getaddrinfo)
    return table


# ---------------------------------------------------------------------------
# URL validation / SSRF guards
# ---------------------------------------------------------------------------

def test_rejects_non_http_schemes():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
        with pytest.raises(UrlNotAllowed):
            validate_public_http_url(url)
    with pytest.raises(UrlNotAllowed):
        validate_public_http_url("https://user:pass@example.com/")  # userinfo
    with pytest.raises(UrlNotAllowed):
        validate_public_http_url("not-a-url")


def test_rejects_private_loopback_linklocal_hosts(fake_dns):
    blocked = [
        "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9",
        "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1",
    ]
    for i, ip in enumerate(blocked):
        host = f"blocked{i}.example.com"
        fake_dns[host] = [ip]
        with pytest.raises(UrlNotAllowed):
            _assert_host_resolves_public(host)

    # End-to-end: a syntactically valid URL pointing at a private address
    # must be rejected by the full fetch flow (not just the DNS helper).
    fake_dns["private.example.com"] = ["192.168.0.10"]
    transport = httpx.MockTransport(
        _handler({"https://private.example.com/x": (200, {"content-type": "text/plain"}, b"hi")})
    )
    with pytest.raises(UrlNotAllowed):
        asyncio.run(
            fetch_url_document("https://private.example.com/x", transport=transport)
        )


def test_allows_public_host(fake_dns):
    fake_dns["example.com"] = ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
    assert _assert_host_resolves_public("example.com") is None
    assert validate_public_http_url("https://example.com/a?page=1") == "https://example.com/a?page=1"


def test_dns_failure_is_fetch_failed_not_ssrf(fake_dns):
    # Unknown host must surface as a 502-class fetch failure (UrlFetchFailed),
    # NOT as the 400-class SSRF rejection — the two mean different things.
    with pytest.raises(UrlFetchFailed):
        _assert_host_resolves_public("no-such-host.example.com")


# ---------------------------------------------------------------------------
# Fetch + redirect + size cap (MockTransport)
# ---------------------------------------------------------------------------

HTML_PAGE = """<html><head><title>知识图谱入门</title></head>
<body>
<nav><a href="/">首页</a><a href="/about">关于我们</a></nav>
<script>var tracking = 1;</script>
<article>
<h1>知识图谱入门</h1>
<p>知识图谱是一种用图结构表示知识的技术。节点表示实体，边表示关系。</p>
<p>构建知识图谱的第一步是实体抽取，第二步是关系抽取。</p>
</article>
<footer>版权所有 2026 示例站点</footer>
</body></html>"""


def _handler(routes):
    """routes: {url: (status, headers, body)}; body may be bytes or str."""

    def handler(request: httpx.Request) -> httpx.Response:
        status_code, headers, body = routes[str(request.url)]
        return httpx.Response(status_code, headers=headers, content=body)

    return handler


def test_html_converted_via_readability_and_html2text(fake_dns):
    fake_dns["example.com"] = ["93.184.216.34"]
    transport = httpx.MockTransport(
        _handler({"https://example.com/a": (200, {"content-type": "text/html; charset=utf-8"}, HTML_PAGE)})
    )
    fetched = asyncio.run(fetch_url_document("https://example.com/a", transport=transport))
    assert fetched.kind == "html"
    assert fetched.data is None
    assert "知识图谱是一种用图结构表示知识的技术" in fetched.markdown
    assert "实体抽取" in fetched.markdown
    # Boilerplate outside the article body must not survive extraction.
    assert "版权所有" not in fetched.markdown
    assert "知识图谱" in (fetched.title or "")


def test_pdf_content_type_persists_file(fake_dns):
    fake_dns["example.com"] = ["93.184.216.34"]
    pdf = b"%PDF-1.4 fake-bytes"
    transport = httpx.MockTransport(
        _handler({"https://example.com/p.pdf": (200, {"content-type": "application/pdf"}, pdf)})
    )
    fetched = asyncio.run(fetch_url_document("https://example.com/p.pdf", transport=transport))
    assert fetched.kind == "pdf"
    assert fetched.data == pdf
    assert fetched.markdown is None


def test_plain_text_and_markdown_routed(fake_dns):
    fake_dns["example.com"] = ["93.184.216.34"]
    routes = {
        "https://example.com/n.txt": (200, {"content-type": "text/plain"}, "# Note\nbody"),
        "https://example.com/n.md": (200, {"content-type": "text/markdown"}, "# Note\nbody"),
    }
    transport = httpx.MockTransport(_handler(routes))
    txt = asyncio.run(fetch_url_document("https://example.com/n.txt", transport=transport))
    md = asyncio.run(fetch_url_document("https://example.com/n.md", transport=transport))
    assert txt.kind == "txt" and txt.data == b"# Note\nbody"
    assert md.kind == "md"


def test_unsupported_content_type_rejected(fake_dns):
    fake_dns["example.com"] = ["93.184.216.34"]
    transport = httpx.MockTransport(
        _handler({"https://example.com/a.json": (200, {"content-type": "application/json"}, "{}")})
    )
    with pytest.raises(UnsupportedContentType):
        asyncio.run(fetch_url_document("https://example.com/a.json", transport=transport))


def test_redirect_hops_are_revalidated(fake_dns):
    # public.example 302s to private.example — the redirect target must be
    # re-resolved and rejected, not silently followed.
    fake_dns["public.example"] = ["93.184.216.34"]
    fake_dns["private.example"] = ["10.0.0.9"]
    transport = httpx.MockTransport(
        _handler({
            "http://public.example/x": (302, {"location": "http://private.example/y"}, b""),
        })
    )
    with pytest.raises(UrlNotAllowed):
        asyncio.run(fetch_url_document("http://public.example/x", transport=transport))


def test_redirect_limit_exceeded_errors(fake_dns):
    fake_dns["loop.example.com"] = ["93.184.216.34"]
    routes = {
        f"https://loop.example.com/{i}": (302, {"location": f"https://loop.example.com/{i + 1}"}, b"")
        for i in range(10)
    }
    transport = httpx.MockTransport(_handler(routes))
    with pytest.raises(UrlFetchFailed):
        asyncio.run(fetch_url_document("https://loop.example.com/0", transport=transport))


def test_body_size_cap_aborts(fake_dns, monkeypatch):
    monkeypatch.setenv("URL_FETCH_MAX_BYTES", "1024")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        fake_dns["example.com"] = ["93.184.216.34"]
        big = b"x" * 4096
        transport = httpx.MockTransport(
            _handler({"https://example.com/big": (200, {"content-type": "text/plain"}, big)})
        )
        with pytest.raises(UrlFetchFailed):
            asyncio.run(fetch_url_document("https://example.com/big", transport=transport))
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Endpoint: POST /api/documents/ingest-url
# ---------------------------------------------------------------------------

def _make_content(**overrides):
    fields = dict(
        kind="html", data=None, markdown="# 标题\n\n正文内容\n",
        title="标题", final_url="https://example.com/a",
    )
    fields.update(overrides)
    return FetchedContent(**fields)


def test_ingest_url_400_invalid_url(tmp_path):
    from app.api.url_ingest import ingest_url

    asyncio.run(init_db())
    body = type("B", (), {"url": "ftp://example.com/x"})()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            ingest_url(body=body, background_tasks=BackgroundTasks(), current_user={"id": 1})
        )
    assert exc.value.status_code == 400


def test_ingest_url_creates_pending_doc_and_dispatches(tmp_path, monkeypatch):
    from app.api.url_ingest import ingest_url

    async def main():
        await init_db()
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
            )
            await db.commit()

    asyncio.run(main())

    async def _fake_fetch(url, *, transport=None):
        return _make_content()

    monkeypatch.setattr("app.api.url_ingest.fetch_url_document", _fake_fetch)

    bt = BackgroundTasks()
    body = type("B", (), {"url": "https://example.com/a"})()
    doc = asyncio.run(
        ingest_url(body=body, background_tasks=bt, current_user={"id": 1})
    )
    assert doc["status"] == "pending"
    assert doc["file_type"] == "html"
    assert len(bt.tasks) == 1
    task = bt.tasks[0]
    assert task.func.__name__ == "process_document_background"
    assert task.args[0] == doc["id"] and task.args[1] == 1
    assert "正文内容" in task.args[2]

    async def check():
        async with get_db() as db:
            async with db.execute(
                "SELECT status, file_path, original_filename FROM documents WHERE id = ?",
                (doc["id"],),
            ) as cur:
                return dict(await cur.fetchone())

    row = asyncio.run(check())
    assert row["status"] == "pending"
    assert row["file_path"] is None
    assert row["original_filename"] == "https://example.com/a"


def test_ingest_url_fetch_failure_maps_502(tmp_path, monkeypatch):
    from app.api.url_ingest import ingest_url

    async def main():
        await init_db()
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
            )
            await db.commit()

    asyncio.run(main())

    async def _boom(url, *, transport=None):
        raise UrlFetchFailed("DNS resolution failed")

    monkeypatch.setattr("app.api.url_ingest.fetch_url_document", _boom)
    bt = BackgroundTasks()
    body = type("B", (), {"url": "https://no-such-host.example.com/x"})()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            ingest_url(body=body, background_tasks=bt, current_user={"id": 1})
        )
    assert exc.value.status_code == 502
    assert len(bt.tasks) == 0


# ---------------------------------------------------------------------------
# Deleting a URL-ingested document (file_path NULL) must not crash
# ---------------------------------------------------------------------------

def test_delete_url_document_handles_null_file_path(tmp_path, monkeypatch):
    from app.api.documents import delete_document

    class _FakeChroma:
        def delete_document_chunks(self, doc_id, user_id):
            return None

    class _FakeNeo4j:
        async def delete_document(self, doc_id, user_id):
            return None

    class _FakeBM25:
        def remove_from_index(self, user_id, chunk_ids):
            return None

    async def _fake_neo4j():
        return _FakeNeo4j()

    monkeypatch.setattr("app.api.documents.get_chroma_client", lambda: _FakeChroma())
    monkeypatch.setattr("app.api.documents.get_neo4j_client", _fake_neo4j)
    monkeypatch.setattr("app.api.documents.get_bm25_service", lambda: _FakeBM25())

    async def main():
        await init_db()
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
            )
            await db.execute(
                "INSERT INTO documents (id, user_id, title, file_path, "
                "original_filename, file_type, status) "
                "VALUES ('doc-url', 1, 'T', NULL, 'https://example.com/a', 'html', 'ready')"
            )
            await db.commit()

    asyncio.run(main())
    result = asyncio.run(delete_document(doc_id="doc-url", current_user={"id": 1}))
    assert result["message"] == "Document deleted successfully"

    async def check():
        async with get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE id = 'doc-url'"
            ) as cur:
                return (await cur.fetchone())["n"]

    assert asyncio.run(check()) == 0
