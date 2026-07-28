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

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")
