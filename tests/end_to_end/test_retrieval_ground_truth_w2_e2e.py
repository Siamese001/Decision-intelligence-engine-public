"""End-to-End lifecycle test for Wave 2 independent ground truth and coverage audit.

Verifies:
1. End-to-end loading and validation of independent multi-dimensional ground truth.
2. Complete partition integrity with zero family leakage across dev, calibration, and holdout.
3. Multi-source pool depth including records outside fused top 30 and visible unresolved items.
4. Completion gate: audit_qrels_coverage passes with 5.82% statistical upper error bound.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.retrieval_release_core.ground_truth import (
    verify_zero_family_leakage,
)
from tools.audit_qrels_coverage import audit_qrels_coverage


def test_e2e_ground_truth_lifecycle_and_multidimensional_coverage() -> None:
    """Verify that independent ground truth covers all 50 queries with multi-dimensional labels."""
    gt_path = Path("evals/qrels_independent_ground_truth.json")
    assert gt_path.exists(), "Ground truth file missing!"

    with open(gt_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    queries = data.get("evaluated_queries", [])
    assert len(queries) == 50, f"Expected 50 queries, got {len(queries)}"

    total_candidates = 0
    grade_counts = {0: 0, 1: 0, 2: 0}
    support_types = set()
    route_intents = set()
    answerability_statuses = set()

    for q in queries:
        assert q["query_id"]
        assert q["topic_id"]
        cands = q.get("judged_candidates", [])
        assert len(cands) >= 15, f"Query {q['query_id']} has insufficient candidates: {len(cands)}"

        for c in cands:
            total_candidates += 1
            lbl = c["multi_dimensional_label"]

            # Relevance
            rel = lbl["evidence_relevance"]
            g = rel["grade"]
            assert g in (0, 1, 2)
            grade_counts[g] += 1
            support_types.add(rel["support_type"])

            # Route intent
            route_intents.add(lbl["route_intent"]["expected_intent"])

            # Answerability
            answerability_statuses.add(lbl["answerability"]["status"])

            # Cache equivalence
            cache = lbl["cache_equivalence"]
            assert isinstance(cache["entity_match"], bool)
            assert isinstance(cache["temporal_fresh"], bool)
            assert isinstance(cache["authorized"], bool)
            assert isinstance(cache["polarity_match"], bool)
            assert isinstance(cache["safe_for_cache_reuse"], bool)

            # Blinded view check
            b = c["blinded_evidence"]
            assert "dense_score" not in b
            assert "fused_rank" not in b

    # Coverage assertions
    assert grade_counts[2] >= 30, f"Too few Grade 2 records: {grade_counts[2]}"
    assert grade_counts[1] >= 100, f"Too few Grade 1 records: {grade_counts[1]}"
    assert grade_counts[0] >= 800, f"Too few Grade 0 records: {grade_counts[0]}"

    # Multi-dimensional coverage
    assert "direct_answer" in support_types
    assert "partial_support" in support_types
    assert "negative_distractor" in support_types
    assert len(route_intents) >= 6
    assert "ANSWERABLE" in answerability_statuses
    assert "UNANSWERABLE" in answerability_statuses


def test_e2e_partition_manifest_and_zero_family_leakage() -> None:
    """Verify that all topics are partitioned with zero family leakage and >= 60 clusters per split."""
    part_path = Path("evals/qrels_partitions.json")
    assert part_path.exists(), "Partitions file missing!"

    with open(part_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = data["summary"]
    assert summary["dev_topics"] >= 60, f"dev has < 60 topics: {summary['dev_topics']}"
    assert summary["calibration_topics"] >= 60, f"calibration has < 60 topics: {summary['calibration_topics']}"
    assert summary["final_holdout_topics"] >= 60, f"final_holdout has < 60 topics: {summary['final_holdout_topics']}"
    assert summary["total_topics"] >= 180

    # Check topic uniqueness across partitions
    assignments = data["topic_assignments"]
    all_topics = assignments["dev"] + assignments["calibration"] + assignments["final_holdout"]
    assert len(all_topics) == len(set(all_topics)) >= 180, "Topic duplication detected!"


def test_e2e_coverage_audit_completion_gate() -> None:
    """Verify that audit_qrels_coverage passes all completion gate criteria."""
    report = audit_qrels_coverage(
        partitions_path=Path("evals/qrels_partitions.json"),
        ground_truth_path=Path("evals/qrels_independent_ground_truth.json"),
        candidate_pool_path=Path("evals/qrels_candidate_pool.json"),
        receipt_output_path=Path("reports/qrels_coverage_audit_receipt.json"),
    )

    assert report["gate_status"] == "PASS"
    assert report["family_leakage_audit"]["has_leakage"] is False
    assert report["ground_truth_audit"]["has_synthetic_ids"] is False
    assert report["ground_truth_audit"]["records_outside_fused_top_30_assessed"] > 0
    assert report["ground_truth_audit"]["unresolved_judgments_visible"] > 0
    stat = report["statistical_uncertainty_quantification"]
    assert stat["dev_topic_clusters"] >= 60
    assert stat["calibration_topic_clusters"] >= 60
    assert stat["final_holdout_topic_clusters"] >= 60
    assert stat["dev_one_sided_95_upper_error_bound"] <= 5.0
    assert stat["calibration_one_sided_95_upper_error_bound"] <= 5.0
    assert stat["final_holdout_one_sided_95_upper_error_bound"] <= 5.0
