"""
tests/test_release_thresholds_ssot.py

Comprehensive test suite verifying that all evaluator modules (evaluator.py,
orchestrator.py, state_machine.py) strictly consume CANONICAL_RELEASE_POLICY
and yield 100% consistent verdicts across all score boundaries.
"""

from pathlib import Path

from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.release_thresholds import (
    CANONICAL_RELEASE_POLICY,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.evaluator import _eval_pass_flags

ROOT = Path(__file__).resolve().parent.parent


def test_evaluator_pass_flags_boundary_consistency():
    """Verify _eval_pass_flags respects canonical threshold boundaries."""
    # Perfect score -> all clean pass
    j1, j2, j3, all_passed, is_advisory = _eval_pass_flags(
        {"composite_score": 90, "verdict": "PASS"},
        {"composite_score": 95, "verdict": "PASS"},
        {"composite_score": 95, "verdict": "PASS"},
        {"passed": True},
    )
    assert j1 is True
    assert j2 is True
    assert j3 is True
    assert all_passed is True
    assert is_advisory is True

    # Output 1 at 85 (advisory) and Outputs 2 & 3 at 90+ (PASS) -> advisory pass
    j1, j2, j3, all_passed, is_advisory = _eval_pass_flags(
        {"composite_score": 85, "verdict": "PASS_ADVISORY"},
        {"composite_score": 90, "verdict": "PASS"},
        {"composite_score": 90, "verdict": "PASS"},
        {"passed": True},
    )
    assert j1 is False  # not clean pass (is advisory)
    assert j2 is True
    assert j3 is True
    assert all_passed is False
    assert is_advisory is True

    # Output 3 < 90 (e.g. 86) -> instant FAIL, no advisory allowed for Output 3
    j1, j2, j3, all_passed, is_advisory = _eval_pass_flags(
        {"composite_score": 90, "verdict": "PASS"},
        {"composite_score": 90, "verdict": "PASS"},
        {"composite_score": 86, "verdict": "FAIL"},
        {"passed": True},
    )
    assert j1 is True
    assert j2 is True
    assert j3 is False
    assert all_passed is False
    assert is_advisory is False

    # Output 2 < 90 (e.g. 86) -> instant FAIL, no advisory allowed for Output 2
    j1, j2, j3, all_passed, is_advisory = _eval_pass_flags(
        {"composite_score": 90, "verdict": "PASS"},
        {"composite_score": 86, "verdict": "FAIL"},
        {"composite_score": 90, "verdict": "PASS"},
        {"passed": True},
    )
    assert j1 is True
    assert j2 is False
    assert j3 is True
    assert all_passed is False
    assert is_advisory is False

    # Below advisory floor (<80) -> FAIL
    j1, j2, j3, all_passed, is_advisory = _eval_pass_flags(
        {"composite_score": 79, "verdict": "FAIL"},
        {"composite_score": 95, "verdict": "PASS"},
        {"composite_score": 95, "verdict": "PASS"},
        {"passed": True},
    )
    assert j1 is False
    assert all_passed is False
    assert is_advisory is False


def test_decomposed_multi_judge_orchestrator_policy_alignment():
    """Verify DecomposedMultiJudge.evaluate_parallel evaluates release via canonical policy."""
    orchestrator = DecomposedMultiJudge(mock=True)

    # In mock mode, check evaluate_parallel output structure
    res = orchestrator.evaluate_parallel(
        output_1_text="Mock transcript text",
        output_2_text="Mock recap text",
        output_3_text="{}",
        raw_transcript="Mock raw",
    )
    assert "status" in res
    assert "authoritative_write_authorized" in res
    assert "release_decision_metadata" in res

    meta = res["release_decision_metadata"]
    assert meta["policy_version"] == CANONICAL_RELEASE_POLICY.policy_version
    assert meta["policy_digest"] == CANONICAL_RELEASE_POLICY.compute_policy_digest()


def test_state_machine_policy_alignment():
    """Verify SelectiveRemediationStateMachine uses CANONICAL_RELEASE_POLICY thresholds."""
    sm = SelectiveRemediationStateMachine()

    judge_report_pass = {
        "output_1_evaluation": {"composite_score": 90, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    qa_report_pass = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }

    all_passed, failed_stages = sm.evaluate_attempt({}, judge_report_pass, qa_report_pass)
    assert all_passed is True
    assert len(failed_stages) == 0

    # Test Output 3 scoring 88 (below 90 threshold — should fail stage for retry)
    judge_report_retry = {
        "output_1_evaluation": {"composite_score": 90, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 88, "verdict": "PASS_ADVISORY"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    all_passed2, failed_stages2 = sm.evaluate_attempt({}, judge_report_retry, qa_report_pass)
    assert all_passed2 is False
    assert "output_3" in failed_stages2
    assert "output_1" not in failed_stages2
    assert "output_2" not in failed_stages2


def test_verify_decision_seal_document_mode(tmp_path: Path):
    """Verify verify_authoritative_release_gate handles document-mode packages where Output 1 is in metadata.json."""
    import json

    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
        verify_authoritative_release_gate,
    )

    doc_text = "Apex Power & Grid Infrastructure Automation Request for Proposal"
    out3_json = json.dumps({"schema_version": "5.7", "evidence_index": {}}, indent=2)

    judge_report = {
        "rubric_version": "2.0",
        "mode": "document",
        "status": "PASS",
        "output_1_evaluation": {"passed": True, "verdict": "SKIPPED"},
        "output_2_evaluation": {"passed": True, "verdict": "SKIPPED"},
        "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True},
        "overall_rubric_summary": "Passed",
    }
    eval_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = eval_digest

    manifest = build_artifact_manifest(
        f1_text=doc_text,
        f2_text="",
        f3_text=out3_json,
        model="gpt-6-luna",
        reasoning_effort="low",
        judge_eval_digest=eval_digest,
    )

    decision = {
        "mode": "document",
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "output_3_llm_score_pass": True,
        "final_pass": True,
        "authoritative_write_authorized": True,
        "status": "PASS",
        "artifact_manifest": manifest,
    }

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(parents=True)
    pkg_file = pkg_dir / "intake_package.json"
    pkg_file.write_text(
        json.dumps({"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"})
    )

    (pkg_dir / "03_test_canonical_signal_projection.json").write_text(out3_json)
    (pkg_dir / "metadata.json").write_text(json.dumps({"source_text": doc_text}))
    (pkg_dir / "final_release_decision.json").write_text(json.dumps(decision))
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps(judge_report))

    # Must pass without raising RuntimeError
    verify_authoritative_release_gate([pkg_file], allow_unjudged=False)
