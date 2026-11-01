"""
tests/test_copilot_predicate_quality_e2e.py

End-to-end tests for Wave 3: Question Predicate Quality Gate & Scope Containment.
Verifies that:
1. HAS_CLAIM_TEXT extraction triples are strictly rejected from next-questions.
2. When only raw extraction triples exist, get_next_questions returns an empty list.
3. Technical anchors from unrelated meetings do not leak across project boundaries.
4. get_next_actions rejects HAS_CLAIM_TEXT and only returns actionable predicates.
"""
from __future__ import annotations

from typing import Any, Dict
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_node_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _create_test_topology(
    nodes: Dict[str, Dict[str, Any]],
    assertions: Dict[str, Dict[str, Any]],
) -> InMemoryGraphTopology:
    forward_adj = {nid: [] for nid in nodes}
    reverse_adj = {nid: [] for nid in nodes}
    for aid, a in assertions.items():
        s = a.get("subject_node_id")
        o = a.get("object_node_id")
        if s and s in forward_adj:
            forward_adj[s].append({"assertion_id": aid, "predicate": a.get("predicate"), "target": o})
        if o and o in reverse_adj:
            reverse_adj[o].append({"assertion_id": aid, "predicate": a.get("predicate"), "target": s})
    return InMemoryGraphTopology(
        graph_path="/tmp/mock.sqlite",
        graph_sha256="mock_hash_000",
        nodes=nodes,
        assertions=assertions,
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency=forward_adj,
        reverse_adjacency=reverse_adj,
        undirected_adjacency=forward_adj,
        canonical_edges=[],
    )


def test_copilot_get_next_questions_rejects_has_claim_text():
    """Verify that get_next_questions rejects raw HAS_CLAIM_TEXT and accepts QUESTION_PREDICATES."""
    nodes = {
        "entity-vector-db": {
            "node_id": "entity-vector-db",
            "node_type": "technical_anchor",
            "canonical_name": "Vector Database",
            "display_name": "Vector Database",
            "description": "Production vector database for semantic retrieval in Project Nexus.",
            "engagement_id": "project:project_nexus",
        }
    }
    assertions = {
        "ast-raw-1": {
            "assertion_id": "ast-raw-1",
            "subject_node_id": "entity-vector-db",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
            "object_literal": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
        "ast-raw-2": {
            "assertion_id": "ast-raw-2",
            "subject_node_id": "entity-vector-db",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "Atlas Persistent Learning Database is an identified technical_anchor in 20260929 Pgd Followup Anthony Eduardo.",
            "object_literal": "Atlas Persistent Learning Database is an identified technical_anchor in 20260929 Pgd Followup Anthony Eduardo.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
        "ast-valid-risk": {
            "assertion_id": "ast-valid-risk",
            "subject_node_id": "entity-vector-db",
            "predicate": "HAS_RISK",
            "claim_text": "Vector database memory scaling introduces latency spikes under concurrent tenant load.",
            "object_literal": "Vector database memory scaling introduces latency spikes under concurrent tenant load.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    questions = retriever.get_next_questions("vector database", max_questions=3)
    assert len(questions) == 1
    assert questions[0]["assertion_id"] == "ast-valid-risk"
    assert questions[0]["predicate"] == "HAS_RISK"
    retrieved_ids = [q["assertion_id"] for q in questions]
    assert "ast-raw-1" not in retrieved_ids
    assert "ast-raw-2" not in retrieved_ids


def test_copilot_get_next_questions_empty_when_only_has_claim_text_present():
    """Verify that get_next_questions returns empty list when only raw extraction triples exist."""
    nodes = {
        "entity-vector-db": {
            "node_id": "entity-vector-db",
            "node_type": "technical_anchor",
            "canonical_name": "Vector Database",
            "display_name": "Vector Database",
            "description": "Vector Database in Project Nexus.",
            "engagement_id": "project:project_nexus",
        }
    }
    assertions = {
        "ast-raw-1": {
            "assertion_id": "ast-raw-1",
            "subject_node_id": "entity-vector-db",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    questions = retriever.get_next_questions("vector database", max_questions=3)
    assert questions == []


def test_copilot_unrelated_technical_anchor_not_in_scope():
    """Verify that technical anchors from unrelated projects fail scope validation unless enterprise policy."""
    lens_anchor = {
        "node_id": "entity-03cd78c50a0e662a045afe3be69b",
        "node_type": "technical_anchor",
        "canonical_name": "Persistent Database",
        "display_name": "Persistent Database",
        "description": "Database receiving the final output from the LENS processing sequence.",
    }
    sap_enterprise_anchor = {
        "node_id": "entity-sap-erp",
        "node_type": "technical_anchor",
        "canonical_name": "SAP ERP",
        "display_name": "SAP ERP",
        "description": "Global enterprise resource planning system across Apex Global Enterprises.",
        "is_enterprise_policy": True,
    }

    # Under Project Nexus binding:
    assert not is_node_in_scope(
        node_id=lens_anchor["node_id"],
        node_data=lens_anchor,
        bound_engagement="project:project_nexus",
    ), "LENS persistent database technical anchor must NOT be in scope for Project Nexus"

    assert is_node_in_scope(
        node_id=sap_enterprise_anchor["node_id"],
        node_data=sap_enterprise_anchor,
        bound_engagement="project:project_nexus",
    ), "Global SAP enterprise technical anchor MUST be in scope for Project Nexus"


def test_copilot_get_next_actions_rejects_has_claim_text():
    """Verify that get_next_actions rejects HAS_CLAIM_TEXT and accepts ACTION_PREDICATES."""
    nodes = {
        "entity-vector-db": {
            "node_id": "entity-vector-db",
            "node_type": "technical_anchor",
            "canonical_name": "Vector Database",
            "display_name": "Vector Database",
            "engagement_id": "project:project_nexus",
        }
    }
    assertions = {
        "ast-raw": {
            "assertion_id": "ast-raw",
            "subject_node_id": "entity-vector-db",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "Vector Database is an identified technical_anchor in Project Nexus.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
        "ast-action": {
            "assertion_id": "ast-action",
            "subject_node_id": "entity-vector-db",
            "predicate": "OWNS_OR_LEADS",
            "claim_text": "Data Engineering owns the vector database indexing pipeline.",
            "engagement_id": "project:project_nexus",
            "status": "ACTIVE",
        },
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    actions = retriever.get_next_actions("vector database", max_actions=3)
    assert len(actions) == 1
    assert actions[0]["assertion_id"] == "ast-action"
    assert actions[0]["predicate"] == "OWNS_OR_LEADS"


def test_copilot_project_beacon_outranks_candidate_ai_sdlc_controls_and_stubs():
    """Verify project:project_beacon assertions outrank Candidate AI-SDLC control checklists and reject stubs."""
    nodes = {
        "workstream_project_beacon": {
            "node_id": "workstream_project_beacon",
            "node_type": "workstream",
            "canonical_name": "Project Beacon",
            "display_name": "Project Beacon",
            "engagement_id": "project:project_beacon",
        },
        "pattern_ai_sdlc": {
            "node_id": "pattern_ai_sdlc",
            "node_type": "pattern",
            "canonical_name": "AI SDLC",
            "display_name": "AI SDLC",
            "engagement_id": "project:enterprise",
            "is_enterprise_policy": True,
        },
    }
    assertions = {
        "asrt-stub-ninan": {
            "assertion_id": "asrt-stub-ninan",
            "subject_node_id": "workstream_project_beacon",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "Speaker_Beta is an identified stakeholder_person in 20261013 Project Beacon.",
            "engagement_id": "project:project_beacon",
            "status": "ACTIVE",
        },
        "asrt-control-noise": {
            "assertion_id": "asrt-control-noise",
            "subject_node_id": "pattern_ai_sdlc",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": (
                "Candidate AI-SDLC control — Tool-vs-Outcome Attribution: minimum enterprise control: "
                "Productivity increase and velocity metrics must not attribute commit volume to outcome value."
            ),
            "engagement_id": "project:enterprise",
            "status": "ACTIVE",
        },
        "asrt-sherpa-velocity": {
            "assertion_id": "asrt-sherpa-velocity",
            "subject_node_id": "workstream_project_beacon",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": (
                "Amit Ayer and Andy Kilinskis reported that the outcome-velocity criticism had not yet been "
                "tied to a specific KPI, calculation, or shared scorecard."
            ),
            "engagement_id": "project:project_beacon",
            "status": "ACTIVE",
        },
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_beacon")

    results = retriever.retrieve_structured_assertions("challenge productivity increase from Speaker_Beta", max_results=5)
    returned_ids = [r["assertion_id"] for r in results]
    assert "asrt-sherpa-velocity" in returned_ids
    assert "asrt-control-noise" not in returned_ids
    assert "asrt-stub-ninan" not in returned_ids
