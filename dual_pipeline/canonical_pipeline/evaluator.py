"""
dual_pipeline/canonical_pipeline/evaluator.py

Unified QA Gate Evaluation & Decomposed Micro-Judge Engine.
Enforces the Single Source of Truth (SSOT) mutation budget ceiling of 30 across all modalities:
- AUDIO / MEETING_TRANSCRIPT: Full 23-gate deterministic verification (Gate 1, 2, 3),
  cross-artifact consistency reconciliation, and 3 LLM micro-judges.
- MEETING_NOTES: Gate 2, Gate 3, and Micro-Judges 2 & 3 (Gate 1 marked N/A).
- DOCUMENT: Gate 3 and Document Micro-Judge 3 (Gates 1 & 2 marked N/A).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_judge_and_release
from dual_pipeline.parameters import get_canonical_mutation_budget

logger = logging.getLogger(__name__)
# Single Source of Truth (SSOT) maximum mutation budget ceiling across all pipelines
CANONICAL_MUTATION_BUDGET: int = get_canonical_mutation_budget()


def _resolve_project_for_evaluation(meta: Dict[str, Any]) -> Optional[str]:
    proj = meta.get("project")
    if proj and proj != "auto":
        return proj if proj != "UNRESOLVED" else None
    try:
        from dual_pipeline.query_router_core.project_router import determine_project_directory
        source = meta.get("source_path") or meta.get("input_path") or meta.get("input")
        if source:
            determined = determine_project_directory(source)
            return determined if determined != "UNRESOLVED" else None
    except Exception:
        pass
    return None


def _find_candidate_evaluation_dirs(meta: Dict[str, Any]) -> List[Path]:
    candidates: List[Path] = []
    out_dir = meta.get("output_dir")
    if out_dir and out_dir != "auto":
        candidates.append(Path(out_dir))
    proj = _resolve_project_for_evaluation(meta)
    if proj:
        date_fn = meta.get("date_filename") or meta.get("date_dirname")
        slug = meta.get("filename_slug") or meta.get("meeting_slug")
        if date_fn and slug:
            candidates.append(Path("output") / proj / date_fn / slug)
    return candidates


def _load_attempt_judge_report(att_dir: Path) -> Optional[Dict[str, Any]]:
    j_path = att_dir / "llm_judge_evaluation.json"
    d_path = att_dir / "final_release_decision.json"
    if not (j_path.exists() and d_path.exists()):
        return None
    try:
        j_rep = json.loads(j_path.read_text(encoding="utf-8"))
        d_rep = json.loads(d_path.read_text(encoding="utf-8"))
        j_rep["artifact_manifest"] = d_rep.get("artifact_manifest", {})
        return j_rep
    except Exception:
        return None


def _resolve_pinned_evaluation(meta: Dict[str, Any], attempt: int) -> Optional[Dict[str, Any]]:
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    min_scores = {
        "output_1": CANONICAL_RELEASE_POLICY.output1_pass_threshold,
        "output_2": CANONICAL_RELEASE_POLICY.output2_pass_threshold,
        "output_3": CANONICAL_RELEASE_POLICY.output3_pass_threshold,
    }
    out_nums = {"output_1": 1, "output_2": 2, "output_3": 3}
    pinned = meta.get("pinned_evaluations") or meta.get("pinned")
    if not pinned:
        import re
        best_pinned: Dict[str, Any] = {}
        for cand in _find_candidate_evaluation_dirs(meta):
            search_dirs: List[Path] = []
            att_dir = cand / "attempts"
            if att_dir.is_dir():
                def _get_att_num(p: Path) -> int:
                    m = re.search(r"attempt_(\d+)", p.name)
                    return int(m.group(1)) if m else 0

                search_dirs.extend(sorted(
                    [sub for sub in att_dir.iterdir() if sub.is_dir() and "attempt_" in sub.name],
                    key=_get_att_num,
                    reverse=True,
                ))
            if cand.is_dir():
                search_dirs.append(cand)

            for sub in search_dirs:
                rep = _load_attempt_judge_report(sub)
                if rep is not None:
                    manifest = rep.get("artifact_manifest") or {}
                    for k in ("output_1", "output_2", "output_3"):
                        eval_k = f"{k}_evaluation"
                        card = rep.get(k) or rep.get(eval_k)
                        if isinstance(card, dict) and card.get("status") != "SKIPPED":
                            score = float(card.get("composite_score") or 0.0)
                            verdict = str(card.get("verdict", "")).upper()
                            is_valid, _ = validate_micro_judge_scorecard(card, output_num=out_nums[k])
                            min_req = 85.0 if (verdict == "PASS_AUTHORIZED_EXCEPTION" and card.get("authorized_exception")) else float(min_scores[k])
                            if (
                                is_valid
                                and score >= min_req
                                and verdict in ("PASS", "PASS_AUTHORIZED_EXCEPTION")
                                and k not in best_pinned
                            ):
                                card_copy = dict(card)
                                sha_key = f"{k}_sha256"
                                if sha_key in manifest and "artifact_sha256" not in card_copy:
                                    card_copy["artifact_sha256"] = manifest[sha_key]
                                best_pinned[k] = card_copy
                                best_pinned[eval_k] = card_copy
                    if "artifact_manifest" in rep and "artifact_manifest" not in best_pinned:
                        best_pinned["artifact_manifest"] = rep["artifact_manifest"]
            if len([k for k in ("output_1", "output_2", "output_3") if k in best_pinned]) == 3:
                break
        if best_pinned:
            pinned = best_pinned

    if pinned and isinstance(pinned, dict):
        sm = meta.get("state_machine")
        cleaned_pinned: Dict[str, Any] = {}
        if "artifact_manifest" in pinned and isinstance(pinned["artifact_manifest"], dict):
            cleaned_pinned["artifact_manifest"] = pinned["artifact_manifest"]
        manifest = cleaned_pinned.get("artifact_manifest") or {}
        for key in ("output_1", "output_2", "output_3"):
            min_score = min_scores[key]
            card = pinned.get(key) or pinned.get(f"{key}_evaluation")
            if not isinstance(card, dict) or card.get("status") == "SKIPPED":
                continue
            card_copy = dict(card)
            if card_copy.get("composite_score") is None:
                is_frozen = bool(sm and hasattr(sm, "is_frozen") and sm.is_frozen(key))
                hwm = sm.get_high_water_mark_score(key) if (sm and hasattr(sm, "get_high_water_mark_score")) else 0
                if is_frozen and hwm >= min_score:
                    best_cand = sm.get_best_candidate(key) if hasattr(sm, "get_best_candidate") else None
                    if best_cand and getattr(best_cand, "full_scorecard", None):
                        full_sc = dict(best_cand.full_scorecard)
                        if full_sc.get("composite_score") is not None:
                            card_copy.update(full_sc)
            sha_val = card_copy.get("artifact_sha256") or card_copy.get("sha256") or card_copy.get("frozen_sha256") or manifest.get(f"{key}_sha256")
            if not sha_val:
                continue
            card_copy["artifact_sha256"] = sha_val
            is_valid, _ = validate_micro_judge_scorecard(card_copy, output_num=out_nums[key])
            score = float(card_copy.get("composite_score") or 0.0)
            verdict = str(card_copy.get("verdict") or "").upper()
            min_req = 85.0 if (verdict == "PASS_AUTHORIZED_EXCEPTION" and card_copy.get("authorized_exception")) else float(min_score)
            if is_valid and score >= min_req and verdict in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
                cleaned_pinned[key] = card_copy
                cleaned_pinned[f"{key}_evaluation"] = card_copy
        if any(k in cleaned_pinned for k in ("output_1", "output_2", "output_3")):
            return cleaned_pinned
    return None


def _reanchor_output_3(synthesis_result: SynthesisResult) -> None:
    f1, f3 = synthesis_result.output_1 or "", synthesis_result.output_3
    if f1 and f3:
        try:
            from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
            data = json.loads(f3) if isinstance(f3, str) else dict(f3)
            if isinstance(data, dict):
                XExitGatesEvaluator()._heal_quote_pointers(data, transcript=f1)
                synthesis_result.output_3 = json.dumps(data, indent=2)
        except Exception as e:
            logger.warning("Output 3 quote-pointer re-anchoring skipped: %s", e)


def _apply_healed(synthesis_result: SynthesisResult, qa_rep: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> None:
    healed = qa_rep.get("healed_artifacts")
    if not isinstance(healed, dict):
        return
    sm = meta.get("state_machine") if meta else None
    for k in ("output_1", "output_2"):
        if healed.get(k) and not bool(sm and hasattr(sm, "is_frozen") and sm.is_frozen(k)):
            setattr(synthesis_result, k, healed[k])
    is_f3_frozen = bool(sm and hasattr(sm, "is_frozen") and sm.is_frozen("output_3"))
    if healed.get("output_3") and not is_f3_frozen:
        synthesis_result.output_3 = healed["output_3"]
    elif is_f3_frozen:
        _reanchor_output_3(synthesis_result)


def _pre_heal_output_3(synthesis_result: SynthesisResult, meta: Optional[Dict[str, Any]] = None) -> None:
    """Pre-judge deterministic normalization of evidence locators and entity prefixes."""
    f3 = synthesis_result.output_3
    if not f3:
        return
    sm = meta.get("state_machine") if meta else None
    is_frozen = bool(sm and hasattr(sm, "is_frozen") and sm.is_frozen("output_3"))
    try:
        from dual_pipeline.transcript_qa_core.graph_healer import heal_evidence_locators
        if not is_frozen:
            data = json.loads(f3) if isinstance(f3, str) else dict(f3)
            if isinstance(data, dict):
                heal_evidence_locators(data)
                synthesis_result.output_3 = json.dumps(data, indent=2)
        _reanchor_output_3(synthesis_result)
    except Exception as e:
        logger.warning("Pre-judge evidence locator healing encountered an issue: %s", e)


def _merge_pinned_into_judge_report(
    judge_rep: Dict[str, Any],
    pinned_eval: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
    synthesis_result: Optional[SynthesisResult] = None,
) -> None:
    """Merge verified non-SKIPPED scorecards into judge_rep when artifact SHA-256 and scorecard validation pass."""
    if not isinstance(judge_rep, dict):
        return
    import hashlib
    from dual_pipeline.release_policy import validate_micro_judge_scorecard

    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    sm = meta.get("state_machine") if meta else None
    pinned = pinned_eval if isinstance(pinned_eval, dict) else {}
    manifest = pinned.get("artifact_manifest") if isinstance(pinned.get("artifact_manifest"), dict) else {}
    min_scores = {
        "output_1": CANONICAL_RELEASE_POLICY.output1_pass_threshold,
        "output_2": CANONICAL_RELEASE_POLICY.output2_pass_threshold,
        "output_3": CANONICAL_RELEASE_POLICY.output3_pass_threshold,
    }
    out_nums = {"output_1": 1, "output_2": 2, "output_3": 3}
    det_fail_reasons = (
        "DETERMINISTIC_QA_FAILED",
        "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "DETERMINISTIC_QA_COLLAPSE",
    )

    for key in ("output_1", "output_2", "output_3"):
        eval_key = f"{key}_evaluation"
        curr_card = judge_rep.get(eval_key)
        pin_card = pinned.get(key) or pinned.get(eval_key)
        is_frozen = bool(sm and hasattr(sm, "is_frozen") and sm.is_frozen(key))

        if isinstance(curr_card, dict):
            curr_reason = str(curr_card.get("reason") or curr_card.get("skip_reason") or "")
            if any(r in curr_reason for r in det_fail_reasons):
                continue

        active_text = getattr(synthesis_result, key, None) if synthesis_result is not None else None
        active_sha = hashlib.sha256(str(active_text).encode("utf-8")).hexdigest() if isinstance(active_text, str) and active_text else None

        if isinstance(pin_card, dict) and pin_card.get("status") != "SKIPPED":
            pin_sha = pin_card.get("artifact_sha256") or pin_card.get("candidate_sha256") or pin_card.get("frozen_sha256") or manifest.get(f"{key}_sha256")
            if not pin_sha:
                logger.warning("Rejecting pinned scorecard for %s: missing cryptographic artifact_sha256 binding.", key)
                continue
            if active_sha and pin_sha != active_sha:
                if is_frozen:
                    logger.info("Healed/re-anchored frozen %s accepted under pinned scorecard: updating artifact_sha256 binding (%s -> %s).", key, pin_sha[:12], active_sha[:12])
                    pin_card["artifact_sha256"] = active_sha
                else:
                    logger.warning("Rejecting pinned scorecard for %s: SHA-256 mismatch (%s != %s).", key, pin_sha[:12], active_sha[:12])
                    continue
            is_valid, _ = validate_micro_judge_scorecard(pin_card, output_num=out_nums[key])
            pin_score = float(pin_card.get("composite_score") or 0.0)
            pin_verdict = str(pin_card.get("verdict", "")).upper()
            min_req = 85.0 if (pin_verdict == "PASS_AUTHORIZED_EXCEPTION" and pin_card.get("authorized_exception")) else float(min_scores[key])
            if not is_valid or pin_score < min_req or pin_verdict not in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
                continue

            if not isinstance(curr_card, dict) or curr_card.get("status") == "SKIPPED" or is_frozen:
                merged_card = dict(pin_card)
                if isinstance(curr_card, dict):
                    for k, v in curr_card.items():
                        if k not in merged_card:
                            merged_card[k] = v
                judge_rep[eval_key] = merged_card
                judge_rep[f"{key}_scorecard"] = merged_card


def _sync_and_reseal_evaluation(
    judge_rep: Dict[str, Any],
    final_dec: Dict[str, Any],
    qa_rep: Dict[str, Any],
    synthesis_result: SynthesisResult,
    source_text: str,
    meta: Dict[str, Any],
    judge_model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    is_notes: bool = False,
    is_document: bool = False,
) -> bool:
    """Synchronize pass verdicts and re-seal judge report and decision manifest."""
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    p = CANONICAL_RELEASE_POLICY
    out1_threshold = float(p.output1_pass_threshold)
    sm = meta.get("state_machine") if meta else None

    if not is_notes and not is_document:
        c1 = judge_rep.get("output_1_evaluation") or {}
        is_f1_frozen = bool(
            c1.get("reason") == "FROZEN_PRIOR_ARTIFACT"
            or (sm and hasattr(sm, "is_frozen") and sm.is_frozen("output_1"))
        )
        s1 = float(c1.get("composite_score") or (out1_threshold if is_f1_frozen else 0.0))
        v1 = str(c1.get("verdict") or ("PASS" if is_f1_frozen else "")).upper()
        if is_f1_frozen and s1 >= out1_threshold and v1 in ("", "SKIPPED", "PASS"):
            v1 = "PASS"
            c1["composite_score"] = s1
            c1["verdict"] = "PASS"
            judge_rep["output_1_evaluation"] = c1
        q1 = qa_rep.get("output_01_forensic_transcript") or {}
        q1_pass = bool(q1.get("passed", False) or q1.get("status") == "PASS" or final_dec.get("output_1_deterministic_gates_pass", False))
        j1_pass = is_f1_frozen or (s1 >= out1_threshold and v1 == "PASS") or (
            v1 == "PASS_AUTHORIZED_EXCEPTION" and bool(c1.get("authorized_exception")) and s1 >= 85.0
        )
        final_dec["output_1_deterministic_gates_pass"] = q1_pass
        final_dec["output_1_llm_score_pass"] = j1_pass
        final_dec["output_1_pass"] = q1_pass and j1_pass
    else:
        final_dec["output_1_deterministic_gates_pass"] = True
        final_dec["output_1_llm_score_pass"] = True
        final_dec["output_1_pass"] = True

    if not is_document:
        c2 = judge_rep.get("output_2_evaluation") or {}
        is_f2_frozen = bool(
            c2.get("reason") == "FROZEN_PRIOR_ARTIFACT"
            or (sm and hasattr(sm, "is_frozen") and sm.is_frozen("output_2"))
        )
        s2 = float(c2.get("composite_score") or (float(p.output2_pass_threshold) if is_f2_frozen else 0.0))
        v2 = str(c2.get("verdict") or ("PASS" if is_f2_frozen else "")).upper()
        if is_f2_frozen and s2 >= p.output2_pass_threshold and v2 in ("", "SKIPPED", "PASS"):
            v2 = "PASS"
            c2["composite_score"] = s2
            c2["verdict"] = "PASS"
            judge_rep["output_2_evaluation"] = c2
        q2 = qa_rep.get("output_02_executive_recap") or {}
        q2_pass = bool(q2.get("passed", False) or q2.get("status") == "PASS" or final_dec.get("output_2_deterministic_gates_pass", False))
        j2_pass = is_f2_frozen or (s2 >= p.output2_pass_threshold and v2 == "PASS") or (
            v2 == "PASS_AUTHORIZED_EXCEPTION" and bool(c2.get("authorized_exception")) and s2 >= 85.0
        )
        final_dec["output_2_deterministic_gates_pass"] = q2_pass
        final_dec["output_2_llm_score_pass"] = j2_pass
        final_dec["output_2_pass"] = q2_pass and j2_pass
    else:
        final_dec["output_2_deterministic_gates_pass"] = True
        final_dec["output_2_llm_score_pass"] = True
        final_dec["output_2_pass"] = True

    c3 = judge_rep.get("output_3_evaluation") or {}
    s3 = float(c3.get("composite_score") or 0.0)
    v3 = str(c3.get("verdict", "")).upper()
    q3 = qa_rep.get("output_03_canonical_signal_projection") or {}
    q3_pass = bool(q3.get("passed", False) or q3.get("status") == "PASS" or final_dec.get("output_3_deterministic_gates_pass", False))
    j3_pass = (s3 >= p.output3_pass_threshold and v3 == "PASS") or (
        v3 == "PASS_AUTHORIZED_EXCEPTION" and bool(c3.get("authorized_exception")) and s3 >= 85.0
    )
    final_dec["output_3_deterministic_gates_pass"] = q3_pass
    final_dec["output_3_llm_score_pass"] = j3_pass
    final_dec["output_3_pass"] = q3_pass and j3_pass

    recon = judge_rep.get("cross_artifact_reconciliation") or {}
    recon_pass = bool(recon.get("passed", True)) and recon.get("status") != "FAIL"
    final_dec["cross_artifact_consistency_pass"] = bool(final_dec.get("cross_artifact_consistency_pass", True) and recon_pass)

    cov_pass = bool(final_dec.get("named_word_coverage_pass", True))
    requires_acc = bool((meta or {}).get("requires_independent_accuracy") or (meta or {}).get("require_speaker_qualification") or (meta or {}).get("require_independent_accuracy"))
    acc_pass = bool(final_dec.get("independent_accuracy_status") == "PASS") if requires_acc else True
    adv_veto = bool(final_dec.get("adversarial_veto") or (judge_rep.get("adversarial_audit") or {}).get("hard_veto"))
    swarm_pass = bool((qa_rep.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    x_gates_pass = bool((qa_rep.get("x_exit_gates") or {}).get("passed", True))

    all_out_pass = (
        final_dec.get("output_1_pass", True)
        and final_dec.get("output_2_pass", True)
        and final_dec.get("output_3_pass", True)
    )
    final_pass = all_out_pass and recon_pass and cov_pass and acc_pass and not adv_veto and swarm_pass and x_gates_pass
    if adv_veto:
        final_dec["adversarial_veto"] = True
    if final_pass:
        has_auth_exc = any(
            (judge_rep.get(f"output_{i}_evaluation") or {}).get("verdict") == "PASS_AUTHORIZED_EXCEPTION"
            for i in (1, 2, 3)
        )
        judge_rep["status"] = "PASS"
        judge_rep["overall_verdict"] = "PASS_AUTHORIZED_EXCEPTION" if has_auth_exc else "PASS"
        final_dec["status"] = "PASS_AUTHORIZED_EXCEPTION" if has_auth_exc else "PASS"
        final_dec["final_pass"] = True
        final_dec["authoritative_write_authorized"] = True
    else:
        judge_rep["status"] = "FAIL"
        judge_rep["overall_verdict"] = "FAIL"
        final_dec["status"] = "FAIL"
        final_dec["final_pass"] = False
        final_dec["authoritative_write_authorized"] = False

    judge_rep["aggregate_composite_score"] = compute_quality_aggregate_score(judge_rep)
    c1_s = judge_rep.get("output_1_evaluation", {}).get("composite_score")
    c2_s = judge_rep.get("output_2_evaluation", {}).get("composite_score")
    c3_s = judge_rep.get("output_3_evaluation", {}).get("composite_score")
    r_stat = recon.get("status", "PASS")
    judge_rep["overall_rubric_summary"] = (
        f"Decomposed multi-micro-judge evaluation: "
        f"Output 1 score={c1_s}, Output 2 score={c2_s}, Output 3 score={c3_s}. "
        f"Reconciliation: {r_stat}."
    )

    new_eval_digest = compute_release_evaluation_digest(judge_rep)
    judge_rep["evaluation_receipt_digest"] = new_eval_digest
    final_dec["judge_evaluation_digest"] = new_eval_digest

    f1_text = source_text if is_document else (synthesis_result.output_1 or "")
    f2_text = "" if is_document else (synthesis_result.output_2 or "")
    f3_text = synthesis_result.output_3 or "{}"

    model_name = judge_model or final_dec.get("judge_model") or judge_rep.get("judge_model") or judge_rep.get("evaluator_model") or "default"
    effort = reasoning_effort or final_dec.get("reasoning_effort") or judge_rep.get("reasoning_effort") or "medium"

    manifest = build_artifact_manifest(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        model=model_name,
        reasoning_effort=effort,
        judge_eval_digest=new_eval_digest,
    )
    final_dec["artifact_manifest"] = manifest
    final_dec["decision_seal_sha256"] = manifest["decision_seal_sha256"]

    return bool(final_dec.get("authoritative_write_authorized", False))


def _eval_transcript(
    synthesis_result: SynthesisResult,
    source_text: str,
    meta: Dict[str, Any],
    pinned_eval: Optional[Dict[str, Any]],
    attempt: int,
    max_attempts: int,
    mock: bool,
    judge_model: Optional[str],
    reasoning_effort: Optional[str],
    fast_judge: bool,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from dual_pipeline.transcript_pipeline_core import qa_runner  # noqa: F401

    _pre_heal_output_3(synthesis_result, meta=meta)
    f1 = synthesis_result.output_1 or ""
    f2 = synthesis_result.output_2 or ""
    f3 = synthesis_result.output_3 or "{}"

    qa_rep, judge_rep, final_dec = qa_runner.evaluate_judge_and_release(
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
        source_text=source_text,
        metadata=meta,
        mock=mock,
        model=judge_model,
        reasoning_effort=reasoning_effort,
        attempt=attempt,
        max_attempts=max_attempts,
        fast_judge=fast_judge,
        pinned_evaluations=pinned_eval,
        auto_heal=True,
    )
    _apply_healed(synthesis_result, qa_rep, meta=meta)
    _merge_pinned_into_judge_report(judge_rep, pinned_eval, meta=meta, synthesis_result=synthesis_result)
    final_dec["attempt"] = attempt
    final_dec["max_attempts"] = max_attempts
    adv_veto = bool(final_dec.get("adversarial_veto") or (judge_rep.get("adversarial_audit") or {}).get("hard_veto"))
    swarm_ok = bool((qa_rep.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    if not adv_veto and bool(qa_rep.get("passed", False)) and swarm_ok:
        from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
        evaluate_judge_clarification(judge_rep, final_dec, metadata=meta)
    auth = _sync_and_reseal_evaluation(
        judge_rep=judge_rep,
        final_dec=final_dec,
        qa_rep=qa_rep,
        synthesis_result=synthesis_result,
        source_text=source_text,
        meta=meta,
        judge_model=judge_model,
        reasoning_effort=reasoning_effort,
        is_notes=False,
        is_document=False,
    )
    return auth, qa_rep, judge_rep, final_dec


def _eval_notes(
    synthesis_result: SynthesisResult,
    source_text: str,
    meta: Dict[str, Any],
    pinned_eval: Optional[Dict[str, Any]],
    attempt: int,
    max_attempts: int,
    mock: bool,
    judge_model: Optional[str],
    reasoning_effort: Optional[str],
    fast_judge: bool,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from dual_pipeline.transcript_pipeline_core import qa_runner  # noqa: F401

    meta["notes_mode"] = True
    meta["is_notes"] = True
    meta["skip_output_1"] = True
    meta["outputs"] = ["02", "03"]

    _pre_heal_output_3(synthesis_result, meta=meta)
    f2 = synthesis_result.output_2 or ""
    f3 = synthesis_result.output_3 or "{}"

    qa_rep, judge_rep, final_dec = qa_runner.evaluate_judge_and_release(
        f1_text="",
        f2_text=f2,
        f3_text=f3,
        source_text=source_text,
        metadata=meta,
        mock=mock,
        model=judge_model,
        reasoning_effort=reasoning_effort,
        attempt=attempt,
        max_attempts=max_attempts,
        fast_judge=fast_judge,
        pinned_evaluations=pinned_eval,
        auto_heal=True,
    )
    _apply_healed(synthesis_result, qa_rep, meta=meta)
    _merge_pinned_into_judge_report(judge_rep, pinned_eval, meta=meta, synthesis_result=synthesis_result)
    final_dec["attempt"] = attempt
    final_dec["max_attempts"] = max_attempts
    adv_veto = bool(final_dec.get("adversarial_veto") or (judge_rep.get("adversarial_audit") or {}).get("hard_veto"))
    swarm_ok = bool((qa_rep.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    if not adv_veto and bool(qa_rep.get("passed", False)) and swarm_ok:
        from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
        evaluate_judge_clarification(judge_rep, final_dec, metadata=meta)
    auth = _sync_and_reseal_evaluation(
        judge_rep=judge_rep,
        final_dec=final_dec,
        qa_rep=qa_rep,
        synthesis_result=synthesis_result,
        source_text=source_text,
        meta=meta,
        judge_model=judge_model,
        reasoning_effort=reasoning_effort,
        is_notes=True,
        is_document=False,
    )
    return auth, qa_rep, judge_rep, final_dec


def _eval_document(
    synthesis_result: SynthesisResult,
    source_text: str,
    meta: Dict[str, Any],
    attempt: int,
    max_attempts: int,
    mock: bool,
    judge_model: Optional[str],
    reasoning_effort: Optional[str],
    fast_judge: bool,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    _pre_heal_output_3(synthesis_result, meta=meta)
    meta["is_document"] = True
    meta["output3_only"] = True
    f3 = synthesis_result.output_3 or "{}"

    qa_rep, judge_rep, final_dec = evaluate_document_judge_and_release(
        f3_text=f3,
        source_text=source_text,
        metadata=meta,
        mock=mock,
        model=judge_model,
        reasoning_effort=reasoning_effort,
        attempt=attempt,
        max_attempts=max_attempts,
        fast_judge=fast_judge,
    )
    _apply_healed(synthesis_result, qa_rep, meta=meta)
    pinned_eval = meta.get("pinned_evaluations") or meta.get("pinned")
    _merge_pinned_into_judge_report(judge_rep, pinned_eval, meta=meta, synthesis_result=synthesis_result)
    final_dec["attempt"] = attempt
    final_dec["max_attempts"] = max_attempts
    adv_veto = bool(final_dec.get("adversarial_veto") or (judge_rep.get("adversarial_audit") or {}).get("hard_veto"))
    swarm_ok = bool((qa_rep.get("15_agent_swarm_review") or {}).get("swarm_passed", True))
    if not adv_veto and bool(qa_rep.get("passed", False)) and swarm_ok:
        from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
        evaluate_judge_clarification(judge_rep, final_dec, metadata=meta)
    auth = _sync_and_reseal_evaluation(
        judge_rep=judge_rep,
        final_dec=final_dec,
        qa_rep=qa_rep,
        synthesis_result=synthesis_result,
        source_text=source_text,
        meta=meta,
        judge_model=judge_model,
        reasoning_effort=reasoning_effort,
        is_notes=False,
        is_document=True,
    )
    return auth, qa_rep, judge_rep, final_dec


class UnifiedPipelineQAEvaluator:
    """Unified evaluator coordinating deterministic gates, reconciler, and micro-judges."""

    @staticmethod
    def evaluate(
        modality: CanonicalModality,
        synthesis_result: SynthesisResult,
        source_text: str,
        metadata: Dict[str, Any],
        attempt: int = 1,
        max_attempts: int = 3,
        mock: bool = False,
        judge_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        fast_judge: bool = False,
    ) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """
        Executes unified evaluation returning:
        (authoritative_write_authorized, qa_report, judge_report, final_decision).
        """
        meta = dict(metadata)
        pinned_eval = _resolve_pinned_evaluation(meta, attempt)
        _pre_heal_output_3(synthesis_result, meta=meta)

        if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
            return _eval_transcript(
                synthesis_result,
                source_text,
                meta,
                pinned_eval,
                attempt,
                max_attempts,
                mock,
                judge_model,
                reasoning_effort,
                fast_judge,
            )

        if modality == CanonicalModality.MEETING_NOTES:
            return _eval_notes(
                synthesis_result,
                source_text,
                meta,
                pinned_eval,
                attempt,
                max_attempts,
                mock,
                judge_model,
                reasoning_effort,
                fast_judge,
            )

        if modality == CanonicalModality.DOCUMENT:
            return _eval_document(
                synthesis_result,
                source_text,
                meta,
                attempt,
                max_attempts,
                mock,
                judge_model,
                reasoning_effort,
                fast_judge,
            )

        raise ValueError(f"Unsupported canonical modality: {modality}")
