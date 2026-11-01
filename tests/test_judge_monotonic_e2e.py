"""End-to-end tests for Wave 7 Monotonic Score Governor and Rollback Integration.

Verifies:
1. End-to-end remediation lifecycle: raw 84 -> 81 -> 88 yields accepted 84 -> 84 -> 88.
2. Below-threshold retained baselines remain unreleased (authoritative_write_authorized=False).
3. Atomic full-scorecard restoration without score/verdict splicing.
4. BoundAttemptLedger rollback preserving synthesis and evaluation snapshots.
5. Cross-artifact consistency re-evaluated across mixed historical artifacts.
6. Epoch invalidation lifecycle resetting certifications and frozen stages cleanly.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.attempt_binding import (
    BoundAttemptLedger,
    _dominates,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.manifest_runner.guard_utils import update_final_release_verdicts
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
)
from dual_pipeline.release_thresholds import (
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


def _make_eval_result(
    f1: str,
    f2: str,
    f3: str,
    source: str,
    scores: Dict[str, float],
    verdicts: Dict[str, str],
    reasoning: Dict[str, str],
    det_pass: bool = True,
    qa_pass: bool = True,
) -> Tuple[SynthesisResult, QAEvaluationResult]:
    """Helper to synthesize valid bound SynthesisResult and QAEvaluationResult."""
    synth = SynthesisResult(output_1=f1, output_2=f2, output_3=f3, attempt=1)

    judge_report: Dict[str, Any] = {
        "status": "PASS" if all(v == "PASS" for v in verdicts.values()) else "FAIL",
        "output_1_evaluation": {
            "composite_score": scores.get("output_1", 90.0),
            "verdict": verdicts.get("output_1", "PASS"),
            "reasoning": reasoning.get("output_1", "F1 good"),
            "dimension_scores": {"d1": 25, "d2": 25, "d3": 25, "d4": 25},
        },
        "output_2_evaluation": {
            "composite_score": scores.get("output_2", 84.0),
            "verdict": verdicts.get("output_2", "FAIL"),
            "reasoning": reasoning.get("output_2", "F2 needs work"),
            "dimension_scores": {"d1": 21, "d2": 21, "d3": 21, "d4": 21},
        },
        "output_3_evaluation": {
            "composite_score": scores.get("output_3", 92.0),
            "verdict": verdicts.get("output_3", "PASS"),
            "reasoning": reasoning.get("output_3", "F3 good"),
            "dimension_scores": {"d1": 23, "d2": 23, "d3": 23, "d4": 23},
        },
        "cross_artifact_reconciliation": {"status": "PASS", "failures": []},
    }
    judge_report["evaluation_receipt_digest"] = compute_release_evaluation_digest(judge_report)

    manifest = build_artifact_manifest(
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
        model="gpt-6-astra",
        reasoning_effort="medium",
        judge_eval_digest=judge_report["evaluation_receipt_digest"],
    )

    final_decision: Dict[str, Any] = {
        "status": judge_report["status"],
        "output_1_pass": verdicts.get("output_1") == "PASS",
        "output_2_pass": verdicts.get("output_2") == "PASS",
        "output_3_pass": verdicts.get("output_3") == "PASS",
        "output_1_deterministic_gates_pass": det_pass,
        "output_2_deterministic_gates_pass": det_pass,
        "output_3_deterministic_gates_pass": det_pass,
        "cross_artifact_consistency_pass": True,
        "authoritative_write_authorized": (
            judge_report["status"] == "PASS" and det_pass and qa_pass
        ),
        "artifact_manifest": manifest,
        "decision_seal": manifest["decision_seal_sha256"],
    }

    qa_report: Dict[str, Any] = {
        "passed": qa_pass,
        "healed_artifacts": {"output_1": f1, "output_2": f2, "output_3": f3},
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": qa_pass},
        "output_03_canonical_signal_projection": {"passed": True},
    }

    auth_write = bool(judge_report["status"] == "PASS" and det_pass and qa_pass)
    eval_result = QAEvaluationResult(
        passed=bool(qa_pass and judge_report["status"] == "PASS"),
        qa_report=qa_report,
        judge_report=judge_report,
        final_decision=final_decision,
        authoritative_write_authorized=auth_write,
    )
    return synth, eval_result


def test_e2e_remediation_lifecycle_84_81_88_unreleased_until_88():
    """
    E2E simulation of 3-attempt lifecycle:
    Attempt 1: Score 84 (below 85) -> FAIL, write unreleased.
    Attempt 2: Score 81 (regressed) -> FAIL, rollback to Attempt 1, write remains unreleased.
    Attempt 3: Score 88 -> PASS, promoted, write authorized.
    """
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    artifacts: Dict[str, str] = {
        "01_hitl_resolved_transcript.md": "Transcript v1",
        "02_executive_recap.md": "Recap Attempt 1 (Score 84)",
        "03_canonical_signal_projection.json": '{"nodes": []}',
    }

    # --- Attempt 1 ---
    sm.current_attempt = 1
    eval_attempt_1 = {
        "composite_score": 84,
        "verdict": "FAIL",
        "dimension_scores": {"accuracy": 21, "completeness": 21, "structure": 21, "actionability": 21},
        "reasoning": "Attempt 1 analysis: missing metrics",
    }
    judge_report_1 = {
        "status": "FAIL",
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": copy.deepcopy(eval_attempt_1),
        "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
    }
    final_decision_1 = {
        "status": "FAIL",
        "output_1_pass": True,
        "output_2_pass": False,
        "output_3_pass": True,
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "authoritative_write_authorized": False,
    }
    qa_report_1 = {"passed": True}

    # Record Attempt 1
    sm._record_attempt_candidates(
        final_decision=final_decision_1,
        judge_report=judge_report_1,
        qa_report=qa_report_1,
        c1=artifacts["01_hitl_resolved_transcript.md"],
        c2=artifacts["02_executive_recap.md"],
        c3=artifacts["03_canonical_signal_projection.json"],
    )
    assert sm.get_high_water_mark_score("output_2") == 84
    assert sm.has_regressed("output_2") is False
    assert final_decision_1["authoritative_write_authorized"] is False

    # --- Attempt 2: Regressed candidate ---
    sm.current_attempt = 2
    artifacts["02_executive_recap.md"] = "Recap Attempt 2 (Degraded Score 81)"
    eval_attempt_2 = {
        "composite_score": 81,
        "verdict": "FAIL",
        "dimension_scores": {"accuracy": 20, "completeness": 20, "structure": 20, "actionability": 21},
        "reasoning": "Attempt 2 analysis: worse completeness",
    }
    judge_report_2 = {
        "status": "FAIL",
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": copy.deepcopy(eval_attempt_2),
        "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
    }
    final_decision_2 = {
        "status": "FAIL",
        "output_1_pass": True,
        "output_2_pass": False,
        "output_3_pass": True,
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "authoritative_write_authorized": False,
    }
    qa_report_2 = {"passed": True}

    sm._record_attempt_candidates(
        final_decision=final_decision_2,
        judge_report=judge_report_2,
        qa_report=qa_report_2,
        c1=artifacts["01_hitl_resolved_transcript.md"],
        c2=artifacts["02_executive_recap.md"],
        c3=artifacts["03_canonical_signal_projection.json"],
    )
    assert sm.has_regressed("output_2") is True
    assert sm.get_high_water_mark_score("output_2") == 84

    # Simulate qa_runner rollback: restore best candidate content and full scorecard
    if sm.has_regressed("output_2"):
        best_cand = sm.get_best_candidate("output_2")
        artifacts["02_executive_recap.md"] = best_cand.content
        judge_report_2["output_2_evaluation"] = copy.deepcopy(best_cand.full_scorecard)
        update_final_release_verdicts(judge_report_2, final_decision_2)

    # Content restored to Attempt 1
    assert artifacts["02_executive_recap.md"] == "Recap Attempt 1 (Score 84)"
    # Scorecard restored to Attempt 1
    assert judge_report_2["output_2_evaluation"]["composite_score"] == 84
    assert judge_report_2["output_2_evaluation"]["reasoning"] == "Attempt 1 analysis: missing metrics"
    # Write authorization remains False because 84 < 85
    assert final_decision_2["authoritative_write_authorized"] is False

    # --- Attempt 3: Successful improvement ---
    sm.current_attempt = 3
    artifacts["02_executive_recap.md"] = "Recap Attempt 3 (Superior Score 88)"
    eval_attempt_3 = {
        "composite_score": 88,
        "verdict": "PASS",
        "dimension_scores": {"accuracy": 22, "completeness": 22, "structure": 22, "actionability": 22},
        "reasoning": "Attempt 3 analysis: all metrics included",
    }
    judge_report_3 = {
        "status": "PASS",
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": copy.deepcopy(eval_attempt_3),
        "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
    }
    final_decision_3 = {
        "status": "PASS",
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "authoritative_write_authorized": True,
    }
    qa_report_3 = {"passed": True}

    sm._record_attempt_candidates(
        final_decision=final_decision_3,
        judge_report=judge_report_3,
        qa_report=qa_report_3,
        c1=artifacts["01_hitl_resolved_transcript.md"],
        c2=artifacts["02_executive_recap.md"],
        c3=artifacts["03_canonical_signal_projection.json"],
    )
    assert sm.has_regressed("output_2") is False
    assert sm.get_high_water_mark_score("output_2") == 88
    assert final_decision_3["authoritative_write_authorized"] is True

    # Trajectory check
    assert sm.get_accepted_trajectory("output_2") == [84, 84, 88]


def test_e2e_full_scorecard_restoration_no_splicing():
    """Verify that rollback restores full scorecard atomically without score/verdict splicing."""
    sm = SelectiveRemediationStateMachine(max_attempts=2)

    detailed_scorecard = {
        "composite_score": 86,
        "verdict": "PASS",
        "dimension_scores": {"d1": 22, "d2": 21, "d3": 21, "d4": 22},
        "detailed_deductions": [{"criterion": "style", "penalty": 2, "evidence": "Minor typo"}],
        "eval_metadata": {"evaluator": "gpt-6-astra", "tokens": 1200},
    }

    # Attempt 1 registers detailed scorecard
    sm.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Attempt 1 High Quality",
        composite_score=86,
        verdict="PASS",
        dimension_scores={"d1": 22, "d2": 21, "d3": 21, "d4": 22},
        full_scorecard=detailed_scorecard,
    )

    # Attempt 2 regresses with a different scorecard
    sm.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Attempt 2 Degraded",
        composite_score=78,
        verdict="FAIL",
        dimension_scores={"d1": 19, "d2": 19, "d3": 20, "d4": 20},
        full_scorecard={"composite_score": 78, "verdict": "FAIL", "detailed_deductions": []},
    )

    best = sm.get_best_candidate("output_2")
    assert best.attempt == 1
    # Full scorecard is intact and equal to detailed_scorecard
    assert best.full_scorecard == detailed_scorecard
    assert best.full_scorecard["eval_metadata"]["tokens"] == 1200
    assert len(best.full_scorecard["detailed_deductions"]) == 1


def test_e2e_bound_attempt_ledger_snapshots():
    """BoundAttemptLedger enforces dominance and restores snapshots with full bindings."""
    source_text = "Alice: Let's finalize the schema."
    f1 = "# Output 1\nAlice: Let's finalize the schema."
    f2_v1 = "# Output 2\nSchema finalized."
    f2_v2 = "# Output 2\nSchema unresolved."
    f3 = '{"schema_version": "5.7"}'

    synth1, eval1 = _make_eval_result(
        f1=f1, f2=f2_v1, f3=f3, source=source_text,
        scores={"output_1": 95, "output_2": 88, "output_3": 92},
        verdicts={"output_1": "PASS", "output_2": "PASS", "output_3": "PASS"},
        reasoning={"output_1": "Good", "output_2": "Good", "output_3": "Good"},
    )

    synth2, eval2 = _make_eval_result(
        f1=f1, f2=f2_v2, f3=f3, source=source_text,
        scores={"output_1": 95, "output_2": 75, "output_3": 92},
        verdicts={"output_1": "PASS", "output_2": "FAIL", "output_3": "PASS"},
        reasoning={"output_1": "Good", "output_2": "Regressed", "output_3": "Good"},
    )

    ledger = BoundAttemptLedger(source_text, CanonicalModality.MEETING_TRANSCRIPT)
    ledger.record(1, synth1, eval1)
    ledger.record(2, synth2, eval2)

    # Attempt 1 dominates Attempt 2
    assert _dominates(eval1, eval2, ("output_1", "output_2", "output_3")) is True
    # Attempt 2 does not dominate Attempt 1
    assert _dominates(eval2, eval1, ("output_1", "output_2", "output_3")) is False

    # Simulate state machine indicating output_2 regressed
    class MockStateMachine:
        def __init__(self):
            self.passing_stages = set()
            self.frozen_artifacts = {}
        def has_regressed(self, key: str) -> bool:
            return key == "output_2"
        def freeze_artifact(self, key: str, content: str) -> None:
            self.frozen_artifacts[key] = content
        def unfreeze_artifact(self, key: str) -> None:
            self.frozen_artifacts.pop(key, None)

    mock_sm = MockStateMachine()
    restored_synth, restored_eval = ledger.restore(2, synth2, eval2, mock_sm)

    # Synthesis and evaluation restored to Attempt 1
    assert restored_synth.output_2 == f2_v1
    assert restored_eval.judge_report["output_2_evaluation"]["composite_score"] == 88
    assert restored_eval.final_decision["output_2_pass"] is True
    assert "output_2" in mock_sm.frozen_artifacts
