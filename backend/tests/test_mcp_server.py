"""Tests for the MCP server (GUIDE-003 T2-3).

Offline unit tests: token guard, tool surface, and read-only verification.
The end-to-end stdio handshake (initialize / list_tools / call_tool against
a real subprocess) was verified during implementation; reproducing it in
pytest would hardcode the venv python path, so the offline tests below pin
the properties that matter.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")


# ---------- token guard ------------------------------------------------------

def test_missing_token_exits(monkeypatch):
    from mcp_server import auth

    monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)
    with pytest.raises(SystemExit) as exc:
        auth.require_token()
    assert exc.value.code == 2


@pytest.mark.parametrize("placeholder", ["change-me", "CHANGEME", "", "  "])
def test_placeholder_tokens_rejected(monkeypatch, placeholder):
    from mcp_server import auth

    monkeypatch.setenv(auth.TOKEN_ENV_VAR, placeholder)
    with pytest.raises(SystemExit):
        auth.require_token()


def test_valid_token_accepted(monkeypatch):
    from mcp_server import auth

    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "t" * 40)
    assert auth.require_token() == "t" * 40


# ---------- tool surface -----------------------------------------------------

def _load_tools():
    """Import server.py without running main(); returns FastMCP instance."""
    import mcp_server.server as server_mod

    return server_mod


def test_four_tools_registered():
    srv = _load_tools()
    # FastMCP stores tools keyed by name inside an async local registry;
    # list_tools via the underlying server is async, so use its sync mirror.
    names = set(srv.mcp._tool_manager._tools.keys())
    assert names == {
        "search_knowledge", "search_graph", "get_entity_detail", "list_documents",
    }


def test_tool_schemas_declare_user_id():
    """Every tool takes user_id — cross-user isolation is explicit, never
    ambient. This pins the contract so a future tool can't silently drop it."""
    srv = _load_tools()
    for name in ("search_knowledge", "search_graph",
                 "get_entity_detail", "list_documents"):
        tool = srv.mcp._tool_manager._tools[name]
        props = tool.parameters.get("properties", {})
        assert "user_id" in props, f"{name} lost its user_id parameter"


def test_all_tools_declare_read_only_annotation():
    """All four tools carry readOnlyHint=True so MCP clients can run them
    without confirmation (codex gates tool auto-approval on this hint)."""
    srv = _load_tools()
    for name in ("search_knowledge", "search_graph",
                 "get_entity_detail", "list_documents"):
        tool = srv.mcp._tool_manager._tools[name]
        annotations = getattr(tool, "annotations", None)
        assert annotations is not None, f"{name} missing ToolAnnotations"
        assert annotations.readOnlyHint is True, (
            f"{name} must declare readOnlyHint=True"
        )


# ---------- read-only verification ------------------------------------------

def test_no_write_paths_in_tool_handlers():
    """Static guard: tool handler source must not contain write SQL / Neo4j
    write clauses. The acceptance criterion 'all tools are read-only' is a
    property of the CODE, so check the code itself."""
    import inspect

    srv = _load_tools()
    forbidden = (
        "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE ",
        "MERGE ", "DETACH DELETE", ".commit()", "executemany",
    )
    for name in ("search_knowledge", "search_graph",
                 "get_entity_detail", "list_documents"):
        fn = getattr(srv, name)
        src = inspect.getsource(fn).upper()
        for token in forbidden:
            assert token.upper() not in src, (
                f"{name} contains write construct {token!r}"
            )
