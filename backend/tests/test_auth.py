"""Tests for the auth layer: JWT round-trip and the /api/auth endpoints.

Direct-coroutine style (suite-wide pattern). Covers: token create/verify
round-trip; expired / forged / missing-sub / garbage tokens; register
success + duplicate-400 + weak-password validation; the Neo4j-failure
compensation rollback (503 + SQLite row removed); login success + wrong
password / unknown user 401s; and get_current_user over the REAL JWT path
(no dependency_overrides — the suite-wide hole this file plugs).
"""
import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from jose import jwt
from pydantic import ValidationError

from app.auth.jwt_handler import create_access_token, verify_token
from app.auth.security import get_password_hash
from app.api.auth import get_current_user, login, register
from app.config import get_settings
from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "auth_test.db"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _bootstrap(username: str = "alice", password: str = "Passw0rd1"):
    """init_db + a user with a real bcrypt hash."""
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, get_password_hash(password)),
        )
        await db.commit()


class _FakeNeo4j:
    """No-op graph client for the happy-path registration."""

    async def create_user_node(self, user_id: int, username: str) -> None:
        pass


# =========================================================================
# JWT unit tests (jwt_handler)
# =========================================================================


def test_create_and_verify_token_roundtrip():
    """create_access_token → verify_token returns the original payload
    plus an exp claim in the future."""
    import time as _time

    token = create_access_token(data={"sub": "alice"})
    payload = verify_token(token)
    assert payload["sub"] == "alice"
    assert payload["exp"] > _time.time()


def test_verify_token_returns_sub_for_token_with_extra_claims():
    token = create_access_token(data={"sub": "bob", "role": "admin"})
    payload = verify_token(token)
    assert payload["sub"] == "bob"
    assert payload["role"] == "admin"


def test_verify_expired_token_raises_401():
    token = create_access_token(
        data={"sub": "alice"}, expires_delta=timedelta(seconds=-10)
    )
    with pytest.raises(HTTPException) as exc:
        verify_token(token)
    assert exc.value.status_code == 401


def test_verify_token_signed_with_wrong_secret_raises_401(monkeypatch):
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "alice"}, "not-the-real-secret", algorithm=settings.JWT_ALGORITHM
    )
    with pytest.raises(HTTPException) as exc:
        verify_token(forged)
    assert exc.value.status_code == 401


def test_verify_token_missing_sub_raises_401():
    settings = get_settings()
    no_sub = jwt.encode(
        {"exp": 9999999999}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    with pytest.raises(HTTPException) as exc:
        verify_token(no_sub)
    assert exc.value.status_code == 401


def test_verify_garbage_token_raises_401():
    with pytest.raises(HTTPException) as exc:
        verify_token("not-a-jwt-at-all")
    assert exc.value.status_code == 401


# =========================================================================
# Register endpoint
# =========================================================================


@pytest.mark.asyncio
async def test_register_creates_user(monkeypatch):
    await init_db()
    # get_neo4j_client is awaited in the handler (auth.py:102) — the mock
    # must be awaitable itself, not return the client synchronously.
    async def _get_fake():
        return _FakeNeo4j()

    monkeypatch.setattr(
        "app.services.neo4j_client.get_neo4j_client", _get_fake
    )
    user = await register(
        pytest.importorskip("app.models.user").UserCreate(
            username="newbie", password="Passw0rd1"
        )
    )
    assert user["username"] == "newbie"
    assert user["id"] > 0
    # Password is hashed, never stored in the clear.
    async with get_db() as db:
        async with db.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["password_hash"] != "Passw0rd1"
    assert row["password_hash"].startswith("$2")


@pytest.mark.asyncio
async def test_register_duplicate_username_400():
    from app.models.user import UserCreate

    await _bootstrap("alice")
    with pytest.raises(HTTPException) as exc:
        await register(UserCreate(username="alice", password="Passw0rd1"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_register_weak_password_rejected():
    """Validation is at the model boundary — a password without a digit
    must never reach the handler."""
    from app.models.user import UserCreate

    with pytest.raises(ValidationError):
        UserCreate(username="alice", password="abcdefgh")
    with pytest.raises(ValidationError):
        UserCreate(username="alice", password="12345678")


@pytest.mark.asyncio
async def test_register_neo4j_down_rolls_back_503(monkeypatch):
    """If the graph store is down, registration must fail loudly AND undo
    the committed SQLite row — otherwise the username is burned forever."""

    async def _boom():
        raise RuntimeError("neo4j unavailable")

    monkeypatch.setattr(
        "app.services.neo4j_client.get_neo4j_client", _boom
    )
    await init_db()
    from app.models.user import UserCreate

    with pytest.raises(HTTPException) as exc:
        await register(UserCreate(username="ghost", password="Passw0rd1"))
    assert exc.value.status_code == 503
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE username = 'ghost'"
        ) as cur:
            row = await cur.fetchone()
    assert row["n"] == 0


@pytest.mark.asyncio
async def test_register_creates_neo4j_user_node(monkeypatch):
    created = []

    class _Recorder(_FakeNeo4j):
        async def create_user_node(self, user_id, username):
            created.append((user_id, username))

    async def _get_recorder():
        # get_neo4j_client is awaited in the handler (auth.py:102) — the
        # mock must be awaitable itself, not return the client synchronously.
        return _Recorder()

    monkeypatch.setattr(
        "app.services.neo4j_client.get_neo4j_client", _get_recorder
    )
    await init_db()
    from app.models.user import UserCreate

    user = await register(UserCreate(username="neo4j_ok", password="Passw0rd1"))
    assert created == [(user["id"], "neo4j_ok")]


# =========================================================================
# Login endpoint
# =========================================================================


def _form(username: str, password: str):
    from fastapi.security import OAuth2PasswordRequestForm

    return OAuth2PasswordRequestForm(
        username=username, password=password, grant_type="password"
    )


@pytest.mark.asyncio
async def test_login_success_returns_valid_token():
    await _bootstrap("alice", "Passw0rd1")
    result = await login(form_data=_form("alice", "Passw0rd1"))
    assert result["token_type"] == "bearer"
    payload = verify_token(result["access_token"])
    assert payload["sub"] == "alice"


@pytest.mark.asyncio
async def test_login_wrong_password_401():
    await _bootstrap("alice", "Passw0rd1")
    with pytest.raises(HTTPException) as exc:
        await login(form_data=_form("alice", "WrongPass1"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_401():
    await _bootstrap("alice", "Passw0rd1")
    with pytest.raises(HTTPException) as exc:
        await login(form_data=_form("nobody", "Passw0rd1"))
    assert exc.value.status_code == 401


# =========================================================================
# get_current_user over the REAL JWT path
# =========================================================================


@pytest.mark.asyncio
async def test_get_current_user_with_real_token():
    await _bootstrap("alice", "Passw0rd1")
    token = create_access_token(data={"sub": "alice"})
    user = await get_current_user(token)
    assert user["username"] == "alice"
    assert user["id"] > 0


@pytest.mark.asyncio
async def test_get_current_user_unknown_sub_401():
    """A well-formed token for a user that no longer exists must 401 —
    deleted-user tokens must not resurrect."""
    await _bootstrap("alice", "Passw0rd1")
    token = create_access_token(data={"sub": "deleted_user"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_forged_token_401():
    await _bootstrap("alice", "Passw0rd1")
    with pytest.raises(HTTPException) as exc:
        await get_current_user("forged.token.here")
    assert exc.value.status_code == 401
