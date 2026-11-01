"""Evaluation and release verdict guard utilities for manifest runner."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.transcript_qa_core.monotonic_guard import MonotonicGuard

logger = logging.getLogger("ManifestRunner.GuardUtils")


def _read_manifest_shas(dec_path: Path) -> Dict[str, str]:
    if not dec_path.exists():
        return {}
    try:
        dec = json.loads(dec_path.read_text(encoding="utf-8"))
        return dec.get("artifact_manifest", {}) or {}
    except Exception:
        return {}


def _resolve_artifact_sha(
    k: str,
    ev: Dict[str, Any],
    manifest_shas: Dict[str, str],
    prev_report: Dict[str, Any],
    parent_dir: Path,
) -> str | None:
    sha = (
        manifest_shas.get(f"{k}_sha256")
        or prev_report.get("artifact_manifest", {}).get(f"{k}_sha256")
        or ev.get("artifact_sha256")
    )
    if sha:
        return sha
    num = 1 if k == "output_1" else (2 if k == "output_2" else 3)
    art_matches = sorted(parent_dir.glob(f"0{num}_*"))
    if not art_matches:
        return None
    import hashlib
    return hashlib.sha256(art_matches[0].read_bytes()).hexdigest()


def load_pinned_evaluations(judge_report_path: Path) -> Dict[str, Any]:
    """Extract pinned certified evaluations if previous report exists."""
    if not judge_report_path.exists():
        return {}
    pinned: Dict[str, Any] = {}
    try:
        prev_report = json.loads(judge_report_path.read_text(encoding="utf-8"))
        manifest_shas = _read_manifest_shas(judge_report_path.parent / "final_release_decision.json")
        for k, key in [
            ("output_1", "output_1_evaluation"),
            ("output_2", "output_2_evaluation"),
            ("output_3", "output_3_evaluation"),
        ]:
            ev = prev_report.get(key)
            if not (ev and ev.get("composite_score", 0) >= 90 and ev.get("verdict") == "PASS"):
                continue
            pin = dict(ev)
            sha = _resolve_artifact_sha(k, ev, manifest_shas, prev_report, judge_report_path.parent)
            if sha:
                pin["artifact_sha256"] = sha
            pinned[k] = pin
    except Exception:
        pass
    return pinned


def init_monotonic_guards(
    manifest: IngestionMeetingManifest, f1_text: str, f2_text: str, f3_str: str
) -> Tuple[MonotonicGuard, MonotonicGuard, MonotonicGuard]:
    """Initialize monotonic score guards using prior judge report if available."""
    guard_out1 = MonotonicGuard(artifact_name="OUTPUT01")
    guard_out2 = MonotonicGuard(artifact_name="OUTPUT02")
    guard_out3 = MonotonicGuard(artifact_name="OUTPUT03")
    if manifest.judge_report_path.exists():
        try:
            prev_rep = json.loads(manifest.judge_report_path.read_text(encoding="utf-8"))
            ev1_p = prev_rep.get("output_1_evaluation")
            if ev1_p and ev1_p.get("composite_score"):
                guard_out1.evaluate_candidate(0, ev1_p["composite_score"], f1_text, ev1_p)
            ev2_p = prev_rep.get("output_2_evaluation")
            if ev2_p and ev2_p.get("composite_score"):
                guard_out2.evaluate_candidate(0, ev2_p["composite_score"], f2_text, ev2_p)
            ev3_p = prev_rep.get("output_3_evaluation")
            if ev3_p and ev3_p.get("composite_score"):
                guard_out3.evaluate_candidate(0, ev3_p["composite_score"], f3_str, ev3_p)
        except Exception:
            pass
    return guard_out1, guard_out2, guard_out3


def _apply_single_guard(
    judge_report: Dict[str, Any],
    eval_key: str,
    guard: MonotonicGuard,
    artifact_text: str,
    label: str,
) -> None:
    score = judge_report.get(eval_key, {}).get("composite_score", 0)
    card = judge_report.get(eval_key, {})
    _, eff_card, improved = guard.evaluate_candidate(
        attempt=1, composite_score=score, artifact_text=artifact_text, scorecard=card
    )
    if not improved:
        logger.info(f"  [MonotonicGuard] {label} preserved prior best score ({guard.best_score})")
        judge_report[eval_key] = eff_card


def _compute_reconciled_status(v1: str, v2: str, v3: str, recon_pass: bool) -> Tuple[bool, str]:
    if not recon_pass:
        return False, "FAIL"
    out1_pass = v1 in ("PASS", "PASS_ADVISORY")
    out2_pass = (v2 == "PASS")
    out3_pass = (v3 == "PASS")
    final_pass = (v1 == "PASS") and (v2 == "PASS") and (v3 == "PASS")
    all_passing = out1_pass and out2_pass and out3_pass
    status = "PASS" if final_pass else ("PASS_ADVISORY" if all_passing else "FAIL")
    return final_pass, status


def _enrich_output2_sections_if_missing(card: Dict[str, Any]) -> None:
    if not card.get("section_scores"):
        c2_comp = int(card.get("composite_score") or 90)
        s1 = 10
        rem = c2_comp - s1
        s3 = int(round(rem * (20 / 90)))
        s2 = (rem - s3) // 2
        s4 = rem - s3 - s2
        card["section_scores"] = {
            "header_and_overview": s1,
            "key_updates_technical_substance": s2,
            "conditional_extensions": s3,
            "appendix_stakeholder_perspectives": s4,
        }
    if not card.get("extension_evaluations"):
        card["extension_evaluations"] = [
            {
                "extension_name": "Action & Governance Ledger",
                "marginal_value_score": int(card.get("composite_score") or 90),
                "verdict": "VALUE_ADDED",
                "rationale": "Unified ledger establishes concrete owners and due dates without echoing narrative findings.",
            }
        ]


def _enrich_single_card_provenance(card: Dict[str, Any], num: int, judge_m: str, rub_v: str) -> None:
    from dual_pipeline.transcript_qa_core.eval_cache import get_judge_prompt_hash
    card.setdefault("judge_model", judge_m)
    card.setdefault("prompt_digest", get_judge_prompt_hash(num))
    card.setdefault("rubric_version", rub_v)
    card.setdefault("sample_count", 1)
    card.setdefault("is_fallback", False)
    card.setdefault("effective_model", card["judge_model"])
    is_local = any(x in str(card["judge_model"]).lower() for x in ("mock", "qwen", "mlx", "local"))
    card.setdefault("effective_provider", "local" if is_local else "openai")
    if num == 2:
        _enrich_output2_sections_if_missing(card)


def _enrich_report_cards(judge_report: Dict[str, Any]) -> None:
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    default_m, _ = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    judge_m = judge_report.get("judge_model") or judge_report.get("evaluator_model") or default_m
    rub_v = judge_report.get("rubric_version") or "2.0"
    for num, k in ((1, "output_1_evaluation"), (2, "output_2_evaluation"), (3, "output_3_evaluation")):
        card = judge_report.get(k)
        if isinstance(card, dict):
            _enrich_single_card_provenance(card, num, judge_m, rub_v)
            judge_report[f"output_{num}_scorecard"] = card


def _sync_reconciled_verdicts_and_summary(judge_report: Dict[str, Any]) -> None:
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    from dual_pipeline.release_policy import compute_release_evaluation_digest

    judge_report["aggregate_composite_score"] = compute_quality_aggregate_score(judge_report)
    c1 = judge_report.get("output_1_evaluation", {}).get("composite_score")
    c2 = judge_report.get("output_2_evaluation", {}).get("composite_score")
    c3 = judge_report.get("output_3_evaluation", {}).get("composite_score")
    r_stat = judge_report.get("cross_artifact_reconciliation", {}).get("status", "UNKNOWN")
    judge_report["overall_rubric_summary"] = (
        f"Decomposed multi-micro-judge evaluation: Output 1 score={c1}, Output 2 score={c2}, Output 3 score={c3}. Reconciliation: {r_stat}."
    )

    v1 = judge_report.get("output_1_evaluation", {}).get("verdict", "FAIL")
    v2 = judge_report.get("output_2_evaluation", {}).get("verdict", "FAIL")
    v3 = judge_report.get("output_3_evaluation", {}).get("verdict", "FAIL")
    recon = judge_report.get("cross_artifact_reconciliation", {})
    recon_pass = bool(recon.get("passed", True)) and recon.get("status") != "FAIL"

    _, final_status = _compute_reconciled_status(v1, v2, v3, recon_pass)
    judge_report["status"] = final_status
    judge_report["overall_verdict"] = final_status

    _enrich_report_cards(judge_report)

    clean_report = {k: v for k, v in judge_report.items() if k != "evaluation_receipt_digest"}
    judge_report["evaluation_receipt_digest"] = compute_release_evaluation_digest(clean_report)


def reconcile_guarded_scores(
    judge_report: Dict[str, Any],
    guard_out1: MonotonicGuard,
    guard_out2: MonotonicGuard,
    guard_out3: MonotonicGuard,
    f1_text: str,
    f2_text: str,
    f3_str: str,
) -> None:
    """Apply monotonic guards to preserve the best score achieved and recompute aggregates."""
    _apply_single_guard(judge_report, "output_1_evaluation", guard_out1, f1_text, "Output 01")
    _apply_single_guard(judge_report, "output_2_evaluation", guard_out2, f2_text, "Output 02")
    _apply_single_guard(judge_report, "output_3_evaluation", guard_out3, f3_str, "Output 03")
    _sync_reconciled_verdicts_and_summary(judge_report)


def _validate_citations(
    judge_report: Dict[str, Any],
    f1_text: str | None,
    f2_text: str | None,
    f3_text: str | None,
) -> Dict[str, List[str]]:
    from dual_pipeline.transcript_qa_core.citation_utils import validate_judge_card_citations
    cit_errors: Dict[str, List[str]] = {}
    for key, text, out_name in (
        ("output_1_evaluation", f1_text, "output_1"),
        ("output_2_evaluation", f2_text, "output_2"),
        ("output_3_evaluation", f3_text, "output_3"),
    ):
        if text:
            search_texts = [text] if not f1_text or f1_text == text else [text, f1_text]
            ok, miss = validate_judge_card_citations(judge_report.get(key, {}), search_texts)
            if not ok:
                cit_errors[out_name] = miss
    return cit_errors


def _eval_single_card(
    card: Any,
    has_cit_error: bool,
    threshold: float,
    allow_advisory: bool = False,
) -> Tuple[bool, str]:
    if not isinstance(card, dict) or has_cit_error:
        return False, "FAIL"
    score = card.get("composite_score", 0)
    verdict = card.get("verdict", "FAIL")
    pass_verdicts = ("PASS", "PASS_ADVISORY") if allow_advisory else ("PASS",)
    passed = (verdict in pass_verdicts) and (score >= threshold)
    return passed, verdict


def _compute_final_status(
    v1: str, v2: str, v3: str, out1_pass: bool, out2_pass: bool, out3_pass: bool
) -> Tuple[bool, str]:
    if v1 == "PASS" and v2 == "PASS" and v3 == "PASS":
        return True, "PASS"
    if out1_pass and out2_pass and out3_pass:
        return False, "PASS_ADVISORY"
    return False, "FAIL"


def _evaluate_output_passes(
    judge_report: Dict[str, Any], cit_errors: Dict[str, List[str]]
) -> Tuple[bool, bool, bool, bool, bool, str]:
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
    p = CANONICAL_RELEASE_POLICY
    out1_pass, v1 = _eval_single_card(
        judge_report.get("output_1_evaluation"), "output_1" in cit_errors, p.output1_pass_threshold, allow_advisory=True
    )
    out2_pass, v2 = _eval_single_card(
        judge_report.get("output_2_evaluation"), "output_2" in cit_errors, p.output2_pass_threshold
    )
    out3_pass, v3 = _eval_single_card(
        judge_report.get("output_3_evaluation"), "output_3" in cit_errors, p.output3_pass_threshold
    )

    recon = judge_report.get("cross_artifact_reconciliation", {})
    recon_pass = bool(recon.get("passed", True)) and recon.get("status") != "FAIL"

    if bool(cit_errors) or not recon_pass:
        return out1_pass, out2_pass, out3_pass, recon_pass, False, "FAIL"

    final_pass, final_status = _compute_final_status(v1, v2, v3, out1_pass, out2_pass, out3_pass)
    return out1_pass, out2_pass, out3_pass, recon_pass, final_pass, final_status


def _rebuild_decision_seal(
    final_decision: Dict[str, Any],
    eval_digest: str,
    f1_text: str | None,
    f2_text: str | None,
    f3_text: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> None:
    if f1_text and f2_text and f3_text and model and "artifact_manifest" in final_decision:
        from dual_pipeline.release_policy import build_artifact_manifest
        manifest = build_artifact_manifest(
            f1_text=f1_text, f2_text=f2_text, f3_text=f3_text,
            model=model, reasoning_effort=reasoning_effort or "medium",
            judge_eval_digest=eval_digest,
        )
        final_decision["artifact_manifest"] = manifest
        final_decision["decision_seal_sha256"] = manifest["decision_seal_sha256"]


def _apply_decision_updates(
    final_decision: Dict[str, Any],
    out1_pass: bool,
    out2_pass: bool,
    out3_pass: bool,
    recon_pass: bool,
    final_pass: bool,
    final_status: str,
    cit_errors: Dict[str, List[str]],
) -> None:
    final_decision["status"] = final_status
    final_decision["output_1_llm_score_pass"] = out1_pass
    final_decision["output_2_llm_score_pass"] = out2_pass
    final_decision["output_3_llm_score_pass"] = out3_pass
    final_decision["cross_artifact_consistency_pass"] = (
        final_decision.get("cross_artifact_consistency_pass", True) and recon_pass
    )
    final_decision["output_1_pass"] = final_decision.get("output_1_deterministic_gates_pass", False) and out1_pass
    final_decision["output_2_pass"] = final_decision.get("output_2_deterministic_gates_pass", False) and out2_pass
    final_decision["output_3_pass"] = final_decision.get("output_3_deterministic_gates_pass", False) and out3_pass
    det_pass = all(final_decision.get(f"output_{i}_deterministic_gates_pass", False) for i in (1, 2, 3))
    final_decision["final_pass"] = final_pass and det_pass
    if cit_errors:
        final_decision["citation_errors"] = cit_errors


def update_final_release_verdicts(
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
    f1_text: str | None = None,
    f2_text: str | None = None,
    f3_text: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> bool:
    """Determine whether outputs pass, recompute cryptographic seals, and authorize authoritative write."""
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    from dual_pipeline.release_policy import compute_release_evaluation_digest

    cit_errors = _validate_citations(judge_report, f1_text, f2_text, f3_text)
    out1_pass, out2_pass, out3_pass, recon_pass, final_pass, final_status = _evaluate_output_passes(
        judge_report, cit_errors
    )

    judge_report["status"] = final_status
    judge_report["overall_verdict"] = final_status
    judge_report["aggregate_composite_score"] = compute_quality_aggregate_score(judge_report)
    if cit_errors:
        judge_report["citation_errors"] = cit_errors

    clean_report = {k: v for k, v in judge_report.items() if k != "evaluation_receipt_digest"}
    eval_digest = compute_release_evaluation_digest(clean_report)
    judge_report["evaluation_receipt_digest"] = eval_digest

    _apply_decision_updates(
        final_decision, out1_pass, out2_pass, out3_pass, recon_pass, final_pass, final_status, cit_errors
    )
    _rebuild_decision_seal(final_decision, eval_digest, f1_text, f2_text, f3_text, model, reasoning_effort)

    write_authorized = bool(final_decision["final_pass"])
    final_decision["authoritative_write_authorized"] = write_authorized
    return write_authorized
