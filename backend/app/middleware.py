"""Lightweight ASGI middleware.

Pure-ASGI (not BaseHTTPMiddleware) so the request body is never buffered
into memory - important for the upload endpoint, which streams large
files. Each middleware is a thin wrapper around the ASGI callable.
"""
import contextvars
import json
import uuid

# Per-request correlation id. Set by RequestIDMiddleware on every HTTP
# request, read by the logging filter so log lines can be tied back to the
# request that produced them. Defaults to "-" for records emitted outside
# any request (startup, background tasks).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


async def _send_json(send, status: int, detail: str) -> None:
    """Minimal JSON error response without depending on Starlette here."""
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """Internal: a chunked body exceeded the cap while being streamed in."""


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies, declared OR chunked.

    Fast path: a declared Content-Length over ``max_bytes`` is turned away
    at the door. Chunked / length-less bodies (no Content-Length header)
    would otherwise bypass that check and be fully buffered into memory by
    the JSON endpoints — so we count their bytes as they arrive and abort
    at the cap. The upload endpoint is exempt: it streams and enforces its
    own MAX_FILE_SIZE, and this wrapper must never buffer a large upload.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await _send_json(send, 413, "Request body too large")
                        return
                except ValueError:
                    pass
                break
        path = scope.get("path", "").rstrip("/")
        if path == "/api/documents/upload":
            # Streaming upload endpoint — never count/buffer its body here.
            await self.app(scope, receive, send)
            return

        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, receive=limited_receive, send=send)
        except _BodyTooLarge:
            # The cap tripped mid-stream, before the app could send a
            # response (receive runs first). Reply 413 ourselves.
            await _send_json(send, 413, "Request body too large")


class RequestIDMiddleware:
    """Stamp every request with a correlation id.

    Reads an inbound ``X-Request-ID`` header if present, otherwise
    generates one. The id is stored in ``request_id_var`` (so log records
    pick it up via the logging filter) and echoed back on the response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        rid = "-"
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                rid = value.decode("latin-1") or "-"
                break
        if rid == "-":
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append([b"x-request-id", rid.encode("latin-1")])
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
