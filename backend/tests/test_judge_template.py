"""Judge prompt must come from the template, not an inline copy (FEAT-029).

templates/README.md rule 1: 所有 LLM 提示词的唯一存放处是 app/prompts/templates/。
eval/judge.py historically loaded the "judge" template and then immediately
OVERWROTE the module attribute with an inline duplicate string — a
template/inline drift trap (they happened to be byte-identical, so nothing
broke, but any future edit to judge.md would silently not apply).

The fix makes the template authoritative. These tests pin that contract:

  * the template file itself renders and carries the v2 anchor phrases,
  * the judge module's prompt attribute equals the rendered template,
  * the module source no longer contains an inline copy of the prompt.
"""
import inspect
from pathlib import Path

from app.prompts import load_prompt

import eval.judge as judge_mod

_ANCHORS = ("当且仅当", "overall_confidence", "显得严格")


def test_judge_template_renders_with_anchors():
    rendered = load_prompt("judge")
    for anchor in _ANCHORS:
        assert anchor in rendered


def test_judge_module_prompt_is_the_template():
    # The module attribute must BE the template render — an inline string
    # that merely happens to match would defeat the whole point.
    assert judge_mod._JUDGE_SYSTEM_PROMPT == load_prompt("judge")


def test_judge_source_has_no_inline_prompt_copy():
    source = inspect.getsource(judge_mod)
    # One anchor phrase is enough: it lives in the prompt text, so if it
    # still appears in judge.py the inline duplicate survives.
    assert "严格、客观的 RAG 质量评估员" not in source


def test_template_is_substitute_safe():
    # load_prompt runs string.Template.substitute — a stray '$' in the text
    # would raise at import time (this also guards future edits).
    rendered = load_prompt("judge")
    assert "$" not in rendered
    assert isinstance(rendered, str) and len(rendered) > 200
