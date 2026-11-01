"""
Evaluator module for transcript QA:
Executes deterministic QA gates across all 3 artifacts and coordinates multi-micro-judge release evaluation.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

if TYPE_CHECKING:
    from dual_pipeline.llm_judge_core import DecomposedMultiJudge
else:
    DecomposedMultiJudge = None
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY, ReleaseVerdict

from .consistency import run_cross_artifact_consistency
from .eval_cache import (
    _build_skip_eval,
    build_short_circuit_eval,
    resolve_pins_and_cache,
    update_evaluation_cache,
)
from .gate1 import run_qa_gate_1
from .gate2 import run_qa_gate_2
from .gate3 import run_qa_gate_3
from .healing_mesh import DeterministicHealer
from .qa_swarm import QASwarm

logger = logging.getLogger(__name__)


def _is_notes_mode(meta: Dict[str, Any]) -> bool:
    return bool(
        meta.get("notes_mode")
        or meta.get("is_notes")
        or meta.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
        or meta.get("pipeline_mode") in ("notes", "copilot")
        or meta.get("mode") in ("notes", "copilot")
        or meta.get("outputs") == ["02", "03"]
    )


def _build_skipped_gate_1() -> Dict[str, Any]:
    return {
        "status": "SKIPPED", "passed": True, "reason": "NOT_APPLICABLE_NOTES_MODE",
        "details": "Source is pre-processed copilot notes / meeting summary; forensic transcript (01) is not required.",
        "failures": [], "gates": {},
    }


from .frozen_gates import apply_frozen_to_qa_report, build_frozen_gate, is_stage_frozen


def _resolve_gate_inputs(
    f1_text: Optional[str],
    f2_text: Optional[str],
    f3_text: Optional[Union[str, Dict[str, Any]]],
    transcript_text: Optional[str],
    recap_text: Optional[str],
    graph_data: Optional[Union[str, Dict[str, Any]]],
    kwargs: Dict[str, Any],
) -> Tuple[str, str, Any]:
    f1 = transcript_text if transcript_text is not None else (f1_text or kwargs.get("f1", ""))
    f2 = recap_text if recap_text is not None else (f2_text or kwargs.get("f2", ""))
    f3 = graph_data if graph_data is not None else (f3_text if f3_text is not None else kwargs.get("f3", "{}"))
    return f1, f2, f3


def _prepare_artifacts_and_healing(
    f1: str, f2: str, f3: Any, meta: Dict[str, Any], auto_heal: bool
) -> Tuple[str, str, str, Dict[str, Any]]:
    sm = meta.get("state_machine")
    is_f1_frz = is_stage_frozen(sm, "output_1")
    is_f2_frz = is_stage_frozen(sm, "output_2")
    is_f3_frz = is_stage_frozen(sm, "output_3")

    if auto_heal:
        f1_h, f2_h, f3_h, healing_report = DeterministicHealer.heal_all(f1, f2, f3, meta)
        eff_f1 = f1 if is_f1_frz else f1_h
        eff_f2 = f2 if is_f2_frz else f2_h
        eff_f3 = (json.dumps(f3, indent=2) if isinstance(f3, dict) else str(f3)) if is_f3_frz else f3_h
        return eff_f1, eff_f2, eff_f3, healing_report
    f3_str = json.dumps(f3, indent=2) if isinstance(f3, dict) else str(f3)
    return f1, f2, f3_str, {}


def evaluate_all_qa_gates(
    f1_text: Optional[str] = None,
    f2_text: Optional[str] = None,
    f3_text: Optional[Union[str, Dict[str, Any]]] = None,
    source_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    transcript_text: Optional[str] = None,
    recap_text: Optional[str] = None,
    graph_data: Optional[Union[str, Dict[str, Any]]] = None,
    auto_heal: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run deterministic QA gates across all 3 artifacts and compile qa_gates_report.json."""
    meta = metadata or kwargs.get("metadata") or {}
    f1_in, f2_in, f3_in = _resolve_gate_inputs(
        f1_text, f2_text, f3_text, transcript_text, recap_text, graph_data, kwargs
    )
    f1, f2, f3_str, healing_report = _prepare_artifacts_and_healing(f1_in, f2_in, f3_in, meta, auto_heal)

    is_notes = _is_notes_mode(meta)
    sm = meta.get("state_machine")
    is_f1_frozen, is_f2_frozen, is_f3_frozen = is_stage_frozen(sm, "output_1"), is_stage_frozen(sm, "output_2"), is_stage_frozen(sm, "output_3")

    meta_for_g1 = dict(meta, prior_executive_recap=f2, current_executive_recap=f2)
    q1 = _build_skipped_gate_1() if is_notes else (build_frozen_gate("Output 1 (Forensic Transcript)") if is_f1_frozen else run_qa_gate_1(f1, source_text, meta_for_g1))
    q2 = build_frozen_gate("Output 2 (Executive Recap)") if is_f2_frozen else run_qa_gate_2(f2, meta)
    ref_for_g3 = (source_text or f2) if is_notes else f1
    q3 = build_frozen_gate("Output 3 (Canonical Signal Projection)") if is_f3_frozen else run_qa_gate_3(f3_str, ref_for_g3, meta)
    cross = run_cross_artifact_consistency(q1, q2, q3, f1, f2, f3_str, meta)

    # Wave 1: 15-Agent Swarm QA Review
    skip_swarm = bool(meta.get("skip_swarm") or meta.get("test_fast") or os.environ.get("NEE_FAST_TEST_MODE"))
    if skip_swarm:
        swarm_report = QASwarm.fast_pass_report()
    else:
        swarm = QASwarm()
        swarm_report = swarm.evaluate_all(None if is_notes else f1, f2, f3_str)

    all_passed = bool(q1["passed"] and q2["passed"] and q3["passed"] and cross["passed"] and swarm_report["swarm_passed"])
    report = {
        "status": "PASS" if all_passed else "FAIL",
        "passed": all_passed,
        "output_01_forensic_transcript": q1,
        "output_02_executive_recap": q2,
        "output_03_canonical_signal_projection": q3,
        "cross_artifact_consistency": cross,
        "15_agent_swarm_review": swarm_report,
    }
    if auto_heal:
        report["deterministic_healing"] = healing_report
        report["healed_artifacts"] = {
            "output_1": f1,
            "output_2": f2,
            "output_3": f3_str,
        }
    return report





def _resolve_client(mock: bool, model: str, reasoning_effort: str, needs_eval: bool) -> Tuple[Any, bool]:
    if mock:
        return None, True
    if not needs_eval:
        return None, False
    from dual_pipeline.credentials import get_openai_client
    client, creds = get_openai_client(
        allow_mock=False,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    if creds.is_mock or client is None:
        raise RuntimeError(
            f"Zero mocks invariant violated: Live OpenAI client required for {model}, "
            f"but credentials resolved with is_mock={creds.is_mock}."
        )
    return client, False


def _dispatch_judge(
    executor: concurrent.futures.ThreadPoolExecutor,
    judge: DecomposedMultiJudge,
    f1_text: str,
    f2_text: str,
    f3_text: str,
    ref_text: str,
    src: str,
    client: Any,
    mock: bool,
    arch: Optional[MeetingArchetype],
    evals: Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]],
    is_notes: bool,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out1_eval, out2_eval, out3_eval = evals
    futures: Dict[str, Any] = {}
    import contextvars
    if not is_notes and out1_eval is None:
        futures["out1"] = executor.submit(contextvars.copy_context().run, judge.judge_forensic.evaluate, f1_text, src, client, mock, arch, meta)
    if out2_eval is None:
        collateral_ctx = str((meta or {}).get("attachment_prompt_block") or (meta or {}).get("uploaded_collateral") or "")
        futures["out2"] = executor.submit(contextvars.copy_context().run, judge.judge_recap.evaluate, f2_text, ref_text, collateral_ctx, client, mock, arch, metadata=meta)
    if out3_eval is None:
        arch_val = arch.value if arch else None
        eval_g = getattr(judge, "judge_graph", None)
        fn = getattr(eval_g, "evaluate", None) or judge.evaluate_graph
        futures["out3"] = executor.submit(contextvars.copy_context().run, fn, f3_text, ref_text, client=client, mock=mock, archetype=arch_val, metadata=meta)
    return futures


def _await_micro_judge(fut: Any, key: str, out_num: int) -> Dict[str, Any]:
    from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
    from config.request_deadline import remaining_timeout

    default_timeout = float(CANONICAL_PROMPT_CONFIG.judge.deadline_seconds or 600.0)
    raw_timeout = float(os.environ.get("MICRO_JUDGE_TIMEOUT", str(default_timeout)))
    timeout = remaining_timeout(raw_timeout)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.error("MicroJudge %s timed out after %.0fs", key, timeout)
        payload: Dict[str, Any] = {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 0.0,
            "failure_type": "TIMEOUT",
            "passed_pre_check": False,
            "reason": f"MicroJudge {key} timed out after {timeout}s",
            "evaluator": f"MicroJudgeOutput{out_num}",
            "is_valid_scorecard": True,
            "rubric_version": "2.0",
            "judge_model": "timeout-fallback",
            "prompt_digest": "timeout_digest",
            "sample_count": 1,
            "is_fallback": True,
            "evaluated_span_coverage": 0.0,
            "defect_codes": ["ERR_JUDGE_TIMEOUT"],
            "actionable_remediation_diff": "Retry judge evaluation with bounded reasoning effort.",
            "forensic_rationale": f"MicroJudge {key} evaluation timed out after {timeout}s.",
        }
        if out_num == 1:
            payload.update({
                "dimension_1_forensic_grounding_and_fidelity": 0.0,
                "dimension_2_attribution_and_room_mic_hygiene": 0.0,
                "dimension_3_chronological_and_timestamp_integrity": 0.0,
                "dimension_4_asr_cleanup_and_domain_preservation": 0.0,
                "dimension_1_speaker_identity_fidelity": 0.0,
                "dimension_2_turn_boundary_timestamp_integrity": 0.0,
                "dimension_3_verbatim_completeness_grounding": 0.0,
                "dimension_4_register_framing_absence": 0.0,
            })
        elif out_num == 2:
            payload.update({
                "section_scores": {
                    "header_and_overview": 0.0,
                    "key_updates_technical_substance": 0.0,
                    "conditional_extensions": 0.0,
                    "appendix_stakeholder_perspectives": 0.0,
                },
                "executive_rationale": f"MicroJudge {key} evaluation timed out after 300.0s.",
            })
        elif out_num == 3:
            payload.update({
                "dimension_1_strategic_edge": 0.0,
                "dimension_2_knowledge_layer_density": 0.0,
                "dimension_3_semantic_atomicity": 0.0,
                "dimension_4_predicate_specificity": 0.0,
                "dimension_5_exclusion_of_action_items": 0.0,
                "dimension_6_evidence_grounding_and_schema": 0.0,
            })
        return payload


def _run_micro_judges_concurrently(
    judge: Any,
    f1_text: str,
    f2_text: str,
    f3_text: str,
    src: str,
    client: Any,
    mock: bool,
    meta: Dict[str, Any],
    evals: Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]],
    keys: Tuple[str, str, str],
    f3_data: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    out1_eval, out2_eval, out3_eval = evals
    k1, k2, k3 = keys
    is_notes_mode = _is_notes_mode(meta)
    is_doc_mode = bool(
        meta.get("is_document")
        or meta.get("canonical_modality") == "DOCUMENT"
        or meta.get("source_type") == "document"
        or meta.get("outputs") == ["03"]
    )
    if is_notes_mode:
        out1_eval = {
            "status": "SKIPPED", "composite_score": None, "verdict": "NOT_APPLICABLE",
            "reason": "NOT_APPLICABLE_NOTES_MODE", "evaluator": "MicroJudgeForensic",
        }
    elif is_doc_mode:
        out1_eval = {
            "status": "SKIPPED", "composite_score": None, "verdict": "NOT_APPLICABLE",
            "reason": "NOT_APPLICABLE_DOCUMENT_MODE", "evaluator": "MicroJudgeForensic",
        }
        out2_eval = {
            "status": "SKIPPED", "composite_score": None, "verdict": "NOT_APPLICABLE",
            "reason": "NOT_APPLICABLE_DOCUMENT_MODE", "evaluator": "MicroJudgeRecap",
        }
    arch_val = meta.get("archetype") or meta.get("meeting_type") or meta.get("meeting_archetype")
    arch = MeetingArchetype.from_string(arch_val) if arch_val else None
    ref_text = src if (is_notes_mode or is_doc_mode) else f1_text
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = _dispatch_judge(executor, judge, f1_text, f2_text, f3_text, ref_text, src, client, mock, arch, (out1_eval, out2_eval, out3_eval), is_notes_mode, meta)
        if "out1" in futures: out1_eval = _await_micro_judge(futures["out1"], "out1", 1)
        if "out2" in futures: out2_eval = _await_micro_judge(futures["out2"], "out2", 2)
        if "out3" in futures: out3_eval = _await_micro_judge(futures["out3"], "out3", 3)
    out1 = out1_eval or evals[0] or {}
    out2 = out2_eval or evals[1] or {}
    out3 = out3_eval or evals[2] or {}
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    for out_num, card in [(1, out1), (2, out2), (3, out3)]:
        if card.get("status") in ("SKIPPED", "NOT_APPLICABLE"):
            continue
        is_valid, errs = validate_micro_judge_scorecard(card, output_num=out_num)
        card["is_valid_scorecard"] = is_valid
        if not is_valid:
            card["status"] = "FAIL"
            card["verdict"] = "FAIL"
            card["validation_errors"] = errs
        elif "status" not in card:
            card["status"] = (
                "PASS" if card.get("verdict") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
                else ("HITL_REQUIRED" if card.get("verdict") == "HITL_REQUIRED" else "FAIL")
            )
    update_evaluation_cache(k1, out1, k2, out2, k3, out3)
    if hasattr(judge, "reconciler") and judge.reconciler is not None:
        recon = judge.reconciler.reconcile(f1_text, f2_text, f3_data, meta)
    else:
        recon = {"status": "FAIL", "passed": False, "reason": "MISSING_JUDGE_RECONCILER"}
    return out1, out2, out3, recon


def _extract_output_eval(result: Dict[str, Any], key_suffix: str, passed: bool) -> Dict[str, Any]:
    from dual_pipeline.l0.evaluation_types import validate_evaluator_payload
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    e = result.get(f"output_{key_suffix}") or result.get(f"output{key_suffix}") or {
        "status": "FAIL", "verdict": "FAIL", "composite_score": 0,
        "reason": f"Missing explicit scorecard for output {key_suffix}; synthetic scores prohibited",
        "evaluated_modality": "SEMANTIC",
    }
    ok, errs, _ = validate_evaluator_payload(e)
    is_valid_card, card_errs = validate_micro_judge_scorecard(e, output_num=int(key_suffix))
    if not ok or not is_valid_card:
        e["status"] = e["verdict"] = "FAIL"
        e["composite_score"] = 0
        e["is_valid_scorecard"] = False
        e["validation_errors"] = (errs or []) + (card_errs or [])
    else:
        e["is_valid_scorecard"] = True
    return e


def _run_decomposed_judges(
    judge: DecomposedMultiJudge,
    f1_text: str,
    f2_text: str,
    f3_text: str,
    source_text: Optional[str],
    client: Any,
    mock: bool,
    meta: Dict[str, Any],
    evals: Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]],
    keys: Tuple[str, str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    src = source_text or f1_text
    try:
        f3_data = json.loads(f3_text) if isinstance(f3_text, str) else (f3_text or {})
        mutations = f3_data.get("graph_mutations", {}) if isinstance(f3_data, dict) else {}
        claims = f3_data.get("entities", []) or mutations.get("entity_upserts", [])
        facts = (
            (f3_data.get("decisions", []) + f3_data.get("risks", []))
            or (mutations.get("relationship_signals", []) + mutations.get("strategic_intent_signals", []))
        )
    except Exception:
        f3_data, claims, facts = {}, [], []

    if hasattr(judge, "judge_graph"):
        return _run_micro_judges_concurrently(judge, f1_text, f2_text, f3_text, src, client, mock, meta, evals, keys, f3_data)

    result = judge.evaluate_all(transcript=src, recap=f2_text, claims=claims, active_facts=facts)
    passed = result.get("passed", False)
    out1_eval = _extract_output_eval(result, "01", passed)
    out2_eval = _extract_output_eval(result, "02", passed)
    out3_eval = _extract_output_eval(result, "03", passed)
    recon = result.get("reconciliation") or {"status": "SUCCESS" if passed else "FAIL", "passed": bool(passed)}
    return out1_eval, out2_eval, out3_eval, recon


def _eval_output1(out1_eval: Dict[str, Any], s1: int) -> Tuple[ReleaseVerdict, bool]:
    reason = str(out1_eval.get("reason", ""))
    if out1_eval.get("status") == "SKIPPED":
        if reason in ("NOT_APPLICABLE_NOTES_MODE", "FROZEN_PRIOR_ARTIFACT"):
            return ReleaseVerdict.PASS, True
        return ReleaseVerdict.FAIL, False
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    is_valid, errs = validate_micro_judge_scorecard(out1_eval, output_num=1)
    if not is_valid:
        out1_eval["validation_errors"] = errs
        out1_eval["verdict"] = "FAIL"
        out1_eval["is_valid_scorecard"] = False
        return ReleaseVerdict.FAIL, False
    if "NOT_APPLICABLE_DOCUMENT_MODE" in reason:
        return ReleaseVerdict.FAIL, False
    v1 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, s1)
    if out1_eval.get("verdict") == "PASS_AUTHORIZED_EXCEPTION" and s1 >= 85:
        return ReleaseVerdict.PASS, True
    pass_flag = (v1 == ReleaseVerdict.PASS) and (
        out1_eval.get("verdict") in ("PASS", "PASS_AUTHORIZED_EXCEPTION")
    )
    return v1, pass_flag


def _eval_output_n(output_num: int, out_eval: Dict[str, Any], score: int, threshold: int) -> Tuple[ReleaseVerdict, bool]:
    reason = str(out_eval.get("reason", ""))
    if out_eval.get("status") == "SKIPPED":
        if reason == "FROZEN_PRIOR_ARTIFACT":
            return ReleaseVerdict.PASS, True
        return ReleaseVerdict.FAIL, False
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    is_valid, errs = validate_micro_judge_scorecard(out_eval, output_num=output_num)
    if not is_valid:
        out_eval["validation_errors"] = errs
        out_eval["verdict"] = "FAIL"
        out_eval["is_valid_scorecard"] = False
        return ReleaseVerdict.FAIL, False
    if "NOT_APPLICABLE_DOCUMENT_MODE" in reason:
        return ReleaseVerdict.FAIL, False
    verdict = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(output_num, score)
    if out_eval.get("verdict") == "PASS_AUTHORIZED_EXCEPTION" and score >= 85:
        return ReleaseVerdict.PASS, True
    pass_flag = (verdict == ReleaseVerdict.PASS) and (
        out_eval.get("verdict") in ("PASS", "PASS_AUTHORIZED_EXCEPTION")
    )
    return verdict, pass_flag



def _eval_pass_flags(
    out1_eval: Dict[str, Any],
    out2_eval: Dict[str, Any],
    out3_eval: Dict[str, Any],
    recon: Dict[str, Any],
) -> Tuple[bool, bool, bool, bool, bool]:
    s1 = out1_eval.get("composite_score", 0) or 0
    s2 = out2_eval.get("composite_score", 0) or 0
    s3 = out3_eval.get("composite_score", 0) or 0
    recon_pass = bool(recon.get("passed", False))

    v1_eval, j1_pass = _eval_output1(out1_eval, s1)
    v2_eval, j2_pass = _eval_output_n(2, out2_eval, s2, CANONICAL_RELEASE_POLICY.output2_pass_threshold)
    v3_eval, j3_pass = _eval_output_n(3, out3_eval, s3, CANONICAL_RELEASE_POLICY.output3_pass_threshold)
    judge_passed = bool(j1_pass and j2_pass and j3_pass and recon_pass)

    advisory_scores = bool(
        v1_eval in (ReleaseVerdict.PASS, ReleaseVerdict.PASS_ADVISORY)
        and (v2_eval == ReleaseVerdict.PASS or out2_eval.get("status") == "SKIPPED")
        and (v3_eval == ReleaseVerdict.PASS or out3_eval.get("status") == "SKIPPED")
    )
    advisory_v1 = (out1_eval.get("verdict") in ("PASS", "PASS_ADVISORY", "NOT_APPLICABLE") or (out1_eval.get("status") == "SKIPPED" and "DOCUMENT" not in str(out1_eval.get("reason", ""))))
    advisory_v2 = (out2_eval.get("verdict") in ("PASS", "PASS_ADVISORY", "HITL_REQUIRED", "NOT_APPLICABLE") or (out2_eval.get("status") == "SKIPPED" and "DOCUMENT" not in str(out2_eval.get("reason", "")))) and (s2 >= CANONICAL_RELEASE_POLICY.output2_pass_threshold or out2_eval.get("status") == "SKIPPED")
    advisory_v3 = (out3_eval.get("verdict") in ("PASS", "PASS_ADVISORY", "NOT_APPLICABLE") or out3_eval.get("status") == "SKIPPED") and (s3 >= CANONICAL_RELEASE_POLICY.output3_pass_threshold or out3_eval.get("status") == "SKIPPED")
    is_advisory = bool(advisory_scores and advisory_v1 and advisory_v2 and advisory_v3 and recon_pass)

    return j1_pass, j2_pass, j3_pass, judge_passed, is_advisory


def _build_final_decision(
    qa_report: Dict[str, Any],
    j1_pass: bool,
    j2_pass: bool,
    j3_pass: bool,
    recon_pass: bool,
    is_advisory: bool,
    manifest: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = metadata or {}
    q1 = qa_report["output_01_forensic_transcript"]
    q2 = qa_report["output_02_executive_recap"]
    q3 = qa_report["output_03_canonical_signal_projection"]

    # Invariant: Document skip markers cannot satisfy transcript or notes gates
    q1_reason = str(q1.get("reason", ""))
    q2_reason = str(q2.get("reason", ""))
    if "NOT_APPLICABLE_DOCUMENT_MODE" in q1_reason or (q1.get("status") == "SKIPPED" and q1_reason not in ("NOT_APPLICABLE_NOTES_MODE", "FROZEN_PRIOR_ARTIFACT")):
        q1_pass = False
    else:
        q1_pass = q1.get("passed", False) or q1.get("status") in ("PASS", "SKIPPED")

    if "NOT_APPLICABLE_DOCUMENT_MODE" in q2_reason or (q2.get("status") == "SKIPPED" and q2_reason != "FROZEN_PRIOR_ARTIFACT"):
        q2_pass = False
    else:
        q2_pass = q2.get("status") == "PASS" or q2.get("passed", False)

    q3_pass = q3.get("status") == "PASS" or q3.get("passed", False)
    cross_pass = qa_report.get("cross_artifact_consistency", {}).get("status") == "PASS"
    swarm_pass = bool((qa_report.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    all_q_pass = q1_pass and q2_pass and q3_pass and cross_pass and swarm_pass
    final_pass = all_q_pass and j1_pass and j2_pass and j3_pass and recon_pass

    spk_cov = meta.get("released_text_coverage") or q1.get("coverage_metrics") or {}
    named_word_cov = spk_cov.get("named_word_coverage_pct")
    indep_acc = meta.get("independent_speaker_accuracy_pct") or meta.get("speaker_attribution_accuracy_pct")
    indep_status = meta.get("independent_accuracy_status") or (
        "PASS" if (indep_acc is not None and indep_acc >= 98.0) else ("FAIL" if indep_acc is not None else "NOT_EVALUATED")
    )

    cov_pass = True
    total_words = spk_cov.get("released_dialogue_words", 0)
    if named_word_cov is not None and total_words > 0 and named_word_cov < 95.0:
        cov_pass = False

    requires_acc = bool(meta.get("require_speaker_qualification") or meta.get("require_independent_accuracy"))
    if requires_acc:
        acc_pass = bool(indep_acc is not None and indep_acc >= 98.0)
    else:
        acc_pass = True

    auth_authorized = bool(final_pass and cov_pass and (acc_pass if requires_acc else True))
    if not cov_pass or (requires_acc and not acc_pass):
        auth_authorized = False

    status = "PASS" if (final_pass and cov_pass and (acc_pass if requires_acc else True)) else ("PASS_ADVISORY" if (all_q_pass and is_advisory and cov_pass and (acc_pass if requires_acc else True)) else "FAIL")
    att = int(meta.get("attempt", 1))
    max_att = int(meta.get("max_attempts", 1))
    decision: Dict[str, Any] = {
        "attempt": att, "max_attempts": max_att,
        "output_1_deterministic_gates_pass": q1_pass, "output_1_llm_score_pass": j1_pass, "output_1_pass": q1_pass and j1_pass,
        "output_2_deterministic_gates_pass": q2_pass, "output_2_llm_score_pass": j2_pass, "output_2_pass": q2_pass and j2_pass,
        "output_3_deterministic_gates_pass": q3_pass, "output_3_llm_score_pass": j3_pass, "output_3_pass": q3_pass and j3_pass,
        "cross_artifact_consistency_pass": cross_pass and recon_pass,
        "named_word_coverage_pass": cov_pass,
        "independent_accuracy_status": indep_status,
        "final_pass": bool(final_pass and cov_pass and (acc_pass if requires_acc else True)),
        "authoritative_write_authorized": auth_authorized,
        "status": status,
    }
    if manifest:
        decision["artifact_manifest"] = manifest
        if manifest.get("judge_evaluation_digest"):
            decision["judge_evaluation_digest"] = manifest["judge_evaluation_digest"]
    decision["policy_version"] = "2.0.0"
    decision["rubric_version"] = "2.0"
    return decision


def _resolve_judge_tier_and_model(
    meta: Dict[str, Any],
    model: Optional[str],
    reasoning_effort: Optional[str],
    judge_tier: Optional[str],
    kwargs: Dict[str, Any],
) -> Tuple[str, str, str]:
    from dual_pipeline.model_registry import ModelTier, resolve_tier_model

    resolved_tier = str(
        judge_tier
        or kwargs.get("judge_tier")
        or meta.get("judge_tier")
        or kwargs.get("tier")
        or "production"
    ).strip().lower()

    if resolved_tier == "fast":
        tier_model, tier_effort = resolve_tier_model(ModelTier.FAST_ROUTING)
    elif resolved_tier == "standard":
        tier_model, tier_effort = resolve_tier_model(ModelTier.FRONTIER_REASONING)
    else:
        resolved_tier = "production"
        tier_model, tier_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)

    actual_model = model or tier_model
    actual_effort = reasoning_effort or tier_effort or "high"

    has_explicit_tier = bool(judge_tier or meta.get("judge_tier") or kwargs.get("judge_tier") or kwargs.get("tier"))
    if not has_explicit_tier and model:
        fast_m, _ = resolve_tier_model(ModelTier.FAST_ROUTING)
        std_m, _ = resolve_tier_model(ModelTier.FRONTIER_REASONING)
        prod_m, _ = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        if actual_model == fast_m:
            resolved_tier = "fast"
        elif actual_model == std_m:
            resolved_tier = "standard"
        elif actual_model == prod_m:
            resolved_tier = "production"
        else:
            resolved_tier = "custom"

    return resolved_tier, actual_model, actual_effort


def _apply_partial_qa_skips(
    qa_report: Dict[str, Any],
    evals: Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]],
    meta: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Pre-populate failed deliverables with skipped failure scorecards to prevent ungrounded LLM evaluation."""
    out1_eval, out2_eval, out3_eval = evals
    q1_pass = bool(qa_report.get("output_01_forensic_transcript", {}).get("passed", False) or qa_report.get("output_01_forensic_transcript", {}).get("status") in ("PASS", "SKIPPED"))
    q2_pass = bool(qa_report.get("output_02_executive_recap", {}).get("passed", False) or qa_report.get("output_02_executive_recap", {}).get("status") in ("PASS", "SKIPPED"))
    q3_pass = bool(qa_report.get("output_03_canonical_signal_projection", {}).get("passed", False) or qa_report.get("output_03_canonical_signal_projection", {}).get("status") in ("PASS", "SKIPPED"))

    def _fail_skip() -> Dict[str, Any]:
        return {
            "status": "SKIPPED",
            "composite_score": None,
            "verdict": "FAIL",
            "reason": "DETERMINISTIC_QA_FAILED",
            "is_valid_scorecard": False,
        }

    if not q1_pass and not _is_notes_mode(meta) and out1_eval is None:
        out1_eval = _fail_skip()
    if not q2_pass and out2_eval is None:
        out2_eval = _fail_skip()
    if not q3_pass and out3_eval is None:
        out3_eval = _fail_skip()
    return out1_eval, out2_eval, out3_eval


def _record_diagnostics_if_needed(
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
    meta: Dict[str, Any],
) -> None:
    try:
        from pathlib import Path
        from .judge_failure_diagnostics import JudgeFailureDiagnosticsCollector

        collector = JudgeFailureDiagnosticsCollector()
        out_dir = meta.get("output_dir")
        diag_path = Path(meta["diagnostics_path"]) if meta.get("diagnostics_path") else (Path(out_dir) / "judge_failure_diagnostics.json" if out_dir else None)
        if diag_path and diag_path.exists():
            collector.load_existing(diag_path)

        att = int(meta.get("attempt", 1))
        retry_cnt = int(meta.get("retry_count", max(0, att - 1)))
        collector.extract_from_qa_report(qa_report, attempt=att, retry_count=retry_cnt)
        collector.extract_from_judge_report(judge_report, attempt=att, retry_count=retry_cnt)
        collector.record_attempt(
            attempt=att,
            outputs={
                "output_1": final_decision.get("output_1_pass"),
                "output_2": final_decision.get("output_2_pass"),
                "output_3": final_decision.get("output_3_pass"),
            },
        )
        diag = collector.to_diagnostics_payload()
        judge_report["judge_failure_diagnostics"] = diag
        final_decision["judge_failure_diagnostics"] = diag

        current_failed_outputs = []
        if final_decision.get("output_1_pass") is False:
            current_failed_outputs.append("output_1")
        if final_decision.get("output_2_pass") is False:
            current_failed_outputs.append("output_2")
        if final_decision.get("output_3_pass") is False:
            current_failed_outputs.append("output_3")
        failed_outputs = sorted(set(current_failed_outputs))
        if failed_outputs:
            scoped_str = ",".join(sorted(set(failed_outputs)))
            meta["retry_scope"] = scoped_str
            final_decision["retry_scope"] = scoped_str
            judge_report["retry_scope"] = scoped_str
        diag_path = meta.get("diagnostics_path")
        if diag_path:
            collector.save_report(Path(diag_path))
        elif out_dir and (collector.failures or meta.get("write_diagnostics")):
            collector.save_report(Path(out_dir) / "judge_failure_diagnostics.json")
        elif meta.get("write_diagnostics"):
            collector.save_report()
    except Exception as exc:
        logger.debug("Judge failure diagnostics recording skipped: %s", exc)


def evaluate_judge_and_release(
    f1_text: str,
    f2_text: str,
    f3_text: str,
    source_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    mock: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    judge_tier: Optional[str] = None,
    auto_heal: bool = False,
    pinned_evaluations: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Execute Deterministic QA Gates, Decomposed Multi-Micro-Judge, and Final Release Decision."""
    meta = metadata or {}
    if "attempt" in kwargs:
        meta["attempt"] = int(kwargs["attempt"])
    if "max_attempts" in kwargs:
        meta["max_attempts"] = int(kwargs["max_attempts"])
    if "attempt" in meta:
        meta["retry_count"] = max(0, int(meta["attempt"]) - 1)

    resolved_tier, model, reasoning_effort = _resolve_judge_tier_and_model(
        meta, model, reasoning_effort, judge_tier, kwargs
    )

    healing_report = {}
    should_auto_heal = auto_heal or bool(meta.get("auto_heal"))
    if should_auto_heal:
        qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_text, source_text, meta, auto_heal=True)
        if qa_report.get("healed_artifacts"):
            healed = qa_report["healed_artifacts"]
            f1_text = healed.get("output_1", f1_text)
            f2_text = healed.get("output_2", f2_text)
            f3_text = healed.get("output_3", f3_text)
    else:
        qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_text, source_text, meta, auto_heal=False)

    cand_texts = {"output_1": f1_text, "output_2": f2_text, "output_3": f3_text}
    apply_frozen_to_qa_report(qa_report, meta.get("state_machine"), metadata=meta, candidate_texts=cand_texts)

    if not qa_report.get("passed", False) and not kwargs.get("force_judge_on_qa_fail", False) and not mock:
        q1_p = bool(qa_report.get("output_01_forensic_transcript", {}).get("passed", False) or qa_report.get("output_01_forensic_transcript", {}).get("status") in ("PASS", "SKIPPED"))
        q2_p = bool(qa_report.get("output_02_executive_recap", {}).get("passed", False) or qa_report.get("output_02_executive_recap", {}).get("status") in ("PASS", "SKIPPED"))
        q3_p = bool(qa_report.get("output_03_canonical_signal_projection", {}).get("passed", False) or qa_report.get("output_03_canonical_signal_projection", {}).get("status") in ("PASS", "SKIPPED"))
        has_partial = q1_p or q2_p or q3_p
        if not (kwargs.get("allow_partial_judge", True) and has_partial):
            res_qa, res_judge, res_final = build_short_circuit_eval(
                qa_report, f1_text, f2_text, f3_text, model, reasoning_effort, judge_tier=resolved_tier,
                metadata=meta,
            )
            _record_diagnostics_if_needed(res_qa, res_judge, res_final, meta)
            return res_qa, res_judge, res_final

    pinned = pinned_evaluations or kwargs.get("pinned") or {}
    out1_eval, out2_eval, out3_eval, k1, k2, k3 = resolve_pins_and_cache(
        f1_text, f2_text, f3_text, model, reasoning_effort, pinned, source_text=source_text,
        metadata=meta,
        archetype=meta.get("archetype") or meta.get("meeting_archetype"),
    )
    sm_eval = meta.get("state_machine")
    is_f1_frozen = bool(
        qa_report.get("output_01_forensic_transcript", {}).get("frozen")
        or (
            (meta.get("freeze_output_1") or meta.get("output3_only"))
            and (meta.get("output_1_sha256") or meta.get("artifact_manifest", {}).get("output_1_sha256"))
        )
        or (sm_eval and hasattr(sm_eval, "is_frozen") and sm_eval.is_frozen("output_1"))
    )
    is_f2_frozen = bool(
        qa_report.get("output_02_executive_recap", {}).get("frozen")
        or (
            meta.get("output3_only")
            and (meta.get("output_2_sha256") or meta.get("artifact_manifest", {}).get("output_2_sha256"))
        )
        or (sm_eval and hasattr(sm_eval, "is_frozen") and sm_eval.is_frozen("output_2"))
    )
    is_f3_frozen = bool(
        qa_report.get("output_03_canonical_signal_projection", {}).get("frozen")
        or (sm_eval and hasattr(sm_eval, "is_frozen") and sm_eval.is_frozen("output_3"))
    )
    if out1_eval is None and is_f1_frozen:
        qa_report.setdefault("output_01_forensic_transcript", {})["frozen"] = True
        out1_eval = _build_skip_eval(qa_report, "output_01_forensic_transcript", f1_text)
    if out2_eval is None and is_f2_frozen:
        qa_report.setdefault("output_02_executive_recap", {})["frozen"] = True
        out2_eval = _build_skip_eval(qa_report, "output_02_executive_recap", f2_text)
    if out3_eval is None and is_f3_frozen:
        qa_report.setdefault("output_03_canonical_signal_projection", {})["frozen"] = True
        out3_eval = _build_skip_eval(qa_report, "output_03_canonical_signal_projection", f3_text)

    if not qa_report.get("passed", False):
        out1_eval, out2_eval, out3_eval = _apply_partial_qa_skips(qa_report, (out1_eval, out2_eval, out3_eval), meta)
    needs_eval = out1_eval is None or out2_eval is None or out3_eval is None
    client, mock = _resolve_client(mock, model, reasoning_effort, needs_eval)
    if DecomposedMultiJudge is not None:
        judge = DecomposedMultiJudge(model=model, reasoning_effort=reasoning_effort)
    else:
        from dual_pipeline.llm_judge_core import DecomposedMultiJudge as _DMJ
        judge = _DMJ(model=model, reasoning_effort=reasoning_effort)


    out1_eval, out2_eval, out3_eval, recon = _run_decomposed_judges(
        judge,
        f1_text,
        f2_text,
        f3_text,
        source_text,
        client,
        mock,
        meta,
        (out1_eval, out2_eval, out3_eval),
        (k1, k2, k3),
    )

    j1_pass, j2_pass, j3_pass, judge_passed, is_advisory = _eval_pass_flags(
        out1_eval, out2_eval, out3_eval, recon
    )

    judge_status = "PASS" if judge_passed else ("PASS_ADVISORY" if is_advisory else "FAIL")
    judge_report = {
        "rubric_version": "2.0",
        "scorecard_schema_version": "2.0.0",
        "release_policy_version": "2.0.0",
        "judge_tier": resolved_tier,
        "judge_model": model,
        "evaluator_model": model,
        "reasoning_effort": reasoning_effort,
        "evaluation_mode": "DECOMPOSED_CONCURRENT",
        "status": judge_status,
        "overall_verdict": judge_status,
        "output_1_evaluation": out1_eval,
        "output_2_evaluation": out2_eval,
        "output_3_evaluation": out3_eval,
        "output_1_scorecard": out1_eval,
        "output_2_scorecard": out2_eval,
        "output_3_scorecard": out3_eval,
        "cross_artifact_reconciliation": recon,
        "overall_rubric_summary": (
            f"Decomposed multi-micro-judge evaluation: "
            f"Output 1 score={out1_eval.get('composite_score')}, "
            f"Output 2 score={out2_eval.get('composite_score')}, "
            f"Output 3 score={out3_eval.get('composite_score')}. "
            f"Reconciliation: {recon.get('status')}."
        ),
    }
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    judge_report["aggregate_composite_score"] = compute_quality_aggregate_score(judge_report)

    # Prosecutorial / Adversarial Probe integration
    adv_veto = False
    try:
        from dual_pipeline.llm_judge_core.adversarial_probe import BoundedAdversarialProbe
        f3_dict = json.loads(f3_text) if isinstance(f3_text, str) and f3_text.strip().startswith("{") else (f3_text if isinstance(f3_text, dict) else {})
        probe = BoundedAdversarialProbe()
        adv_res = probe.audit(f1_text, f2_text, f3_dict, mock=mock)
        judge_report["adversarial_audit"] = adv_res.to_dict()
        if adv_res.hard_veto:
            adv_veto = True
    except Exception as adv_exc:
        logger.debug("Adversarial probe skipped or failed gracefully: %s", adv_exc)

    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )
    eval_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = eval_digest

    manifest = build_artifact_manifest(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        model=model,
        reasoning_effort=reasoning_effort or "medium",
        judge_eval_digest=eval_digest,
    )

    final_decision = _build_final_decision(
        qa_report, j1_pass, j2_pass, j3_pass, bool(recon.get("passed", False)), is_advisory, manifest=manifest, metadata=meta
    )
    final_decision["judge_tier"] = resolved_tier
    final_decision["judge_model"] = model
    if adv_veto:
        final_decision["status"] = "FAIL"
        final_decision["authoritative_write_authorized"] = False
        final_decision["adversarial_veto"] = True

    # Adaptive uncertainty sampling and borderline clarification
    swarm_ok = bool((qa_report.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    det_qa_ok = bool(qa_report.get("passed", False)) and swarm_ok
    cov_ok = bool(final_decision.get("named_word_coverage_pass", True))
    acc_ok = final_decision.get("independent_accuracy_status") != "FAIL"
    can_clarify = not adv_veto and det_qa_ok and cov_ok and acc_ok
    if can_clarify and (
        final_decision.get("status") == "FAIL"
        or any(e.get("verdict") == "HITL_REQUIRED" for e in (out1_eval, out2_eval, out3_eval) if isinstance(e, dict))
    ):
        try:
            from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
            clarified = evaluate_judge_clarification(judge_report, final_decision, meta)
            if clarified:
                final_decision["status"] = "PASS_AUTHORIZED_EXCEPTION"
                final_decision["authoritative_write_authorized"] = True
        except Exception as clar_exc:
            logger.debug("Judge clarification skipped or failed gracefully: %s", clar_exc)

    _record_diagnostics_if_needed(qa_report, judge_report, final_decision, meta)
    return qa_report, judge_report, final_decision
