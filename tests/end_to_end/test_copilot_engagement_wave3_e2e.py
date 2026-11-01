"""
tests/end_to_end/test_copilot_engagement_wave3_e2e.py

Wave 3 E2E lifecycle acceptance tests:
Verifies full end-to-end copilot retrieval boundaries, prefetch packets,
cross-engagement request handling, and session switch invalidation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import ScopeBinding
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
    SessionContextPacket,
    prefetch_session_packet,
)
from tests.fixtures.copilot_engagement.loader import build_in_memory_graph


def _setup_multi_engagement_graph() -> InMemoryGraphTopology:
    graph = build_in_memory_graph()

    # Add Nexus with an evidenced relationship to JARVIS
    graph.nodes["project:nexus"] = {
        "canonical_name": "Project Nexus",
        "node_type": "project",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }
    graph.nodes["capability:ocr"] = {
        "canonical_name": "Document OCR",
        "node_type": "capability",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }
    graph.assertions["assertion-nexus-ocr"] = {
        "assertion_id": "assertion-nexus-ocr",
        "subject_node_id": "project:nexus",
        "object_node_id": "capability:ocr",
        "predicate": "PROVIDES",
        "claim_text": "Project Nexus provides Document OCR pipelines.",
        "engagement_id": "project:nexus",
        "canonical_status": "ACTIVE",
    }
    graph.assertions["assertion-jarvis-nexus-link"] = {
        "assertion_id": "assertion-jarvis-nexus-link",
        "subject_node_id": "project:jarvis",
        "object_node_id": "project:nexus",
        "predicate": "INTEGRATES_WITH",
        "claim_text": "Engineering Jarvis integrates with Project Nexus for OCR processing.",
        "engagement_id": "project:jarvis",
        "canonical_status": "ACTIVE",
    }

    # Add Enterprise Policy
    graph.nodes["policy:identity_governance"] = {
        "canonical_name": "Identity Governance",
        "node_type": "policy",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
    }
    graph.assertions["assertion-policy-id"] = {
        "assertion_id": "assertion-policy-id",
        "subject_node_id": "policy:identity_governance",
        "object_node_id": "concept:security",
        "predicate": "REQUIRES",
        "claim_text": "Enterprise Identity Governance requires centralized Okta SSO authentication.",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
        "canonical_status": "ACTIVE",
    }

    return graph


def test_e2e_copilot_retrieval_and_prefetch_boundary_lifecycle() -> None:
    """
    E2E test verifying that prefetch packets and continuous retrieval
    maintain strict engagement isolation across JARVIS and LENS.
    """
    graph = _setup_multi_engagement_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-e2e",
        attendees=[{"name": "Alex Rivera"}, {"name": "Shared Engineer"}],
        graph_sha256="fake-sha-e2e",
        generation=1,
    )

    # 1. Prefetch packet creation
    bridge = CopilotPrefetchBridge(retriever=retriever)
    packet = bridge.prefetch(
        project_name="JARVIS",
        meeting_name="Engineering Jarvis Weekly Sync",
        attendees=[{"name": "Alex Rivera"}, {"name": "Shared Engineer"}],
        engagement_id="project:jarvis",
        session_id="session-jarvis-e2e",
    )

    assert packet.engagement_id == "project:jarvis"
    assert packet.session_id == "session-jarvis-e2e"
    assert packet.prefetch_status in ("SUCCESS", "PARTIAL")

    # Verify zero LENS claims in packet collections
    all_packet_claims = [
        str(it.get("claim") or it.get("text") or it.get("claim_text") or "")
        for it in (*packet.open_decisions, *packet.open_risks, *packet.open_action_items, *packet.recent_assertions)
    ]
    for claim in all_packet_claims:
        assert "Spatial File Upload" not in claim, f"Leaked LENS claim: {claim}"
        assert "LENS" not in claim, f"Leaked LENS claim: {claim}"

    # Verify formatted prompt does not leak LENS
    prompt = packet.format_for_copilot_prompt()
    assert "LENS" not in prompt, "LENS leaked into formatted copilot prompt"
    assert "Spatial File Upload" not in prompt

    # 2. Continuous Retrieval under JARVIS scope
    # Query for JIRA (present in LENS)
    jira_results = retriever.retrieve_structured_assertions("What are the JIRA ticket dependencies?")
    jira_claims = [r.get("claim_text", "") for r in jira_results]
    for c in jira_claims:
        assert "Spatial File Upload" not in c
        assert "LENS" not in c

    # Query for general risk
    risk_results = retriever.retrieve_structured_assertions("What risks were discussed?")
    for r in risk_results:
        assert r.get("assertion_id") != "assertion-b7cd2f78b3a1c4995bd4cfb1ee31"


def test_e2e_cross_engagement_request_with_evidenced_relationship_lifecycle() -> None:
    """
    E2E test verifying that cross-engagement retrieval succeeds when
    explicitly requested with an evidenced graph relationship, and fails when unevidenced.
    """
    graph = _setup_multi_engagement_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-e2e",
        generation=1,
    )

    # Evidenced relationship: JARVIS -> Nexus
    nexus_query = "Can you check the Project Nexus Document OCR pipeline status?"
    nexus_results = retriever.retrieve_structured_assertions(nexus_query)
    nexus_aids = [r["assertion_id"] for r in nexus_results]
    assert "assertion-nexus-ocr" in nexus_aids, (
        "Nexus assertion must be retrieved when query explicitly requests Nexus and an evidenced link exists"
    )

    # Unevidenced relationship: JARVIS -> LENS
    lens_query = "Can you compare our progress with Project LENS drone inspection computer vision?"
    lens_results = retriever.retrieve_structured_assertions(lens_query)
    lens_aids = [r["assertion_id"] for r in lens_results]
    assert "assertion-lens-01" not in lens_aids, "LENS assertion must not be retrieved without an evidenced link"
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" not in lens_aids


def test_e2e_session_switch_and_delayed_invalidation_lifecycle() -> None:
    """
    E2E test verifying that concurrent sessions maintain isolated scopes
    and that switching session IDs invalidates pending context.
    """
    graph = _setup_multi_engagement_graph()

    # Session A: JARVIS
    retriever_a = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-A",
        generation=1,
    )
    # Session B: LENS
    retriever_b = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:lens",
        session_id="session-B",
        generation=1,
    )

    # Query for JIRA in Session A -> Empty / zero LENS
    res_a = retriever_a.retrieve_structured_assertions("JIRA dependencies")
    assert not any("Spatial File Upload" in r.get("claim_text", "") for r in res_a)

    # Query for JIRA in Session B -> Successfully retrieves LENS assertion
    res_b = retriever_b.retrieve_structured_assertions("JIRA dependencies")
    assert any("Spatial File Upload" in r.get("claim_text", "") for r in res_b)

    # Delayed request from Session A presented with Session B's context fails closed
    assert not retriever_a._scope_binding.is_valid("session-B", "project:jarvis", 1)
    retriever_a.session_id = "session-B"
    res_delayed = retriever_a.retrieve_structured_assertions("Metal transcription")
    assert len(res_delayed) == 0, "Delayed cross-session query must return empty"
