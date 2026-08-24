"""Prompt templates package (GUIDE-003 T2-1).

All LLM prompt text lives in ``templates/*.md``; load through
:mod:`app.prompts.loader` only. See templates/README.md for the catalog.
"""
from app.prompts.loader import (
    PromptTemplateError,
    assert_templates_exist,
    load_prompt,
)

__all__ = ["PromptTemplateError", "assert_templates_exist", "load_prompt"]
