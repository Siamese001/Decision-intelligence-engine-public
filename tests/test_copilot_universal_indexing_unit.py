"""
tests/test_copilot_universal_indexing_unit.py

Wave 1 Acceptance Suite: Universal In-Memory Indexing & Provenance Schema Reconciliation.
Verifies that:
1. retriever._allowed_nodes indexes all non-quarantined nodes across the active graph (>= 750 nodes).
2. Cross-workstream entities ('stakeholder_mandy_hopmann', 'workstream_pd_conduit') remain searchable under project:project_nexus.
3. Rule C-19 quarantined nodes remain strictly excluded from retriever._allowed_nodes.
4. compute_provenance_score produces score >= 0.30 for assertions containing primary_source_id.
5. compute_provenance_score produces score >= 0.30 for assertions with verified validation_state.
6. Weak/unbacked assertions without provenance receipts receive scores < 0.30.
"""
from __future__ import annotations

import time
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
    load_copilot_graph,
)
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    compute_provenance_score,
)
from dual_pipeline.live_meeting_recorder_core.c19_firewall import (
    is_c19_quarantined_node,
)


def test_universal_indexing_active_graph_node_count():
    """Verifies that retriever._allowed_nodes indexes all non-quarantined nodes (>= 750)."""
    status, in_mem, retriever = load_copilot_graph(engagement_id="project:project_nexus")
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert in_mem is not None
    assert retriever is not None

    # Graph contains 798 active nodes in active_pair
    assert len(retriever._allowed_nodes) >= 750, (
        f"Expected >= 750 allowed nodes, got {len(retriever._allowed_nodes)}"
    )


def test_cross_workstream_entities_indexed():
    """Verifies that stakeholder_mandy_hopmann and workstream_pd_conduit are indexed under nexus."""
    status, in_mem, retriever = load_copilot_graph(engagement_id="project:project_nexus")
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert retriever is not None

    # harper Hoffman and Conduit must be present in allowed nodes
    assert "stakeholder_mandy_hopmann" in retriever._allowed_nodes, (
        "stakeholder_mandy_hopmann missing from retriever._allowed_nodes"
    )
    assert "workstream_pd_conduit" in retriever._allowed_nodes, (
        "workstream_pd_conduit missing from retriever._allowed_nodes"
    )

    # Inverted index must map their tokens
    assert "mandy" in retriever._inverted_index
    assert "conduit" in retriever._inverted_index

    # Salient assertions must be indexed
    mandy_asrts = retriever._salient_assertions.get("stakeholder_mandy_hopmann", [])
    conduit_asrts = retriever._salient_assertions.get("workstream_pd_conduit", [])
    assert len(mandy_asrts) > 0, "Expected salient assertions for stakeholder_mandy_hopmann"
    assert len(conduit_asrts) > 0, "Expected salient assertions for workstream_pd_conduit"


def test_c19_quarantined_nodes_excluded():
    """Verifies that Rule C-19 quarantined nodes remain strictly excluded from allowed nodes."""
    status, in_mem, retriever = load_copilot_graph(engagement_id="project:project_nexus")
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert in_mem is not None
    assert retriever is not None

    # Verify no quarantined nodes entered allowed_nodes
    for node_id in retriever._allowed_nodes:
        node_data = in_mem.nodes.get(node_id, {})
        assert not is_c19_quarantined_node(node_id, node_data), (
            f"Rule C-19 quarantined node {node_id} leaked into _allowed_nodes"
        )


def test_provenance_score_primary_source_id():
    """Verifies that assertions with primary_source_id produce score >= 0.30."""
    now = time.time()
    asrt_with_primary_source = {
        "assertion_id": "asrt_mandy_conduit_01",
        "primary_source_id": "01_10-09-2026_PGD_Conduit_Weekly_master_100234",
        "predicate": "LEADS_WORKSTREAM",
        "claim_text": "harper Hoffman leads the Conduit platform workstream.",
        "observed_at": "2026-10-09",
        "canonical_status": "ACTIVE",
    }
    score = compute_provenance_score(asrt_with_primary_source, current_epoch=now)
    assert score >= 0.30, f"Expected score >= 0.30 for primary_source_id, got {score}"


def test_provenance_score_validation_state():
    """Verifies that assertions with verified validation_state produce score >= 0.30."""
    now = time.time()
    verified_asrt = {
        "assertion_id": "asrt_verified_canonical_01",
        "validation_state": "verified",
        "predicate": "REPORTS_TO",
        "claim_text": "Technician Conduit reports to PGD umbrella.",
        "observed_at": "2026-09-15",
    }
    score = compute_provenance_score(verified_asrt, current_epoch=now)
    assert score >= 0.30, f"Expected score >= 0.30 for verified validation_state, got {score}"


def test_provenance_score_weak_unbacked_assertion():
    """Verifies that unbacked assertions without sources or validation receive score < 0.30."""
    now = time.time()
    weak_asrt = {
        "assertion_id": "asrt_weak_unbacked_01",
        "claim_text": "Unverified hearsay without evidence.",
        "observed_at": "2020-01-01",
    }
    score = compute_provenance_score(weak_asrt, current_epoch=now)
    assert score < 0.30, f"Expected score < 0.30 for unbacked assertion, got {score}"
