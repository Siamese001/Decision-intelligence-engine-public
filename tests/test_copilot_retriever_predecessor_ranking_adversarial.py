"""
tests/test_copilot_retriever_predecessor_ranking_adversarial.py

Adversarial test suite verifying provenance ranking weights (1.0x primary vs 0.75x predecessor),
predecessor explicit query boost (1.15x), prompt formatting with [Predecessor Context: <Origin>],
and sub-millisecond retrieval latency (< 1.0ms for 100 queries).
"""
from __future__ import annotations

import concurrent.futures
import time
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    _tokenize_text,
)


def _build_ranking_adversarial_graph() -> Any:
    """Builds a topology with conflicting primary and predecessor claims for identical topics."""
    nodes: Dict[str, Any] = {
        "project:jarvis": {
            "canonical_name": "Project Jarvis",
            "node_type": "project",
            "engagement_id": "project:jarvis",
            "namespace": "CANONICAL",
        },
        "project:nexus": {
            "canonical_name": "Project Nexus",
            "node_type": "project",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "system:sap": {
            "canonical_name": "SAP ERP",
            "node_type": "system",
            "engagement_id": "project:jarvis",
            "namespace": "CANONICAL",
        },
        "person:primary_lead": {
            "canonical_name": "Alice Primary",
            "node_type": "person",
            "engagement_id": "project:jarvis",
            "namespace": "CANONICAL",
        },
        "person:predecessor_lead": {
            "canonical_name": "Bob Predecessor",
            "node_type": "person",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "policy:data_retention": {
            "canonical_name": "Enterprise Data Retention Policy",
            "node_type": "policy",
            "is_enterprise_policy": True,
            "namespace": "ENTERPRISE_POLICY",
        },
        "node:c19_quarantined": {
            "canonical_name": "Executive Career Positioning",
            "node_type": "person",
            "engagement_id": "project:jarvis",
            "notes": "Targeting $1.2M compensation trajectory",
            "namespace": "INTERNAL_ADVISORY",
        },
    }

    assertions: Dict[str, Any] = {
        "asrt_rel_predecessor": {
            "assertion_id": "asrt_rel_predecessor",
            "subject_node_id": "project:jarvis",
            "predicate": "PREDECESSOR_PROJECT",
            "object_node_id": "project:nexus",
            "claim_text": "Project Nexus is the predecessor of Project Jarvis.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        },
        "asrt_primary_sap": {
            "assertion_id": "asrt_primary_sap",
            "subject_node_id": "person:primary_lead",
            "predicate": "OWNS",
            "object_node_id": "system:sap",
            "claim_text": "Alice Primary owns SAP ERP architecture for Jarvis.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        },
        "asrt_predecessor_sap": {
            "assertion_id": "asrt_predecessor_sap",
            "subject_node_id": "person:predecessor_lead",
            "predicate": "OWNS",
            "object_node_id": "system:sap",
            "claim_text": "Bob Predecessor owns SAP ERP architecture for Nexus legacy.",
            "engagement_id": "project:nexus",
            "canonical_status": "ACTIVE",
        },
        "asrt_policy_sap": {
            "assertion_id": "asrt_policy_sap",
            "subject_node_id": "policy:data_retention",
            "predicate": "MANDATES",
            "object_node_id": "system:sap",
            "claim_text": "Enterprise Data Retention Policy mandates SAP audit logging.",
            "is_enterprise_policy": True,
            "canonical_status": "ACTIVE",
        },
        "asrt_c19_leak_attempt": {
            "assertion_id": "asrt_c19_leak_attempt",
            "subject_node_id": "node:c19_quarantined",
            "predicate": "TRACKS",
            "object_node_id": "system:sap",
            "claim_text": "SAP deployment provides career leverage and compensation negotiation power.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        },
    }

    return SimpleNamespace(
        nodes=nodes,
        assertions=assertions,
        forward_adjacency={
            "project:jarvis": [{"to_node": "project:nexus", "relationship_family": "PREDECESSOR_PROJECT"}],
            "person:primary_lead": [{"to_node": "system:sap", "relationship_family": "OWNS"}],
            "person:predecessor_lead": [{"to_node": "system:sap", "relationship_family": "OWNS"}],
        },
    )


def test_primary_assertion_outranks_predecessor_on_identical_match() -> None:
    """Confirms primary engagement assertion outranks predecessor assertion due to 1.0x vs 0.75x weighting."""
    graph = _build_ranking_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-adv-001",
    )

    query = "Who owns SAP ERP architecture?"
    results = retriever.retrieve_structured_assertions(query, max_results=5)
    assert len(results) >= 2

    # First result must be the primary lead
    assert results[0]["assertion_id"] == "asrt_primary_sap"
    assert results[0]["is_primary"] is True
    assert results[0]["provenance_scope"] == "PRIMARY"

    # Predecessor assertion should be present but ranked lower
    aids = [r["assertion_id"] for r in results]
    assert "asrt_predecessor_sap" in aids
    assert aids.index("asrt_primary_sap") < aids.index("asrt_predecessor_sap")

    # Score inspection: primary multiplier (1.0) vs predecessor (0.75)
    q_tokens = _tokenize_text(query)
    primary_asrt = next(r for r in results if r["assertion_id"] == "asrt_primary_sap")
    pred_asrt = next(r for r in results if r["assertion_id"] == "asrt_predecessor_sap")

    primary_score = retriever._score_assertion(primary_asrt, q_tokens, query)
    pred_score = retriever._score_assertion(pred_asrt, q_tokens, query)

    assert primary_score > pred_score
    # Both have identical token overlaps; ratio reflects primary vs predecessor provenance (1.0/0.75) and affinity bonus (1.25/1.10)
    expected_ratio = (1.0 * 1.25) / (0.75 * 1.10)
    assert pytest.approx(primary_score / pred_score, rel=1e-2) == expected_ratio



def test_explicit_query_boost_for_predecessor() -> None:
    """Confirms explicit query mention of 'nexus' boosts predecessor score by 1.15x."""
    graph = _build_ranking_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-adv-002",
    )

    pred_records = retriever._salient_assertions.get("person:predecessor_lead", [])
    assert len(pred_records) > 0
    pred_asrt = pred_records[0]

    tokens_base = _tokenize_text("Who owns SAP ERP architecture?")
    tokens_nexus = _tokenize_text("Who owns SAP ERP architecture in nexus?")

    # Calculate score with and without 'nexus' query mention
    score_unboosted = retriever._score_assertion(pred_asrt, tokens_base, "Who owns SAP ERP architecture?")
    score_boosted = retriever._score_assertion(pred_asrt, tokens_base, "Who owns SAP ERP architecture in nexus?")

    # With the same tokens_base, the explicit query text containing 'nexus' triggers the 1.15x boost
    assert pytest.approx(score_boosted / score_unboosted, rel=1e-3) == 1.15


def test_format_node_context_appends_predecessor_label() -> None:
    """Verifies that format_node_context appends [Predecessor Context: <Origin>] only for predecessor nodes."""
    graph = _build_ranking_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-adv-003",
    )

    # 1. Predecessor node formatting
    pred_lines = retriever._format_node_context("person:predecessor_lead", max_assertions_per_entity=3)
    pred_claim_lines = [l for l in pred_lines if l.startswith("  *")]
    assert len(pred_claim_lines) > 0
    for l in pred_claim_lines:
        assert "[Predecessor Context: project:nexus]" in l

    # 2. Primary node formatting must not have predecessor tag
    primary_lines = retriever._format_node_context("person:primary_lead", max_assertions_per_entity=3)
    primary_claim_lines = [l for l in primary_lines if l.startswith("  *")]
    assert len(primary_claim_lines) > 0
    for l in primary_claim_lines:
        assert "[Predecessor Context:" not in l

    # 3. retrieve_context contains the predecessor label
    context_str = retriever.retrieve_context("Bob Predecessor SAP")
    assert "[Predecessor Context: project:nexus]" in context_str


def test_rule_c19_career_firewall_blocks_quarantined_claims() -> None:
    """Verifies that career leverage claims and quarantined nodes never enter _salient_assertions."""
    graph = _build_ranking_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-adv-004",
    )

    assert "node:c19_quarantined" not in retriever._allowed_nodes
    assert "node:c19_quarantined" not in retriever._salient_assertions

    results = retriever.retrieve_structured_assertions("compensation leverage SAP", max_results=10)
    for r in results:
        assert r["assertion_id"] != "asrt_c19_leak_attempt"
        claim = r.get("claim_text", "")
        assert "$1.2M" not in claim
        assert "career leverage" not in claim.lower()


def test_retrieval_latency_under_1ms_for_100_concurrent_queries() -> None:
    """Benchmarks 100 concurrent retrieval queries asserting latency strictly < 1.0ms per query."""
    graph = _build_ranking_adversarial_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-adv-005",
    )

    queries = [
        "Who owns SAP ERP architecture?",
        "What does Alice Primary manage?",
        "Tell me about Project Nexus predecessor.",
        "Enterprise Data Retention Policy for SAP",
        "Who leads SAP integration?",
    ] * 20  # 100 queries total

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(retriever.retrieve_structured_assertions, q, 5) for q in queries]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            assert isinstance(res, list)
    elapsed_total_s = time.perf_counter() - t0
    avg_latency_ms = (elapsed_total_s / len(queries)) * 1000

    assert avg_latency_ms < 1.0, f"Average query latency {avg_latency_ms:.3f}ms exceeded 1.0ms limit"
