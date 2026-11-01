"""
dual_pipeline/transcript_pipeline_core/qa_runner.py

Stage X1–X3: Exit Gates, Mandatory QA & Micro-Judge Verification, Healing Application,
and Remediation Lifecycle Controller.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.remediation_core import RemediationDiffInjector
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.unified_agentic_pipeline import PipelineStage

from .uwg_deployer import deploy_uwg_artifacts

logger = logging.getLogger(__name__)


def _apply_healed_artifacts(
    qa_report: Dict[str, Any],
    artifacts: Dict[str, Any],
    is_notes_mode: bool,
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
) -> Tuple[str, str, str]:
    if not qa_report.get("healed_artifacts"):
        return forensic_transcript, executive_recap, graph_json
    healed = qa_report["healed_artifacts"]
    if not is_notes_mode and healed.get("output_1"):
        forensic_transcript = healed["output_1"]
        artifacts["01_hitl_resolved_transcript.md"] = forensic_transcript
    if healed.get("output_2"):
        executive_recap = healed["output_2"]
        artifacts["02_executive_recap.md"] = executive_recap
    if healed.get("output_3"):
        graph_json = healed["output_3"]
        artifacts["03_canonical_signal_projection.json"] = graph_json
    return forensic_transcript, executive_recap, graph_json


def _freeze_passing_outputs(
    state_machine: Any,
    final_decision: Dict[str, Any],
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
    judge_report: Optional[Dict[str, Any]] = None,
    qa_report: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    j_rep = judge_report or {}
    q_rep = qa_report or {}

    # Monotonic ledger tracking: record candidate scores and contents
    if hasattr(state_machine, "record_candidate"):
        o1_s, o1_v = int(j_rep.get("output_1_evaluation", {}).get("composite_score", 0)), j_rep.get("output_1_evaluation", {}).get("verdict", "FAIL")
        o2_s, o2_v = int(j_rep.get("output_2_evaluation", {}).get("composite_score", 0)), j_rep.get("output_2_evaluation", {}).get("verdict", "FAIL")
        o3_s, o3_v = int(j_rep.get("output_3_evaluation", {}).get("composite_score", 0)), j_rep.get("output_3_evaluation", {}).get("verdict", "FAIL")
        q1_p = q_rep.get("output_01_forensic_transcript", {}).get("passed", True)
        q2_p = q_rep.get("output_02_executive_recap", {}).get("passed", True)
        q3_p = q_rep.get("output_03_canonical_signal_projection", {}).get("passed", True)

        state_machine.record_candidate(
            "output_1", forensic_transcript, o1_s, verdict=o1_v,
            dimension_scores=j_rep.get("output_1_evaluation", {}).get("dimension_scores"),
            qa_passed=q1_p,
            deterministic_gates_pass=final_decision.get("output_1_deterministic_gates_pass", False),
        )
        state_machine.record_candidate(
            "output_2", executive_recap, o2_s, verdict=o2_v,
            dimension_scores=j_rep.get("output_2_evaluation", {}).get("dimension_scores"),
            qa_passed=q2_p,
            deterministic_gates_pass=final_decision.get("output_2_deterministic_gates_pass", False),
        )
        state_machine.record_candidate(
            "output_3", graph_json, o3_s, verdict=o3_v,
            dimension_scores=j_rep.get("output_3_evaluation", {}).get("dimension_scores"),
            qa_passed=q3_p,
            deterministic_gates_pass=final_decision.get("output_3_deterministic_gates_pass", False),
        )

    import hashlib
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
    pinned = metadata.setdefault("pinned_evaluations", {}) if metadata is not None else {}

    ckpt_dir = getattr(state_machine, "checkpoint_dir", None) or (metadata.get("checkpoint_dir") if metadata else None) or (metadata.get("output_dir") if metadata else None)

    out1_eval = dict(j_rep.get("output_1_evaluation", {}))
    out1_score = float(out1_eval.get("composite_score") or 0.0)
    out1_verdict = str(out1_eval.get("verdict") or "")
    out1_auth_exc = bool(out1_eval.get("authorized_exception") or out1_verdict == "PASS_AUTHORIZED_EXCEPTION")
    if (
        final_decision.get("output_1_pass", False)
        and final_decision.get("output_1_deterministic_gates_pass", False)
        and out1_eval.get("status") != "SKIPPED"
        and ((out1_score >= float(CANONICAL_RELEASE_POLICY.output1_pass_threshold) and out1_verdict == "PASS") or out1_auth_exc)
    ):
        is_valid1, _ = validate_micro_judge_scorecard(out1_eval, output_num=1)
        if is_valid1:
            state_machine.freeze_artifact("output_1", forensic_transcript)
            if ckpt_dir:
                try:
                    from dual_pipeline.remediation_core.stage_sealer import persist_stage_checkpoint_seal
                    persist_stage_checkpoint_seal(ckpt_dir, "output_1", forensic_transcript)
                except Exception:
                    pass
            out1_eval["artifact_sha256"] = hashlib.sha256(forensic_transcript.encode("utf-8")).hexdigest()
            pinned["output_1"] = out1_eval

    out2_eval = dict(j_rep.get("output_2_evaluation", {}))
    out2_score = float(out2_eval.get("composite_score") or 0.0)
    out2_verdict = str(out2_eval.get("verdict") or "")
    out2_auth_exc = bool(out2_eval.get("authorized_exception") or out2_verdict == "PASS_AUTHORIZED_EXCEPTION")
    if (
        final_decision.get("output_2_pass", False)
        and final_decision.get("output_2_deterministic_gates_pass", False)
        and out2_eval.get("status") != "SKIPPED"
        and ((out2_score >= float(CANONICAL_RELEASE_POLICY.output2_pass_threshold) and out2_verdict == "PASS") or out2_auth_exc)
    ):
        is_valid2, _ = validate_micro_judge_scorecard(out2_eval, output_num=2)
        if is_valid2:
            state_machine.freeze_artifact("output_2", executive_recap)
            if ckpt_dir:
                try:
                    from dual_pipeline.remediation_core.stage_sealer import persist_stage_checkpoint_seal
                    persist_stage_checkpoint_seal(ckpt_dir, "output_2", executive_recap)
                except Exception:
                    pass
            out2_eval["artifact_sha256"] = hashlib.sha256(executive_recap.encode("utf-8")).hexdigest()
            pinned["output_2"] = out2_eval

    out3_eval = dict(j_rep.get("output_3_evaluation", {}))
    out3_score = float(out3_eval.get("composite_score") or 0.0)
    out3_verdict = str(out3_eval.get("verdict") or "")
    out3_auth_exc = bool(out3_eval.get("authorized_exception") or out3_verdict == "PASS_AUTHORIZED_EXCEPTION")
    if (
        final_decision.get("output_3_pass", False)
        and final_decision.get("output_3_deterministic_gates_pass", False)
        and out3_eval.get("status") != "SKIPPED"
        and ((out3_score >= float(CANONICAL_RELEASE_POLICY.output3_pass_threshold) and out3_verdict == "PASS") or out3_auth_exc)
    ):
        is_valid3, _ = validate_micro_judge_scorecard(out3_eval, output_num=3)
        if is_valid3:
            state_machine.freeze_artifact("output_3", graph_json)
            if ckpt_dir:
                try:
                    from dual_pipeline.remediation_core.stage_sealer import persist_stage_checkpoint_seal
                    persist_stage_checkpoint_seal(ckpt_dir, "output_3", graph_json)
                except Exception:
                    pass
            out3_eval["artifact_sha256"] = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
            pinned["output_3"] = out3_eval


# RC-4: Deterministic failure markers — failures that come from Python code logic, not LLM output quality.
# Retrying the LLM on these is guaranteed futile and burns 7-15 min per attempt.
_DETERMINISTIC_GATE_FAILURE_MARKERS = (
    "C27_UNGROUNDED_STAKEHOLDER_IN_RECAP",
    "Gate_1_10_Referenced_Entity_Context",
    "C27_UNGROUNDED",
    "turn density failure",
    "GATE_2_1_SEMANTIC",
    "forbidden ISO-8601",
    "word count density failure",
    "GATE_2_FORBIDDEN_SECTION_DETECTED",
    "GATE_2_2_INDETERMINATE",
    "PydanticValidationError",
    "Deterministic Pydantic validation failed",
    "PDA_PARSE_FAILURE",
    "SCHEMA_VIOLATION",
)


def _is_deterministic_only_failure(
    qa_report: Dict[str, Any],
    final_decision: Dict[str, Any],
) -> bool:
    """RC-4: Detect when all gate failures are from deterministic Python logic, not LLM quality.

    When output_X_deterministic_gates_pass=False but output_X_llm_score_pass=True, or when all
    failing gates match deterministic Python code logic and LLM judging was short-circuited,
    the failure is guaranteed to be a code-level gate check. Retrying with a new LLM call cannot
    change the gate outcome — only a code fix resolves it. Return True to fail-closed immediately
    rather than burning 7-15 minutes on futile LLM retries.
    """
    all_failure_strs: list = []
    for section_key in (
        "output_01_forensic_transcript",
        "output_02_executive_recap",
        "output_03_canonical_signal_projection",
        "cross_artifact_consistency",
    ):
        section = qa_report.get(section_key, {})
        all_failure_strs.extend(section.get("failures", []))

    if not all_failure_strs:
        return False

    deterministic_failures = [
        f for f in all_failure_strs
        if any(marker in f for marker in _DETERMINISTIC_GATE_FAILURE_MARKERS)
    ]
    if not deterministic_failures:
        return False

    # Primary signal: all failing gates match deterministic code logic
    if len(deterministic_failures) == len(all_failure_strs):
        logger.error(
            f"RC-4 FAIL-CLOSED: All {len(all_failure_strs)} gate failure(s) match deterministic code gates. "
            f"Failure is from Python code logic — retrying LLM will not resolve this. "
            f"Failing closed immediately. Failures: {deterministic_failures}"
        )
        return True

    # Secondary signal: at least one output has deterministic gates failing while LLM scoring passed
    for output_idx in ("1", "2", "3"):
        det_pass = final_decision.get(f"output_{output_idx}_deterministic_gates_pass", False)
        llm_pass = final_decision.get(f"output_{output_idx}_llm_score_pass", False)
        if not det_pass and llm_pass:
            logger.error(
                f"RC-4 FAIL-CLOSED: Output {output_idx} has deterministic gate failures with LLM scoring passing. "
                f"Failure is from Python code logic — retrying LLM will not resolve this. "
                f"Failing closed immediately. Failures: {deterministic_failures}"
            )
            return True
    return False


def _handle_pass(
    attempt: int,
    max_judge_retries: int,
    x_gates: Any,
    tracker: Any,
    notify_callback: Optional[Callable[[int, str], None]],
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
    metadata: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any], str, str, str]:
    passed = x_gates.execute(executive_recap, graph_json, metadata)
    if not passed:
        raise RuntimeError("X-gates evaluation failed")
    if notify_callback:
        notify_callback(9, f"X1-X3 & QA/Judge passed on attempt {attempt}")
    logger.info(f"Release authorized on attempt {attempt}/{max_judge_retries + 1}.")
    tracker.complete_stage(
        PipelineStage.X_EXIT_GATES,
        status="PASS",
        details={
            "attempt": attempt,
            "qa_passed": qa_report.get("passed", False),
            "judge_status": final_decision.get("status"),
        },
    )
    return True, qa_report, judge_report, final_decision, forensic_transcript, executive_recap, graph_json


def _handle_remediation(
    attempt: int,
    tracker: Any,
    metadata: Dict[str, Any],
    is_notes_mode: bool,
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
    state_machine: Optional[Any] = None,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any], str, str, str]:
    tracker.request_remediation(
        from_stage=PipelineStage.X_EXIT_GATES,
        to_stage=PipelineStage.L2_SYNTHESIS,
        reason=f"Attempt {attempt} failed QA/Judge evaluation: {final_decision.get('status')}",
        details={"attempt": attempt, "judge_status": final_decision.get("status")},
    )
    out1_skipped = judge_report.get("output_1_evaluation", {}).get("status") == "SKIPPED"
    out2_skipped = judge_report.get("output_2_evaluation", {}).get("status") == "SKIPPED"
    out3_skipped = judge_report.get("output_3_evaluation", {}).get("status") == "SKIPPED"

    out1_failed = not final_decision.get("output_1_deterministic_gates_pass", False) or (
        not final_decision.get("output_1_pass", False) and not out1_skipped
    )
    out2_failed = not final_decision.get("output_2_deterministic_gates_pass", False) or (
        not final_decision.get("output_2_pass", False) and not out2_skipped
    )
    out3_failed = not final_decision.get("output_3_deterministic_gates_pass", False) or (
        not final_decision.get("output_3_pass", False) and not out3_skipped
    )

    if not is_notes_mode and out1_failed:
        if state_machine:
            state_machine.unfreeze_artifact("output_1")
        metadata["remediation_directive_forensic"] = RemediationDiffInjector.extract_directive(
            "output_1", judge_report, qa_report
        )
    else:
        metadata.pop("remediation_directive_forensic", None)

    if out2_failed:
        if state_machine:
            state_machine.unfreeze_artifact("output_2")
        metadata["remediation_directive_recap"] = RemediationDiffInjector.extract_directive(
            "output_2", judge_report, qa_report
        )
    else:
        metadata.pop("remediation_directive_recap", None)

    if out3_failed:
        if state_machine:
            state_machine.unfreeze_artifact("output_3")
        metadata["remediation_directive_graph"] = RemediationDiffInjector.extract_directive(
            "output_3", judge_report, qa_report
        )
    else:
        metadata.pop("remediation_directive_graph", None)
    return False, qa_report, judge_report, final_decision, forensic_transcript, executive_recap, graph_json


def _handle_terminal_failure(
    attempt: int,
    max_judge_retries: int,
    tracker: Any,
    artifacts: Dict[str, Any],
    metadata: Dict[str, Any],
    output_dir: Optional[str],
    pres_file: Optional[Union[str, Path]],
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
) -> None:
    tracker.complete_stage(
        PipelineStage.X_EXIT_GATES,
        status="FAIL",
        details={"attempt": attempt, "judge_status": final_decision.get("status")},
    )
    try:
        deploy_uwg_artifacts(
            artifacts,
            metadata,
            output_dir,
            qa_report=qa_report,
            judge_report=judge_report,
            final_decision=final_decision,
            presentation_path=pres_file,
        )
    except Exception as write_err:
        logger.warning(f"Failed writing diagnostic failure artifacts: {write_err}")

    try:
        fail_receipt = tracker.build_receipt(output_dir, terminal_outcome="FAILED")
        fail_receipt.write_to_dir(output_dir)
    except Exception as rc_err:
        logger.debug(f"Failed writing failure receipt: {rc_err}")

    all_failures = [f for qk in ("output_01_forensic_transcript", "output_02_executive_recap", "output_03_canonical_signal_projection", "cross_artifact_consistency") for f in qa_report.get(qk, {}).get("failures", [])]
    all_failures.extend(judge_report.get("cross_artifact_reconciliation", {}).get("failures", []))
    if judge_report.get("overall_rubric_summary"):
        all_failures.append(judge_report["overall_rubric_summary"])
    err_msg = "; ".join(all_failures) if all_failures else "Unknown"
    raise RuntimeError(
        f"Mandatory QA Gate or Micro-Judge evaluation failed after {max_judge_retries + 1} attempts: {err_msg}"
    )


def _restore_candidate_eval(eval_key: str, best_cand: Any, judge_report: Dict[str, Any]) -> None:
    if eval_key not in judge_report:
        return
    full_sc = getattr(best_cand, "full_scorecard", None)
    if full_sc:
        import copy
        judge_report[eval_key] = copy.deepcopy(full_sc)
    elif best_cand.composite_score > judge_report[eval_key].get("composite_score", 0):
        judge_report[eval_key].update({"composite_score": best_cand.composite_score, "verdict": best_cand.verdict})


def _prepare_eval_meta(metadata: Dict[str, Any], state_machine: Any) -> Dict[str, Any]:
    meta = dict(metadata)
    if state_machine:
        meta["state_machine"] = state_machine
    return meta


def evaluate_exit_gates_and_verification(
    attempt: int,
    max_judge_retries: int,
    artifacts: Dict[str, Any],
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
    transcript_text: str,
    u0_context: Dict[str, Any],
    metadata: Dict[str, Any],
    state_machine: Any,
    x_gates: Any,
    tracker: Any,
    recorder: Any,
    provider_type: str,
    judge_model: Optional[str],
    reasoning_effort: Optional[str],
    is_notes_mode: bool,
    notify_callback: Optional[Callable[[int, str], None]] = None,
    output_dir: Optional[str] = None,
    pres_file: Optional[Union[str, Path]] = None,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any], str, str, str]:
    """
    Executes exit gates X1-X3, deterministic QA gates, and micro-judge evaluation.
    Applies automatic healing, updates state machine, and handles remediation requests or terminal failures.
    """
    tracker.start_stage(PipelineStage.X_EXIT_GATES)
    with recorder.span("nee.uc1.x.exit"):
        tier_name = str(metadata.get("judge_tier") or "production").strip().lower()
        if tier_name == "fast":
            default_judge_model, default_judge_effort = resolve_tier_model(ModelTier.FAST_ROUTING)
        elif tier_name == "standard":
            default_judge_model, default_judge_effort = resolve_tier_model(ModelTier.FRONTIER_REASONING)
        else:
            default_judge_model, default_judge_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        actual_judge_model = judge_model or default_judge_model
        actual_reasoning_effort = reasoning_effort or default_judge_effort

        import sys
        pipeline_mod = sys.modules.get("dual_pipeline.transcript_pipeline_core.pipeline")
        pipeline_eval = getattr(pipeline_mod, "evaluate_judge_and_release", None)
        eval_fn = pipeline_eval if (hasattr(pipeline_eval, "assert_called") or hasattr(pipeline_eval, "return_value")) else evaluate_judge_and_release

        if notify_callback:
            notify_callback(8, f"X1-X3 exit gates & micro-judge evaluation (attempt {attempt}/{max_judge_retries + 1})")

        from dual_pipeline.canonical_pipeline.parallel_orchestrator import bind_verified_stage_checkpoint_pins
        eval_meta = _prepare_eval_meta(metadata, state_machine)
        bind_verified_stage_checkpoint_pins(eval_meta, forensic_transcript, executive_recap, graph_json)

        qa_report, judge_report, final_decision = eval_fn(
            f1_text=forensic_transcript,
            f2_text=executive_recap,
            f3_text=graph_json,
            source_text=transcript_text,
            metadata=eval_meta,
            mock=(provider_type == "offline"),
            model=actual_judge_model,
            reasoning_effort=actual_reasoning_effort,
            auto_heal=True,
            pinned_evaluations=metadata.get("pinned_evaluations"),
        )

        forensic_transcript, executive_recap, graph_json = _apply_healed_artifacts(
            qa_report, artifacts, is_notes_mode, forensic_transcript, executive_recap, graph_json
        )

        final_decision["attempt"] = attempt
        final_decision["max_attempts"] = max_judge_retries + 1
        final_decision["input_hash"] = u0_context.get("hash")
        if metadata.get("source_file_hash"):
            final_decision["source_file_hash"] = metadata["source_file_hash"]

        _freeze_passing_outputs(state_machine, final_decision, forensic_transcript, executive_recap, graph_json, judge_report=judge_report, qa_report=qa_report, metadata=metadata)

        # Monotonic regression check: restore best candidates if regressions occurred
        if hasattr(state_machine, "has_regressed") and hasattr(state_machine, "get_best_candidate"):
            regressed_any = False
            for out_key, art_file, eval_key in (("output_1", "01_hitl_resolved_transcript.md", "output_1_evaluation"), ("output_2", "02_executive_recap.md", "output_2_evaluation"), ("output_3", "03_canonical_signal_projection.json", "output_3_evaluation")):
                if state_machine.has_regressed(out_key):
                    best_cand = state_machine.get_best_candidate(out_key)
                    if best_cand and best_cand.content:
                        regressed_any = True
                        logger.warning(
                            f"MONOTONIC INVARIANT VIOLATION: {out_key} regressed on attempt {attempt}. "
                            f"Restoring best candidate from Attempt {best_cand.attempt} (score: {best_cand.composite_score})."
                        )
                        if out_key == "output_1": forensic_transcript = best_cand.content
                        elif out_key == "output_2": executive_recap = best_cand.content
                        elif out_key == "output_3": graph_json = best_cand.content
                        artifacts[art_file] = best_cand.content

                        _restore_candidate_eval(eval_key, best_cand, judge_report)

            if regressed_any:
                from dual_pipeline.manifest_runner.guard_utils import update_final_release_verdicts
                update_final_release_verdicts(judge_report, final_decision)

        from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
        evaluate_judge_clarification(judge_report, final_decision, metadata=metadata)
        final_pass = bool(
            final_decision.get("authoritative_write_authorized")
            and final_decision.get("status") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
            and qa_report.get("passed")
        )

        if final_pass:
            return _handle_pass(
                attempt, max_judge_retries, x_gates, tracker, notify_callback,
                qa_report, judge_report, final_decision,
                forensic_transcript, executive_recap, graph_json, metadata
            )

        logger.warning(
            f"Release NOT authorized on attempt {attempt}/{max_judge_retries + 1}. Status: {final_decision.get('status')}."
        )

        if attempt <= max_judge_retries:
            # RC-4 FAIL-CLOSED GUARD: Deterministic code failures cannot be resolved by LLM retries
            if _is_deterministic_only_failure(qa_report, final_decision):
                logger.error(
                    f"RC-4: Deterministic-only gate failure detected on attempt {attempt}. "
                    f"Failing closed immediately — LLM retry would be futile. "
                    f"Fix the underlying Python gate logic and re-run the pipeline."
                )
                _handle_terminal_failure(
                    attempt, max_judge_retries, tracker, artifacts, metadata,
                    output_dir, pres_file, qa_report, judge_report, final_decision
                )
                return False, qa_report, judge_report, final_decision, forensic_transcript, executive_recap, graph_json
            return _handle_remediation(
                attempt, tracker, metadata, is_notes_mode,
                qa_report, judge_report, final_decision,
                forensic_transcript, executive_recap, graph_json,
                state_machine=state_machine,
            )

        _handle_terminal_failure(
            attempt, max_judge_retries, tracker, artifacts, metadata,
            output_dir, pres_file, qa_report, judge_report, final_decision
        )
        return False, qa_report, judge_report, final_decision, forensic_transcript, executive_recap, graph_json
