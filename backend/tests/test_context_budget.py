"""Tests for the context injection budget breaker (GUIDE-003 T1-2)."""
import pytest

from app.services.context_budget import (
    BudgetFitResult,
    enforce_rag_context_budget,
    estimate_tokens,
    fit_blocks_to_budget,
    truncate_middle,
)


# ---------- estimate_tokens ----------

class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_pure_cjk_uses_weight(self):
        # 10 Han chars * 1.5 = 15
        assert estimate_tokens("一二三四五六七八九十") == 15

    def test_pure_ascii_bytes_over_four(self):
        # "abcdefgh" = 8 bytes / 4 = 2
        assert estimate_tokens("abcdefgh") == 2
        # 5 bytes rounds UP to 2 (never under-count)
        assert estimate_tokens("abcde") == 2

    def test_mixed(self):
        # 4 CJK * 1.5 = 6; " ab" = 3 bytes -> ceil(3/4)=1; total 7
        assert estimate_tokens("你好世界 ab") == 7

    def test_emoji_counts_as_wide(self):
        # East-asian Wide emoji counts at CJK weight, not raw bytes.
        assert estimate_tokens("😀😀") == 3


# ---------- truncate_middle ----------

class TestTruncateMiddle:
    def test_short_passes_through(self):
        assert truncate_middle("短文本", 100) == "短文本"

    def test_long_keeps_head_and_tail_with_marker(self):
        text = "A" * 60 + "B" * 60  # 120 chars
        out = truncate_middle(text, 40)
        assert len(out) == 40
        assert out.startswith("A")
        assert out.endswith("B")
        assert "已省略" in out and "80" in out  # marker names omitted count

    def test_marker_count_is_exact(self):
        text = "X" * 200
        out = truncate_middle(text, 50)
        assert "[已省略 150 字]" in out

    def test_tiny_budget_degrades_to_head_cut(self):
        out = truncate_middle("Y" * 100, 10)
        assert out == "Y" * 10

    def test_zero_budget_returns_empty(self):
        assert truncate_middle("Z" * 50, 0) == ""


# ---------- fit_blocks_to_budget ----------

class TestFitBlocks:
    def test_everything_fits_is_untouched(self):
        src = "块一\n\n块二\n\n块三"
        result = fit_blocks_to_budget(src, budget_tokens=10_000)
        assert result.fitted == src
        assert not result.over_budget
        assert result.dropped_blocks == 0

    def test_tail_block_dropped_with_note(self):
        blocks = ["甲" * 100, "乙" * 100, "丙" * 100]
        src = "\n\n".join(blocks)
        # Each 100-CJK-char block estimates to 150 tokens (total 451); the
        # omission note costs ~27. Budget 400: reservation leaves ~373 for
        # loading, so two blocks fit (300) but not the third (450 > 373);
        # with the note the total is 327 <= 400.
        result = fit_blocks_to_budget(src, budget_tokens=400)
        assert "丙" not in result.fitted
        assert "甲" in result.fitted and "乙" in result.fitted
        assert "省略 1 个资料块" in result.fitted
        assert result.dropped_blocks == 1
        assert result.over_budget
        assert result.used_tokens <= 400

    def test_first_block_alone_over_budget_gets_middle_truncated(self):
        huge = "H" * 5000
        result = fit_blocks_to_budget(huge + "\n\n尾块", budget_tokens=200)
        assert result.truncated_blocks == 1
        assert "已省略" in result.fitted
        assert "尾块" in result.fitted or "省略 1 个资料块" in result.fitted

    def test_nothing_fits_still_keeps_first_block_truncated(self):
        huge = "K" * 4000
        result = fit_blocks_to_budget(huge + "\n\n" + "M" * 4000, budget_tokens=100)
        assert result.fitted  # prompt never empty
        assert "已省略" in result.fitted

    def test_note_line_respects_budget_by_evicting_more(self):
        # Three ASCII blocks of 360 bytes/4 = 90 tokens each; budget 200 fits
        # two (180), the note then evicts one more to reserve its own room.
        blocks = ["N" * 360] * 3
        result = fit_blocks_to_budget("\n\n".join(blocks), budget_tokens=200)
        assert result.used_tokens <= 200
        assert "省略" in result.fitted

    def test_never_returns_note_only_prompt(self):
        # Healthy budget: the sole surviving block must survive eviction
        # (possibly shrunk) — a bare omission note is not acceptable output.
        huge = "Q" * 4000 + "\n\n" + "R" * 4000
        result = fit_blocks_to_budget(huge, budget_tokens=120)
        body = result.fitted.split("…[因上下文长度限制")[0]
        assert "QQQQ" in body or "RRRR" in body

    def test_result_always_within_budget(self):
        blocks = [f"第{i}块" + "内容" * (i * 150) for i in range(6)]
        src = "\n\n".join(blocks)
        for budget in (50, 200, 1000, 5000):
            result = fit_blocks_to_budget(src, budget_tokens=budget)
            assert result.used_tokens <= budget or not result.fitted


# ---------- wiring: enforce_rag_context_budget ----------

class TestEnforceWiring:
    def test_passthrough_when_disabled(self, monkeypatch):
        from app.config import get_settings
        monkeypatch.setattr(
            get_settings(), "CONTEXT_BUDGET_ENABLED", False, raising=False,
        )
        long_src = "长" * 90000
        assert enforce_rag_context_budget(long_src) == long_src

    def test_passthrough_when_empty(self):
        assert enforce_rag_context_budget("") == ""

    def test_huge_context_is_clamped_and_logged(self, caplog):
        blocks = ["内容" * 800] * 20  # ~32000 CJK chars ≈ 48000 est. tokens
        src = "\n\n".join(blocks)
        with caplog.at_level("INFO", logger="app.services.context_budget"):
            out = enforce_rag_context_budget(src)
        assert "context budget:" in caplog.text
        assert len(out) < len(src)
        assert "[Context" not in out  # no fabricated numbering by the breaker

    def test_normal_traffic_never_trips(self):
        # Realistic full load: 8 blocks x 600 CJK chars ≈ 7200 est tokens < 8000.
        blocks = ["字" * 600] * 8
        src = "\n\n".join(f"[Context {i+1}] (from: 文档)\n{b}"
                          for i, b in enumerate(blocks))
        result = fit_blocks_to_budget(src, budget_tokens=8000)
        assert not result.over_budget
