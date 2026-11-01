"""Short-circuit evaluation builder when deterministic QA gates fail."""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

logger = logging.getLogger(__name__)


def _collect_failed_gates(qa_report: Dict[str, Any]) -> List[str]:
    """Collects all failing gate IDs from gates list and section objects."""
    failed: List[str] = []
    if "gates" in qa_report and isinstance(qa_report["gates"], list):
        failed.extend([g.get("gate_id", str(g)) for g in qa_report["gates"] if not g.get("passed", False)])
    keys = (
        "output_01_forensic_transcript",
        "output_02_executive_recap",
        "output_03_canonical_signal_projection",
        "cross_artifact_consistency",
    )
    for sec_key in keys:
        sec = qa_report.get(sec_key, {})
        for gid, ginfo in sec.get("gates", {}).items():
            if isinstance(ginfo, dict) and not ginfo.get("passed", False):
                failed.append(gid)
        for fail_msg in sec.get("failures", []):
            if fail_msg not in failed:
                failed.append(fail_msg)
    return failed


def _build_skip_eval(qa_report: Dict[str, Any], sec_key: str, candidate_text: Optional[str] = None) -> Dict[str, Any]:
    """Builds a skip evaluation card for a single section."""
    sec = qa_report.get(sec_key, {})
    if sec.get("frozen"):
        sha = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest() if candidate_text else sec.get("sha256")
        default_score = sec.get("composite_score") or sec.get("score") or (
            CANONICAL_RELEASE_POLICY.output1_pass_threshold if "output_01" in sec_key else CANONICAL_RELEASE_POLICY.output2_pass_threshold
        )
        card: Dict[str, Any] = {
            "status": "SKIPPED",
            "composite_score": int(default_score),
            "verdict": "PASS",
            "reason": "FROZEN_PRIOR_ARTIFACT",
        }
        if sha:
            card["candidate_sha256"] = sha
            card["frozen_sha256"] = sec.get("sha256") or sha
        return card
    return {
        "status": "SKIPPED",
        "composite_score": None,
        "verdict": "FAIL",
        "reason": "DETERMINISTIC_QA_FAILED",
        "forensic_rationale": "Skipped due to deterministic QA gate failure.",
    }


def _is_section_passed(qa_report: Dict[str, Any], sec_key: str) -> bool:
    """Checks whether a QA report section passed or was safely skipped."""
    sec = qa_report.get(sec_key, {})
    return bool(sec.get("passed", False) or sec.get("status") in ("PASS", "SKIPPED"))


def build_short_circuit_eval(
    qa_report: Dict[str, Any],
    f1_text: str,
    f2_text: str,
    f3_text: str,
    model: str,
    reasoning_effort: Optional[str] = None,
    *,
    judge_tier: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Build a skipped judge report and failing final decision when deterministic QA fails."""
    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )
    failed_gates = _collect_failed_gates(qa_report)

    logger.warning(
        "[LLM_JUDGE_SHORT_CIRCUITED_BY_QA] Deterministic QA gates failed (%d failing gate(s)). "
        "Bypassing LLM judge calls to protect token budget and avoid ungrounded evaluations.",
        len(failed_gates),
    )
    summary = (
        f"Judge evaluation SKIPPED: Deterministic QA gates failed ({len(failed_gates)} failing gate(s)). "
        "Short-circuiting evaluation to prevent un-converging retry loops."
    )
    judge_report: Dict[str, Any] = {
        "rubric_version": "2.0",
        "scorecard_schema_version": "2.0.0",
        "release_policy_version": "2.0.0",
        "evaluator_model": model,
        "reasoning_effort": reasoning_effort or "medium",
        "evaluation_mode": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "status": "FAIL",
        "short_circuit": True,
        "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
        "skipped_reason": "DETERMINISTIC_QA_FAILED",
        "failed_gates": failed_gates,
        "output_1_evaluation": _build_skip_eval(qa_report, "output_01_forensic_transcript", f1_text),
        "output_2_evaluation": _build_skip_eval(qa_report, "output_02_executive_recap", f2_text),
        "output_3_evaluation": _build_skip_eval(qa_report, "output_03_canonical_signal_projection", f3_text),
        "output_1_scorecard": _build_skip_eval(qa_report, "output_01_forensic_transcript", f1_text),
        "output_2_scorecard": _build_skip_eval(qa_report, "output_02_executive_recap", f2_text),
        "output_3_scorecard": _build_skip_eval(qa_report, "output_03_canonical_signal_projection", f3_text),
        "cross_artifact_reconciliation": {"status": "SKIPPED", "passed": False, "failures": []},
        "overall_rubric_summary": summary,
    }
    if judge_tier is not None:
        judge_report.update(judge_tier=judge_tier, judge_model=model)
    eval_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = eval_digest

    manifest = build_artifact_manifest(
        f1_text=f1_text, f2_text=f2_text, f3_text=f3_text,
        model=model, reasoning_effort=reasoning_effort or "medium",
        judge_eval_digest=eval_digest,
    )
    q1_pass = _is_section_passed(qa_report, "output_01_forensic_transcript")
    q2_pass = _is_section_passed(qa_report, "output_02_executive_recap")
    q3_pass = _is_section_passed(qa_report, "output_03_canonical_signal_projection")
    cross_pass = _is_section_passed(qa_report, "cross_artifact_consistency")

    meta = metadata or {}
    att = int(meta.get("attempt", 1))
    max_att = int(meta.get("max_attempts", 1))

    q1_frozen = bool(qa_report.get("output_01_forensic_transcript", {}).get("frozen"))
    q2_frozen = bool(qa_report.get("output_02_executive_recap", {}).get("frozen"))
    q3_frozen = bool(qa_report.get("output_03_canonical_signal_projection", {}).get("frozen"))

    final_decision: Dict[str, Any] = {
        "attempt": att, "max_attempts": max_att, "short_circuit": True,
        "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
        "output_1_deterministic_gates_pass": q1_pass, "output_1_llm_score_pass": q1_frozen,
        "output_1_skipped": q1_pass, "output_1_pass": q1_pass and q1_frozen,
        "output_2_deterministic_gates_pass": q2_pass, "output_2_llm_score_pass": q2_frozen,
        "output_2_skipped": q2_pass, "output_2_pass": q2_pass and q2_frozen,
        "output_3_deterministic_gates_pass": q3_pass, "output_3_llm_score_pass": q3_frozen,
        "output_3_skipped": q3_pass, "output_3_pass": q3_pass and q3_frozen,
        "cross_artifact_consistency_pass": cross_pass, "final_pass": False,
        "authoritative_write_authorized": False, "status": "FAIL",
        "rubric_version": "2.0",
        "policy_version": "2.0.0",
        "validation_status": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "judge_evaluation_digest": eval_digest,
        "artifact_manifest": manifest,
    }
    if judge_tier is not None:
        final_decision.update(judge_tier=judge_tier, judge_model=model)
    return qa_report, judge_report, final_decision
