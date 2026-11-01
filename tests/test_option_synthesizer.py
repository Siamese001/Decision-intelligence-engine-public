"""Tests for Wave 6: Constraint-Satisfaction Option Synthesis."""
from __future__ import annotations

from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from dual_pipeline.decision_engine_core.option_synthesizer import (
    HYBRID_MITIGATED_OPTION_LABEL,
)


def test_evaluative_counterfactual_query_synthesizes_hybrid_winner_when_constraints_collide() -> None:
    question = (
        "Provide honest analysis of Brad and Beau discussion on pods local to West Palm Beach "
        "area from the perspective of NEE: what might work and what will not work?"
    )
    frame = build_decision_frame(question=question)
    approved_lane = [
        {
            "record": {
                "record_id": "rec_wpb_proposal",
                "title": "West Palm Beach Local Pod Proposal",
                "retrieval_text": (
                    "Brad and Beau proposed 10 local pods (50 engineers) in West Palm Beach "
                    "under Accelerate Power with 42 hours/week fixed billing."
                ),
                "record_type": "claim",
                "answer_use": "strategic_context",
                "evidence_ids": ["ev_wpb_proposal"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "strategic_context"},
        },
        {
            "record": {
                "record_id": "rec_nee_constraints",
                "title": "NEE Engineering Bar, Capacity & Deprovisioning Constraints",
                "retrieval_text": (
                    "NEE enforces a 5-10% technical screening pass rate, a 1:8 lead-to-engineer ratio, "
                    "is phasing out segregated pods across 4 active workstreams (5-6 engineers per pod), "
                    "faces a 45% higher onshore rate differential, and requires two weeks prior (14-day) "
                    "IT deprovisioning notice."
                ),
                "record_type": "claim",
                "answer_use": "decision_constraint",
                "evidence_ids": ["ev_nee_constraints"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "decision_constraint"},
        },
    ]
    pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_wave6_wpb",
            "question": question,
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
    result = evaluate_decision(
        frame=frame,
        evidence_pack=pack,
        answer=None,
        execution={"query_run_id": "run_wave6_wpb"},
    )
    assert result["terminal_state"] == "DECISION_PRODUCED"
    assert result["recommended_option"] is not None
    assert result["recommended_option"]["label"] == HYBRID_MITIGATED_OPTION_LABEL
    assert result["recommended_option"]["source"] == "constraint_satisfaction_synthesis"
    assert len(result["recommended_option"]["parameter_mutations"]) >= 2
    assert set(result["material_conclusions"][0]["evidence_ids"]) == {
        "ev_wpb_proposal",
        "ev_nee_constraints",
    }

    # Verify Devil's Advocate extension includes parameter_mutation_directives
    da_ext = result["extensions"]["com.partner_advisory.nee.counterfactual-challenge"]
    assert len(da_ext["parameter_mutation_directives"]) >= 2
