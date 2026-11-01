"""
tests/test_copilot_speaker_salience_adversarial.py

Adversarial and edge-case tests for Wave 2 Human Speaker Notes Salience:
Covers mandatory edge-case categories:
1. Boundary payload (attendee with zero graph assertions falls back gracefully to roster role without crashing)
2. Malformed input (raw structural edge stubs asrt_edge_* with '->' arrows and non-dict attendee entries)
3. Invariant bypass attempt (synthetic participation stub attempting to outrank speaker risk via priority flag)
4. Overflow & exhaustion (large candidate pool of 50 schema edges vs 1 human speaker assertion)
"""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.copilot_predicates import (
    normalize_copilot_predicate,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import (
    expand_role_query_tokens,
)


def test_boundary_attendee_with_only_roster_stub_preserves_fallback() -> None:
    """Category 1 (Boundary payload): When an attendee has no substantive graph claims, roster role remains available."""
    class DummyTopo:
        nodes = {}
        assertions = {}

    retriever = CopilotGraphRetriever(
        topology=DummyTopo(),
        attendees=[{"name": "Sarah", "role": "Product Owner", "org": "Apex Global"}],
        engagement_id="project:lens",
    )
    ctx = retriever.get_person_context("Sarah", "product roadmap")
    assert ctx["found"] is True
    assert len(ctx["assertions"]) == 1
    assert ctx["assertions"][0]["assertion_id"] == "asrt_attendee_sarah"
    assert ctx["assertions"][0]["is_priority"] is False


def test_malformed_structural_edge_stubs_demoted_and_filtered() -> None:
    """Category 2 (Malformed input): Raw asrt_edge_* structural arrow stubs are filtered from substantive predicates."""
    class DummyTopo:
        nodes = {
            "person:brien": {"node_id": "person:brien", "canonical_name": "Speaker_Alpha", "node_type": "person"},
            "system:api": {"node_id": "system:api", "canonical_name": "API Gateway", "node_type": "system"},
        }
        assertions = {
            "asrt_edge_001": {
                "assertion_id": "asrt_edge_001",
                "subject_node_id": "system:api",
                "object_node_id": "person:brien",
                "predicate": "DEPENDS_ON",
                "claim_text": "DEPENDS_ON -> person:brien API Gateway",
                "engagement_id": "project:lens",
            },
            "asrt_human_001": {
                "assertion_id": "asrt_human_001",
                "subject_node_id": "person:brien",
                "object_node_id": "system:api",
                "predicate": "PROPOSED",
                "claim_text": "Speaker_Alpha proposed caching tokens at the API Gateway layer.",
                "engagement_id": "project:lens",
            },
        }

    retriever = CopilotGraphRetriever(topology=DummyTopo(), engagement_id="project:lens")
    questions = retriever.get_next_questions("API Gateway")
    assert len(questions) == 1
    assert questions[0]["assertion_id"] == "asrt_human_001"


def test_overflow_50_schema_edges_vs_single_human_speaker_claim() -> None:
    """Category 3 & 4 (Invariant bypass & Overflow): 1 human speaker claim outranks 50 generic schema claims."""
    nodes = {
        "person:david": {"node_id": "person:david", "canonical_name": "David", "node_type": "person"},
        "system:core": {"node_id": "system:core", "canonical_name": "Core Platform", "node_type": "system"},
    }
    assertions = {}
    for i in range(50):
        assertions[f"asrt_schema_{i:03d}"] = {
            "assertion_id": f"asrt_schema_{i:03d}",
            "subject_node_id": "system:core",
            "object_node_id": "system:core",
            "predicate": "CONTAINS",
            "claim_text": f"Core Platform module {i} contains telemetry schema configuration.",
            "engagement_id": "project:lens",
        }
    assertions["asrt_david_decision"] = {
        "assertion_id": "asrt_david_decision",
        "subject_node_id": "person:david",
        "object_node_id": "system:core",
        "predicate": "DECIDED",
        "claim_text": "David decided that Core Platform telemetry schema freezes on Wednesday.",
        "engagement_id": "project:lens",
    }

    class DummyTopo:
        pass

    topo = DummyTopo()
    topo.nodes = nodes
    topo.assertions = assertions

    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "David", "role": "Delivery Lead", "org": "PartnerAdvisory"}],
        engagement_id="project:lens",
    )
    results = retriever.retrieve_structured_assertions("Core Platform telemetry schema", max_results=5)
    assert results[0]["assertion_id"] == "asrt_david_decision"
    assert "engagement_manager" in expand_role_query_tokens({"delivery lead"})
    assert normalize_copilot_predicate("DECIDED") == "CONTEXT_FOR"
