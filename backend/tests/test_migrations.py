"""Tests for the lightweight schema migration framework."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

import aiosqlite

from app.database import _run_migrations


def test_migrations_stamp_version_and_are_idempotent(tmp_path):
    """_run_migrations records the baseline version and doesn't reapply."""
    async def run():
        db_path = str(tmp_path / "migrate_test.db")
        async with aiosqlite.connect(db_path) as db:
            await _run_migrations(db)
            async with db.execute(
                "SELECT version, name FROM schema_version ORDER BY version"
            ) as cur:
                rows = await cur.fetchall()
            versions = [r[0] for r in rows]
            assert 1 in versions, f"baseline version 1 missing: {versions}"

            # Re-running must not insert duplicate version rows.
            await _run_migrations(db)
            async with db.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ) as cur:
                rows2 = await cur.fetchall()
            assert [r[0] for r in rows2] == versions, "re-run reapplied migrations"

    asyncio.run(run())
