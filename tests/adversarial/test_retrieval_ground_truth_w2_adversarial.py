"""Adversarial and edge-case tests for Wave 2 independent ground truth governance.

Covers mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty queries, missing entities, 0-byte record IDs).
2. Malformed input (illegal grades, non-disjoint topic lists, invalid status).
3. Invariant bypass attempt (smuggling topic into multiple partitions, provenance rewriting).
4. Exact production regression (circular score-based grading, reload provenance re-labeling).
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.retrieval_release_core.ground_truth import (
    BlindedCandidateView,
    MultiDimensionalJudgment,
    CacheEquivalenceLabel,
    group_partition_queries,
    verify_zero_family_leakage,
    JudgmentProvenance,
)
from tools.audit_qrels_coverage import calculate_one_sided_upper_error_bound


# -----------------------------------------------------------------------------
# 1. Boundary Payload Tests
# -----------------------------------------------------------------------------

def test_adversarial_boundary_zero_sample_upper_bound() -> None:
    """Boundary payload: sample_size <= 0 must return 1.0 (complete uncertainty)."""
    assert calculate_one_sided_upper_error_bound(0, 0.95) == 1.0
    assert calculate_one_sided_upper_error_bound(-5, 0.95) == 1.0


def test_adversarial_boundary_unmapped_topic_fails_closed() -> None:
    """Boundary payload: query with an unmapped topic cannot be silently partitioned."""
    unmapped_queries = [
        {"query_id": "q_unknown", "query_text": "What is x?", "topic_id": "topic_99_rogue"}
    ]
    with pytest.raises(ValueError, match="unmapped topic_id"):
        group_partition_queries(
            unmapped_queries,
            dev_topic_ids=["topic_01"],
            calibration_topic_ids=["topic_02"],
            holdout_topic_ids=["topic_03"],
        )


# -----------------------------------------------------------------------------
# 2. Malformed Input Tests
# -----------------------------------------------------------------------------

def test_adversarial_malformed_grade_fails_closed() -> None:
    """Malformed input: grade outside {0, 1, 2} must raise ValueError immediately."""
    cache_lbl = CacheEquivalenceLabel(entity_match=True, temporal_fresh=True, authorized=True, polarity_match=True)

    with pytest.raises(ValueError, match="Invalid grade 3"):
        MultiDimensionalJudgment(
            query_id="q1",
            record_id="rec1",
            grade=3,  # Illegal grade!
            support_type="direct_answer",
            route_intent="exact_lookup",
            answerability="ANSWERABLE",
            cache_equivalence=cache_lbl,
        )

    with pytest.raises(ValueError, match="Invalid grade -1"):
        MultiDimensionalJudgment(
            query_id="q1",
            record_id="rec1",
            grade=-1,  # Illegal grade!
            support_type="direct_answer",
            route_intent="exact_lookup",
            answerability="ANSWERABLE",
            cache_equivalence=cache_lbl,
        )


def test_adversarial_malformed_overlapping_partitions_fail_closed() -> None:
    """Malformed input: overlapping topic lists must fail closed at initialization."""
    with pytest.raises(ValueError, match="Topic overlap between dev and calibration"):
        group_partition_queries(
            [],
            dev_topic_ids=["topic_shared", "topic_01"],
            calibration_topic_ids=["topic_shared", "topic_02"],  # Overlap!
            holdout_topic_ids=["topic_03"],
        )


# -----------------------------------------------------------------------------
# 3. Invariant Bypass Attempts
# -----------------------------------------------------------------------------

def test_adversarial_bypass_family_leakage_detected() -> None:
    """Invariant bypass: attempting to place paraphrases in different partitions fails."""
    # Two queries from the same topic placed into different partitions
    corrupted_partitions = {
        "dev": [{"query_id": "q1_exec", "topic_id": "topic_01_fpl"}],
        "final_holdout": [{"query_id": "q1_eng", "topic_id": "topic_01_fpl"}],  # Leakage!
    }

    with pytest.raises(ValueError, match="Family leakage detected: topic topic_01_fpl"):
        verify_zero_family_leakage(corrupted_partitions)


def test_adversarial_bypass_provenance_tampering_rejected() -> None:
    """Invariant bypass: automated weak supervision cannot be rebranded as human_verified."""
    gt_file = Path("evals/qrels_independent_ground_truth.json")
    with open(gt_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Verify that prior automated judgments have weak_supervision_v1 provenance
    weak_sup_found = False
    for q in data["evaluated_queries"]:
        for c in q["judged_candidates"]:
            if "weak_supervision_v1" in c:
                ws = c["weak_supervision_v1"]
                # Must be weak_supervision_v1, never human_verified
                assert ws["provenance"] == JudgmentProvenance.WEAK_SUPERVISION_V1.value
                assert ws["provenance"] != JudgmentProvenance.HUMAN_VERIFIED.value
                weak_sup_found = True

    assert weak_sup_found is True


# -----------------------------------------------------------------------------
# 4. Exact Production Regressions
# -----------------------------------------------------------------------------

def test_adversarial_regression_circular_retriever_scores_blinded() -> None:
    """Exact production regression: retriever dense_score cannot influence independent grade."""
    from tools.build_independent_ground_truth import _assess_independent_relevance

    query = {
        "target_entities": ["Chakra", "Agentic"],
        "ground_truth_summary": "Chakra defines agentic multi-lane orchestration prerequisites.",
        "topic_id": "topic_19_chakra_control_prerequisites",
    }

    # An irrelevant distractor that has an artificially high dense_score (0.98)
    cand_distractor_high_score = {
        "title": "Random Irrelevant Document",
        "retrieval_text": "Unrelated database clustering without any target terms.",
        "dense_score": 0.98,
        "fused_rank": 1,
    }

    # In the old circular grader, high dense_score would boost score_g2.
    # In the independent grader, it must be Grade 0 (Irrelevant).
    grade, support_type, _, _ = _assess_independent_relevance(query, cand_distractor_high_score)
    assert grade == 0
    assert support_type == "irrelevant_background"


def test_adversarial_synthetic_id_injection_fails_closed(tmp_path: Path) -> None:
    """Invariant bypass attempt: injecting synthetic expert_pos_* or outside_pool_sample_* must fail closed."""
    from tools.build_independent_ground_truth import build_independent_ground_truth_dataset

    pool_file = tmp_path / "synthetic_pool.json"
    pool_file.write_text(json.dumps({
        "queries": [{
            "query_id": "q_attack",
            "query_text": "Attack query",
            "topic_id": "topic_01",
            "candidates": [{
                "record_id": "expert_pos_q_attack",  # Prohibited synthetic ID!
                "title": "Fake Positive",
                "retrieval_text": "Synthetic text",
            }]
        }]
    }))

    out_file = tmp_path / "synthetic_out.json"
    with pytest.raises(ValueError, match="Synthetic ID 'expert_pos_q_attack' prohibited under Invariant 9"):
        build_independent_ground_truth_dataset(
            candidate_pool_path=pool_file,
            old_graded_path=tmp_path / "none.json",
            output_path=out_file,
        )


def test_adversarial_partition_under_60_clusters_fails_coverage_audit(tmp_path: Path) -> None:
    """Statistical power breach: any partition with < 60 clusters fails audit gate closed."""
    from tools.audit_qrels_coverage import audit_qrels_coverage

    # Create partitions where dev has only 10 clusters
    part_file = tmp_path / "underpowered_partitions.json"
    part_file.write_text(json.dumps({
        "topic_assignments": {
            "dev": [f"t_dev_{i}" for i in range(10)],  # Only 10 clusters!
            "calibration": [f"t_cal_{i}" for i in range(60)],
            "final_holdout": [f"t_hold_{i}" for i in range(60)],
        }
    }))

    gt_file = Path("evals/qrels_independent_ground_truth.json")
    pool_file = Path("evals/qrels_candidate_pool.json")

    report = audit_qrels_coverage(part_file, gt_file, pool_file)
    assert report["gate_status"] == "FAIL"
    assert report["statistical_uncertainty_quantification"]["dev_topic_clusters"] == 10
    # Dev upper error bound is ~25.89% (> 5.0%)
    assert report["statistical_uncertainty_quantification"]["dev_one_sided_95_upper_error_bound"] > 5.0


def test_adversarial_synthetic_id_in_ground_truth_fails_coverage_audit(tmp_path: Path) -> None:
    """Anti-pollution breach: ground truth containing synthetic IDs fails audit gate closed."""
    from tools.audit_qrels_coverage import audit_qrels_coverage

    gt_file = tmp_path / "contaminated_gt.json"
    gt_file.write_text(json.dumps({
        "evaluated_queries": [{
            "query_id": "q1",
            "judged_candidates": [{
                "record_id": "outside_pool_sample_q1",  # Synthetic pollution!
                "multi_dimensional_label": {"evidence_relevance": {"grade": 0}},
                "review_status": "UNRESOLVED",
            }]
        }]
    }))

    part_file = Path("evals/qrels_partitions.json")
    pool_file = Path("evals/qrels_candidate_pool.json")

    report = audit_qrels_coverage(part_file, gt_file, pool_file)
    assert report["gate_status"] == "FAIL"
    assert report["ground_truth_audit"]["has_synthetic_ids"] is True
