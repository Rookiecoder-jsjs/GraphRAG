"""Pytest configuration.

``get_settings()`` refuses to start with a known placeholder JWT_SECRET (a
security fix). The test suite therefore needs a strong, throwaway value set in
the real environment BEFORE any app module is imported or the settings cache is
populated. pytest imports conftest.py first, so setting it here is safe.

Environment variables take precedence over the ``.env`` file in
pydantic-settings, so this also overrides any placeholder that may sit in
``backend/.env``.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

# Isolate the suite from the real developer database. The endpoint tests
# exercise the real FastAPI lifespan via TestClient, and lifespan runs
# init_db() — against the live backend/data/sqlite/app.db unless we redirect
# it. Point SQLITE_PATH at a throwaway file so tests are hermetic and can
# neither mutate nor depend on real data. (Per-process name keeps parallel
# workers from clobbering each other's file.)
if "SQLITE_PATH" not in os.environ:
    os.environ["SQLITE_PATH"] = str(
        Path(tempfile.gettempdir()) / f"kg-test-{os.getpid()}.db"
    )
