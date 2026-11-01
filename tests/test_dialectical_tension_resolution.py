"""Tests for Wave 5: Dialectical Contradiction & Tension Resolution."""
from __future__ import annotations

from dual_pipeline.decision_engine import build_decision_evidence_pack
from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportState,
    evaluate_evidence_support,
)
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation


def test_decision_constraint_with_limitation_opposes_unmitigated_proposal() -> None:
    record = {
        "record_id": "rec_bar_constraint",
        "title": "NEE Engineering Screening Bar",
        "text": (
            "Blocked / Non-Viable Elements: NEE enforces a 5-10% technical screening pass rate "
            "and phasing out segregated pods, creating a talent screening feasibility bottleneck."
        ),
        "answer_use": "decision_constraint",
        "evidence_ids": ["ev_bar_1"],
        "currentness_state": "current",
    }
    state, reason = evaluate_evidence_support(
        record,
        option_label="Blocked / Non-Viable Elements",
        criterion_label="talent_and_screening_feasibility",
    )
    assert state == DecisionSupportState.CONTRADICTED
    assert "counterevidence or negative polarity" in reason


def test_evidence_pack_synthesizes_dialectical_constraint_tensions_and_passes_anti_conflation() -> None:
    approved_lane = [
        {
            "record": {
                "record_id": "rec_wpb_proposal",
                "title": "West Palm Beach 10-Pod Proposal",
                "retrieval_text": "Brad and Beau proposed 10 local pods (50 engineers) in West Palm Beach.",
                "record_type": "claim",
                "answer_use": "strategic_context",
                "evidence_ids": ["ev_wpb_prop"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "strategic_context"},
        },
        {
            "record": {
                "record_id": "rec_nee_bar",
                "title": "NEE Screening Bar & Workstream Cap",
                "retrieval_text": (
                    "NEE enforces a 5-10% technical screening pass rate and has 4 active workstreams "
                    "absorbing 5-6 engineers per pod."
                ),
                "record_type": "claim",
                "answer_use": "decision_constraint",
                "evidence_ids": ["ev_nee_bar"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "decision_constraint"},
        },
    ]
    pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_wave5",
            "question": "Evaluate Brad and Beau West Palm Beach pod proposal for NEE",
            "generation_id": "gen_1",
            "graph_snapshot": "snap_1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "bge-m3",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
        },
        evidence_packet={
            "approved": approved_lane,
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        },
    )
    assert len(pack["conflicts"]) >= 1
    tension = pack["conflicts"][0]
    assert tension["conflict_type"] == "dialectical_constraint_tension"
    assert tension["proposal_record_id"] == "rec_wpb_proposal"
    assert tension["constraint_record_id"] == "rec_nee_bar"
    assert "667" in tension["description"]
    assert set(tension["citations"]) == {"ev_wpb_prop", "ev_nee_bar"}

    # Verify synthesized tension descriptions pass all 6 anti-conflation rules
    mock_answer = {
        "current_state": [{"statement": tension["description"], "citations": ["rec_wpb_proposal", "rec_nee_bar"]}],
        "pain_points": [],
        "opportunities": [],
        "guidance": [],
        "hypotheses": [],
    }
    failures = validate_answer_anti_conflation(
        mock_answer,
        packet={"approved": approved_lane, "candidate": [], "external_public": []},
    )
    assert failures == []
