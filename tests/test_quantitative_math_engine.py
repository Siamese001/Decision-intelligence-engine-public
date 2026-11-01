"""Tests for Wave 3: Quantitative Funnel, Capacity & Rate Math Engine."""
from __future__ import annotations

from dual_pipeline.decision_engine_core.constants import extract_tokens
from dual_pipeline.decision_engine_core.evidence_pack import build_decision_evidence_pack
from dual_pipeline.decision_engine_core.quantitative_math import (
    compute_commercial_burn_math,
    compute_portfolio_absorption_math,
    compute_recruiting_funnel_math,
)
from dual_pipeline.graph_retrieval_core.tokenize import _question_tokens, bm25_query_tokens


def test_recruiting_funnel_math_yields_667_candidates_for_50_engineers_at_7_5_pct() -> None:
    proof = compute_recruiting_funnel_math(
        proposed_headcount=50.0,
        pass_rate_pct=7.5,
        evidence_id="ev_bar_1",
    )
    assert proof["proof_type"] == "recruiting_funnel"
    assert proof["required_candidate_pool"] == 667
    assert proof["feasibility_status"] == "BREACH"
    assert proof["evidence_id"] == "ev_bar_1"


def test_portfolio_absorption_math_yields_24_max_and_26_overrun_for_50_engineers() -> None:
    proof = compute_portfolio_absorption_math(
        proposed_headcount=50.0,
        active_workstreams=4.0,
        engineers_per_workstream=6.0,
        evidence_id="ev_cap_1",
    )
    assert proof["proof_type"] == "portfolio_absorption"
    assert proof["max_absorbable_headcount"] == 24.0
    assert proof["capacity_overrun_headcount"] == 26.0
    assert proof["feasibility_status"] == "BREACH"
    assert proof["evidence_id"] == "ev_cap_1"


def test_commercial_burn_math_captures_weekly_cap_and_rate_differential() -> None:
    proof = compute_commercial_burn_math(
        pod_count=10.0,
        quarterly_rate_usd=600_000.0,
        weekly_hours=42.0,
        onshore_premium_pct=45.0,
        evidence_id="ev_comm_1",
    )
    assert proof["proof_type"] == "commercial_burn"
    assert proof["quarterly_burn_usd"] == 6_000_000.0
    assert proof["excess_weekly_hours"] == 2.0
    assert proof["onshore_premium_pct"] == 45.0
    assert proof["feasibility_status"] == "BREACH"


def test_tokenize_preserves_percentage_and_ratio_tokens_without_single_digit_leakage() -> None:
    q = "What is the impact of a 5-10% pass rate and 1:8 ratio on 5% conversion?"
    bm25 = bm25_query_tokens(q)
    q_tokens = _question_tokens(q)
    c_tokens = extract_tokens(q)
    for token_set in (set(bm25), q_tokens, c_tokens):
        assert "5pct" in token_set
        assert "10pct" in token_set
        assert "1to8" in token_set

    # Verify single-character digits remain excluded
    assert bm25_query_tokens("a b c d e x y z 1 2 9") == []
    assert _question_tokens("a b c d e x y z 1 2 9") == set()


def test_evidence_pack_enriches_constraints_with_quantitative_math() -> None:
    records_lane = [
        {
            "record": {
                "record_id": "rec_proposal_wpb",
                "title": "West Palm Beach Pod Proposal",
                "retrieval_text": "Brad and Beau proposed 50 local full-stack engineers in West Palm Beach.",
                "record_type": "claim",
                "answer_use": "strategic_context",
                "evidence_ids": ["ev_wpb_1"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "strategic_context"},
        },
        {
            "record": {
                "record_id": "rec_constraint_bar",
                "title": "NEE Engineering Bar & Pod Capacity",
                "retrieval_text": (
                    "NEE requires a 5-10% technical screening pass rate, a 1:8 lead-to-engineer ratio, "
                    "and 4 active workstreams absorbing 5-6 engineers per pod under a 42 hours/week cap."
                ),
                "record_type": "claim",
                "answer_use": "decision_constraint",
                "evidence_ids": ["ev_nee_bar_1"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "decision_constraint"},
        },
    ]
    pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_wave3_test",
            "question": "Evaluate the 50-person West Palm Beach local pod proposal for NEE",
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
            "approved": records_lane,
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        },
    )
    assert len(pack["constraints"]) == 1
    constraint = pack["constraints"][0]
    assert "funnel_math_proofs" in constraint
    by_type = {p["proof_type"]: p for p in constraint["funnel_math_proofs"]}
    assert by_type["recruiting_funnel"]["required_candidate_pool"] == 667
    assert by_type["portfolio_absorption"]["max_absorbable_headcount"] == 24.0
    assert by_type["portfolio_absorption"]["capacity_overrun_headcount"] == 26.0
    assert by_type["commercial_burn"]["feasibility_status"] == "BREACH"
