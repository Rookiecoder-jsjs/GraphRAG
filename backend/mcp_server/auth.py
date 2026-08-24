"""Token guard for the NC knowledge-base MCP server (GUIDE-003 T2-3).

KG_MCP_TOKEN is mandatory: the server refuses to start with a missing or
placeholder value — the same fail-fast discipline as JWT_SECRET. In stdio
mode the token is injected via env by the parent process (Claude Desktop /
Claude Code / codex config), so a leaked value can be rotated without
touching code.
"""
import os
import sys

# Public placeholders that must never be accepted at runtime.
_INSECURE_TOKENS = {
    "",
    "change-me",
    "changeme",
    "replace-me",
    "your-token-here",
}

TOKEN_ENV_VAR = "KG_MCP_TOKEN"


def require_token() -> str:
    """Return the configured MCP token, or exit with a clear error.

    Called once at server startup. Exits (rather than raises) because this
    runs before the stdio loop starts and there is no caller to catch.
    """
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token or token.lower() in _INSECURE_TOKENS:
        print(
            f"[mcp_server] refusing to start: {TOKEN_ENV_VAR} is missing or a "
            f"known placeholder. Generate one with "
            f"`python -c \"import secrets; print(secrets.token_urlsafe(32))\"`.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def verify(token_present: bool) -> None:
    """Per-call guard hook.

    The stdio transport is already process-scoped (only the parent that holds
    our env can talk to us), so there is no per-request credential to check;
    this exists so tool handlers declare the dependency explicitly and so a
    future network transport (SSE/HTTP) has a single place to enforce auth.
    """
    if not token_present:
        raise PermissionError("MCP token not verified")
