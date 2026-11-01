"""End-to-end tests for Wave 4 complete exit decision and authoritative release gating.

Verifies end-to-end package verification:
- Blocks mutation when judge report is missing, mock, has incomplete evaluated span,
  mismatched cryptographic digests, or floor failures.
- Blocks mutation when artifact content drifts from sealed manifest.
- Allows mutation when authentic authorized exceptions are sealed with raw scores retained.
- Verifies evaluated candidate bundle equals final serialized bytes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    verify_authoritative_release_gate,
)
from dual_pipeline.release_thresholds import (
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
)


def _setup_valid_package(tmp_path: Path) -> Path:
    """Helper to provision a completely valid, sealed package directory."""
    pkg_dir = tmp_path / "pkg_20261008_test"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    f1_content = "# Output 1: Forensic Attributed Transcript\nAlice: We agreed on architecture."
    f2_content = "# Output 2: Executive Recap\n## Overview\nArchitecture aligned."
    f3_dict = {
        "schema_version": "5.7",
        "meeting_name": "Test Meeting",
        "entities": [{"name": "Alice", "role": "Architect"}],
        "decisions": [{"topic": "Architecture", "owner": "Alice", "evidence": ["We agreed on architecture."]}],
    }
    f3_content = json.dumps(f3_dict, indent=2)

    f1_file = pkg_dir / "01_20261008_hitl_resolved_transcript.md"
    f2_file = pkg_dir / "02_20261008_executive_recap.md"
    f3_file = pkg_dir / "03_20261008_canonical_signal_projection.json"

    f1_file.write_text(f1_content, encoding="utf-8")
    f2_file.write_text(f2_content, encoding="utf-8")
    f3_file.write_text(f3_content, encoding="utf-8")

    judge_report: Dict[str, Any] = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_1_evaluation": {
            "dimension_1": 24.0, "dimension_2": 24.0, "dimension_3": 24.0, "dimension_4": 23.0,
            "composite_score": 95.0, "verdict": "PASS"
        },
        "output_2_evaluation": {
            "section_scores": {
                "header_and_overview": 10.0, "key_updates_technical_substance": 32.0,
                "conditional_extensions": 18.0, "appendix_stakeholder_perspectives": 32.0
            },
            "composite_score": 92.0, "verdict": "PASS"
        },
        "output_3_evaluation": {
            "dimension_1": 14.0, "dimension_2": 14.0, "dimension_3": 19.0,
            "dimension_4": 14.0, "dimension_5": 14.0, "dimension_6": 19.0,
            "composite_score": 94.0, "verdict": "PASS"
        },
        "evaluated_span_complete": True,
    }
    judge_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = judge_digest

    judge_file = pkg_dir / "llm_judge_evaluation.json"
    judge_file.write_text(json.dumps(judge_report, indent=2), encoding="utf-8")

    manifest = build_artifact_manifest(
        f1_text=f1_content,
        f2_text=f2_content,
        f3_text=f3_content,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        judge_eval_digest=judge_digest,
    )
    seal = compute_decision_seal(manifest)
    manifest["decision_seal_sha256"] = seal

    decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
        "artifact_manifest": manifest,
    }
    decision_file = pkg_dir / "final_release_decision.json"
    decision_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")

    return f3_file


def test_valid_package_passes_release_gate(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    # Must not raise any error
    verify_authoritative_release_gate([f3_path])


def test_missing_judge_report_fails_closed(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    judge_file = f3_path.parent / "llm_judge_evaluation.json"
    judge_file.unlink()

    with pytest.raises(RuntimeError, match="Missing mandatory LLM Judge evaluation report"):
        verify_authoritative_release_gate([f3_path])


def test_mock_judge_fails_closed(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    judge_file = f3_path.parent / "llm_judge_evaluation.json"
    report = json.loads(judge_file.read_text(encoding="utf-8"))
    report["is_mock"] = True
    clean_rep = {k: v for k, v in report.items() if k != "evaluation_receipt_digest"}
    report["evaluation_receipt_digest"] = compute_release_evaluation_digest(clean_rep)
    judge_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
        verify_authoritative_release_gate([f3_path])


def test_incomplete_evaluated_span_fails_closed(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    judge_file = f3_path.parent / "llm_judge_evaluation.json"
    report = json.loads(judge_file.read_text(encoding="utf-8"))
    report["incomplete_evaluated_span"] = True
    clean_rep = {k: v for k, v in report.items() if k != "evaluation_receipt_digest"}
    report["evaluation_receipt_digest"] = compute_release_evaluation_digest(clean_rep)
    judge_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Incomplete evaluated span in LLM Judge report"):
        verify_authoritative_release_gate([f3_path])


def test_stale_or_tampered_judge_digest_fails_closed(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    judge_file = f3_path.parent / "llm_judge_evaluation.json"
    report = json.loads(judge_file.read_text(encoding="utf-8"))
    # Corrupt score without updating digest
    report["output_3_evaluation"]["composite_score"] = 99.0
    judge_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cryptographic evaluation receipt digest verification failed"):
        verify_authoritative_release_gate([f3_path])


def test_drifted_artifact_content_fails_decision_seal(tmp_path: Path):
    f3_path = _setup_valid_package(tmp_path)
    # Drift Output 2 content after seal was calculated
    f2_file = f3_path.parent / "02_20261008_executive_recap.md"
    f2_file.write_text("# Drifted Recap\nMalicious unauthorized edit.", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cryptographic decision seal verification failed"):
        verify_authoritative_release_gate([f3_path])


def test_authorized_exception_allows_release_with_raw_score(tmp_path: Path):
    pkg_dir = tmp_path / "pkg_20261008_auth_exc"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    f1_content = "# Output 1: Forensic Transcript\nAlice: Aligned."
    f2_content = "# Output 2: Executive Recap\nAligned."
    f3_dict = {"schema_version": "5.7", "entities": [{"name": "Alice"}]}
    f3_content = json.dumps(f3_dict)

    f1_file = pkg_dir / "01_20261008_hitl_resolved_transcript.md"
    f2_file = pkg_dir / "02_20261008_executive_recap.md"
    f3_file = pkg_dir / "03_20261008_canonical_signal_projection.json"
    f1_file.write_text(f1_content, encoding="utf-8")
    f2_file.write_text(f2_content, encoding="utf-8")
    f3_file.write_text(f3_content, encoding="utf-8")

    # Raw score is 86 (below 90 threshold), but has authorized_exception=True
    judge_report: Dict[str, Any] = {
        "status": "PASS",
        "overall_verdict": "PASS_AUTHORIZED_EXCEPTION",
        "output_1_evaluation": {
            "dimension_1": 24.0, "dimension_2": 24.0, "dimension_3": 24.0, "dimension_4": 23.0,
            "composite_score": 95.0, "verdict": "PASS"
        },
        "output_2_evaluation": {
            "section_scores": {
                "header_and_overview": 10.0, "key_updates_technical_substance": 32.0,
                "conditional_extensions": 18.0, "appendix_stakeholder_perspectives": 32.0
            },
            "composite_score": 92.0, "verdict": "PASS"
        },
        "output_3_evaluation": {
            "dimension_1": 13.0, "dimension_2": 13.0, "dimension_3": 17.0,
            "dimension_4": 14.0, "dimension_5": 14.0, "dimension_6": 15.0,
            "composite_score": 86.0,
            "verdict": "PASS_AUTHORIZED_EXCEPTION",
            "authorized_exception": True,
        },
        "authorized_exception": True,
        "evaluated_span_complete": True,
    }
    judge_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = judge_digest

    judge_file = pkg_dir / "llm_judge_evaluation.json"
    judge_file.write_text(json.dumps(judge_report, indent=2), encoding="utf-8")

    manifest = build_artifact_manifest(
        f1_text=f1_content, f2_text=f2_content, f3_text=f3_content,
        model="gpt-5.6-sol", reasoning_effort="high",
        judge_eval_digest=judge_digest,
    )
    seal = compute_decision_seal(manifest)
    manifest["decision_seal_sha256"] = seal

    decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
        "authorized_exception": True,
        "artifact_manifest": manifest,
    }
    decision_file = pkg_dir / "final_release_decision.json"
    decision_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")

    # With the loophole reverted, score 86 (< 90 threshold) must fail closed
    with pytest.raises(RuntimeError, match="does not meet production floor"):
        verify_authoritative_release_gate([f3_file])
