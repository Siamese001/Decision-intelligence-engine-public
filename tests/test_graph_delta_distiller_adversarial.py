"""
Adversarial and edge-case tests for Wave 3: Graph-Aware Delta Distillation, Perspective Gap Analyzer & Targeted HITL Batching.

Covers:
1. Boundary payloads (empty candidate bundle, missing SQLite database path).
2. Same-document multi-row allocation suppression (zero false-positive CONTRADICTION holds when a PM or contractor is assigned to 6 workstreams).
3. Placeholder identity reconciliation ('unresolved_jake_wellington_roster_2026_07_11' -> 'Jake Wellington').
4. SSOT 20% ambiguity margin gating (items with spread <= 0.20 require operator decision; items with spread > 0.20 resolve autonomously).
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.intake_core.graph_delta_distiller import (
    _build_hitl_item,
    _detect_placeholder_reconciliations,
    distill_document_graph_delta,
    is_portfolio_allocation_relationship,
)
from dual_pipeline.intake_core.routing import _route_partial_overlap


def test_boundary_empty_bundle_and_nonexistent_graph_path(tmp_path: Path) -> None:
    """Verify empty candidate bundle and missing graph file return an empty DocumentGraphDeltaReport without crashing."""
    empty_bundle = {
        "run_id": "run-empty-01",
        "source_id": "source-empty-01",
        "candidates": [],
        "extensions": {},
    }
    report = distill_document_graph_delta(
        empty_bundle, [], {"comparisons": {}}, tmp_path / "nonexistent.sqlite"
    )
    assert report.run_id == "run-empty-01"
    assert report.net_new_workstreams == []
    assert report.corroborated_workstreams == []
    assert report.hitl_review_items == []


def test_same_document_multi_team_allocation_contradiction_suppression() -> None:
    """Verify multi-row portfolio relationships (MEMBER_OF, LEADS, USES_SYSTEM, HAS_METRIC) never route to false-positive CONTRADICTION on partial overlap."""
    for pred in ("MEMBER_OF", "AFFILIATED_WITH", "LEADS", "OWNS_OR_LEADS", "USES_SYSTEM", "HAS_METRIC"):
        cand = {
            "candidate_id": f"cand-{pred.lower()}",
            "candidate_type": "architecture_relationship",
            "normalized_text": f"Dave Sunderman {pred} Regulatory Workstream.",
        }
        assert is_portfolio_allocation_relationship(cand) is True
        comp = {"partial_overlaps": [{"record_id": "sd-1", "object_id": "obj-1", "lane": "candidate"}]}
        assert _route_partial_overlap(cand, comp) is None


def test_perspective_owner_shift_routes_to_candidate_promotion_review() -> None:
    """Verify non-portfolio overlaps with perspective_owner_shift route to CANDIDATE_PROMOTION_REVIEW."""
    cand = {
        "candidate_id": "cand-shift-1",
        "candidate_type": "assertion",
        "normalized_text": "Andrew Turvey is the primary owner of 5.Forge.Customer.",
    }
    comp = {
        "partial_overlaps": [{"record_id": "sd-1", "object_id": "obj-1", "lane": "approved"}],
        "perspective_owner_shift": True,
    }
    route_res = _route_partial_overlap(cand, comp)
    assert route_res is not None
    assert route_res[0] == "CANDIDATE_PROMOTION_REVIEW"


def test_placeholder_identity_reconciliation_detection() -> None:
    """Verify _detect_placeholder_reconciliations matches unresolved_jake_wellington_roster_2026_07_11 to Jake Wellington."""
    nodes_by_id = {
        "unresolved_jake_wellington_roster_2026_07_11": {
            "node_id": "unresolved_jake_wellington_roster_2026_07_11",
            "canonical_name": "unresolved_jake_wellington_roster_2026_07_11",
            "node_type": "stakeholder",
        },
        "person:other": {
            "node_id": "person:other",
            "canonical_name": "William Lane",
            "node_type": "stakeholder",
        },
    }
    matches = _detect_placeholder_reconciliations("Jake Wellington", nodes_by_id)
    assert len(matches) == 1
    assert matches[0]["placeholder_node_id"] == "unresolved_jake_wellington_roster_2026_07_11"
    assert matches[0]["resolved_canonical_name"] == "Jake Wellington"
    assert matches[0]["recommended_action"] == "MERGE_PLACEHOLDER_INTO_CANONICAL"


def test_ssot_20_percent_margin_gate_on_hitl_items() -> None:
    """Verify HITL items with confidence spread <= 0.20 require operator decision while spread > 0.20 resolves autonomously."""
    ambiguous_item = _build_hitl_item(
        run_id="run-1",
        candidate_id="cand-1",
        category="PERSPECTIVE_OWNER_SHIFT",
        question="Reconcile workstream lead?",
        top_option="Update lead",
        runner_up_option="Keep prior lead",
        top_confidence=0.58,
        runner_up_confidence=0.42,
        evidence_ids=["ev-1"],
    )
    assert ambiguous_item["confidence_margin"] == 0.16
    assert ambiguous_item["requires_operator_decision"] is True

    decisive_item = _build_hitl_item(
        run_id="run-1",
        candidate_id="cand-2",
        category="HIGH_CONFIDENCE_MATCH",
        question="Auto-link workstream?",
        top_option="Link exact match",
        runner_up_option="Create duplicate",
        top_confidence=0.92,
        runner_up_confidence=0.60,
        evidence_ids=["ev-2"],
    )
    assert decisive_item["confidence_margin"] == 0.32
    assert decisive_item["requires_operator_decision"] is False
