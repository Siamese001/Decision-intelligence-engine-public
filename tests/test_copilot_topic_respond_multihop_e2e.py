"""
tests/test_copilot_topic_respond_multihop_e2e.py

End-to-end integration test verifying:
1. Stage 2 multi-hop neighborhood fallback in eval_topic_respond (< 5.0ms).
2. Vocative addressee clause verification preserving leading target addresses ("Jason, ...").
3. Zero Rule C-20 ownership conflicts with Daniel Brankin recognized as owner of SAP ERP.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
    strip_vocative_target,
    verify_and_filter_sentences,
)
from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    eval_topic_respond,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    _check_c20_graph_ownership,
    extract_verified_owners,
)


def _build_test_topology() -> InMemoryGraphTopology:
    """Builds test topology connecting JARVIS base project to NEXUS SAP ERP."""
    nodes: Dict[str, Any] = {
        "project:jarvis": {
            "node_id": "project:jarvis",
            "canonical_name": "Project Jarvis",
            "node_type": "project",
            "engagement_id": "project:jarvis",
        },
        "person:jason": {
            "node_id": "person:jason",
            "canonical_name": "Jason Doe",
            "display_name": "Jason",
            "node_type": "person",
            "engagement_id": "project:jarvis",
        },
        "project:nexus": {
            "node_id": "project:nexus",
            "canonical_name": "Project Nexus",
            "node_type": "project",
            "engagement_id": "project:nexus",
        },
        "person:daniel_brankin": {
            "node_id": "person:daniel_brankin",
            "canonical_name": "Daniel Brankin",
            "display_name": "Daniel",
            "node_type": "person",
            "engagement_id": "project:nexus",
        },
        "system:sap": {
            "node_id": "system:sap",
            "canonical_name": "SAP ERP",
            "display_name": "SAP",
            "node_type": "system",
            "engagement_id": "project:nexus",
        },
    }

    assertions: Dict[str, Any] = {
        "asrt_jarvis_predecessor_nexus": {
            "assertion_id": "asrt_jarvis_predecessor_nexus",
            "subject_node_id": "project:jarvis",
            "predicate": "PREDECESSOR_PROJECT",
            "object_node_id": "project:nexus",
            "claim_text": "Project Jarvis inherits architecture and SAP integration from predecessor Project Nexus.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
            "retrieval_eligible": 1,
            "materiality": "high",
        },
        "asrt_nexus_daniel_sap": {
            "assertion_id": "asrt_nexus_daniel_sap",
            "subject_node_id": "person:daniel_brankin",
            "predicate": "LEADS",
            "object_node_id": "system:sap",
            "claim_text": "Daniel Brankin leads SAP technical architecture and ERP integration.",
            "engagement_id": "project:nexus",
            "canonical_status": "ACTIVE",
            "retrieval_eligible": 1,
            "materiality": "high",
        },
    }

    forward_adj = {
        "project:jarvis": [
            {
                "to_node_id": "project:nexus",
                "predicate": "PREDECESSOR_PROJECT",
                "assertion_id": "asrt_jarvis_predecessor_nexus",
                "to_name": "Project Nexus",
            }
        ],
        "project:nexus": [
            {
                "to_node_id": "system:sap",
                "predicate": "LEADS",
                "assertion_id": "asrt_nexus_daniel_sap",
                "to_name": "SAP ERP",
            }
        ],
        "person:daniel_brankin": [
            {
                "to_node_id": "system:sap",
                "predicate": "LEADS",
                "assertion_id": "asrt_nexus_daniel_sap",
                "to_name": "SAP ERP",
            }
        ],
    }

    return InMemoryGraphTopology(
        graph_path="/tmp/mock_graph.sqlite",
        graph_sha256="mock-sha-w4",
        nodes=nodes,
        assertions=assertions,
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency=forward_adj,
        reverse_adjacency={},
        undirected_adjacency=forward_adj,
        canonical_edges=[],
    )


def _setup_retriever_with_neighborhood_store() -> CopilotGraphRetriever:
    topo = _build_test_topology()
    retriever = CopilotGraphRetriever(
        topo,
        attendees=[{"name": "Jason Doe", "role": "Stakeholder"}],
        engagement_id="project:jarvis",
        session_id="session-w4-001",
    )

    # Multi-hop chain bridging Jarvis to Nexus SAP ownership
    sap_chain = {
        "seed_id": "project:jarvis",
        "target_id": "system:sap",
        "hops": 2,
        "path": ["project:jarvis", "project:nexus", "system:sap"],
        "predicates": ["PREDECESSOR_PROJECT", "LEADS"],
        "chain_str": "Project Jarvis --[PREDECESSOR_PROJECT]--> Project Nexus --[LEADS]--> SAP ERP",
        "is_priority": True,
        "last_claim": "Daniel Brankin leads SAP technical architecture and ERP integration.",
        "assertions": [
            topo.assertions["asrt_jarvis_predecessor_nexus"],
            topo.assertions["asrt_nexus_daniel_sap"],
        ],
    }
    retriever.neighborhood_store = CopilotNeighborhoodStore(
        chains=[sap_chain],
        status="COMPLETE",
        engagement_id="project:jarvis",
        permitted_engagements={"project:nexus"},
    )
    return retriever


def test_topic_respond_multihop_fallback_resolves_sap_owner() -> None:
    """Verifies that topic_respond uses neighborhood store fallback in <5ms when direct search yields 0."""
    retriever = _setup_retriever_with_neighborhood_store()

    # Query with target "Jason", but topic keywords "sap", "tech owner"
    t0 = time.perf_counter()
    body, assertions, ok = eval_topic_respond(
        retriever=retriever,
        target="Jason",
        selected_utterance=None,
        topic_keywords=["sap", "owner"],
        topic_query="Who is the tech owner for SAP ERP?",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 1. Multi-hop fallback must succeed
    assert ok is True, "Expected multi-hop fallback to succeed"
    assert len(assertions) > 0, "Expected non-empty assertions from multi-hop fallback"
    assert elapsed_ms < 5.0, f"Multi-hop fallback {elapsed_ms:.2f}ms exceeded 5.0ms deadline"

    # 2. Assertions contain SAP ownership claim
    assertion_ids = {a.get("assertion_id") for a in assertions}
    assert "asrt_nexus_daniel_sap" in assertion_ids
    assert "Daniel Brankin" in assertions[0].get("claim_text", "")


def test_vocative_target_stripping_and_clause_verification() -> None:
    """Verifies that leading vocative targets ('Jason, ...') are stripped before entity checks."""
    topo = _build_test_topology()
    asrt = topo.assertions["asrt_nexus_daniel_sap"]
    by_id = {asrt["assertion_id"]: asrt}

    # 1. Unit verification of strip_vocative_target
    assert strip_vocative_target("Jason, Daniel Brankin leads SAP.", target="Jason") == "Daniel Brankin leads SAP."
    assert strip_vocative_target("Hey Jason: Daniel Brankin leads SAP.", target="Jason") == "Daniel Brankin leads SAP."
    assert strip_vocative_target("Jason - Daniel Brankin leads SAP.", target="Jason") == "Daniel Brankin leads SAP."
    assert strip_vocative_target("Daniel Brankin leads SAP.", target="Jason") == "Daniel Brankin leads SAP."

    # 2. Clause grounding with vocative target "Jason, "
    clause = "Jason, Daniel Brankin leads SAP technical architecture and ERP integration."
    hit = check_clause_grounding(clause, [asrt], by_id, target="Jason")
    assert hit == "asrt_nexus_daniel_sap", "Vocative clause should map to valid assertion without hallucination rejection"

    # 3. verify_and_filter_sentences preserves vocative sentence
    retained = verify_and_filter_sentences(clause, [asrt], by_id, set(), target="Jason")
    assert len(retained) == 1
    assert "Daniel Brankin leads SAP" in retained[0]


def test_zero_c20_ownership_conflicts_for_daniel_brankin() -> None:
    """Verifies that stating Daniel Brankin leads SAP triggers zero Rule C-20 conflicts."""
    topo = _build_test_topology()
    owners = extract_verified_owners(topo)

    # Verified owners mapping must include Daniel Brankin for SAP
    assert "system:sap" in owners
    assert any("daniel" in o for o in owners["system:sap"])

    parsed = {
        "suggested_script": "Jason, could we clarify that Daniel Brankin leads SAP technical architecture?",
        "snap_cue": "Clarify SAP ownership",
        "category": "TECHNICAL",
        "target": "Jason",
    }
    _check_c20_graph_ownership(parsed, parsed["suggested_script"], topo)

    # Must NOT be sanitized to RACI clarification fallback
    assert "Can we verify technical delivery ownership" not in parsed["suggested_script"]
    assert parsed["snap_cue"] == "Clarify SAP ownership"
    assert parsed["category"] == "TECHNICAL"
