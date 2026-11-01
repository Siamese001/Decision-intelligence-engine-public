"""
dual_pipeline/document_pipeline_core/evaluator.py

Deterministic QA gate evaluation and micro-judge evaluation for document and email ingestion.
Evaluates Output 03 (Canonical Signal Projection) directly against source document text.
Certifies Outputs 01 and 02 as NOT_APPLICABLE_DOCUMENT_MODE.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple, Union

from dual_pipeline.evidence_field_limits import validate_projection_field_lengths
from dual_pipeline.llm_judge_core.judges import GraphExtractionJudge
from dual_pipeline.model_registry import ModelTier, resolve_tier_model, resolve_tier_model_name
from dual_pipeline.prompts import OUTPUT3_CONTRACT
from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.release_thresholds import OUTPUT3_PRODUCTION_PASS_THRESHOLD
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3


def _assert_patch_preservation(metadata: Dict[str, Any], candidate: str) -> None:
    request = metadata.get("document_patch_request")
    if request is not None:
        from dual_pipeline.document_pipeline_core.bounded_patch import validate_patch_preservation
        validate_patch_preservation(metadata["prior_graph_candidate"], request, candidate)


def _apply_document_grounding_and_yield_checks(
    q3: Dict[str, Any],
    projection: Dict[str, Any],
    source_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    from dual_pipeline.document_pipeline_core.quality import apply_document_quality_and_yield_checks
    return apply_document_quality_and_yield_checks(q3, projection, source_text, metadata=metadata)


def evaluate_document_qa_gates(
    f3_text: Union[str, Dict[str, Any]],
    source_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute Gate 3 against source document text, marking 01/02 as non-applicable."""
    validate_projection_field_lengths(f3_text)
    meta = dict(metadata or {})
    meta.setdefault("is_document", True)
    meta.setdefault("output3_only", True)
    if source_text and "source_text" not in meta:
        meta["source_text"] = source_text
    if meta.get("document_patch_request") is None:
        from dual_pipeline.document_pipeline_core.audit_dedup import healed_output_3_cached
        f3_str, _ = healed_output_3_cached(f3_text, metadata=meta, source_text=source_text)
    else:
        f3_str = f3_text if isinstance(f3_text, str) else json.dumps(f3_text, ensure_ascii=False, indent=2)
    validate_projection_field_lengths(f3_str)
    _assert_patch_preservation(meta, f3_str)

    q3 = run_qa_gate_3(f3_str, f1_text=source_text, metadata=meta)
    projection = json.loads(f3_str) if isinstance(f3_str, str) else f3_str
    passed = _apply_document_grounding_and_yield_checks(q3, projection, source_text, metadata=meta)

    skipped_stub = {
        "status": "SKIPPED",
        "passed": True,
        "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
        "details": "Source is a written document/email; no spoken dialogue or meeting recap required.",
    }

    return {
        "mode": "document",
        "status": "PASS" if passed else "FAIL",
        "overall_status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gate_1": skipped_stub,
        "gate_2": skipped_stub,
        "gate_3": q3,
        "output_01_forensic_transcript": skipped_stub,
        "output_02_executive_recap": skipped_stub,
        "output_03_canonical_signal_projection": q3,
        "cross_artifact_consistency": skipped_stub,
        "healed_artifacts": {
            "output_1": "",
            "output_2": "",
            "output_3": f3_str,
        },
    }


def _build_short_circuit_judge_result(qa_report: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, int]:
    """Emit zero-cost synthetic failure when Gate 3 fails deterministically."""
    failures = qa_report["output_03_canonical_signal_projection"].get("failures", [])
    failure_msg = "; ".join(failures) if failures else "Deterministic Gate 3 validation failed."
    j3_eval = {
        "status": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "reason": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "_reasoning": f"Short-circuited LLM judge: {failure_msg}",
        "meeting_archetype": "DOCUMENT_MODE",
        "dimension_1_strategic_edge": 0,
        "dimension_2_knowledge_layer_density": 0,
        "dimension_3_semantic_atomicity": 0,
        "dimension_4_predicate_specificity": 0,
        "dimension_5_exclusion_of_action_items": 0,
        "dimension_6_evidence_grounding_and_schema": 0,
        "composite_score": 0,
        "verdict": "FAIL",
        "forensic_rationale": f"Short-circuited LLM judge due to Gate 3 failure: {failure_msg}",
        "actionable_remediation_diff": failure_msg,
        "grounding_citations": [],
        "execution": {
            "status": "SKIPPED_DETERMINISTIC_QA_FAILURE",
            "effective_model": None,
            "effective_effort": None,
            "model_identity": "none",
        },
    }
    return j3_eval, False, 0


DOCUMENT_PRODUCTION_PASS_THRESHOLD: int = OUTPUT3_PRODUCTION_PASS_THRESHOLD


def _ensure_judge_execution_telemetry(
    j3_eval: Dict[str, Any], judge: Any, eff_model: str, judge_effort: str, mock: bool
) -> None:
    if mock and j3_eval.get("verdict") == "PASS":
        j3_eval["dimension_6_evidence_grounding_and_schema"] = 20
        j3_eval["composite_score"] = sum(
            j3_eval[dimension.key] for dimension in OUTPUT3_CONTRACT.rubric_dimensions
        )
        j3_eval["_reasoning"] = "Offline fixture score after deterministic document grounding passed."
    if not isinstance(j3_eval.get("execution"), dict):
        actual_model = getattr(judge, "model", None)
        actual_effort = getattr(judge, "reasoning_effort", None)
        j3_eval["execution"] = {
            "requested_model": eff_model,
            "requested_effort": judge_effort,
            "effective_model": actual_model if isinstance(actual_model, str) else eff_model,
            "effective_effort": actual_effort if isinstance(actual_effort, str) else judge_effort,
            "status": "OFFLINE_FIXTURE" if mock else "COMPLETED",
            "model_identity": "configured_only",
        }


def _execute_document_judge(
    eff_model: str,
    eff_effort: str,
    reasoning_effort: Optional[str],
    fast_judge: bool,
    f3_str: str,
    source_text: str,
    mock: bool,
    archetype: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool, int]:
    frontier_model = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING).lower()
    is_astra = bool(eff_model and (eff_model.lower() == frontier_model or "astra" in eff_model.lower()))
    judge_effort = "low" if (fast_judge and reasoning_effort is None and not is_astra) else eff_effort
    judge = GraphExtractionJudge(model=eff_model, reasoning_effort=judge_effort, mock=mock)
    doc_arch = archetype or "DOCUMENT_MODE"
    doc_meta = {**(metadata or {}), "archetype": doc_arch, "modality": "DOCUMENT", "is_document": True}
    j3_eval = judge.evaluate(
        f3_text=f3_str,
        f1_text=source_text,
        mock=mock,
        archetype=doc_arch,
        max_completion_tokens=CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens,
        metadata=doc_meta,
    )
    _ensure_judge_execution_telemetry(j3_eval, judge, eff_model, judge_effort, mock)
    from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
    had_proj = "projection" in j3_eval
    had_src = "source_text" in j3_eval
    if not had_proj and f3_str:
        try:
            j3_eval["projection"] = json.loads(f3_str)
        except Exception:
            pass
    if not had_src and source_text:
        j3_eval["source_text"] = source_text
    qualified, passed, score = qualify_document_judge(j3_eval)
    if not had_proj:
        qualified.pop("projection", None)
    if not had_src:
        qualified.pop("source_text", None)
    return qualified, passed, score


def _evaluate_output3_judge(
    q3_pass: bool,
    qa_report: Dict[str, Any],
    meta: Dict[str, Any],
    eff_model: str,
    eff_effort: str,
    reasoning_effort: Optional[str],
    fast_judge: bool,
    f3_str: str,
    source_text: str,
    mock: bool,
    attempt: int,
    max_attempts: int,
) -> Tuple[Dict[str, Any], bool, Any]:
    if not q3_pass:
        return _build_short_circuit_judge_result(qa_report)
    doc_archetype = "DOCUMENT_MODE"
    j3_eval, j3_pass, s3 = _execute_document_judge(
        eff_model=eff_model,
        eff_effort=eff_effort,
        reasoning_effort=reasoning_effort,
        fast_judge=fast_judge,
        f3_str=f3_str,
        source_text=source_text,
        mock=mock,
        archetype=doc_archetype,
        metadata=meta,
    )
    if not j3_pass:
        j3_eval["verdict"] = "FAIL"
    return j3_eval, j3_pass, s3


def _assemble_judge_report(
    j3_eval: Dict[str, Any],
    j3_pass: bool,
    s3: Any,
    eff_model: str,
    reasoning_effort: Optional[str],
    fast_judge: bool,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    skipped_eval = {
        "status": "NOT_APPLICABLE",
        "composite_score": None,
        "verdict": "NOT_APPLICABLE",
        "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
    }
    skipped_recon = {
        "status": "SKIPPED",
        "passed": True,
        "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
    }
    execution = j3_eval.get("execution", {"status": "NOT_CALLED", "effective_model": None,
                                          "effective_effort": None, "model_identity": "none"})
    reported_model = execution.get("effective_model") or eff_model
    reported_effort = execution.get("effective_effort")
    rep = {
        "rubric_version": "2.0",
        "mode": "document",
        "evaluator_model": reported_model,
        "reasoning_effort": reported_effort,
        "requested_model": eff_model,
        "requested_reasoning_effort": reasoning_effort,
        "fast_judge": bool(fast_judge),
        "execution": execution,
        "extraction_execution": dict(meta.get("_extraction_execution") or {}),
        "status": "PASS" if j3_pass else "FAIL",
        "output_1_evaluation": skipped_eval,
        "output_2_evaluation": skipped_eval,
        "output_3_evaluation": j3_eval,
        "cross_artifact_reconciliation": skipped_recon,
        "overall_rubric_summary": f"Document mode graph extraction judge: Output 3 score={s3}, verdict={j3_eval.get('verdict')}.",
    }
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    rep["aggregate_composite_score"] = compute_quality_aggregate_score(rep)
    return rep


def _build_final_decision(
    attempt: int, max_attempts: int, q3_pass: bool, j3_pass: bool, manifest: Dict[str, Any]
) -> Dict[str, Any]:
    final_pass = bool(q3_pass and j3_pass)
    return {
        "mode": "document",
        "attempt": attempt,
        "max_attempts": max_attempts,
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": True,
        "output_1_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_2_llm_score_pass": True,
        "output_2_pass": True,
        "output_3_deterministic_gates_pass": q3_pass,
        "output_3_llm_score_pass": j3_pass,
        "output_3_pass": final_pass,
        "cross_artifact_consistency_pass": True,
        "final_pass": final_pass,
        "authoritative_write_authorized": final_pass,
        "status": "PASS" if final_pass else "FAIL",
        "judge_evaluation_digest": manifest.get("judge_evaluation_digest"),
        "policy_version": "2.0.0",
        "rubric_version": "2.0",
        "artifact_manifest": manifest,
    }


def evaluate_document_judge_and_release(
    f3_text: Union[str, Dict[str, Any]],
    source_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    mock: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    attempt: int = 1,
    max_attempts: int = 1,
    fast_judge: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Run Gate 3 QA and GraphExtractionJudge on Output 03, emitting all 3 receipts."""
    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )

    default_model, default_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    eff_model = model or default_model
    eff_effort = reasoning_effort or default_effort or "high"

    meta = dict(metadata or {})
    if source_text and "source_text" not in meta:
        meta["source_text"] = source_text

    # 1. Deterministic QA Gate 3 (handles field-length checks, healing, and patch preservation)
    qa_report = evaluate_document_qa_gates(f3_text, source_text=source_text, metadata=meta)
    q3 = qa_report["output_03_canonical_signal_projection"]
    q3_pass = q3.get("passed") is True and q3.get("status") == "PASS"
    f3_str = qa_report["healed_artifacts"]["output_3"]

    # 2. Decomposed Graph Judge with Short-Circuit & Fast-Path Micro-Judge
    j3_eval, j3_pass, s3 = _evaluate_output3_judge(
        q3_pass=q3_pass,
        qa_report=qa_report,
        meta=meta,
        eff_model=eff_model,
        eff_effort=eff_effort,
        reasoning_effort=reasoning_effort,
        fast_judge=fast_judge,
        f3_str=f3_str,
        source_text=source_text,
        mock=mock,
        attempt=attempt,
        max_attempts=max_attempts,
    )

    judge_report = _assemble_judge_report(
        j3_eval=j3_eval,
        j3_pass=j3_pass,
        s3=s3,
        eff_model=eff_model,
        reasoning_effort=reasoning_effort,
        fast_judge=fast_judge,
        meta=meta,
    )
    eval_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = eval_digest

    manifest = build_artifact_manifest(
        f1_text=source_text,
        f2_text="",
        f3_text=f3_str,
        model=judge_report["evaluator_model"],
        reasoning_effort=judge_report["reasoning_effort"],
        judge_eval_digest=eval_digest,
    )

    # 3. Final Release Decision
    final_decision = _build_final_decision(attempt, max_attempts, q3_pass, j3_pass, manifest)
    return qa_report, judge_report, final_decision
