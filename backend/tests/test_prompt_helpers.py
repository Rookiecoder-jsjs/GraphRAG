"""Tests for the shared RAG prompt + extraction helpers.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_prompt_helpers.py

Coverage:
  1. _normalize_relation_type folds free-form labels onto the controlled
     vocabulary (canonical passthrough / English+Chinese synonyms / unknown
     -> RELATED_TO / empty -> RELATED_TO)
  2. _parse_extraction_response applies that normalization to relations
  3. build_graph_context shows triples AND isolated entity names, never
     silently dropping an entity; returns "" when there is nothing
  4. build_rag_system_prompt renders graph facts INSIDE <context> (so the
     prompt-injection guard covers them) plus citation/comparison blocks
  5. chat_complete appends a truncation marker when finish_reason == length
     (opt-in) and leaves JSON-producing calls untouched
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.llm import (  # noqa: E402
    LLMService,
    _CITATION_INSTRUCTION,
    _RELATION_TYPES,
    _normalize_relation_type,
    build_graph_context,
    build_rag_system_prompt,
)


# =========================================================================
# Standalone runner
# =========================================================================

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


# =========================================================================
# 1. _normalize_relation_type
# =========================================================================

def test_relation_type_normalization():
    """Free-form labels map onto the controlled vocabulary; unknown -> RELATED_TO."""
    # Canonical passthrough (case-insensitive).
    check("canonical 'PART_OF' stays",
          _normalize_relation_type("PART_OF") == "PART_OF")
    check("canonical 'located_in' uppercased",
          _normalize_relation_type("located_in") == "LOCATED_IN")
    # English synonyms.
    check("'is part of' -> PART_OF",
          _normalize_relation_type("is part of") == "PART_OF")
    check("'belongs_to' -> PART_OF",
          _normalize_relation_type("belongs_to") == "PART_OF")
    check("'located at' -> LOCATED_IN",
          _normalize_relation_type("located at") == "LOCATED_IN")
    check("'works for' -> WORKS_AT",
          _normalize_relation_type("works for") == "WORKS_AT")
    # Chinese synonyms.
    check("'属于' -> PART_OF",
          _normalize_relation_type("属于") == "PART_OF")
    check("'位于' -> LOCATED_IN",
          _normalize_relation_type("位于") == "LOCATED_IN")
    # Unknown / empty -> RELATED_TO (never a new free-form type).
    check("unknown 'betrays' -> RELATED_TO",
          _normalize_relation_type("betrays") == "RELATED_TO")
    check("empty -> RELATED_TO",
          _normalize_relation_type("") == "RELATED_TO")
    check("None -> RELATED_TO",
          _normalize_relation_type(None) == "RELATED_TO")


# =========================================================================
# 2. _parse_extraction_response applies normalization
# =========================================================================

def test_parse_extraction_response_normalizes_relations():
    svc = LLMService()
    svc.api_key = "test-key"  # not needed for parsing, keeps the double valid
    raw = json.dumps({
        "entities": [{"name": "A", "type": "ORGANIZATION", "description": ""}],
        "relations": [
            {"source": "A", "target": "B", "relation_type": "belongs to"},
            {"source": "A", "target": "C", "relation_type": "随机未知类型"},
        ],
    })
    result = svc._parse_extraction_response(raw)
    types = [r["relation_type"] for r in result["relations"]]
    check("relation 'belongs to' -> PART_OF",
          types == ["PART_OF", "RELATED_TO"])
    # Sources/targets survive untouched.
    check("relation source/target preserved",
          result["relations"][0]["source"] == "A"
          and result["relations"][0]["target"] == "B")


# =========================================================================
# 3. build_graph_context
# =========================================================================

def test_build_graph_context_shows_triples_and_isolated_entities():
    ctx = build_graph_context(
        related_entities=[
            {"name": "华为"}, {"name": "清华大学"}, {"name": "孤岛节点"},
        ],
        related_relations=[
            {"source": "华为", "target": "清华大学", "relation_type": "COLLABORATES_WITH"},
        ],
    )
    check("triple rendered",
          "Related Knowledge Graph Facts: (华为) -[COLLABORATES_WITH]-> (清华大学)" in ctx)
    # Isolated entity must NOT vanish just because a triple exists.
    check("isolated entity still listed",
          "Related Entities: 孤岛节点" in ctx)
    # Entity already carried by a triple is not repeated redundantly.
    check("triple-linked names not repeated",
          "Related Entities: 华为" not in ctx and "Related Entities: 清华大学" not in ctx)


def test_build_graph_context_entities_only_and_empty():
    check("entities-only fallback",
          "Related Entities: 华为, 北京" in build_graph_context(
              related_entities=[{"name": "华为"}, {"name": "北京"}]
          ))
    check("nothing -> empty string",
          build_graph_context() == "")


# =========================================================================
# 4. build_rag_system_prompt — graph facts inside <context>
# =========================================================================

def test_rag_prompt_contains_graph_facts_and_blocks():
    sp = build_rag_system_prompt(
        context_str="[Context 1] (from: A)\n正文",
        related_relations=[
            {"source": "X", "target": "Y", "relation_type": "PART_OF"},
        ],
        citation_instruction=_CITATION_INSTRUCTION,
        comparison_mode=True,
    )
    check("graph facts present", "Related Knowledge Graph Facts:" in sp)
    check("graph facts INSIDE <context>",
          sp.index("Related Knowledge Graph Facts:") > sp.index("<context>")
          and sp.index("Related Knowledge Graph Facts:") < sp.index("</context>"))
    check("citation block present", "CITATIONS:" in sp)
    check("comparison block present", "COMPARISON MODE:" in sp)
    # Once </context> closes, the only trailing blocks are citation/comparison
    # — never graph facts (which sit under the DATA guard).
    tail = sp[sp.index("</context>") + len("</context>"):]
    check("no graph facts after </context>",
          "Related Knowledge Graph Facts:" not in tail)


def test_rag_prompt_empty_context_still_closes_tag():
    # The intro prose mentions "<context>" in words, so assert on the
    # rendered tag pair at the end rather than a global count.
    sp = build_rag_system_prompt(context_str="")
    check("empty context still renders a closed <context> block",
          "<context>\n\n</context>" in sp or "<context>\n</context>" in sp)
    check("prompt ends with the closing tag",
          sp.rstrip().endswith("</context>"))


# =========================================================================
# 5. chat_complete truncation marker (opt-in)
# =========================================================================

class _FakeHTTPClient:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def post(self, url, headers=None, json=None):
        outcome = self._outcomes[self.calls] if self.calls < len(self._outcomes) else self._outcomes[-1]
        self.calls += 1
        return outcome


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _make_llm_service(client) -> LLMService:
    svc = LLMService()
    svc.api_key = "test-key"
    svc._client = client
    return svc


def test_chat_complete_appends_marker_on_length():
    """finish_reason == 'length' + opt-in marker -> marker appended."""
    svc = _make_llm_service(_FakeHTTPClient([
        _FakeResponse({"choices": [{
            "finish_reason": "length",
            "message": {"content": "前半段回答"},
        }]}),
    ]))
    out = asyncio.run(svc.chat_complete(
        [{"role": "user", "content": "hi"}],
        truncation_marker="…（已截断）",
    ))
    check("truncated answer gets marker", out == "前半段回答…（已截断）")


def test_chat_complete_no_marker_keeps_json_clean():
    """Without a marker (extraction/rewrite calls), finish_reason=length must
    NOT inject text into what is parsed as JSON."""
    svc = _make_llm_service(_FakeHTTPClient([
        _FakeResponse({"choices": [{
            "finish_reason": "length",
            "message": {"content": "[{\"name\": \"A\"}]"},
        }]}),
    ]))
    out = asyncio.run(svc.chat_complete(
        [{"role": "user", "content": "hi"}],
    ))
    check("no marker -> content unchanged", out == '[{"name": "A"}]')


def test_chat_complete_normal_finish_ignores_marker():
    svc = _make_llm_service(_FakeHTTPClient([
        _FakeResponse({"choices": [{
            "finish_reason": "stop",
            "message": {"content": "完整回答"},
        }]}),
    ]))
    out = asyncio.run(svc.chat_complete(
        [{"role": "user", "content": "hi"}],
        truncation_marker="…（已截断）",
    ))
    check("normal finish -> untouched", out == "完整回答")


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_relation_type_normalization,
    test_parse_extraction_response_normalizes_relations,
    test_build_graph_context_shows_triples_and_isolated_entities,
    test_build_graph_context_entities_only_and_empty,
    test_rag_prompt_contains_graph_facts_and_blocks,
    test_rag_prompt_empty_context_still_closes_tag,
    test_chat_complete_appends_marker_on_length,
    test_chat_complete_no_marker_keeps_json_clean,
    test_chat_complete_normal_finish_ignores_marker,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for prompt helpers...")
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\n{len(ALL_TESTS) - len(_failures)}/{len(ALL_TESTS)} checks passed")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
