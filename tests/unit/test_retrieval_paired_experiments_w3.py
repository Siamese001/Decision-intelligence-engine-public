"""Unit tests for Wave 3 paired retrieval experiments and evaluation parity."""

from __future__ import annotations

import pytest

from dual_pipeline.retrieval_release_core.paired_experiments import (
    EvaluationMode,
    QueryJudgments,
    RetrievalConfig,
    apply_holm_bonferroni_correction,
    compute_config_score,
    compute_family_clustered_differences,
    evaluate_query_ranking,
    filter_active_candidates,
    rank_candidates_for_query,
    validate_holdout_protection,
)


def test_holdout_protection_blocks_final_holdout_queries():
    partitions = {
        "query_partitions": {
            "dev": ["dev_q1", "dev_q2"],
            "final_holdout": ["holdout_q1", "holdout_q2"],
        }
    }
    # Dev queries pass
    validate_holdout_protection(["dev_q1", "dev_q2"], partitions)

    # Any holdout query must trigger fail-closed ValueError
    with pytest.raises(ValueError, match="HOLDOUT LEAKAGE VIOLATION"):
        validate_holdout_protection(["dev_q1", "holdout_q1"], partitions)


def test_filter_active_candidates_regenerated_mode_excludes_disabled_engines():
    candidates = [
        {"record_id": "c1", "dense_rank": 1, "bm25_rank": None, "sparse_rank": None, "colbert_rank": None},
        {"record_id": "c2", "dense_rank": None, "bm25_rank": 2, "sparse_rank": None, "colbert_rank": None},
        {"record_id": "c3", "dense_rank": None, "bm25_rank": None, "sparse_rank": 3, "colbert_rank": None},
        {"record_id": "c4", "dense_rank": 4, "bm25_rank": 5, "sparse_rank": 6, "colbert_rank": 1},
    ]

    # In fixed pool rerank, all candidates are preserved
    fixed = filter_active_candidates(candidates, RetrievalConfig.BM25_ONLY, EvaluationMode.FIXED_POOL_RERANK)
    assert len(fixed) == 4

    # In regenerated mode, BM25_ONLY admits only candidates with bm25_rank
    bm25_admitted = filter_active_candidates(candidates, RetrievalConfig.BM25_ONLY, EvaluationMode.REGENERATED_CANDIDATES)
    assert [c["record_id"] for c in bm25_admitted] == ["c2", "c4"]

    # NO_BM25 admits candidates with dense, sparse, or colbert support, but not BM25-only
    no_bm25_admitted = filter_active_candidates(candidates, RetrievalConfig.NO_BM25, EvaluationMode.REGENERATED_CANDIDATES)
    assert [c["record_id"] for c in no_bm25_admitted] == ["c1", "c3", "c4"]


def test_compute_config_score_ablation_weights():
    candidate = {
        "record_id": "c1",
        "dense_rank": 1,
        "sparse_rank": 2,
        "bm25_rank": 3,
        "colbert_rank": 4,
    }
    # Dense only returns dense_score or -infinity
    assert compute_config_score({"dense_score": 0.85}, RetrievalConfig.DENSE_ONLY) == 0.85
    assert compute_config_score({}, RetrievalConfig.DENSE_ONLY) == -1e9

    # HYBRID_FULL uses all 4 components
    score_full = compute_config_score(candidate, RetrievalConfig.HYBRID_FULL, rrf_k=60)
    assert score_full > 0.0

    # NO_COLBERT uses only dense, sparse, bm25
    score_no_colbert = compute_config_score(candidate, RetrievalConfig.NO_COLBERT, rrf_k=60)
    assert score_no_colbert > 0.0


def test_rank_candidates_exact_anchor_promotion_parity():
    candidates = [
        {"record_id": "c1", "dense_score": 0.9, "dense_rank": 1},
        {"record_id": "c2", "dense_score": 0.5, "dense_rank": 2},
        {"record_id": "c3_anchor", "dense_score": 0.1, "dense_rank": 3},
    ]

    # Without anchor, c1 is top
    ranking_no_anchor = rank_candidates_for_query(
        candidates, RetrievalConfig.DENSE_ONLY, EvaluationMode.FIXED_POOL_RERANK
    )
    assert ranking_no_anchor == ["c1", "c2", "c3_anchor"]

    # With anchor on c3_anchor, c3_anchor is promoted to rank 1
    ranking_with_anchor = rank_candidates_for_query(
        candidates,
        RetrievalConfig.DENSE_ONLY,
        EvaluationMode.FIXED_POOL_RERANK,
        exact_anchor_ids=["c3_anchor"],
    )
    assert ranking_with_anchor == ["c3_anchor", "c1", "c2"]


def test_evaluate_query_ranking_metrics_and_unjudged():
    gt = QueryJudgments(
        query_id="q1",
        query_text="Sample query?",
        topic_id="top1",
        phrasing_type="executive",
        judgments={"c1": 2, "c2": 1, "c3": 0},
        relevant_ids={"c1", "c2"},
        is_answerable=True,
    )
    # c4 is unjudged
    retrieved = ["c1", "c4", "c2"]
    cand_recall, metrics_at_k = evaluate_query_ranking(retrieved, gt, cutoffs=(5,))

    assert cand_recall == 1.0  # both c1 and c2 are in retrieved
    assert metrics_at_k[5]["recall"] == 1.0
    assert metrics_at_k[5]["mrr"] == 1.0  # c1 is rank 1
    assert metrics_at_k[5]["ndcg"] > 0.0
    # c4 is not in gt.judgments, out of 5 cutoff (cutoff has 3 items, but denominator is k=5)
    assert metrics_at_k[5]["unjudged"] == pytest.approx(1.0 / 5.0)


def test_compute_family_clustered_differences_and_ci():
    gt_map = {
        "q1": QueryJudgments("q1", "text", "topic_A", "exec", {}, {"c1"}, True),
        "q2": QueryJudgments("q2", "text", "topic_A", "eng", {}, {"c1"}, True),
        "q3": QueryJudgments("q3", "text", "topic_B", "exec", {}, {"c1"}, True),
        "q4": QueryJudgments("q4", "text", "topic_B", "eng", {}, {"c1"}, True),
    }
    base = {
        "q1": {"ndcg_10": 0.8},
        "q2": {"ndcg_10": 0.8},
        "q3": {"ndcg_10": 0.7},
        "q4": {"ndcg_10": 0.7},
    }
    # Comparator is +0.1 on topic_A and +0.05 on topic_B
    comp = {
        "q1": {"ndcg_10": 0.9},
        "q2": {"ndcg_10": 0.9},
        "q3": {"ndcg_10": 0.75},
        "q4": {"ndcg_10": 0.75},
    }
    mean_d, se_d, (ci_lo, ci_hi) = compute_family_clustered_differences(base, comp, gt_map)
    assert mean_d == pytest.approx(0.075, abs=0.001)
    assert se_d > 0.0
    assert ci_lo < mean_d < ci_hi


def test_apply_holm_bonferroni_correction_step_down_and_monotonicity():
    raw_p = {
        "engine_sparse": 0.01,
        "engine_dense": 0.04,
        "engine_bm25": 0.04,
        "engine_colbert": 0.20,
    }
    adjusted = apply_holm_bonferroni_correction(raw_p)
    assert len(adjusted) == 4
    assert adjusted["engine_sparse"] == pytest.approx(0.04)
    assert adjusted["engine_dense"] == pytest.approx(0.12)
    assert adjusted["engine_bm25"] == pytest.approx(0.12)
    assert adjusted["engine_colbert"] == pytest.approx(0.20)

    # Monotonicity check
    vals = [adjusted[k] for k in sorted(raw_p.keys(), key=lambda x: raw_p[x])]
    assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
    assert apply_holm_bonferroni_correction({}) == {}


def test_temporal_validity_filters_superseded_and_stale_assertions():
    from dual_pipeline.index_core.anchor_traversal_support import _is_assertion_temporally_valid

    base_asrt = {
        "assertion_id": "a1",
        "currentness_state": "current",
        "status": "APPROVED",
        "superseded_by_assertion_id": None,
        "superseded_at": None,
        "valid_to": None,
        "stale_after": None,
    }
    assert _is_assertion_temporally_valid(base_asrt) is True

    # Superseded ID
    assert _is_assertion_temporally_valid(dict(base_asrt, superseded_by_assertion_id="a2")) is False

    # Superseded at timestamp
    assert _is_assertion_temporally_valid(dict(base_asrt, superseded_at="2026-10-01T00:00:00Z")) is False

    # Status
    for bad_status in ("SUPERSEDED", "RETRACTED", "REJECTED", "DELETED", "INACTIVE"):
        assert _is_assertion_temporally_valid(dict(base_asrt, status=bad_status)) is False

    # Currentness state
    for bad_state in ("superseded", "stale", "retracted"):
        assert _is_assertion_temporally_valid(dict(base_asrt, currentness_state=bad_state)) is False

    # Past valid_to (ISO and MM/DD/YYYY)
    assert _is_assertion_temporally_valid(dict(base_asrt, valid_to="2020-01-01")) is False
    assert _is_assertion_temporally_valid(dict(base_asrt, valid_to="01/01/2020")) is False

    # Past stale_after
    assert _is_assertion_temporally_valid(dict(base_asrt, stale_after="2020-01-01")) is False
    assert _is_assertion_temporally_valid(dict(base_asrt, stale_after="01/01/2020")) is False

    # Future dates pass
    assert _is_assertion_temporally_valid(dict(base_asrt, valid_to="2099-12-31")) is True
    assert _is_assertion_temporally_valid(dict(base_asrt, stale_after="12/31/2099")) is True


def test_anchor_expansion_hop0_anchors_strictly_precede_hop2_pred_matches():
    import sqlite3
    import tempfile
    from pathlib import Path
    from dual_pipeline.index_core.anchor_expansion import expand_retrieval_anchors

    with tempfile.NamedTemporaryFile(suffix=".sqlite") as tf:
        conn = sqlite3.connect(tf.name)
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT)")
        conn.execute(
            "CREATE TABLE assertion ("
            "assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, "
            "object_literal TEXT, classification TEXT, allowed_audiences_json TEXT, currentness_state TEXT, "
            "status TEXT, superseded_by_assertion_id TEXT, superseded_at TEXT, valid_to TEXT, stale_after TEXT, "
            "qualifiers_json TEXT, confidence_score REAL, assurance_tier TEXT, canonical_status TEXT, validation_state TEXT)"
        )
        conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT)")

        conn.execute("INSERT INTO node VALUES ('n1', 'Alpha', 'project')")
        conn.execute("INSERT INTO node VALUES ('n2', 'Beta', 'organization')")
        conn.execute("INSERT INTO node VALUES ('n3', 'Active', 'status')")

        conn.execute(
            "INSERT INTO assertion VALUES ('a1', 'n1', 'relates_to', 'n2', 'Beta', 'internal', "
            "'[\"internal_consulting\"]', 'current', 'APPROVED', NULL, NULL, NULL, NULL, NULL, 0.9, 'gold', 'canonical', 'approved')"
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('a1', 'ev1', 'supports')")

        # a2 matches predicate 'has_status' (2-hop traversal)
        conn.execute(
            "INSERT INTO assertion VALUES ('a2', 'n2', 'has_status', 'n3', 'Active', 'internal', "
            "'[\"internal_consulting\"]', 'current', 'APPROVED', NULL, NULL, NULL, NULL, NULL, 0.9, 'gold', 'canonical', 'approved')"
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('a2', 'ev2', 'supports')")

        conn.commit()
        conn.close()

        # passage_0 is a 0-hop passage anchor that does NOT have predicate 'has_status'
        results = [
            {
                "record_id": "passage_0",
                "record": {
                    "record_type": "source_chunk",
                    "evidence_ids": ["ev1"],
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                },
                "fused_score": 0.9,
            }
        ]
        expanded = expand_retrieval_anchors(
            results,
            Path(tf.name),
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            query="status",
            hop_budget=2,
        )

        record_ids = [r["record_id"] for r in expanded]
        assert "passage_0" in record_ids
        assert "expanded-assertion-a2" in record_ids
        # 0-hop anchor strictly precedes 2-hop predicate match
        assert record_ids.index("passage_0") < record_ids.index("expanded-assertion-a2")
        assert record_ids[0] == "passage_0"

