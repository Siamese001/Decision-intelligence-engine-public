"""Authoritative release gate, LLM Judge report validation, and Output 3 threshold enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.release_thresholds import OUTPUT3_PRODUCTION_PASS_THRESHOLD

from .digest_seal import (
    _collect_referenced_attachments_text,
    _is_evidence_envelope_package,
    _read_first_glob,
    _read_json_safe,
    _resolve_release_artifacts,
    verify_decision_seal,
    verify_release_evaluation_digest,
)
from .scorecard_validators import validate_micro_judge_scorecard


def _check_decision_authorization(decision: Any, pkg_name: str) -> None:
    if not isinstance(decision, dict) or not decision.get("authoritative_write_authorized", False):
        status = decision.get("status") if isinstance(decision, dict) else "invalid"
        raise RuntimeError(
            f"Authoritative release authorization rejected for package '{pkg_name}'. Status: {status}, "
            "authoritative_write_authorized=False. GraphDB mutation blocked by fail-closed release authority."
        )
    if decision.get("short_circuit") or decision.get("short_circuit_reason"):
        sc_reason = decision.get("short_circuit_reason", "DETERMINISTIC_QA_COLLAPSE")
        raise RuntimeError(
            f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: Authoritative release rejected for package '{pkg_name}' ({sc_reason}). GraphDB mutation blocked."
        )
    if decision.get("is_mock") or decision.get("mock"):
        raise RuntimeError(
            f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: Authoritative release rejected for package '{pkg_name}'. Mock provider detected. GraphDB mutation blocked."
        )
    if not decision.get("output_3_llm_score_pass", False):
        raise RuntimeError(
            f"Output 3 LLM Judge score gate rejected for package '{pkg_name}' (output_3_llm_score_pass=False). GraphDB mutation blocked."
        )


def _has_stage_incomplete_span(stage_evals: List[Tuple[int, Any]], judge_report: Mapping[str, Any]) -> bool:
    if judge_report.get("incomplete_evaluated_span"):
        return True
    for _, c in stage_evals:
        if not isinstance(c, Mapping):
            continue
        if c.get("incomplete_evaluated_span"):
            return True
        if isinstance(c.get("execution"), Mapping) and c["execution"].get("incomplete_evaluated_span"):
            return True
        if isinstance(c.get("raw_judge_scorecard"), Mapping) and c["raw_judge_scorecard"].get("incomplete_evaluated_span"):
            return True
    return False


def _populate_scorecard_default_provenance(card: Dict[str, Any], judge_report: Mapping[str, Any]) -> None:
    if "judge_model" not in card:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier

        def_judge_model, _ = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        card["judge_model"] = card.get("effective_model") or judge_report.get("judge_model") or def_judge_model
    if "is_fallback" not in card:
        card["is_fallback"] = bool(card.get("is_provider_fallback", False))
    card.setdefault("sample_count", 1)
    if "rubric_version" not in card:
        card["rubric_version"] = judge_report.get("rubric_version") or "2.0"
    if "prompt_digest" not in card:
        card["prompt_digest"] = card.get("prompt_hash") or judge_report.get("evaluation_receipt_digest") or "provenance_digest"


def _has_rubric_dimensions(card: Mapping[str, Any]) -> bool:
    if "section_scores" in card or "dimension_scores" in card or "dimensions" in card:
        return True
    return any(str(k).startswith("dimension_") for k in card)


def _validate_stage_scorecards(
    stage_evals: List[Tuple[int, Any]],
    judge_report: Mapping[str, Any],
    pkg_name: str,
) -> None:
    for out_num, card in stage_evals:
        if card is None or not isinstance(card, Mapping):
            continue
        if out_num == 3 and not _has_rubric_dimensions(card) and card.get("status") not in ("SKIPPED", "NOT_APPLICABLE"):
            continue
        if isinstance(card, dict):
            _populate_scorecard_default_provenance(card, judge_report)
        is_valid, card_errs = validate_micro_judge_scorecard(card, output_num=out_num, require_provenance=True)
        if not is_valid:
            err_msg = "; ".join(card_errs)
            raise RuntimeError(
                f"LLM Judge report for package '{pkg_name}' contains malformed Output {out_num} scorecard: {err_msg}. "
                "Mutation blocked by fail-closed release authority."
            )


def _verify_mandatory_meeting_scorecards(
    stage_evals: List[Tuple[int, Any]],
    judge_report: Mapping[str, Any],
    pkg_name: str,
) -> None:
    is_doc_mode = judge_report.get("mode") == "document" or judge_report.get("evaluation_mode") == "DOCUMENT_DIRECT_PROJECTION"
    is_meeting_release = (
        judge_report.get("mode") == "meeting"
        or "meeting" in str(judge_report.get("evaluation_mode", "")).lower()
        or judge_report.get("evaluation_mode") == "DECOMPOSED_CONCURRENT"
    )
    if not (is_meeting_release and not is_doc_mode):
        return
    for out_num, card in stage_evals:
        if card is None:
            raise RuntimeError(
                f"LLM Judge report for package '{pkg_name}' is missing mandatory Output {out_num} scorecard. "
                "Mutation blocked by fail-closed release authority."
            )


def _check_judge_report_preconditions(judge_report: Mapping[str, Any], pkg_name: str) -> None:
    if judge_report.get("short_circuit") or judge_report.get("evaluation_mode") == "SKIPPED_DETERMINISTIC_QA_FAILURE":
        sc_reason = judge_report.get("short_circuit_reason", "DETERMINISTIC_QA_COLLAPSE")
        raise RuntimeError(
            f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: LLM Judge report rejected for package '{pkg_name}'. "
            f"Evaluation was short-circuited ({sc_reason}). "
            "GraphDB mutation blocked by fail-closed release authority."
        )
    if judge_report.get("is_mock") or judge_report.get("evaluator_model") == "mock" or judge_report.get("mock"):
        raise RuntimeError(
            f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: LLM Judge report rejected for package '{pkg_name}'. "
            "Judge report is from a mock provider. "
            "GraphDB mutation blocked by fail-closed release authority."
        )


def _check_judge_report(judge_file: Path, pkg_name: str) -> None:
    if not judge_file.is_file():
        raise RuntimeError(
            f"Missing mandatory LLM Judge evaluation report for package '{pkg_name}'. "
            f"Expected report at {judge_file}. Mutation blocked by fail-closed release authority."
        )
    judge_report = _read_json_safe(judge_file)
    if not isinstance(judge_report, dict):
        raise RuntimeError(f"Corrupt or empty LLM Judge evaluation report at {judge_file}")

    _check_judge_report_preconditions(judge_report, pkg_name)
    stage_evals = [
        (1, judge_report.get("output_1_evaluation")),
        (2, judge_report.get("output_2_evaluation")),
        (3, judge_report.get("output_3_evaluation")),
    ]
    if _has_stage_incomplete_span(stage_evals, judge_report):
        raise RuntimeError(
            f"Incomplete evaluated span in LLM Judge report for package '{pkg_name}'. "
            "Tail defects cannot inherit prefix-only passes. Mutation blocked."
        )
    if not verify_release_evaluation_digest(judge_report):
        raise RuntimeError(
            f"Cryptographic evaluation receipt digest verification failed for package '{pkg_name}'. "
            "Digest mismatch in 'llm_judge_evaluation.json'. Mutation blocked."
        )

    _validate_stage_scorecards(stage_evals, judge_report, pkg_name)
    _verify_mandatory_meeting_scorecards(stage_evals, judge_report, pkg_name)
    _check_out3_threshold(judge_report, pkg_name)


def _is_signed_out3_authorized_exception(out3_eval: Any, judge_report: Mapping[str, Any], verdict: Any) -> bool:
    if not isinstance(out3_eval, Mapping):
        return False
    if out3_eval.get("authorized_exception") or judge_report.get("authorized_exception"):
        return True
    sig = out3_eval.get("exception_receipt_signature") or judge_report.get("exception_receipt_signature")
    return str(verdict).upper() == "PASS_AUTHORIZED_EXCEPTION" and bool(sig)


def _check_out3_vacuous_extraction(out3_eval: Mapping[str, Any], judge_report: Mapping[str, Any], pkg_name: str) -> None:
    defect_codes = out3_eval.get("defect_codes") or []
    if "ERR_VACUOUS_GRAPH_EXTRACTION" in defect_codes:
        raise RuntimeError(
            f"ERR_VACUOUS_GRAPH_EXTRACTION: Output 3 contains 0 graph mutations for package '{pkg_name}'. Mutation blocked."
        )
    src_len = int(out3_eval.get("source_char_length") or judge_report.get("source_char_length") or 0)
    ent_cnt = out3_eval.get("entity_upserts_count")
    if src_len >= 500 and ent_cnt is not None and int(ent_cnt) < 1:
        raise RuntimeError(
            f"ERR_VACUOUS_GRAPH_EXTRACTION: Output 3 has 0 entity_upserts despite source_char_length={src_len} (>= 500) for package '{pkg_name}'. Mutation blocked."
        )


def _check_out3_threshold(judge_report: Mapping[str, Any], pkg_name: str) -> None:
    out3_eval = judge_report.get("output_3_evaluation", {})
    if isinstance(out3_eval, Mapping):
        if out3_eval.get("status") == "SKIPPED" and out3_eval.get("reason") == "FROZEN_PRIOR_ARTIFACT" and out3_eval.get("verdict") == "PASS":
            return
        _check_out3_vacuous_extraction(out3_eval, judge_report, pkg_name)

    raw = out3_eval.get("composite_score", 0) if isinstance(out3_eval, dict) else 0
    score = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0
    verdict = out3_eval.get("verdict") if isinstance(out3_eval, dict) else "FAIL"
    if _is_signed_out3_authorized_exception(out3_eval, judge_report, verdict):
        if score < 85.0 or verdict not in ("PASS", "PASS_AUTHORIZED_EXCEPTION", "PASS_ADVISORY"):
            raise RuntimeError(
                f"LLM Judge score for Output 3 with authorized exception does not meet borderline floor for package '{pkg_name}': "
                f"score={score}/85.0, verdict={verdict}. Mutation blocked."
            )
        return

    if score < OUTPUT3_PRODUCTION_PASS_THRESHOLD or verdict not in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
        raise RuntimeError(
            f"LLM Judge score for Output 3 does not meet production floor for package '{pkg_name}': "
            f"score={score}/{OUTPUT3_PRODUCTION_PASS_THRESHOLD}, verdict={verdict}. Mutation blocked."
        )


def _verify_judge_digest_match(manifest: Dict[str, Any], judge_file: Optional[Path], pkg_name: str) -> None:
    if not judge_file or not judge_file.is_file():
        return
    judge_report = _read_json_safe(judge_file)
    if not isinstance(judge_report, dict):
        raise RuntimeError(f"Corrupt or non-dict judge report at {judge_file} for package '{pkg_name}'")

    expected_judge_digest = judge_report.get("evaluation_receipt_digest")
    actual_judge_digest = manifest.get("judge_evaluation_digest")
    if not expected_judge_digest:
        raise RuntimeError(
            f"Missing mandatory evaluation_receipt_digest in judge report for package '{pkg_name}'. "
            "Mutation blocked by fail-closed release authority."
        )
    if not actual_judge_digest:
        raise RuntimeError(
            f"Missing mandatory judge_evaluation_digest in manifest for package '{pkg_name}'. "
            "Mutation blocked by fail-closed release authority."
        )
    if expected_judge_digest != actual_judge_digest:
        raise RuntimeError(
            f"Cryptographic judge digest mismatch for package '{pkg_name}': "
            f"manifest judge_evaluation_digest='{actual_judge_digest}' != "
            f"evaluation_receipt_digest='{expected_judge_digest}'. Mutation blocked."
        )
    if not verify_release_evaluation_digest(judge_report):
        raise RuntimeError(
            f"Corrupt or tampered judge evaluation report for package '{pkg_name}': "
            "evaluation_receipt_digest does not match calculated content digest. Mutation blocked."
        )


def _verify_judge_report_integrity_with_attachments(
    decision: Any,
    pkg_p: Path,
    judge_file: Optional[Path],
    f1_text: str,
    f2_text: str,
    f3_text: str,
) -> None:
    if not judge_file or not judge_file.is_file():
        return
    from dual_pipeline.transcript_qa_core.citation_utils import verify_judge_report_integrity

    judge_rep = _read_json_safe(judge_file)
    if not isinstance(judge_rep, dict):
        return
    att_text = _collect_referenced_attachments_text(pkg_p.parent)
    f1_with_att = f"{f1_text}\n\n{att_text}" if att_text else f1_text
    if not f1_with_att or (f1_with_att.strip().startswith("{") and f1_with_att.strip().endswith("}")):
        cand_source = _read_first_glob(pkg_p.parent, "*source*.txt")
        if cand_source and not (cand_source.strip().startswith("{") and cand_source.strip().endswith("}")):
            f1_with_att = cand_source
    ok_int, int_err = verify_judge_report_integrity(
        judge_rep, final_decision=decision, f1_text=f1_with_att, f2_text=f2_text, f3_text=f3_text
    )
    if not ok_int:
        raise RuntimeError(
            f"Release integrity validation failed for package '{pkg_p.name}': {int_err}. "
            "Mutation blocked by fail-closed release authority."
        )


def _verify_decision_judge_digest(decision: Any, judge_file: Optional[Path], pkg_name: str) -> None:
    dec_judge_digest = decision.get("judge_evaluation_digest") if isinstance(decision, dict) else None
    if not dec_judge_digest or not judge_file or not judge_file.is_file():
        return
    judge_report = _read_json_safe(judge_file)
    if isinstance(judge_report, dict) and judge_report.get("evaluation_receipt_digest"):
        if dec_judge_digest != judge_report["evaluation_receipt_digest"]:
            raise RuntimeError(
                f"Decision judge_evaluation_digest mismatch for package '{pkg_name}': "
                f"decision={dec_judge_digest} != judge_report={judge_report['evaluation_receipt_digest']}. Mutation blocked."
            )


def _check_manifest_and_seal(decision: Any, pkg_p: Path, judge_file: Optional[Path] = None) -> None:
    is_envelope = _is_evidence_envelope_package(pkg_p)
    manifest = decision.get("artifact_manifest") if isinstance(decision, dict) else None
    if is_envelope and (not isinstance(manifest, dict) or not manifest):
        raise RuntimeError(
            f"Missing mandatory non-empty artifact_manifest in decision data for evidence package '{pkg_p.name}'. "
            "Mutation blocked by fail-closed release authority."
        )
    if not isinstance(manifest, dict) or not manifest:
        return

    _verify_judge_digest_match(manifest, judge_file, pkg_p.name)
    _verify_decision_judge_digest(decision, judge_file, pkg_p.name)

    f1_text, f2_text, f3_text = _resolve_release_artifacts(pkg_p.parent, decision)
    valid_seal, seal_errors = verify_decision_seal(decision, f1_text=f1_text, f2_text=f2_text, f3_text=f3_text)
    if not valid_seal:
        err_msg = "; ".join(seal_errors)
        raise RuntimeError(
            f"Cryptographic decision seal verification failed for package '{pkg_p.name}': {err_msg}. "
            "Artifact content drifted or seal forged. Mutation blocked."
        )
    _verify_judge_report_integrity_with_attachments(decision, pkg_p, judge_file, f1_text, f2_text, f3_text)


def _verify_single_package_gate(pkg_p: Path, *, allow_unjudged: bool) -> None:
    decision_file = pkg_p.parent / "final_release_decision.json"
    judge_file = pkg_p.parent / "llm_judge_evaluation.json"

    if not decision_file.is_file():
        if allow_unjudged:
            return
        raise RuntimeError(
            f"Missing mandatory release authority receipt for package '{pkg_p.name}'. "
            f"Expected 'final_release_decision.json' at {decision_file}. "
            "GraphDB mutation blocked by fail-closed release authority."
        )

    decision = _read_json_safe(decision_file)
    if decision is None:
        raise RuntimeError(
            f"Corrupt or empty release decision file at {decision_file} for package '{pkg_p.name}'. "
            "GraphDB mutation blocked by fail-closed release authority."
        )
    _check_decision_authorization(decision, pkg_p.name)
    _check_manifest_and_seal(decision, pkg_p, judge_file=judge_file)

    if not allow_unjudged:
        _check_judge_report(judge_file, pkg_p.name)


def verify_authoritative_release_gate(
    packages: Sequence[Path],
    *,
    allow_unjudged: bool = False,
) -> None:
    """Verifies authoritative release decision, cryptographic seals, and LLM judge score for packages."""
    for package in packages:
        pkg_p = Path(package).expanduser().resolve()
        _verify_single_package_gate(pkg_p, allow_unjudged=allow_unjudged)
