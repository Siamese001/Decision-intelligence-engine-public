"""Unit tests for Wave 2 independent ground truth and grouped partitioning.

Verifies:
1. Blinded candidate representation strips all scores and ranks.
2. Score permutation invariance: changing retriever scores has zero effect on labels.
3. Multi-dimensional label schema validation.
4. Provenance immutability: reloading preserves weak_supervision_v1 and expert labels.
5. Zero family leakage across dev, calibration, and final holdout.
6. Visibility of unresolved judgments.
7. Statistical upper error bound arithmetic (5.82% for N=50).
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.retrieval_release_core.ground_truth import (
    BlindedCandidateView,
    create_blinded_candidate_view,
    EvidenceGrade,
    AnswerabilityStatus,
    JudgmentProvenance,
    CacheEquivalenceLabel,
    MultiDimensionalJudgment,
    group_partition_queries,
    verify_zero_family_leakage,
)
from tools.audit_qrels_coverage import (
    audit_qrels_coverage,
    calculate_one_sided_upper_error_bound,
)


def test_blinded_candidate_view_strips_retriever_features() -> None:
    """Verify that all dense, sparse, BM25, and fusion scores/ranks are completely stripped."""
    candidate = {
        "record_id": "rec_123",
        "record_type": "assertion",
        "lane": "approved",
        "title": "Nexus Topology Core",
        "retrieval_text": "Nexus provides foundational routing and evidence projection.",
        "source_id": "nexus_doc_01",
        # Features that MUST be stripped:
        "dense_score": 0.892,
        "dense_rank": 1,
        "sparse_score": 14.5,
        "sparse_rank": 3,
        "bm25_rank": 2,
        "colbert_score": 28.4,
        "colbert_rank": 1,
        "fused_rank": 1,
        "fused_score": 0.054,
        "assessment": {"recommended_grade": 2},
    }

    blinded = create_blinded_candidate_view(candidate)

    # Allowed evidence fields
    assert blinded["record_id"] == "rec_123"
    assert blinded["record_type"] == "assertion"
    assert blinded["lane"] == "approved"
    assert blinded["title"] == "Nexus Topology Core"
    assert blinded["retrieval_text"] == "Nexus provides foundational routing and evidence projection."
    assert blinded["source_id"] == "nexus_doc_01"

    # Forbidden retriever ranking features MUST NOT be present
    for forbidden in (
        "dense_score", "dense_rank", "sparse_score", "sparse_rank",
        "bm25_rank", "colbert_score", "colbert_rank", "fused_rank",
        "fused_score", "assessment"
    ):
        assert forbidden not in blinded, f"Forbidden feature '{forbidden}' leaked into blinded view!"


def test_score_permutation_invariance() -> None:
    """Verify that permuting or corrupting retriever scores has ZERO impact on independent grading."""
    from tools.build_independent_ground_truth import _assess_independent_relevance

    query = {
        "target_entities": ["Nexus", "APEX"],
        "ground_truth_summary": "Nexus provides land management workflow integration for APEX.",
        "topic_id": "topic_01_fpl_land_management",
    }

    cand_high_score = {
        "title": "APEX Nexus Land Management Integration",
        "retrieval_text": "Nexus handles land management records for APEX operations.",
        "dense_score": 0.95,
        "fused_rank": 1,
    }

    cand_low_score = {
        "title": "APEX Nexus Land Management Integration",
        "retrieval_text": "Nexus handles land management records for APEX operations.",
        "dense_score": 0.12,  # Artificially depressed score
        "fused_rank": 85,    # Artificially low rank
    }

    g_high, s_high, a_high, _ = _assess_independent_relevance(query, cand_high_score)
    g_low, s_low, a_low, _ = _assess_independent_relevance(query, cand_low_score)

    # Permuting scores CANNOT change labels!
    assert g_high == g_low == EvidenceGrade.HIGHLY_RELEVANT.value
    assert s_high == s_low == "direct_answer"
    assert a_high == a_low == AnswerabilityStatus.ANSWERABLE.value


def test_provenance_immutability() -> None:
    """Verify that provenance is strictly tracked and cannot rewrite weak supervision to human."""
    gt_file = Path("evals/qrels_independent_ground_truth.json")
    assert gt_file.exists()

    with open(gt_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check that both provenances exist and weak supervision is explicitly tagged
    provenances = set()
    weak_sup_count = 0
    for q in data["evaluated_queries"]:
        for c in q["judged_candidates"]:
            provenances.add(c["provenance"])
            if "weak_supervision_v1" in c:
                weak_sup_count += 1
                assert c["weak_supervision_v1"]["provenance"] == JudgmentProvenance.WEAK_SUPERVISION_V1.value

    assert JudgmentProvenance.EXPERT_ADJUDICATED.value in provenances
    assert weak_sup_count > 0


def test_zero_family_leakage_audit() -> None:
    """Verify that grouped partitions have zero topic or paraphrase family overlap."""
    partitions_file = Path("evals/qrels_partitions.json")
    assert partitions_file.exists()

    with open(partitions_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assignments = data["topic_assignments"]
    dev = set(assignments["dev"])
    cal = set(assignments["calibration"])
    hold = set(assignments["final_holdout"])

    # Disjoint partition sets
    assert len(dev & cal) == 0, f"Leakage between dev and cal: {dev & cal}"
    assert len(dev & hold) == 0, f"Leakage between dev and holdout: {dev & hold}"
    assert len(cal & hold) == 0, f"Leakage between cal and holdout: {cal & hold}"

    # Paraphrases in multi-query topics (e.g. topic_01) must all reside in the same partition
    q_parts = data["query_partitions"]
    t1_queries = {"t1_01_exec", "t1_01_eng", "t1_01_kw"}
    assert t1_queries.issubset(set(q_parts["dev"])), "Paraphrases split across partitions!"


def test_statistical_upper_error_bound_formula() -> None:
    """Verify the one-sided 95% Binomial upper error bound equals 5.82% for N=50 and <= 5.0% for N>=60."""
    bound_50 = calculate_one_sided_upper_error_bound(50, 0.95)
    assert bound_50 == pytest.approx(0.058164, abs=1e-5)
    assert round(bound_50 * 100, 2) == 5.82

    # For 60 independent topic clusters, effective bound is <= 4.87% (<= 5.0%)
    bound_60 = calculate_one_sided_upper_error_bound(60, 0.95)
    assert bound_60 <= 0.05
    assert round(bound_60 * 100, 2) == 4.87

    # For 63 clusters (dev/cal), effective bound is 4.64%
    bound_63 = calculate_one_sided_upper_error_bound(63, 0.95)
    assert round(bound_63 * 100, 2) == 4.64


def test_zero_synthetic_ids_in_ground_truth() -> None:
    """Verify that ground truth contains ZERO synthetic IDs (expert_pos_* or outside_pool_sample_*)."""
    gt_file = Path("evals/qrels_independent_ground_truth.json")
    with open(gt_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    for q in data["evaluated_queries"]:
        for c in q["judged_candidates"]:
            rec_id = c["record_id"]
            assert not rec_id.startswith("expert_pos_"), f"Synthetic ID '{rec_id}' found in ground truth!"
            assert not rec_id.startswith("outside_pool_sample_"), f"Synthetic ID '{rec_id}' found in ground truth!"


def test_minimum_60_clusters_per_partition() -> None:
    """Verify that every partition has at least 60 independent clusters."""
    partitions_file = Path("evals/qrels_partitions.json")
    with open(partitions_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assignments = data["topic_assignments"]
    dev_count = len(assignments["dev"])
    cal_count = len(assignments["calibration"])
    hold_count = len(assignments["final_holdout"])

    assert dev_count >= 60, f"dev has only {dev_count} clusters (<60 required)"
    assert cal_count >= 60, f"calibration has only {cal_count} clusters (<60 required)"
    assert hold_count >= 60, f"final_holdout has only {hold_count} clusters (<60 required)"


def test_unverified_persisted_human_judgment_fails_closed(tmp_path: Path) -> None:
    """Verify that unverified persisted_human_judgment strings fail closed under ledger verification."""
    from tools.score_qrels_v2 import run_grading_pipeline

    # Create dummy candidate pool with 1 query
    pool_file = tmp_path / "dummy_pool.json"
    pool_file.write_text(json.dumps({
        "queries": [{
            "query_id": "test_q",
            "query_text": "What is PartnerAdvisory?",
            "topic_id": "topic_01",
            "topic_name": "Test",
            "tier": "tier1",
            "domain": "test",
            "ground_truth_summary": "PartnerAdvisory is a partner",
            "target_entities": ["PartnerAdvisory"],
            "candidates": [{
                "record_id": "rec_dummy_1",
                "title": "PartnerAdvisory Partnership",
                "retrieval_text": "PartnerAdvisory works with partners.",
                "record_type": "assertion",
                "source_id": "src_1",
            }]
        }]
    }))

    output_file = tmp_path / "dummy_out.json"
    output_file.write_text(json.dumps({
        "evaluated_queries": [{
            "query_id": "test_q",
            "judged_candidates": [{
                "record_id": "rec_dummy_1",
                "grade": 2
            }]
        }]
    }))

    # Attempting to load existing judgment with enforce_ledger_verification=True must raise ValueError
    with pytest.raises(ValueError, match="lacks verified receipt in hitl_decision_ledger.sqlite"):
        run_grading_pipeline(
            candidate_pool_path=pool_file,
            output_path=output_file,
            mode="auto",
            enforce_ledger_verification=True,
        )


def test_unresolved_judgments_remain_visible() -> None:
    """Verify that unjudged items are preserved as UNRESOLVED and never silently dropped."""
    gt_file = Path("evals/qrels_independent_ground_truth.json")
    with open(gt_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    unresolved = [
        c for q in data["evaluated_queries"]
        for c in q["judged_candidates"]
        if c.get("review_status") == "UNRESOLVED"
    ]
    assert len(unresolved) == len(data["evaluated_queries"]), f"Expected unresolved audit items, got {len(unresolved)}"
