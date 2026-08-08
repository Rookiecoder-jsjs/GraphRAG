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


class RequestBodyLimitMiddleware:
    """Reject requests whose declared Content-Length exceeds ``max_bytes``.

    Runs before the app consumes the body, so an oversized payload is
    turned away at the door instead of being buffered. The upload
    endpoint additionally enforces its own MAX_FILE_SIZE during streaming;
    this is the global backstop for every other endpoint.
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
        await self.app(scope, receive, send)


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
