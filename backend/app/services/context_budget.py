"""Context injection budget: hard caps for anything spliced into an LLM prompt.

Rule #1 of the project (CLAUDE.md §二, adopted from openai/codex AGENTS.md):
every injected item must have a bounded size and a hard cap — we never trust
the model to "mind its own length". This module is the single enforcement
point for the RAG ``<context>`` block.

Design notes (vs. codex's truncate.rs):
- Codex estimates tokens as bytes/4, calibrated on English. For CJK text
  UTF-8 costs 3 bytes/char ≈ 0.75 "tokens"/char by that formula, which
  systematically UNDER-counts (qwen tokenizers run ~1.0–1.6 tokens per Han
  character). A breaker must over-estimate rather than under-count, so CJK
  chars get their own weight (settings.CONTEXT_ESTIMATE_CJK_WEIGHT).
- Truncation keeps head + tail and replaces the middle with a marker that
  names how much was dropped (codex: "…N tokens truncated…").
"""
import logging
import math
import unicodedata
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)

# Below this many characters a middle-truncation marker would not fit; fall
# back to a plain head cut so the marker itself is never truncated.
_MIN_MIDDLE_TRUNCATE_CHARS = 20


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed CJK/ASCII text.

    CJK characters count as settings.CONTEXT_ESTIMATE_CJK_WEIGHT each
    (default 1.5 — an upper bound for qwen-family Chinese tokenization);
    everything else counts as len(bytes)/ASCII_BYTES_PER_TOKEN. The total is
    rounded UP: rounding down would let a single 1.5-weight char slip
    through as 1, violating the over-estimate contract.
    """
    if not text:
        return 0
    settings = get_settings()
    cjk_weight = settings.CONTEXT_ESTIMATE_CJK_WEIGHT
    ascii_bytes_per_token = max(1, settings.CONTEXT_ESTIMATE_ASCII_BYTES_PER_TOKEN)

    cjk_chars = 0
    other_bytes = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            cjk_chars += 1
        else:
            other_bytes += len(ch.encode("utf-8"))
    return math.ceil(cjk_chars * cjk_weight + other_bytes / ascii_bytes_per_token)


def truncate_middle(
    text: str,
    max_chars: int,
    marker_fmt: str = "…[已省略 {n} 字]…",
) -> str:
    """Keep head+tail halves, replace the middle with an omission marker.

    Mirrors codex's truncate_middle semantics: strings within budget pass
    through untouched; when ``max_chars`` is too small to fit the marker
    itself (< _MIN_MIDDLE_TRUNCATE_CHARS), degrade to a plain head cut.
    """
    text = text or ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars < _MIN_MIDDLE_TRUNCATE_CHARS:
        return text[:max_chars]

    omitted = len(text) - max_chars
    marker = marker_fmt.format(n=omitted)
    keep = max_chars - len(marker)
    left = keep // 2
    right = keep - left
    return text[:left] + marker + (text[len(text) - right:] if right else "")


@dataclass(frozen=True)
class BudgetFitResult:
    """Outcome of fitting blocks into a token budget."""

    fitted: str           # final assembled string
    used_tokens: int      # estimated usage of ``fitted``
    dropped_blocks: int   # whole blocks dropped
    truncated_blocks: int # blocks middle-truncated in place
    over_budget: bool     # True when any trimming happened


def fit_blocks_to_budget(
    context_str: str,
    budget_tokens: int,
    separator: str = "\n\n",
) -> BudgetFitResult:
    """Greedy block-packing against a token budget. Never truncates mid-block
    except the single first block when it alone exceeds the whole budget.

    Blocks are split on ``separator`` (aligns with the ``[Context N]`` chunk
    layout built by api/chat.py::_build_citation_context). Retrieval results
    arrive relevance-ordered, so dropping from the tail loses the least.
    The omission note (appended when anything was dropped) always reserves
    room for itself by evicting whole blocks or shrinking the last survivor.
    Degenerate corner: when the budget cannot hold ANY content plus the
    note, the note alone is returned — an honest, bounded prompt beats an
    over-budget one (unreachable at the configured 8000-token budget).
    """
    context_str = context_str or ""
    if budget_tokens <= 0 or not context_str.strip():
        return BudgetFitResult(fitted=context_str, used_tokens=0,
                               dropped_blocks=0, truncated_blocks=0,
                               over_budget=False)

    def _chars_for(token_budget: int) -> int:
        # Conservative inverse of estimate_tokens assuming the WORST case
        # (all-CJK at cjk_weight tokens/char). Under-allots ASCII-heavy
        # text (safe direction); floor keeps the omission marker viable.
        per_char = max(get_settings().CONTEXT_ESTIMATE_CJK_WEIGHT, 1.0)
        return max(_MIN_MIDDLE_TRUNCATE_CHARS, int(token_budget / per_char))

    blocks = context_str.split(separator)
    # Reserve room for the omission note up-front when the raw input doesn't
    # fit: otherwise a full greedy pack leaves no space for the note and the
    # eviction loop would evict blocks that actually fit.
    note_cost = estimate_tokens(
        "…[因上下文长度限制，另省略 0 个资料块]…"  # worst-case width
    )
    load_budget = budget_tokens - note_cost if estimate_tokens(context_str) > budget_tokens else budget_tokens

    kept: list[str] = []
    used = 0
    dropped = 0
    truncated = 0

    for block in blocks:
        cost = estimate_tokens(block)
        if not kept and cost > budget_tokens:
            # The very first block alone blows the budget: middle-truncate it
            # so the prompt never goes empty.
            fitted_block = truncate_middle(block, _chars_for(load_budget))
            kept.append(fitted_block)
            truncated += 1
            used = estimate_tokens(fitted_block)
        elif used + cost <= load_budget:
            kept.append(block)
            used += cost
        else:
            dropped += 1

    fitted_str = separator.join(kept)
    if dropped:
        # The note's room was reserved up-front (load_budget); this loop is
        # now only a belt-and-suspenders guard for the pathological case
        # where the kept content itself still crowds out the note.
        while True:
            note = f"…[因上下文长度限制，另省略 {dropped} 个资料块]…"
            note_cost = estimate_tokens(note)
            if used + note_cost <= budget_tokens:
                break
            if len(kept) > 1:
                removed = kept.pop()
                used -= estimate_tokens(removed)
                dropped += 1
                continue
            if kept:
                # Shrink the sole remaining block to make room for the note;
                # if even that can't fit (degenerate budget), fall through to
                # the note-only outcome documented above.
                room = max(budget_tokens - note_cost, _MIN_MIDDLE_TRUNCATE_CHARS)
                shrunk = truncate_middle(kept[0], _chars_for(room))
                if shrunk and estimate_tokens(shrunk) + note_cost <= budget_tokens:
                    used = estimate_tokens(shrunk)
                    kept[0] = shrunk
                    break
                used -= estimate_tokens(kept.pop())
                dropped += 1
                continue
            break
        fitted_str = separator.join(kept)
        note = f"…[因上下文长度限制，另省略 {dropped} 个资料块]…"
        fitted_str = fitted_str + separator + note if fitted_str else note

    return BudgetFitResult(fitted=fitted_str, used_tokens=used,
                           dropped_blocks=dropped, truncated_blocks=truncated,
                           over_budget=bool(dropped or truncated))


def enforce_rag_context_budget(context_str: str) -> str:
    """Wiring wrapper called at the top of build_rag_system_prompt.

    Pass-through when the breaker is disabled (escape hatch); otherwise fits
    the context into CONTEXT_BUDGET_TOKENS and logs one observation line so
    trimming events are visible without being noisy.
    """
    settings = get_settings()
    if not settings.CONTEXT_BUDGET_ENABLED:
        return context_str
    if not (context_str or "").strip():
        return context_str

    original_tokens = estimate_tokens(context_str)
    result = fit_blocks_to_budget(context_str, settings.CONTEXT_BUDGET_TOKENS)
    if result.over_budget:
        logger.info(
            "context budget: %.0f -> %d estimated tokens (dropped=%d, "
            "truncated=%d, budget=%d)",
            original_tokens, result.used_tokens, result.dropped_blocks,
            result.truncated_blocks, settings.CONTEXT_BUDGET_TOKENS,
        )
    return result.fitted
