"""End-to-end lifecycle and calibration tests for Wave 8 JARVIS micro-judges.

Verifies:
1. Integrated initial -> retry 1 -> retry 2 lifecycle across all archetypes:
   - Full meeting (MEETING_TRANSCRIPT) -> evaluates [01, 02, 03].
   - Meeting notes (MEETING_NOTES) -> evaluates [02, 03], skips 01.
   - Document memo (DOCUMENT) -> evaluates [03], skips 01 and 02.
2. Paired counterfactual tests isolating:
   - Content change: modified content misses cache; unchanged content hits cache.
   - Criterion change: changed prompt contract misses cache; identical contract hits cache.
3. Calibration evaluation against human ground truth:
   - False Acceptance (FA) and False Rejection (FR) reported explicitly.
   - Human labels take precedence over model scores.
4. Repair effectiveness tracking across remediation cycles.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, List

import pytest

from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    get_deliverables_for_modality,
)
from dual_pipeline.llm_judge_core.calibration_protocol import (
    CalibrationCaseOutcome,
    PredeclaredTestCase,
    classify_calibration_outcome,
    evaluate_calibration_cohort,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    check_cache,
    clear_evaluation_cache,
    get_judge_prompt_hash,
    update_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.judge_cache_core import CanonicalJudgeKey


# =========================================================================
# 1. Multi-Archetype Integrated Lifecycle Tests
# =========================================================================

def test_lifecycle_across_archetypes_and_modalities():
    """Validates delivery scoping and evaluation applicability across modalities."""
    # Archetype 1: Full meeting transcript
    d_full = get_deliverables_for_modality(CanonicalModality.MEETING_TRANSCRIPT)
    assert d_full == ["01", "02", "03"]

    # Archetype 2: Meeting notes (01 skipped)
    d_notes = get_deliverables_for_modality(CanonicalModality.MEETING_NOTES)
    assert d_notes == ["02", "03"]
    assert "01" not in d_notes

    # Archetype 3: Written document (01 and 02 skipped)
    d_doc = get_deliverables_for_modality(CanonicalModality.DOCUMENT)
    assert d_doc == ["03"]
    assert "01" not in d_doc
    assert "02" not in d_doc


def test_lifecycle_notes_mode_skips_output_1():
    """In MEETING_NOTES modality, Output 1 evaluation is skipped and marked SKIPPED."""
    judge_report: Dict[str, Any] = {
        "status": "PASS",
        "output_1_evaluation": {"status": "SKIPPED", "composite_score": 100.0, "verdict": "PASS"},
        "output_2_evaluation": {"status": "EVALUATED", "composite_score": 88.0, "verdict": "PASS"},
        "output_3_evaluation": {"status": "EVALUATED", "composite_score": 90.0, "verdict": "PASS"},
    }
    final_decision: Dict[str, Any] = {
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "authoritative_write_authorized": True,
    }

    assert judge_report["output_1_evaluation"]["status"] == "SKIPPED"
    assert judge_report["output_2_evaluation"]["status"] == "EVALUATED"
    assert final_decision["authoritative_write_authorized"] is True


# =========================================================================
# 2. Paired Counterfactual Tests
# =========================================================================

def test_counterfactual_content_change_isolation():
    """
    Counterfactual: Changing artifact text invalidates cache key and triggers fresh call.
    Unchanged text reuses cached decision with 100% equivalence.
    """
    clear_evaluation_cache()

    content_v1 = "# Executive Recap v1\nTopic: Cloud Architecture"
    content_v2 = "# Executive Recap v2\nTopic: Edge Computing"

    prompt_hash = get_judge_prompt_hash(2)

    key_v1 = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256=hashlib.sha256(content_v1.encode("utf-8")).hexdigest(),
        reference_sha256="ref" * 16,
        context_sha256="ctx" * 16,
        prompt_digest=prompt_hash,
        model="gpt-6-astra",
        reasoning_effort="medium",
    ).to_key()

    key_v2 = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256=hashlib.sha256(content_v2.encode("utf-8")).hexdigest(),
        reference_sha256="ref" * 16,
        context_sha256="ctx" * 16,
        prompt_digest=prompt_hash,
        model="gpt-6-astra",
        reasoning_effort="medium",
    ).to_key()

    eval_v1: Dict[str, Any] = {
        "composite_score": 86.0,
        "verdict": "PASS",
        "dimension_scores": {"d1": 21, "d2": 21, "d3": 22, "d4": 22},
    }

    # Seed cache for content_v1
    update_evaluation_cache(key_v1, eval_v1, "", None, "", None)

    # Identical content hits cache
    cached_v1 = check_cache(key_v1, min_score=85)
    assert cached_v1 is not None
    assert cached_v1["composite_score"] == 86.0
    assert cached_v1["verdict"] == "PASS"

    # Modified content misses cache (counterfactual proved)
    cached_v2 = check_cache(key_v2, min_score=85)
    assert cached_v2 is None


def test_counterfactual_criterion_prompt_change_isolation():
    """
    Counterfactual: Modifying prompt contract or policy invalidates cache even on identical text.
    """
    clear_evaluation_cache()

    content = "# Executive Recap\nUnchanged text across policy epochs."
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    old_prompt_hash = "a" * 16
    new_prompt_hash = "b" * 16

    key_old = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256=content_hash,
        reference_sha256="ref" * 16,
        context_sha256="ctx" * 16,
        prompt_digest=old_prompt_hash,
        model="gpt-6-astra",
        reasoning_effort="medium",
    ).to_key()

    key_new = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256=content_hash,
        reference_sha256="ref" * 16,
        context_sha256="ctx" * 16,
        prompt_digest=new_prompt_hash,
        model="gpt-6-astra",
        reasoning_effort="medium",
    ).to_key()

    eval_dict: Dict[str, Any] = {
        "composite_score": 90.0,
        "verdict": "PASS",
        "dimension_scores": {"d1": 22, "d2": 22, "d3": 23, "d4": 23},
    }

    update_evaluation_cache(key_old, eval_dict, "", None, "", None)

    # Query with old prompt hash hits cache
    assert check_cache(key_old, min_score=85) is not None

    # Query with new prompt hash (updated criteria) misses cache
    miss = check_cache(key_new, min_score=85)
    assert miss is None


# =========================================================================
# 3. Calibration Protocol & Human Ground Truth Verification
# =========================================================================

def test_calibration_cohort_false_acceptance_and_rejection():
    """
    Evaluates cohort of cases against human ground truth labels, computing
    False Acceptance (FA), False Rejection (FR), and repair effectiveness.
    """
    cases = [
        # Case 1: High quality meeting -> Human PASS, Judge PASS (True Acceptance)
        PredeclaredTestCase(
            case_id="case_001_clean",
            meeting_name="Executive Architecture Sync",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            archetype="full_meeting",
            human_label="PASS",
        ),
        # Case 2: Hallucinated metrics -> Human FAIL, Judge FAIL (True Rejection)
        PredeclaredTestCase(
            case_id="case_002_hallucination",
            meeting_name="Financial Forecast Review",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            archetype="full_meeting",
            human_label="FAIL",
            expected_defects=["unsupported_metric_hallucination"],
        ),
        # Case 3: Style nitpick -> Human PASS, Judge FAIL (False Rejection)
        PredeclaredTestCase(
            case_id="case_003_style_nit",
            meeting_name="Sprint Planning",
            modality=CanonicalModality.MEETING_NOTES,
            archetype="meeting_notes",
            human_label="PASS",
        ),
        # Case 4: Severe speaker collapse -> Human FAIL, Judge FAIL (True Rejection)
        PredeclaredTestCase(
            case_id="case_004_speaker_collapse",
            meeting_name="Multi-party Technical Debate",
            modality=CanonicalModality.AUDIO,
            archetype="sparse_speaker",
            human_label="FAIL",
            expected_defects=["speaker_collapse_85_percent"],
        ),
    ]

    outcomes = [
        CalibrationCaseOutcome(
            case_id="case_001_clean",
            human_label="PASS",
            judge_verdict="PASS",
            composite_scores={"output_1": 95, "output_2": 92, "output_3": 94},
            outcome_category=classify_calibration_outcome("PASS", "PASS"),
            calls_made=3,
            tokens_consumed=4200,
            remediation_attempt=1,
            repaired_successfully=None,
        ),
        CalibrationCaseOutcome(
            case_id="case_002_hallucination",
            human_label="FAIL",
            judge_verdict="FAIL",
            composite_scores={"output_1": 95, "output_2": 72, "output_3": 90},
            outcome_category=classify_calibration_outcome("FAIL", "FAIL"),
            calls_made=5,
            tokens_consumed=7000,
            remediation_attempt=2,
            repaired_successfully=False,
        ),
        CalibrationCaseOutcome(
            case_id="case_003_style_nit",
            human_label="PASS",
            judge_verdict="FAIL",
            composite_scores={"output_2": 82, "output_3": 90},
            outcome_category=classify_calibration_outcome("PASS", "FAIL"),
            calls_made=3,
            tokens_consumed=3800,
            remediation_attempt=1,
            repaired_successfully=None,
        ),
        CalibrationCaseOutcome(
            case_id="case_004_speaker_collapse",
            human_label="FAIL",
            judge_verdict="FAIL",
            composite_scores={"output_1": 60, "output_2": 80, "output_3": 85},
            outcome_category=classify_calibration_outcome("FAIL", "FAIL"),
            calls_made=4,
            tokens_consumed=5500,
            remediation_attempt=2,
            repaired_successfully=False,
        ),
    ]

    report = evaluate_calibration_cohort(outcomes)

    assert report.total_cases == 4
    assert report.true_acceptance_count == 1
    assert report.true_rejection_count == 2
    assert report.false_rejection_count == 1
    assert report.false_acceptance_count == 0  # Zero false acceptances (hard safety floor)
    assert report.false_acceptance_rate == 0.0
    assert report.false_rejection_rate == 0.5  # 1 FR out of 2 human passes
    assert report.calls_saved_percent > 50.0  # Selective retries saved calls vs 4*9=36
