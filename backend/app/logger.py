"""Centralized logging configuration for the backend.

Importing this module configures the root logger once. Use `logging.getLogger(__name__)`
in service / API modules to get a properly configured logger.

Format is controlled by ``LOG_FORMAT`` (see ``app/config.py``): "json" emits one
JSON object per line, "text" the legacy human-readable form. Call sites that want
queryable fields pass ``extra={...}`` — those keys merge into the JSON object
(visible only in json mode; harmless no-ops in text mode).
"""
import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.middleware import request_id_var


_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [req:%(request_id)s]: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RequestIdFilter(logging.Filter):
    """Inject the current request id onto every log record.

    ``request_id_var`` defaults to "-" outside any request (startup,
    background tasks), so the format string's ``%(request_id)s`` always
    resolves instead of raising KeyError.
    """

    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line: ts/level/logger/request_id/msg (+ extra= fields).

    Keys beyond the stdlib/reserved set on ``record.__dict__`` (i.e. anything set
    via ``extra={...}`` at the call site) are merged in as top-level fields.
    %-args lazy formatting is handled by ``getMessage()``; exc_info / stack_info
    render as string fields.
    """

    _RESERVED = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
        "request_id",  # placed explicitly below
    })

    def format(self, record):
        record.message = record.getMessage()
        entry = {
            # Local wall-clock with explicit UTC offset — readable locally,
            # unambiguous across hosts/DST for log aggregation.
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.message,
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            if key in entry:
                continue
            entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _resolve_log_format(settings) -> str:
    """Explicit ``LOG_FORMAT`` wins (case/whitespace-insensitive); unset ->
    json in production, text in dev. Invalid explicit values raise so a
    typo in .env fails startup loudly instead of silently switching format."""
    if settings.LOG_FORMAT and settings.LOG_FORMAT.strip():
        explicit = settings.LOG_FORMAT.strip().lower()
        if explicit not in ("text", "json"):
            raise ValueError(
                f"LOG_FORMAT must be 'text' or 'json', got {settings.LOG_FORMAT!r}"
            )
        return explicit
    return "json" if settings.APP_ENV == "production" else "text"


def configure_logging() -> None:
    """Configure root logger. Idempotent - safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Imported here to avoid import-time coupling. get_settings is the cached
    # singleton that validates JWT_SECRET — logging must not start under a
    # placeholder secret either.
    from app.config import get_settings

    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = (
        _JsonFormatter()
        if _resolve_log_format(settings) == "json"
        else logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    )
    rid_filter = _RequestIdFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(rid_filter)
    root.addHandler(stream)

    log_dir = Path(settings.LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(rid_filter)
        root.addHandler(file_handler)
    except OSError:
        pass

    # Fold uvicorn loggers into root so access/error lines share the same
    # formatter — otherwise the process emits half JSON, half plain text.
    # uvicorn.access started with access_log=False has its handlers emptied by
    # uvicorn itself; leave that decision alone instead of resurrecting it.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        if name == "uvicorn.access" and not uvicorn_logger.handlers:
            continue
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    _CONFIGURED = True
