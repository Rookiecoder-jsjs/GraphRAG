"""Tests for judge v2: confidence fields + executable criteria (GUIDE-003 T1-1).

The prompt text itself is verified by anchor assertions; parsing behavior is
verified against both v2-shaped and v1-shaped (legacy) judge responses.
"""
import pytest

from eval.judge import (
    _JUDGE_SYSTEM_PROMPT,
    _extract_json_object,
    _fallback_metrics,
    _mean_claim_confidence,
    _to_float,
    judge,
)


# ---------- prompt anchors (lightweight guard per military rule #2) ----------

class TestPromptAnchors:
    def test_has_executable_supported_criterion(self):
        assert "当且仅当" in _JUDGE_SYSTEM_PROMPT
        assert "外部知识" in _JUDGE_SYSTEM_PROMPT

    def test_has_confidence_fields(self):
        assert "confidence" in _JUDGE_SYSTEM_PROMPT
        assert "overall_confidence" in _JUDGE_SYSTEM_PROMPT

    def test_has_anti_sycophancy_rule(self):
        assert "流畅、礼貌" in _JUDGE_SYSTEM_PROMPT  # don't relax for polish

    def test_has_anti_overstrict_rule(self):
        assert "显得严格" in _JUDGE_SYSTEM_PROMPT  # don't deduct to look strict


# ---------- parsing helpers ----------

class TestHelpers:
    def test_to_float_clamps(self):
        assert _to_float("0.7") == 0.7
        assert _to_float(1.5) == 1.0
        assert _to_float(-2) == 0.0
        assert _to_float(None) is None
        assert _to_float(True) == 1.0

    def test_mean_claim_confidence_v2(self):
        claims = [
            {"claim": "a", "supported": True, "confidence": 0.9},
            {"claim": "b", "supported": False, "confidence": 0.3},
        ]
        assert _mean_claim_confidence(claims) == 0.6

    def test_mean_claim_confidence_defaults_for_missing(self):
        # v1-shaped claims without confidence degrade to the default level.
        claims = [{"claim": "a", "supported": True}, {"claim": "b", "supported": True}]
        assert _mean_claim_confidence(claims) == 0.5

    def test_mean_claim_confidence_empty(self):
        assert _mean_claim_confidence([]) is None
        assert _mean_claim_confidence(None) is None


# ---------- end-to-end judge() with mocked LLM ----------

class TestJudgeParsing:
    @pytest.fixture
    def patch_llm(self, monkeypatch):
        """Patch the LLM service factory that eval.judge imports lazily.

        judge() does ``from app.services.llm import get_llm_service`` inside
        the function body, so the patch must land on the source module —
        monkeypatching eval.judge itself finds no such attribute.
        """

        def _install(raw_response: str):
            class _FakeLLM:
                async def chat_complete(self, *args, **kwargs):
                    return raw_response

            import app.services.llm as llm_mod

            async def _fake_get_llm_service():
                return _FakeLLM()

            monkeypatch.setattr(
                llm_mod, "get_llm_service", _fake_get_llm_service
            )

        return _install

    @pytest.mark.asyncio
    async def test_v2_response_yields_judge_confidence(self, patch_llm):
        patch_llm(
            '{"claims": [{"claim": "x", "supported": true, "confidence": 0.8}],'
            ' "answer_relevance": 0.9, "citation_accuracy": 1.0,'
            ' "answer_correctness": 0.8, "overall_confidence": 0.75,'
            ' "notes": "主要依据片段[1]"}'
        )
        metrics = await judge("q", "a [1]", [{"content": "ctx"}])
        assert metrics["judge_confidence"] == 0.75
        assert metrics["faithfulness"] == 1.0
        assert metrics["hallucination_rate"] == 0.0
        assert metrics["notes"] if "notes" in metrics else True

    @pytest.mark.asyncio
    async def test_v1_response_still_parses_without_crash(self, patch_llm):
        # Legacy shape: no confidence/overall_confidence/notes at all.
        patch_llm(
            '{"claims": [{"claim": "x", "supported": false},'
            ' {"claim": "y", "supported": true}],'
            ' "answer_relevance": 0.5, "citation_accuracy": 0.5,'
            ' "answer_correctness": 0.5}'
        )
        metrics = await judge("q", "a", [{"content": "ctx"}])
        # faithfulness algorithm unchanged from v1: 1 of 2 supported.
        assert metrics["faithfulness"] == 0.5
        # Confidence falls back to the per-claim default mean.
        assert metrics["judge_confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_fallback_path_has_no_judge_confidence(self, patch_llm, monkeypatch):
        # LLM explodes -> keyword fallback; field absence means low trust.
        import app.services.llm as llm_mod

        async def _boom():
            raise RuntimeError("provider down")

        monkeypatch.setattr(llm_mod, "get_llm_service", _boom)
        metrics = await judge(
            "q", "答案含关键词算法", [], expected_keywords=["算法"]
        )
        assert "judge_confidence" not in metrics
        assert metrics["answer_correctness"] > 0

    @pytest.mark.asyncio
    async def test_unparseable_json_falls_back(self, patch_llm):
        patch_llm("这不是JSON")
        metrics = await judge(
            "q", "包含机器学习", [], expected_keywords=["机器学习"]
        )
        assert "judge_confidence" not in metrics
        assert "faithfulness" not in metrics

    @pytest.mark.asyncio
    async def test_out_of_range_confidence_is_clamped(self, patch_llm):
        patch_llm(
            '{"claims": [], "answer_correctness": 0.5, "overall_confidence": 7}'
        )
        metrics = await judge("q", "a", [])
        assert metrics["judge_confidence"] == 1.0


class TestFallback:
    def test_no_keywords_no_metrics(self):
        assert _fallback_metrics("any", None) == {}
