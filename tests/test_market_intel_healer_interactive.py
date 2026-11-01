"""Regression for the weekly market intelligence interactive preview path."""

import pytest

from dual_pipeline.transcript_qa_core.market_intel_healer import heal_market_intel_mutations


@pytest.mark.parametrize("raw_predicate", ["SELECTED_TO_DEVELOP", "DEVELOPER_OF"])
def test_project_star_signal_normalizes_both_predicate_fields(raw_predicate: str) -> None:
    signal = {
        "signal_id": "rel-apex_global-energy--project-star--DEVELOPS",
        "from_id": "org:apex_global-energy",
        "to_id": "project:project-star",
        "relationship": "DEVELOPS",
        "predicate": raw_predicate,
    }
    data = {
        "meeting_id": "document-weekly-market-intelligence",
        "graph_mutations": {"entity_upserts": [], "relationship_signals": [signal]},
    }
    changes: list[str] = []

    heal_market_intel_mutations(data, {"is_document": True}, changes)

    assert signal["relationship"] == "DEVELOPS"
    assert signal["predicate"] == "DEVELOPS"
    assert signal["signal_id"] == "rel-apex_global-energy--project-star--DEVELOPS"
    assert "Mapped Apex Global/Project Star predicate to 'DEVELOPS'" in changes


def test_unrelated_signal_is_not_rewritten() -> None:
    signal = {
        "signal_id": "rel-other--project--SELECTED_TO_DEVELOP",
        "from_id": "org:other",
        "to_id": "project:other",
        "predicate": "SELECTED_TO_DEVELOP",
    }
    data = {
        "meeting_id": "document-weekly-market-intelligence",
        "graph_mutations": {"entity_upserts": [], "relationship_signals": [signal]},
    }

    heal_market_intel_mutations(data, {"is_document": True}, [])

    assert signal["predicate"] == "SELECTED_TO_DEVELOP"


def test_transformer_decision_requires_source_and_matching_evidence() -> None:
    data = {
        "meeting_id": "document-20261006",
        "evidence_index": {"ev-04": {"text": "Transformer procurement is proposed."}},
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
    }
    source = "Programmatic advance procurement of transformers is proposed."

    heal_market_intel_mutations(data, {"is_document": True}, [])
    assert data["graph_mutations"].get("decision_updates") is None

    heal_market_intel_mutations(data, {"is_document": True, "source_text": source}, [])
    decisions = data["graph_mutations"]["decision_updates"]
    assert len(decisions) == 1
    assert decisions[0]["evidence_ids"] == ["ev-04"]
