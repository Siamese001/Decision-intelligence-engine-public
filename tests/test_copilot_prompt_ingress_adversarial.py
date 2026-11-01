"""Adversarial and boundary test suite for copilot prompt ingress and topic extraction."""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    filter_assertions_for_utterance_topic,
    parse_copilot_request,
)


@pytest.mark.parametrize(
    "empty_input",
    ["", "   ", "\t\n", None],
)
def test_copilot_ingress_boundary_empty_inputs(empty_input):
    """Category 1: Boundary payload - empty and whitespace inputs must not crash."""
    parsed = parse_copilot_request(raw_request=empty_input)
    assert parsed.inferred_intent == "graph-context"
    assert parsed.target_speaker is None
    assert parsed.topic_query is None
    assert parsed.topic_keywords == []


@pytest.mark.parametrize(
    "malformed_prompt,expected_kw",
    [
        ("///copilot??? !!!@@@ SAP ###", "sap"),
        ("-copilot   \t\t\n  SAP   Maximo   ", "sap"),
        ("what is: SAP/Maximo (RBAC)?", "sap"),
        ("---copilot--- jason: 'SAP tech owner'???", "sap"),
    ],
)
def test_copilot_ingress_malformed_punctuation_and_prefixes(malformed_prompt, expected_kw):
    """Category 2: Malformed input - extreme punctuation, tabs, and abnormal copilot prefixes."""
    parsed = parse_copilot_request(raw_request=malformed_prompt)
    assert expected_kw in parsed.topic_keywords
    assert len(parsed.topic_keywords) >= 1


def test_copilot_ingress_keyword_boundary_cases():
    """Category 3: Boundary - stopwords only vs single character tokens."""
    # Stopwords only
    p_stop = parse_copilot_request(raw_request="what is this and that about who and whom")
    assert p_stop.topic_keywords == []

    # Single character tokens should be filtered (< 2 chars)
    p_single = parse_copilot_request(raw_request="a b c x y z")
    assert p_single.topic_keywords == []


def test_copilot_topic_filtering_empty_and_zero_match_adversarial():
    """Category 4: Boundary & Invariant - empty assertions or disjoint keywords."""
    # Empty assertions list
    assert filter_assertions_for_utterance_topic([], ["sap"]) == []

    # Empty keywords list preserves input assertions without crashing
    dummy = [{"assertion_id": "ast-x", "claim_text": "Random text"}]
    assert filter_assertions_for_utterance_topic(dummy, []) == dummy

    # Completely disjoint topic returns empty list
    res = filter_assertions_for_utterance_topic(dummy, ["unrelated_token_12345"])
    assert res == []
