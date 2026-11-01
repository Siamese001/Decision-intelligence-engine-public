"""Wave 4 verification tests for closed-loop QREL weight proposals, Copilot RRF balancing, and 11-gate qualification."""
from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion import (
    compute_copilot_rrf_weights,
    reciprocal_rank_fusion,
)
from dual_pipeline.release_policy import RETRIEVAL_RELEASE_11_GATES, evaluate_retrieval_11_gates


def test_closed_loop_learning_engine_generates_and_simulates_retrieval_weight_proposal() -> None:
    """ClosedLoopLearningEngine generates a valid retrieval_weight proposal and gates simulation on all 11 BGE-M3 gates."""
    with tempfile.TemporaryDirectory(prefix="clle_bge_m3_w4_") as tmp:
        root = Path(tmp)
        engine = ClosedLoopLearningEngine(runtime_root=root, proposals_dir=root / "proposals")
        cohort = {
            "cohort_key": "telemetry:retrieval:coverage_gap",
            "occurrence_count": 4,
            "run_ids": ["run-qrel-01", "run-qrel-02"],
            "items": [
                {"event_id": "ev-01", "run_id": "run-qrel-01", "created_at": "2026-10-31T10:00:00Z"},
                {"event_id": "ev-02", "run_id": "run-qrel-02", "created_at": "2026-10-31T10:05:00Z"},
            ],
            "latest_timestamp": "2026-10-31T10:05:00Z",
        }
        all_11_gates_true = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}
        proposal = engine.generate_proposal_for_cohort(
            cohort,
            target_override={
                "parameter_name": "retrieval.hybrid_weight_colbert",
                "proposed_value": 0.25,
                "current_value": 0.10,
                "proposed_weights": {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25},
                "target_before": 0.89,
                "target_after": 0.97,
                "bge_gates": all_11_gates_true,
            },
        )
        validate_artifact(proposal)
        assert proposal["proposal_type"] == "query_retrieval"
        ext = proposal["extensions"]["com.partner_advisory.nee.learning"]
        assert ext["target_type"] == "retrieval_weight"
        assert abs(sum(ext["proposed_weights"].values()) - 1.0) < 1e-6

        # Simulation succeeds when all 11 BGE-M3 gates pass
        sim_pass = engine.simulate_proposal(proposal)
        assert sim_pass.success is True
        assert sim_pass.status == "SIMULATED_PASS"

        # Dropping any single BGE-M3 gate fails closed
        degraded_gates = dict(all_11_gates_true)
        degraded_gates["bm25_execution"] = False
        bad_proposal = engine.generate_proposal_for_cohort(
            cohort,
            target_override={
                "parameter_name": "retrieval.hybrid_weight_colbert",
                "proposed_value": 0.25,
                "target_before": 0.89,
                "target_after": 0.97,
                "bge_gates": degraded_gates,
            },
        )
        sim_fail = engine.simulate_proposal(bad_proposal)
        assert sim_fail.success is False
        assert sim_fail.status == "SIMULATED_FAIL"


def test_copilot_rrf_weights_prevent_3_to_1_lexical_dilution_of_dense_ranking() -> None:
    """Calibrated Copilot RRF weights allocate 40% to BGE-M3 dense list and 60% across lexical variants."""
    weights = compute_copilot_rrf_weights(num_lexical_lists=3, has_dense_list=True)
    assert len(weights) == 4
    assert abs(sum(weights) - 1.0) < 1e-5
    assert abs(weights[-1] - 0.40) < 1e-6  # BGE-M3 dense list receives 0.40 weight
    assert abs(sum(weights[:3]) - 0.60) < 1e-5

    # Lexical distractor appears across all 3 lexical variant lists but is absent from BGE-M3 dense top-K,
    # whereas 'asrt_semantic_gold' is #1 in BGE-M3 dense and #2 in primary lexical:
    rankings = [
        [("asrt_lex_noise", 10.0), ("asrt_semantic_gold", 9.5)],
        [("asrt_ask_distractor", 8.0), ("asrt_lex_noise", 7.0)],
        [("asrt_topic_distractor", 6.0), ("asrt_lex_noise", 5.5)],
        [("asrt_semantic_gold", 0.96)],
    ]
    # Under unweighted RRF [1, 1, 1, 1], 3 lexical lists overpower dense 3:1
    fused_unweighted = reciprocal_rank_fusion(rankings, k=60, weights=None)
    assert fused_unweighted[0][0] == "asrt_lex_noise"

    # Under calibrated Copilot RRF weights, the #1 BGE-M3 dense + #2 primary hit wins
    fused_calibrated = reciprocal_rank_fusion(rankings, k=60, weights=weights)
    assert fused_calibrated[0][0] == "asrt_semantic_gold"


def test_11_gate_release_qualification_contract() -> None:
    """All 11 governed BGE-M3 retrieval release gates must be verified and pass without omission."""
    assert len(RETRIEVAL_RELEASE_11_GATES) == 11
    gates = {g: True for g in RETRIEVAL_RELEASE_11_GATES}
    passed, failures = evaluate_retrieval_11_gates(gates)
    assert passed is True
    assert failures == []
