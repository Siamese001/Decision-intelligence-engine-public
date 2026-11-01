"""Adversarial and edge-case tests for Wave 4 exit decision governance.

Covers mandatory edge-case categories:
1. Boundary payload: empty candidates, 0-byte documents, null collections.
2. Malformed input: NaN/inf scores, schema type mismatches, truncated JSON.
3. Invariant bypass attempts: high composite score overriding floor failures,
   spoofed authorized exceptions, tail-defect prefix-pass inheritance, and
   forged decision seals.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
    verify_authoritative_release_gate,
)
from dual_pipeline.release_thresholds import (
    CANONICAL_RELEASE_POLICY,
    ReleaseVerdict,
)
from dual_pipeline.transcript_qa_core.graph_reference_validator import (
    check_conflicting_duplicates,
    check_graph_reference_integrity,
    check_semantic_evidence_entailment,
)


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_boundary_empty_and_null_collections():
    # Empty data dict or collections with None / empty values
    ok, errs = check_conflicting_duplicates({})
    assert ok
    assert len(errs) == 0

    ok, errs = check_graph_reference_integrity({})
    assert ok
    assert len(errs) == 0

    ok, errs = check_semantic_evidence_entailment({})
    assert ok
    assert len(errs) == 0

    # Non-dict collections or None elements
    dirty_data = {
        "entities": [None, "invalid_str", {}],
        "decisions": [None, 12345, {}],
    }
    ok, _ = check_conflicting_duplicates(dirty_data)
    assert ok  # safely handles malformed items without crashing

    ok, _ = check_graph_reference_integrity(dirty_data)
    assert ok

    ok, _ = check_semantic_evidence_entailment(dirty_data)
    assert ok


def test_boundary_zero_byte_release_files(tmp_path: Path):
    pkg_dir = tmp_path / "pkg_empty"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    f3_file = pkg_dir / "03_20261008_canonical_signal_projection.json"
    f3_file.write_text("", encoding="utf-8")

    # Missing final_release_decision.json
    with pytest.raises(RuntimeError, match="Missing mandatory release authority receipt"):
        verify_authoritative_release_gate([f3_file])

    # Zero-byte decision file
    decision_file = pkg_dir / "final_release_decision.json"
    decision_file.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Corrupt or empty release decision file"):
        verify_authoritative_release_gate([f3_file])


# =========================================================================
# Category 2: Malformed Input
# =========================================================================

def test_malformed_scorecard_nan_inf_and_type_mismatches():
    # NaN in section scores
    sc_nan = {
        "section_scores": {
            "header_and_overview": float("nan"),
            "key_updates_technical_substance": 30,
            "conditional_extensions": 15,
            "appendix_stakeholder_perspectives": 30,
        }
    }
    valid, errs = validate_micro_judge_scorecard(sc_nan, output_num=2)
    assert not valid
    assert any("must be finite" in e for e in errs)

    # Inf in Output 3 dimension scores
    sc_inf = {
        "dimension_1_strategic_edge": float("inf"),
        "dimension_2_knowledge_layer_density": 14,
        "dimension_3_semantic_atomicity": 18,
        "dimension_4_predicate_specificity": 14,
        "dimension_5_exclusion_of_action_items": 14,
        "dimension_6_evidence_grounding_and_schema": 18,
    }
    valid, errs = validate_micro_judge_scorecard(sc_inf, output_num=3)
    assert not valid
    assert any("must be finite" in e for e in errs)

    # String type in numerical score
    sc_str = {
        "dimension_1_strategic_edge": "fifteen",
        "dimension_2_knowledge_layer_density": 14,
        "dimension_3_semantic_atomicity": 18,
        "dimension_4_predicate_specificity": 14,
        "dimension_5_exclusion_of_action_items": 14,
        "dimension_6_evidence_grounding_and_schema": 18,
    }
    valid, errs = validate_micro_judge_scorecard(sc_str, output_num=3)
    assert not valid
    assert any("must be a number" in e for e in errs)


def test_malformed_truncated_json_in_judge_report(tmp_path: Path):
    pkg_dir = tmp_path / "pkg_truncated"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    f3_file = pkg_dir / "03_test.json"
    f3_file.write_text("{}", encoding="utf-8")

    decision_file = pkg_dir / "final_release_decision.json"
    decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
    }
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    judge_file = pkg_dir / "llm_judge_evaluation.json"
    judge_file.write_text('{"status": "PASS", "output_3_evaluation": {"comp', encoding="utf-8")

    with pytest.raises(RuntimeError, match="Corrupt or empty LLM Judge evaluation report"):
        verify_authoritative_release_gate([f3_file])


# =========================================================================
# Category 3: Invariant Bypass Attempts
# =========================================================================

def test_invariant_bypass_high_composite_score_with_floor_failure():
    # Composite score sum is 92 (which exceeds pass threshold 90),
    # but dimension_1 is 10 (below mandatory floor 11)
    scorecard = {
        "dimension_1_strategic_edge": 10,  # Below floor 11!
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 18,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 19,
    }
    valid, errs = validate_micro_judge_scorecard(scorecard, output_num=3)
    assert not valid
    assert any("below mandatory floor 11" in e for e in errs)

    # In composite release, high composite score with FAIL verdict is rejected
    verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        s1=95, s2=95, s3=92,
        v1="PASS", v2="PASS", v3="FAIL",
        deterministic_qa_passed=True,
        reconciliation_passed=True,
    )
    assert verdict == ReleaseVerdict.FAIL
    assert write_auth is False


def test_invariant_bypass_spoofed_authorized_exception_without_flag():
    # Verdict claims PASS_AUTHORIZED_EXCEPTION but authorized_exception flag is absent
    # and composite score is below 90
    verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        s1=95, s2=95, s3=85,
        v1="PASS", v2="PASS", v3="FAIL",
        deterministic_qa_passed=True,
        reconciliation_passed=True,
        out3_authorized_exception=False,
    )
    assert verdict == ReleaseVerdict.FAIL
    assert write_auth is False


def test_invariant_bypass_tail_defect_cannot_inherit_prefix_pass(tmp_path: Path):
    # Package has high scores and claims PASS, but report explicitly notes incomplete_evaluated_span
    pkg_dir = tmp_path / "pkg_tail_defect"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    f3_file = pkg_dir / "03_test.json"
    f3_file.write_text("{}", encoding="utf-8")

    judge_report = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_3_evaluation": {"composite_score": 98.0, "verdict": "PASS"},
        "incomplete_evaluated_span": True,  # Prefix pass, tail omitted!
    }
    judge_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = judge_digest

    judge_file = pkg_dir / "llm_judge_evaluation.json"
    judge_file.write_text(json.dumps(judge_report), encoding="utf-8")

    decision_file = pkg_dir / "final_release_decision.json"
    decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
    }
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Tail defects cannot inherit prefix-only passes"):
        verify_authoritative_release_gate([f3_file])


def test_invariant_bypass_forged_decision_seal_rejected(tmp_path: Path):
    pkg_dir = tmp_path / "pkg_forged"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    f1 = pkg_dir / "01_t.md"
    f2 = pkg_dir / "02_r.md"
    f3 = pkg_dir / "03_s.json"
    f1.write_text("orig f1", encoding="utf-8")
    f2.write_text("orig f2", encoding="utf-8")
    f3.write_text("orig f3", encoding="utf-8")

    judge_report = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_3_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
        "evaluated_span_complete": True,
    }
    j_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = j_digest
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps(judge_report), encoding="utf-8")

    manifest = build_artifact_manifest(
        f1_text="orig f1", f2_text="orig f2", f3_text="orig f3",
        model="gpt-5.6-sol", reasoning_effort="high",
        judge_eval_digest=j_digest,
    )
    # Tamper with seal
    manifest["decision_seal_sha256"] = "deadbeef" * 8

    decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
        "artifact_manifest": manifest,
    }
    (pkg_dir / "final_release_decision.json").write_text(json.dumps(decision), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cryptographic decision seal verification failed"):
        verify_authoritative_release_gate([f3])
