"""Unit tests for QueryProcessor._normalize_entities.

Regression for the mock-provider stress finding: extract_entities used to
return the raw parsed LLM JSON, so a shape drift (bare strings instead of
{"name","type"} objects) reached retriever's graph channel and crashed every
affected search with AttributeError → 500. The boundary now normalizes.
"""
import pytest

from app.services.query_processor import QueryProcessor


@pytest.mark.parametrize("raw,expected", [
    # Proper shape: kept as-is (with type defaulting applied only when absent).
    ([{"name": "Python", "type": "TECHNOLOGY"}],
     [{"name": "Python", "type": "TECHNOLOGY"}]),
    # Bare strings (the stress-test shape): name-only entities.
    (["alpha variant", "beta variant"],
     [{"name": "alpha variant", "type": "CONCEPT"},
      {"name": "beta variant", "type": "CONCEPT"}]),
    # Junk elements: dropped, never propagated.
    ([42, None, {"no_name": "x"}, {"name": ""}, {}, "kept"],
     [{"name": "kept", "type": "CONCEPT"}]),
    # Non-list payloads: empty.
    ("not a list", []),
    ({"entities": []}, []),
    ([], []),
])
def test_normalize_entities(raw, expected):
    assert QueryProcessor._normalize_entities(raw) == expected


def test_normalize_entities_mixed_shape():
    raw = [{"name": "Neo4j"}, "graph db", 7, {"name": None}]
    assert QueryProcessor._normalize_entities(raw) == [
        {"name": "Neo4j", "type": "CONCEPT"},
        {"name": "graph db", "type": "CONCEPT"},
    ]
