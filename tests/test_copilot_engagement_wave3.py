"""
tests/test_copilot_engagement_wave3.py

Wave 3 unit tests: Enforcing retrieval boundaries.
Verifies:
1. Zero unrelated claims enter candidates or prompts under bound engagement scope.
2. Unknown engagement ownership fails closed.
3. Cross-engagement answers rejected without an explicit request and evidenced relationship.
4. Cross-engagement answers permitted when explicitly requested with evidenced graph relationship.
5. Enterprise policy assertions remain separately retrievable and attributable.
6. Scope, session, and generation bind reusable context; session switch invalidates context.
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    ScopeBinding,
    detect_cross_engagement_request,
    is_assertion_in_scope,
    is_enterprise_policy,
    is_node_in_scope,
    resolve_permitted_engagements,
    verify_evidenced_relationship,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    compute_neighborhood_chains,
    precompute_neighborhoods,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
    SessionContextPacket,
)
from tests.fixtures.copilot_engagement.loader import build_in_memory_graph


def _build_test_graph_with_relationships() -> InMemoryGraphTopology:
    """Builds a rich multi-engagement graph with JARVIS, LENS, NEXUS, and Enterprise Policy."""
    graph = build_in_memory_graph()

    # Add Nexus project node and assertions
    graph.nodes["project:nexus"] = {
        "canonical_name": "Project Nexus",
        "node_type": "project",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }
    graph.nodes["concept:document_ai"] = {
        "canonical_name": "Document AI",
        "node_type": "concept",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }
    graph.assertions["assertion-nexus-01"] = {
        "assertion_id": "assertion-nexus-01",
        "subject_node_id": "project:nexus",
        "object_node_id": "concept:document_ai",
        "predicate": "USES",
        "claim_text": "Project Nexus uses Document AI extraction.",
        "engagement_id": "project:nexus",
        "canonical_status": "ACTIVE",
    }

    # Add an evidenced relationship between JARVIS and NEXUS (shared architecture)
    graph.assertions["assertion-rel-jarvis-nexus"] = {
        "assertion_id": "assertion-rel-jarvis-nexus",
        "subject_node_id": "project:jarvis",
        "object_node_id": "project:nexus",
        "predicate": "INTEGRATES_WITH",
        "claim_text": "Engineering Jarvis integrates with Project Nexus for document extraction.",
        "engagement_id": "project:jarvis",
        "canonical_status": "ACTIVE",
    }

    # Add Enterprise Policy node and assertion
    graph.nodes["policy:secops_standard"] = {
        "canonical_name": "Enterprise SecOps Policy",
        "node_type": "policy",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
    }
    graph.assertions["assertion-policy-01"] = {
        "assertion_id": "assertion-policy-01",
        "subject_node_id": "policy:secops_standard",
        "object_node_id": "concept:security",
        "predicate": "MANDATES",
        "claim_text": "Enterprise SecOps mandates SSO integration for internal portals.",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
        "canonical_status": "ACTIVE",
    }

    # Add an unowned / unknown assertion (missing engagement_id)
    graph.assertions["assertion-unowned-01"] = {
        "assertion_id": "assertion-unowned-01",
        "subject_node_id": "project:jarvis",
        "object_node_id": "concept:unknown",
        "predicate": "PROPOSES",
        "claim_text": "Unverified proposal without engagement attribution.",
        "canonical_status": "ACTIVE",
    }

    return graph


def test_retrieval_boundary_enforcement_zero_unrelated_claims() -> None:
    """Verifies that an engagement-scoped retriever allows zero unrelated claims from other engagements."""
    graph = _build_test_graph_with_relationships()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
        attendees=[{"name": "Shared Engineer"}],
    )

    # Query for "JIRA" - which in LENS has "Spatial File Upload depends on JIRA"
    results = retriever.retrieve_structured_assertions("JIRA dependencies")
    retrieved_ids = [r["assertion_id"] for r in results]

    # LENS assertion MUST NOT be retrieved
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" not in retrieved_ids, (
        "LENS assertion leaked into JARVIS-scoped retrieval"
    )

    # Only JARVIS or permitted assertions
    for r in results:
        aid = r.get("assertion_id")
        raw_a = graph.assertions.get(aid, {})
        assert is_assertion_in_scope(raw_a, "project:jarvis", None), (
            f"Assertion {aid} ({r.get('claim_text')}) is out of scope for project:jarvis"
        )


def test_unknown_engagement_ownership_fails_closed() -> None:
    """Verifies that nodes and assertions with unknown/missing engagement ownership fail closed."""
    graph = _build_test_graph_with_relationships()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
    )

    # Check unowned assertion
    unowned_assertion = graph.assertions["assertion-unowned-01"]
    assert not is_assertion_in_scope(unowned_assertion, "project:jarvis", None), (
        "Assertion without engagement_id must fail closed"
    )

    # Ensure unowned assertion never appears in retrieval results
    results = retriever.retrieve_structured_assertions("Unverified proposal")
    retrieved_ids = [r["assertion_id"] for r in results]
    assert "assertion-unowned-01" not in retrieved_ids, (
        "Unowned assertion leaked into retrieval results"
    )


def test_cross_engagement_rejected_without_evidenced_relationship() -> None:
    """Verifies that cross-engagement retrieval fails when no evidenced graph relationship exists."""
    graph = _build_test_graph_with_relationships()

    # Check relationship verification helper
    assert not verify_evidenced_relationship(graph, "project:jarvis", "project:lens"), (
        "Expected no evidenced relationship between JARVIS and LENS"
    )

    # Detect request without evidenced relationship
    permitted = resolve_permitted_engagements(
        "Can we compare this with LENS computer vision?",
        "project:jarvis",
        graph,
    )
    assert "project:lens" not in permitted, (
        "LENS must not be permitted without an evidenced graph relationship"
    )

    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
    )
    results = retriever.retrieve_structured_assertions("What is the LENS drone inspection timeline?")
    retrieved_ids = [r["assertion_id"] for r in results]
    assert "assertion-lens-01" not in retrieved_ids
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" not in retrieved_ids


def test_cross_engagement_permitted_with_evidenced_relationship() -> None:
    """Verifies that cross-engagement retrieval is permitted when an explicit relationship exists in the graph."""
    graph = _build_test_graph_with_relationships()

    # Evidenced relationship exists between JARVIS and NEXUS
    assert verify_evidenced_relationship(graph, "project:jarvis", "project:nexus"), (
        "Expected evidenced relationship between JARVIS and NEXUS"
    )

    # Request explicitly references Nexus
    permitted = resolve_permitted_engagements(
        "How does JARVIS integrate with Project Nexus?",
        "project:jarvis",
        graph,
    )
    assert "project:nexus" in permitted, (
        "NEXUS must be permitted when explicitly requested with evidenced relationship"
    )

    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
    )
    results = retriever.retrieve_structured_assertions("How does JARVIS integrate with Project Nexus Document AI?")
    retrieved_ids = [r["assertion_id"] for r in results]
    assert "assertion-nexus-01" in retrieved_ids, (
        "Nexus assertion should be retrieved when evidenced relationship exists and query references it"
    )


def test_enterprise_policy_separate_attribution() -> None:
    """Verifies that enterprise policy assertions are retrievable across engagements but maintain separate attribution."""
    graph = _build_test_graph_with_relationships()
    policy_assertion = graph.assertions["assertion-policy-01"]

    assert is_enterprise_policy(policy_assertion), "Enterprise policy must be detected by policy helper"
    assert is_assertion_in_scope(policy_assertion, "project:jarvis", None), (
        "Enterprise policy must be in scope across engagements"
    )

    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
    )
    results = retriever.retrieve_structured_assertions("Enterprise SecOps policy SSO")
    retrieved_ids = [r["assertion_id"] for r in results]
    assert "assertion-policy-01" in retrieved_ids, "Enterprise policy assertion should be retrievable"


def test_scope_binding_and_session_switch_invalidation() -> None:
    """Verifies that scope binding locks context to session/engagement/generation and invalidates on switch."""
    binding = ScopeBinding(
        session_id="session-jarvis-001",
        engagement_id="project:jarvis",
        generation=1,
    )
    assert binding.is_valid("session-jarvis-001", "project:jarvis", 1)
    # Session switch invalidates
    assert not binding.is_valid("session-lens-002", "project:jarvis", 1)
    # Engagement switch invalidates
    assert not binding.is_valid("session-jarvis-001", "project:lens", 1)
    # Generation increment invalidates
    assert not binding.is_valid("session-jarvis-001", "project:jarvis", 2)

    # Retriever invalidation behavior
    graph = _build_test_graph_with_relationships()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-001",
        generation=1,
    )
    # Valid retrieval under matching session
    res = retriever.retrieve_structured_assertions("Metal transcription")
    assert len(res) > 0

    # Invalidate session binding on retriever
    retriever.session_id = "session-other-999"
    res_invalid = retriever.retrieve_structured_assertions("Metal transcription")
    assert len(res_invalid) == 0, "Retrieval must return empty when session binding is invalid"
