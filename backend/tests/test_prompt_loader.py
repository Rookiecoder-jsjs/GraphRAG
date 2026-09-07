"""Tests for the prompt template loader (GUIDE-003 T2-1)."""
import pytest

from app.prompts import (
    PromptTemplateError,
    assert_templates_exist,
    load_prompt,
)
from app.prompts.templates import TEMPLATE_NAMES


class TestLoader:
    def test_loads_plain_template(self):
        text = load_prompt("rejection_template")
        assert "知识库" in text

    def test_substitutes_placeholders(self):
        text = load_prompt(
            "query_variants", num_variants=3, query="什么是智能体"
        )
        assert "Generate 3" in text
        assert "什么是智能体" in text
        assert "$num_variants" not in text

    def test_literal_braces_need_no_escaping(self):
        # JSON examples in prose must survive rendering untouched.
        text = load_prompt("intent_classify")
        assert '{"intent":' in text
        assert "{{" not in text

    def test_missing_template_raises(self):
        with pytest.raises(PromptTemplateError, match="not found"):
            load_prompt("definitely_not_a_template")

    def test_missing_placeholder_raises_with_names(self):
        # rag_system requires 4 placeholders; none given.
        with pytest.raises(PromptTemplateError, match="placeholder"):
            load_prompt("rag_system")

    def test_extra_placeholders_are_ignored(self):
        text = load_prompt("chitchat_system", unused="x")
        assert "闲聊模式" in text

    def test_dollar_literal_via_double_dollar(self):
        from app.prompts.loader import _read_template, TEMPLATES_DIR
        # Direct Template semantics check without adding a real template:
        from string import Template
        assert Template("cost: $$5").substitute(x=1) == "cost: $5"


class TestRegistry:
    def test_all_registered_templates_exist(self):
        assert_templates_exist(TEMPLATE_NAMES)  # must not raise

    def test_self_check_lists_missing(self):
        with pytest.raises(PromptTemplateError, match="missing prompt templates"):
            assert_templates_exist(["nope_1", "nope_2"])


# ---------- rendered-output anchors (military rule #2 lightweight guard) ----

class TestRenderedAnchors:
    """Key semantic anchors that downstream behavior depends on."""

    def test_rag_prompt_keeps_injection_guard(self):
        prompt = load_prompt(
            "rag_system",
            context_str="[Context 1] data",
            graph_context="", citation_block="", comparison_block="",
        )
        assert "<context>" in prompt and "</context>" in prompt
        assert "DATA" in prompt  # treat-as-data instruction present

    def test_judge_prompt_v2_fields(self):
        prompt = load_prompt("judge")
        assert "overall_confidence" in prompt
        assert "当且仅当" in prompt

    def test_intent_prompt_json_contract(self):
        prompt = load_prompt("intent_classify")
        assert '"intent"' in prompt
        assert "fact_retrieval" in prompt