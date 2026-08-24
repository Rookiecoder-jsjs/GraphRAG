"""Prompt template loading — the single gateway to all LLM prompt text.

Prompts live as markdown files under ``templates/`` (mirroring codex-rs'
``prompts/templates/<feature>/<name>.md`` layout). Code only fills slots;
wording iterations never touch business logic and show up in diffs as pure
text changes.

Templates are cached for the process lifetime after first read (same
semantics as codex's ``include_str!``: immutable within a run, zero I/O
after warmup). Editing a template requires a backend restart — documented in
templates/README.md. Missing templates or mismatched placeholders raise at
startup/call time (fail fast) rather than silently sending an empty or
half-rendered prompt to the model.
"""
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Iterable

TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptTemplateError(Exception):
    """Raised when a template is missing or a placeholder cannot be filled."""


@lru_cache(maxsize=None)
def _read_template(name: str) -> str:
    """Read and cache one template file's raw text."""
    path = TEMPLATES_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise PromptTemplateError(
            f"prompt template not found: {path}"
        ) from e


def load_prompt(name: str, **placeholders: str) -> str:
    """Render ``templates/{name}.md`` with ``$placeholder`` substitution.

    Uses ``string.Template`` ($-placeholders) instead of ``str.format`` so
    literal braces in prose (JSON examples etc.) need no escaping — prompt
    text is full of ``{`` from JSON schema blocks.
    """
    raw = _read_template(name).strip()
    try:
        rendered = Template(raw).substitute(**placeholders)
    except KeyError as e:
        raise PromptTemplateError(
            f"template {name!r} is missing placeholder {e}; placeholders "
            f"required: {sorted(p[2:-1] for p in _placeholders_of(raw))}"
        ) from e
    except ValueError as e:
        # Invalid $-syntax in the template itself.
        raise PromptTemplateError(f"template {name!r} malformed: {e}") from e
    return rendered


def _placeholders_of(raw: str) -> list[str]:
    """List $placeholders declared in a raw template (for error messages)."""
    import re

    return re.findall(r"\$\{?[a-zA-Z_][a-zA-Z0-9_]*\}?", raw)


def assert_templates_exist(names: Iterable[str]) -> None:
    """Startup self-check: refuse to boot when any expected template is gone.

    Called from app.main lifespan. A missing template would otherwise only
    explode at the first chat turn, mid-request.
    """
    missing = []
    for name in names:
        if not (TEMPLATES_DIR / f"{name}.md").exists():
            missing.append(name)
    if missing:
        raise PromptTemplateError(
            f"missing prompt templates: {', '.join(sorted(missing))} "
            f"(expected under {TEMPLATES_DIR})"
        )
