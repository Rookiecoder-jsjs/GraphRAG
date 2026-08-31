"""Unit tests for app.logger: format resolution + JSON formatter behavior.

These deliberately avoid touching the global logging configuration (it's a
process-wide side effect) — they exercise ``_JsonFormatter`` and
``_resolve_log_format`` directly on synthetic LogRecords.
"""
import json
import logging
import re
import sys

import pytest

from app.config import Settings
from app.logger import _JsonFormatter, _resolve_log_format


def _make_settings(**overrides) -> Settings:
    # _env_file=None isolates from backend/.env; explicit kwargs take priority
    # over both env vars and defaults in pydantic-settings v2.
    return Settings(_env_file=None, **overrides)


def _formatted(
    formatter,
    msg="hello %s",
    args=("world",),
    extra=None,
    request_id=None,
    sinfo=None,
) -> str:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
        sinfo=sinfo,
    )
    if request_id is not None:
        record.request_id = request_id
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return formatter.format(record)


def _format_exc_record(formatter) -> str:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
        record.request_id = "-"
        return formatter.format(record)


class TestResolveLogFormat:
    def test_unset_in_development_is_text(self):
        s = _make_settings(APP_ENV="development", LOG_FORMAT=None)
        assert _resolve_log_format(s) == "text"

    def test_unset_in_production_is_json(self):
        s = _make_settings(APP_ENV="production", LOG_FORMAT=None)
        assert _resolve_log_format(s) == "json"

    def test_explicit_json_wins_in_development(self):
        s = _make_settings(APP_ENV="development", LOG_FORMAT="json")
        assert _resolve_log_format(s) == "json"

    def test_explicit_text_wins_in_production(self):
        s = _make_settings(APP_ENV="production", LOG_FORMAT="text")
        assert _resolve_log_format(s) == "text"

    def test_explicit_value_is_case_and_whitespace_insensitive(self):
        assert _resolve_log_format(
            _make_settings(APP_ENV="development", LOG_FORMAT=" JSON ")
        ) == "json"
        assert _resolve_log_format(
            _make_settings(APP_ENV="production", LOG_FORMAT="Text")
        ) == "text"

    def test_invalid_explicit_value_raises(self):
        with pytest.raises(ValueError, match="LOG_FORMAT"):
            _resolve_log_format(
                _make_settings(APP_ENV="production", LOG_FORMAT="banana")
            )


class TestJsonFormatter:
    def test_basic_keys(self):
        out = json.loads(_formatted(_JsonFormatter(), request_id="abc123"))
        assert out["level"] == "INFO"
        assert out["logger"] == "app.test"
        assert out["request_id"] == "abc123"
        assert out["msg"] == "hello world"
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$",
            out["ts"],
        )

    def test_request_id_defaults_to_dash_when_unset(self):
        out = json.loads(_formatted(_JsonFormatter()))
        assert out["request_id"] == "-"

    def test_lazy_args_formatted_into_msg(self):
        out = json.loads(
            _formatted(_JsonFormatter(), msg="user_id=%d q=%s", args=(1, "x"))
        )
        assert out["msg"] == "user_id=1 q=x"

    def test_extra_fields_merged_as_top_level(self):
        out = json.loads(
            _formatted(
                _JsonFormatter(),
                extra={"timing_s": {"total": 1.23}, "user_id": 1},
            )
        )
        assert out["timing_s"] == {"total": 1.23}
        assert out["user_id"] == 1
        # stdlib record internals must not leak into the JSON object.
        for key in ("levelno", "created", "msecs", "pathname", "args"):
            assert key not in out

    def test_exc_info_rendered_as_traceback(self):
        out = json.loads(_format_exc_record(_JsonFormatter()))
        assert "Traceback" in out["exc_info"]
        assert "ValueError: boom" in out["exc_info"]

    def test_non_serializable_extra_falls_back_to_str(self):
        class Opaque:
            pass

        out = json.loads(_formatted(_JsonFormatter(), extra={"opaque": Opaque()}))
        assert isinstance(out["opaque"], str)

    def test_cjk_message_kept_unicode(self):
        out = json.loads(_formatted(_JsonFormatter(), msg="检索耗时 %s", args=("中文",)))
        assert out["msg"] == "检索耗时 中文"

    def test_stack_info_rendered(self):
        out = json.loads(
            _formatted(_JsonFormatter(), sinfo="STACK-LINE-1\nSTACK-LINE-2")
        )
        assert out["stack_info"] == "STACK-LINE-1\nSTACK-LINE-2"

    def test_extra_cannot_overwrite_core_fields(self):
        out = json.loads(
            _formatted(_JsonFormatter(), extra={"ts": "spoof", "level": "SPOOF"})
        )
        assert out["ts"].startswith("20")
        assert out["level"] == "INFO"
