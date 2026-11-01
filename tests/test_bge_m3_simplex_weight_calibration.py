"""Wave 3 verification tests for route-adaptive RRF weight profiles and 3-simplex K-fold calibration."""
from __future__ import annotations

from dual_pipeline.index_core.constants import (
    BM25_RRF_WEIGHTS,
    CALIBRATED_HYBRID_RRF_WEIGHTS,
    RETRIEVAL_MODE_POLICY,
    RETRIEVAL_MODE_RRF_WEIGHTS,
    STRATEGIC_BM25_RRF_WEIGHTS,
)
from dual_pipeline.parameters_subsystems import get_bge_m3_retrieval_weight_profiles
from tools.calibrate_retrieval_thresholds import (
    build_210_query_qrel_workbench,
    generate_simplex_grid,
    paired_bootstrap_ndcg_gain,
    sweep_bge_m3_simplex_weights,
    train_bge_m3_weights_sgd,
)


def test_all_retrieval_modes_have_simplex_normalized_weight_profiles() -> None:
    """Every governed retrieval mode in RETRIEVAL_MODE_POLICY must have a simplex-normalized profile."""
    ssot_profiles = get_bge_m3_retrieval_weight_profiles()
    for mode in RETRIEVAL_MODE_POLICY:
        assert mode in RETRIEVAL_MODE_RRF_WEIGHTS, f"Missing mode in RETRIEVAL_MODE_RRF_WEIGHTS: {mode}"
        assert mode in ssot_profiles, f"Missing mode in get_bge_m3_retrieval_weight_profiles: {mode}"
        weights = RETRIEVAL_MODE_RRF_WEIGHTS[mode]
        assert set(weights.keys()) == {"dense", "sparse", "bm25", "colbert"}
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        for ch, w in weights.items():
            assert 0.05 <= w <= 0.70, f"Invalid channel weight {ch}={w} in mode {mode}"

    assert RETRIEVAL_MODE_RRF_WEIGHTS["factual"] == BM25_RRF_WEIGHTS
    assert RETRIEVAL_MODE_RRF_WEIGHTS["strategic"] == STRATEGIC_BM25_RRF_WEIGHTS
    assert RETRIEVAL_MODE_RRF_WEIGHTS["guidance"] == CALIBRATED_HYBRID_RRF_WEIGHTS
    # Entity navigation modes prioritize dense + sparse + ColBERT over BM25
    assert RETRIEVAL_MODE_RRF_WEIGHTS["entity_navigation"]["colbert"] >= 0.25
    assert RETRIEVAL_MODE_RRF_WEIGHTS["entity_navigation"]["bm25"] <= 0.25


def test_simplex_grid_and_stratified_kfold_weight_optimizer() -> None:
    """Simplex optimizer improves graded nDCG over 85%-lexical baseline with statistically significant bootstrap CI."""
    grid = generate_simplex_grid(step=0.10, min_weight=0.05)
    assert len(grid) > 50
    for w in grid:
        assert abs(sum(w.values()) - 1.0) < 1e-6

    cal = sweep_bge_m3_simplex_weights(step=0.10)
    opt_w = cal["optimal_weights"]
    assert abs(sum(opt_w.values()) - 1.0) < 1e-6
    assert cal["calibrated_mean_ndcg"] > cal["baseline_mean_ndcg"]
    assert cal["paired_bootstrap"]["observed_mean_gain"] > 0.0
    assert cal["paired_bootstrap"]["ci_95_low"] > 0.0
    assert cal["paired_bootstrap"]["p_value"] < 0.05


def test_paired_bootstrap_does_not_clamp_negative_regressions() -> None:
    """Unclamped paired bootstrap must report negative gain when candidate regresses below baseline."""
    base = [0.95, 0.90, 0.92, 0.88, 0.91]
    regressed = [0.70, 0.65, 0.68, 0.60, 0.72]
    stats = paired_bootstrap_ndcg_gain(base, regressed, n_resamples=500, seed=7)
    assert stats["observed_mean_gain"] < -0.15
    assert stats["ci_95_high"] < 0.0
    assert stats["p_value"] == 1.0


def test_210_query_workbench_and_pairwise_sgd_matrix_calibration() -> None:
    """210-query (30 per route) workbench trains the 7x4 matrix via pairwise SGD with strictly decreasing loss."""
    wb = build_210_query_qrel_workbench(per_route=30)
    assert len(wb) == 210
    sgd_res = train_bge_m3_weights_sgd(wb)
    assert sgd_res["total_queries"] == 210
    assert sgd_res["total_pairwise_comparisons"] == 1260
    assert sgd_res["free_parameters_calibrated"] == 22
    matrix = sgd_res["route_weight_matrix"]
    metrics = sgd_res["route_metrics"]
    assert len(matrix) == 7
    for route, weights in matrix.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-5, f"Route {route} weights do not sum to 1.0: {weights}"
        for w in weights.values():
            assert 0.05 <= w <= 0.70
        m = metrics[route]
        assert m["query_count"] == 30.0
        assert m["final_pairwise_loss"] < m["initial_pairwise_loss"]
        assert m["mean_ndcg"] >= 0.75
