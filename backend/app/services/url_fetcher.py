"""URL fetcher for knowledge-base ingestion (FEAT-019).

Fetches a public web page / document and reduces it to the same "markdown in"
shape the upload pipeline consumes, so a URL rides the exact chunk → embed →
extract path a file takes.

SSRF guards (in enforcement order — every hop re-runs 1+2):
  1. ``urllib.parse``: scheme whitelist (``URL_ALLOWED_SCHEMES``), no embedded
     userinfo, hostname required.
  2. ``socket.getaddrinfo`` resolves ALL addresses; if ANY of them is private,
     loopback, link-local, reserved, multicast, or unspecified the URL is
     rejected. IPv4-mapped IPv6 literals are judged by their IPv4 half.
  3. Redirects are NEVER auto-followed: each 3xx Location is joined and the
     next hop re-runs steps 1+2, bounded by ``URL_FETCH_MAX_REDIRECTS``.
  4. The body is streamed and aborted mid-flight once it exceeds
     ``URL_FETCH_MAX_BYTES`` — a huge file never fully lands in memory.

Known limitation (accepted): the DNS rebinding TOCTOU window — the address
check happens before the HTTP connection, and httpx re-resolves on connect.
Pinning the validated IP would break TLS SNI / virtual hosting for the common
case; the scheme whitelist plus private-address rejection plus manual
redirect validation close the mainstream vectors.
"""
import asyncio
import html2text as _html2text
import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class UrlFetchError(Exception):
    """Base error for URL ingestion failures."""


class UrlNotAllowed(UrlFetchError):
    """The URL (or a redirect hop) is structurally invalid or points at a
    non-public address. Maps to HTTP 400."""


class UrlFetchFailed(UrlFetchError):
    """The URL is allowed but the fetch itself failed (DNS, timeout, HTTP
    error status, size cap, redirect loop). Maps to HTTP 502."""


class UnsupportedContentType(UrlFetchError):
    """The server returned a content type this pipeline cannot ingest.
    Maps to HTTP 415."""


@dataclass(frozen=True)
class FetchedContent:
    """Result of one successful fetch.

    ``kind`` selects the storage path: ``html`` carries rendered ``markdown``
    and NO raw bytes (the HTML itself is never persisted); ``pdf``/``txt``/
    ``md`` carry raw ``data`` to persist in UPLOAD_DIR for later reprocessing.
    """

    kind: str  # "html" | "pdf" | "txt" | "md"
    data: Optional[bytes]
    markdown: Optional[str]
    title: Optional[str]
    final_url: str


def _allowed_schemes(settings) -> frozenset:
    return frozenset(
        s.strip().lower() for s in settings.URL_ALLOWED_SCHEMES.split(",") if s.strip()
    )


def validate_public_http_url(url: str) -> str:
    """Structural validation (step 1). Returns the trimmed URL; raises
    :class:`UrlNotAllowed` for anything we refuse to fetch. DNS is NOT
    consulted here — use :func:`_assert_host_resolves_public` for step 2."""
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UrlNotAllowed("URL could not be parsed") from e
    if not parsed.scheme or not parsed.netloc:
        raise UrlNotAllowed("URL must be absolute (scheme + host)")
    if parsed.scheme.lower() not in _allowed_schemes(get_settings()):
        raise UrlNotAllowed(f"URL scheme {parsed.scheme!r} is not allowed")
    if parsed.username or parsed.password:
        raise UrlNotAllowed("URLs with embedded credentials are not allowed")
    if not parsed.hostname:
        raise UrlNotAllowed("URL has no hostname")
    return url


def _assert_host_resolves_public(host: str) -> None:
    """DNS validation (step 2). Raises :class:`UrlFetchFailed` when the host
    cannot be resolved (operator-visible fetch failure, not an SSRF hit) and
    :class:`UrlNotAllowed` when ANY resolved address is non-public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise UrlFetchFailed(f"DNS resolution failed for {host!r}") from e
    if not infos:
        raise UrlFetchFailed(f"DNS returned no addresses for {host!r}")
    for info in infos:
        raw = str(info[4][0])
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])  # strip IPv6 zone id
        except ValueError:
            continue
        # ::ffff:127.0.0.1-style mapped addresses must be judged by the
        # IPv4 half — ipaddress treats them as public IPv6 otherwise.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UrlNotAllowed(
                f"target address {ip} is not reachable for ingestion"
            )


def _charset_from_content_type(header: Optional[str]) -> str:
    if header and "charset=" in header:
        charset = header.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"')
        if charset:
            return charset
    return "utf-8"


def _html_to_markdown(html: str, base_url: str) -> Tuple[str, Optional[str]]:
    """Readable-content extraction + Markdown conversion (CPU-bound; the
    caller runs it via ``asyncio.to_thread``).

    readability-lxml locates the article body (dropping nav / footer /
    script boilerplate); html2text renders the surviving fragment as
    GFM-style markdown. Image refs are dropped on purpose — scraped assets
    would be broken pointers. Raises :class:`UrlFetchFailed` when nothing
    readable survives extraction.
    """
    from readability import Document as ReadabilityDocument

    doc = ReadabilityDocument(html)
    title: Optional[str] = None
    try:
        title = (doc.short_title() or "").strip() or None
    except Exception:  # title extraction is best-effort
        title = None
    try:
        fragment = doc.summary(html_partial=True)
    except Exception as e:
        raise UrlFetchFailed(f"could not extract readable content: {e}") from e

    converter = _html2text.HTML2Text()
    converter.baseurl = base_url
    # No hard wrapping: the chunker and reranker prefer flowing text.
    converter.body_width = 0
    converter.ignore_images = True
    markdown = converter.handle(fragment).strip()
    if not markdown:
        raise UrlFetchFailed("extracted content is empty")
    return markdown, title


def _build_client(settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    """Build the fetch client. ``transport`` is an injection seam for tests
    (httpx.MockTransport) — production passes None."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.URL_FETCH_TIMEOUT_SECONDS,
            connect=settings.URL_FETCH_CONNECT_TIMEOUT,
        ),
        follow_redirects=False,  # step 3: redirects handled manually below
        headers={
            "User-Agent": settings.URL_FETCH_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
        },
        transport=transport,
    )


async def _stream_read(response: httpx.Response, max_bytes: int) -> bytes:
    """Read the body while enforcing the byte cap (step 4). The stream is
    closed and the fetch aborted the moment the cap is crossed, so an
    oversized response never fully lands in memory."""
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            await response.aclose()
            raise UrlFetchFailed(f"response body exceeds the {max_bytes} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_url_document(
    url: str, *, transport: Optional[httpx.AsyncBaseTransport] = None
) -> FetchedContent:
    """Fetch ``url`` and return :class:`FetchedContent`. Raises the module's
    error subclasses; the API layer maps them to 400/502/415."""
    settings = get_settings()
    current_url = validate_public_http_url(url)

    async with _build_client(settings, transport) as client:
        # One iteration per hop; redirects consume an iteration and continue.
        for _hop in range(settings.URL_FETCH_MAX_REDIRECTS + 1):
            host = urlparse(current_url).hostname
            if not host:
                raise UrlNotAllowed(f"URL {current_url!r} has no hostname")
            # Steps 1+2 re-run for EVERY hop — a public-looking entry URL
            # must not be allowed to bounce into a private address.
            await asyncio.to_thread(_assert_host_resolves_public, host)
            try:
                request = client.build_request("GET", current_url)
                response = await client.send(request, stream=True)
            except httpx.HTTPError as e:
                raise UrlFetchFailed(f"failed to fetch {current_url}: {e}") from e

            if response.is_redirect:
                location = response.headers.get("location", "")
                await response.aclose()
                if not location:
                    raise UrlFetchFailed(
                        f"redirect from {current_url} has no Location header"
                    )
                next_url = urljoin(current_url, location)
                if urlparse(next_url).scheme.lower() not in _allowed_schemes(settings):
                    raise UrlNotAllowed(
                        f"redirect target uses a non-allowed scheme: {next_url!r}"
                    )
                current_url = next_url
                continue

            if response.status_code >= 400:
                await response.aclose()
                raise UrlFetchFailed(
                    f"GET {current_url} returned HTTP {response.status_code}"
                )

            content_type = (
                response.headers.get("content-type") or ""
            ).split(";", 1)[0].strip().lower()

            if content_type in ("text/html", "application/xhtml+xml"):
                data = await _stream_read(response, settings.URL_FETCH_MAX_BYTES)
                await response.aclose()
                html = data.decode(
                    _charset_from_content_type(response.headers.get("content-type")),
                    errors="replace",
                )
                markdown, title = await asyncio.to_thread(
                    _html_to_markdown, html, str(response.url)
                )
                return FetchedContent("html", None, markdown, title, str(response.url))
            if content_type == "application/pdf":
                data = await _stream_read(response, settings.URL_FETCH_MAX_BYTES)
                await response.aclose()
                return FetchedContent("pdf", data, None, None, str(response.url))
            if content_type in ("text/plain", "text/markdown"):
                data = await _stream_read(response, settings.URL_FETCH_MAX_BYTES)
                await response.aclose()
                kind = "md" if content_type == "text/markdown" else "txt"
                return FetchedContent(kind, data, None, None, str(response.url))

            await response.aclose()
            raise UnsupportedContentType(
                f"unsupported content type {content_type or 'unknown'}"
            )

    raise UrlFetchFailed(
        f"more than {settings.URL_FETCH_MAX_REDIRECTS} redirects fetching {url}"
    )
