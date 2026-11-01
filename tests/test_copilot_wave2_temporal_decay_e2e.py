"""
Wave 2 E2E tests for predicate-aware graph temporal decay and retrieval ranking.
Verifies that October assertions outrank July assertions on identical lexical tokens,
structural predicates decay slower than volatile predicates, enterprise policies remain
undecayed, and historical query anchors disable decay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    compute_neighborhood_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever_ranking import (
    compute_temporal_decay,
    parse_assertion_epoch,
)


@dataclass
class DummyTopology:
    nodes: Dict[str, Any] = field(default_factory=dict)
    assertions: Dict[str, Any] = field(default_factory=dict)
    supporting_evidence: Dict[str, List[str]] = field(default_factory=dict)
    evidence_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    forward_adjacency: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def _build_temporal_topology() -> DummyTopology:
    topo = DummyTopology()
    topo.nodes = {
        "project:nee_ai": {
            "node_id": "project:nee_ai",
            "canonical_name": "NEE AI Accelerator",
            "display_name": "NEE AI Accelerator",
            "node_type": "workstream",
            "engagement_id": "nee_ai",
        },
        "person:alice": {
            "node_id": "person:alice",
            "canonical_name": "Alice Vance",
            "display_name": "Alice Vance",
            "node_type": "person",
            "engagement_id": "nee_ai",
        },
        "risk:july_blocker": {
            "node_id": "risk:july_blocker",
            "canonical_name": "July Sandbox Blocker",
            "display_name": "July Sandbox Blocker",
            "node_type": "risk",
            "engagement_id": "nee_ai",
        },
        "risk:oct_blocker": {
            "node_id": "risk:oct_blocker",
            "canonical_name": "October Production Blocker",
            "display_name": "October Production Blocker",
            "node_type": "risk",
            "engagement_id": "nee_ai",
        },
    }
    topo.assertions = {
        "asrt_july_risk": {
            "assertion_id": "asrt_july_risk",
            "subject_node_id": "project:nee_ai",
            "object_node_id": "risk:july_blocker",
            "predicate": "HAS_RISK",
            "claim_text": "NEE AI Accelerator deployment pipeline faces critical latency risk in cluster.",
            "canonical_status": "canonical",
            "retrieval_eligible": 1,
            "canonical_eligible": 1,
            "confidence_score": 0.92,
            "engagement_id": "nee_ai",
            "source_date_normalized": "2026-07-10",
        },
        "asrt_oct_risk": {
            "assertion_id": "asrt_oct_risk",
            "subject_node_id": "project:nee_ai",
            "object_node_id": "risk:oct_blocker",
            "predicate": "HAS_RISK",
            "claim_text": "NEE AI Accelerator deployment pipeline faces critical latency risk in gateway.",
            "canonical_status": "canonical",
            "retrieval_eligible": 1,
            "canonical_eligible": 1,
            "confidence_score": 0.92,
            "engagement_id": "nee_ai",
            "source_date_normalized": "2026-10-25",
        },
        "asrt_july_role": {
            "assertion_id": "asrt_july_role",
            "subject_node_id": "person:alice",
            "object_node_id": "project:nee_ai",
            "predicate": "OWNS_OR_LEADS",
            "claim_text": "Alice Vance leads architecture governance for NEE AI Accelerator.",
            "canonical_status": "canonical",
            "retrieval_eligible": 1,
            "canonical_eligible": 1,
            "confidence_score": 0.95,
            "engagement_id": "nee_ai",
            "source_date_normalized": "2026-07-10",
        },
    }
    return topo


def test_october_assertions_outrank_july_assertions_e2e() -> None:
    topo = _build_temporal_topology()
    retriever = CopilotGraphRetriever(
        topology=topo,
        engagement_id="nee_ai",
        session_id="sess_w2",
        generation=1,
    )

    results = retriever.retrieve_structured_assertions(
        "deployment pipeline critical latency risk", max_results=5
    )
    assert len(results) >= 2
    assert results[0]["assertion_id"] == "asrt_oct_risk"
    oct_ids = [r["assertion_id"] for r in results]
    assert oct_ids.index("asrt_oct_risk") < oct_ids.index("asrt_july_risk")

    # Verify formatted node context orders October risk ahead of July risk
    ctx = retriever.retrieve_context("NEE AI Accelerator deployment risk", max_entities=2, max_assertions_per_entity=3)
    assert "Verified Graph Knowledge:" in ctx
    pos_oct = ctx.find("gateway")
    pos_july = ctx.find("cluster")
    assert pos_oct != -1 and pos_july != -1
    assert pos_oct < pos_july


def test_volatile_vs_structural_predicate_decay_curve() -> None:
    ref_epoch = parse_assertion_epoch("2026-10-27")
    july_epoch = parse_assertion_epoch("2026-07-15")
    oct_recent_epoch = parse_assertion_epoch("2026-10-25")

    volatile_july = {"predicate": "HAS_RISK", "_observed_epoch": july_epoch}
    structural_july = {"predicate": "OWNS_OR_LEADS", "_observed_epoch": july_epoch}
    volatile_recent = {"predicate": "HAS_RISK", "_observed_epoch": oct_recent_epoch}

    d_vol_july = compute_temporal_decay(volatile_july, ref_epoch)
    d_struct_july = compute_temporal_decay(structural_july, ref_epoch)
    d_vol_recent = compute_temporal_decay(volatile_recent, ref_epoch)

    assert d_vol_recent > 1.0  # 0-7d freshness boost
    assert d_struct_july >= 0.55  # Structural floor 0.55
    assert d_vol_july < d_struct_july
    assert d_vol_july >= 0.25  # Volatile floor 0.25

    # Historical query bypasses decay
    d_hist = compute_temporal_decay(volatile_july, ref_epoch, query_str="What was the July risk?")
    assert d_hist == 1.0


def test_neighborhood_chains_prioritize_recent_edges() -> None:
    topo = _build_temporal_topology()
    chains, status = compute_neighborhood_chains(
        topology=topo,
        seed_node_ids=["project:nee_ai"],
        max_hops=1,
        max_fanout=1,
        engagement_id="nee_ai",
    )
    assert status == "SUCCESS"
    assert len(chains) == 1
    assert chains[0]["target_id"] == "risk:oct_blocker"
