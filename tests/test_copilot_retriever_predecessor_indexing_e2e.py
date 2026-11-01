"""
tests/test_copilot_retriever_predecessor_indexing_e2e.py

End-to-end test verifying init-time graph retriever scope expansion,
topological predecessor inheritance indexing, and provenance stamping.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


def _build_predecessor_test_graph() -> Any:
    """Builds a multi-tenant graph with JARVIS, predecessor NEXUS (SAP), and unlinked SOLAR."""
    nodes: Dict[str, Any] = {
        "project:jarvis": {
            "canonical_name": "Project Jarvis",
            "node_type": "project",
            "engagement_id": "project:jarvis",
            "namespace": "CANONICAL",
        },
        "person:mitch": {
            "canonical_name": "Mitch Simmons",
            "node_type": "person",
            "engagement_id": "project:jarvis",
            "namespace": "CANONICAL",
        },
        "project:nexus": {
            "canonical_name": "Project Nexus",
            "node_type": "project",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "person:sharon_diaram": {
            "canonical_name": "Sharon Diaram",
            "node_type": "person",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "person:daniel_brankin": {
            "canonical_name": "Daniel Brankin",
            "node_type": "person",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "system:sap": {
            "canonical_name": "SAP ERP",
            "node_type": "system",
            "engagement_id": "project:nexus",
            "namespace": "CANONICAL",
        },
        "project:solar": {
            "canonical_name": "Project Solar",
            "node_type": "project",
            "engagement_id": "project:solar",
            "namespace": "CANONICAL",
        },
        "tech:solar_panel": {
            "canonical_name": "Solar Panel Tech",
            "node_type": "system",
            "engagement_id": "project:solar",
            "namespace": "CANONICAL",
        },
    }

    assertions: Dict[str, Any] = {
        "asrt_rel_jarvis_nexus": {
            "assertion_id": "asrt_rel_jarvis_nexus",
            "subject_node_id": "project:jarvis",
            "predicate": "PREDECESSOR_PROJECT",
            "object_node_id": "project:nexus",
            "claim_text": "Project Nexus is the predecessor architecture for Project Jarvis.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        },
        "asrt_jarvis_mitch": {
            "assertion_id": "asrt_jarvis_mitch",
            "subject_node_id": "person:mitch",
            "predicate": "MANAGES",
            "object_node_id": "project:jarvis",
            "claim_text": "Mitch manages Jarvis sprint planning and delivery.",
            "engagement_id": "project:jarvis",
            "canonical_status": "ACTIVE",
        },
        "asrt_nexus_sharon_sap": {
            "assertion_id": "asrt_nexus_sharon_sap",
            "subject_node_id": "person:sharon_diaram",
            "predicate": "OWNS",
            "object_node_id": "system:sap",
            "claim_text": "Sharon Diaram owns SAP ERP architecture and document pipelines.",
            "engagement_id": "project:nexus",
            "canonical_status": "ACTIVE",
        },
        "asrt_nexus_daniel_sap": {
            "assertion_id": "asrt_nexus_daniel_sap",
            "subject_node_id": "person:daniel_brankin",
            "predicate": "LEADS",
            "object_node_id": "system:sap",
            "claim_text": "Daniel Brankin leads SAP integration and technical delivery.",
            "engagement_id": "project:nexus",
            "canonical_status": "ACTIVE",
        },
        "asrt_solar_foreign": {
            "assertion_id": "asrt_solar_foreign",
            "subject_node_id": "project:solar",
            "predicate": "USES",
            "object_node_id": "tech:solar_panel",
            "claim_text": "Project Solar utilizes photovoltaic panels.",
            "engagement_id": "project:solar",
            "canonical_status": "ACTIVE",
        },
    }

    return SimpleNamespace(
        nodes=nodes,
        assertions=assertions,
        forward_adjacency={
            "project:jarvis": [{"to_node": "project:nexus", "relationship_family": "PREDECESSOR_PROJECT"}],
            "person:sharon_diaram": [{"to_node": "system:sap", "relationship_family": "OWNS"}],
            "person:daniel_brankin": [{"to_node": "system:sap", "relationship_family": "LEADS"}],
        },
    )


def test_copilot_retriever_indexes_predecessor_assertions_at_init() -> None:
    """Verifies Sharon Diaram, Daniel Brankin, and SAP are indexed under project:jarvis."""
    graph = _build_predecessor_test_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-e2e-001",
    )

    # 1. Verify topological discovery
    assert "project:nexus" in retriever.topological_engagements
    assert "project:nexus" in retriever.permitted_engagements

    # 2. Verify salient assertions contain Sharon, Daniel, and SAP
    assert "person:sharon_diaram" in retriever._salient_assertions
    assert "person:daniel_brankin" in retriever._salient_assertions
    assert "system:sap" in retriever._salient_assertions

    # 3. Verify provenance stamping on predecessor assertions
    sharon_records = retriever._salient_assertions["person:sharon_diaram"]
    assert len(sharon_records) > 0
    sharon_rec = sharon_records[0]
    assert sharon_rec["is_predecessor"] is True
    assert sharon_rec["is_primary"] is False
    assert sharon_rec["provenance_scope"] == "PREDECESSOR"
    assert sharon_rec["origin_engagement_id"] == "project:nexus"

    daniel_records = retriever._salient_assertions["person:daniel_brankin"]
    assert len(daniel_records) > 0
    daniel_rec = daniel_records[0]
    assert daniel_rec["is_predecessor"] is True
    assert daniel_rec["is_primary"] is False
    assert daniel_rec["provenance_scope"] == "PREDECESSOR"
    assert daniel_rec["origin_engagement_id"] == "project:nexus"

    # 4. Verify primary assertion stamping on Mitch
    assert "person:mitch" in retriever._salient_assertions
    mitch_rec = retriever._salient_assertions["person:mitch"][0]
    assert mitch_rec["is_primary"] is True
    assert mitch_rec["is_predecessor"] is False
    assert mitch_rec["provenance_scope"] == "PRIMARY"
    assert mitch_rec["origin_engagement_id"] == "project:jarvis"


def test_copilot_retriever_excludes_unlinked_foreign_tenant() -> None:
    """Verifies foreign tenant (project:solar) nodes and assertions are barred from indexing."""
    graph = _build_predecessor_test_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-e2e-002",
    )

    # Solar nodes must not enter allowed nodes
    assert "project:solar" not in retriever._allowed_nodes
    assert "tech:solar_panel" not in retriever._allowed_nodes

    # Zero assertions for solar
    assert "project:solar" not in retriever._salient_assertions
    assert "tech:solar_panel" not in retriever._salient_assertions

    for _node_id, records in retriever._salient_assertions.items():
        for r in records:
            assert r.get("engagement_id") != "project:solar"
            assert r.get("origin_engagement_id") != "project:solar"


def test_natural_query_retrieval_returns_predecessor_claims() -> None:
    """Natural query for SAP retrieves Sharon Diaram and Daniel Brankin without literal 'nexus' keyword."""
    graph = _build_predecessor_test_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-e2e-003",
    )

    results = retriever.retrieve_structured_assertions("Who is the tech owner for SAP?")
    assert len(results) >= 1
    found_aids = {r["assertion_id"] for r in results}
    assert ("asrt_nexus_sharon_sap" in found_aids) or ("asrt_nexus_daniel_sap" in found_aids)

    # Check that retrieved items have predecessor provenance
    for r in results:
        if r["assertion_id"] in ("asrt_nexus_sharon_sap", "asrt_nexus_daniel_sap"):
            assert r["is_predecessor"] is True
            assert r["origin_engagement_id"] == "project:nexus"
