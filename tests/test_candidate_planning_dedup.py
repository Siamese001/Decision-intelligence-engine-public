"""Tests for intake candidate planning entity deduplication and backtest comparison normalization."""
import json
import pytest

from dual_pipeline.backtest_core.comparison import _normalize_graph_data, diff_entities_and_assertions
from dual_pipeline.intake_core.planning import _build_planned_candidate


def test_build_planned_candidate_deduplicates_entities():
    candidate = {
        "candidate_id": "cand-01",
        "candidate_type": "architecture_relationship",
        "materiality": "high",
        "classification": "internal",
        "entity_mentions": [
            {"reported_text": "LENS"},
            {"reported_text": "PartnerAdvisory"},
            {"reported_text": "LENS"},  # Duplicate
            {"reported_text": "PartnerAdvisory"},  # Duplicate
            {"reported_text": "Apex Global"},
        ],
    }
    planned = _build_planned_candidate(candidate)
    assert planned["entities_requiring_resolution"] == ["LENS", "PartnerAdvisory", "Apex Global"]
    assert len(planned["entities_requiring_resolution"]) == len(set(planned["entities_requiring_resolution"]))


def test_normalize_graph_data_handles_graph_mutations():
    raw_payload = {
        "meeting_id": "test_meeting",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:lens", "name": "LENS"},
                {"id": "org:partner_advisory", "name": "PartnerAdvisory"},
            ],
            "relationship_signals": [
                {"from_id": "system:lens", "to_id": "org:partner_advisory", "relationship": "OWNED_BY"},
            ],
            "risk_updates": [
                {"id": "risk:01", "claim_text": "Schedule delay risk"},
            ],
        },
    }
    normalized = _normalize_graph_data(raw_payload)
    assert len(normalized.get("entities", [])) == 2
    assert len(normalized.get("relationships", [])) == 1
    assert len(normalized.get("assertions", [])) == 1

    diff = diff_entities_and_assertions({}, raw_payload)
    assert diff["entities_current_count"] == 2
    assert diff["relationships_current_count"] == 1
    assert diff["assertions_current_count"] == 1
