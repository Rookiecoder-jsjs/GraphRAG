"""Centralized logging configuration for the backend.

Importing this module configures the root logger once. Use `logging.getLogger(__name__)`
in service / API modules to get a properly configured logger.
"""
import logging
import os
import sys
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


def configure_logging() -> None:
    """Configure root logger. Idempotent - safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    rid_filter = _RequestIdFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(rid_filter)
    root.addHandler(stream)

    log_dir = Path(os.environ.get("LOG_DIR", "./data/logs"))
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

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    _CONFIGURED = True


