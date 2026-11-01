"""Typed review handoffs for bounded document remediation."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _record_unbound_document_patch_handoff(
    out_p: Path, attempt: int, source_text: str, candidate: str,
    judge_report: dict[str, Any], directive: str | None, reason: str,
    reason_code: str = "UNBOUND_DOCUMENT_PATCH_SCOPE",
) -> Path:
    """Keep a failed document judgment reviewable without authorizing regeneration."""
    evaluation = judge_report.get("output_3_evaluation", {})
    handoff = {
        "status": "REVIEW_REQUIRED",
        "reason_code": reason_code,
        "reason": reason,
        "attempt": attempt,
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
        "judge_evaluation_digest": judge_report.get("evaluation_receipt_digest"),
        "judge_verdict": evaluation.get("verdict"),
        "judge_score": evaluation.get("composite_score"),
        "remediation_diff": evaluation.get("actionable_remediation_diff"),
        "remediation_directive": directive,
        "allowed_next_action": (
            "Review the failed bounded patch against its sealed candidate and source"
            if reason_code == "INVALID_BOUNDED_PATCH_RESPONSE"
            else "Bind typed failed existing object IDs for a new bounded patch review"
        ),
    }
    path = out_p / "attempts" / f"attempt_{attempt}" / "document_remediation_handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def synthesize_with_document_handoff(
    strategy: Any, *, source_text: str, pa_context: dict[str, Any],
    metadata: dict[str, Any], attempt: int, directive: str | None,
    prior_synthesis: Any, prior_evaluation: Any, output_dir: Path,
    is_document: bool,
) -> Any | None:
    """Return None only when an invalid follow-up patch needs human review."""
    from dual_pipeline.document_pipeline_core.bounded_patch import InvalidBoundedPatchResponseError

    try:
        return strategy.synthesize(
            source_text=source_text, pa_context=pa_context, metadata=metadata,
            attempt=attempt, remediation_directive=directive,
        )
    except InvalidBoundedPatchResponseError as exc:
        if not is_document or attempt == 1 or prior_synthesis is None or prior_evaluation is None:
            raise
        _record_unbound_document_patch_handoff(
            output_dir, attempt - 1, source_text, prior_synthesis.output_3,
            prior_evaluation.judge_report, directive, str(exc),
            reason_code="INVALID_BOUNDED_PATCH_RESPONSE",
        )
        logger.warning("Document bounded remediation requires review: %s; falling back to full regeneration", exc)
        metadata["document_patch_request"] = None
        pa_context["document_patch_request"] = None
        if isinstance(pa_context.get("metadata"), dict):
            pa_context["metadata"]["document_patch_request"] = None
        return strategy.synthesize(
            source_text=source_text, pa_context=pa_context, metadata=metadata,
            attempt=attempt, remediation_directive=directive,
        )


def _has_additive_deficits(directive: str | None, judge_report: dict[str, Any]) -> bool:
    tokens = (
        "err_omitted_high_signal_entities",
        "err_omitted_core_system",
        "document_slide_coverage_gap",
        "document_yield_deficit",
        "omitted high-signal",
        "missing entity",
        "missing entities",
        "slide coverage gap",
        "yield deficit",
        "omitted entities",
        "omitted core system",
    )
    evaluation = judge_report.get("output_3_evaluation", judge_report)
    failures: list[Any] = list(judge_report.get("failures", []))
    if isinstance(evaluation, dict):
        failures.extend(evaluation.get("failures", []))
    q3 = judge_report.get("output_03_canonical_signal_projection", {})
    if isinstance(q3, dict):
        failures.extend(q3.get("failures", []))

    texts = [
        str(directive or ""),
        str(evaluation.get("actionable_remediation_diff") or "") if isinstance(evaluation, dict) else "",
        str(evaluation.get("forensic_rationale") or "") if isinstance(evaluation, dict) else "",
        " ".join(str(f) for f in failures),
    ]
    haystack = " ".join(texts).lower()
    return any(t in haystack for t in tokens)


def build_additive_unit_gap_directive(
    source_text: str,
    candidate: str | dict[str, Any],
    judge_report: dict[str, Any],
    base_directive: str | None = None,
) -> tuple[str, list[int]]:
    """Build a targeted unit-gap remediation directive listing uncovered substantive slides/sections."""
    from dual_pipeline.document_pipeline_core.quality import validate_slide_deck_coverage_and_yield

    try:
        cand_obj = json.loads(candidate) if isinstance(candidate, str) else candidate
    except Exception:
        cand_obj = {}
    _passed, _errors, cov = validate_slide_deck_coverage_and_yield(
        cand_obj if isinstance(cand_obj, dict) else {}, source_text
    )
    uncovered = list(cov.get("uncovered_substantive_units", []))
    parts = [str(base_directive or "").strip()] if base_directive else []
    if uncovered:
        slide_list = ", ".join(f"Slide {s}" for s in uncovered)
        parts.append(
            f"ADDITIVE UNIT-GAP REMEDIATION REQUIRED: Extract verbatim-grounded entities, relationship_signals, "
            f"and evidence_index entries for uncovered substantive units: {slide_list}."
        )
    return "\n\n".join(p for p in parts if p), uncovered


def merge_additive_unit_remediation(
    prior_candidate: str | dict[str, Any],
    gap_candidate: str | dict[str, Any],
) -> dict[str, Any]:
    """Merge an additive unit-gap extraction into a prior document projection without dropping prior slides."""
    from dual_pipeline.document_pipeline_core.unit_decomposition import merge_unit_batch_projections

    p_obj = json.loads(prior_candidate) if isinstance(prior_candidate, str) else dict(prior_candidate)
    g_obj = json.loads(gap_candidate) if isinstance(gap_candidate, str) else dict(gap_candidate)
    return merge_unit_batch_projections([p_obj, g_obj])


def prepare_document_patch_or_handoff(
    metadata: dict[str, Any], *, candidate: str, judge_report: dict[str, Any],
    source_text: str, directive: str | None, output_dir: Path, attempt: int,
) -> bool:
    """Bind a patch to failed IDs, or seal a review handoff and fall back to full re-extraction."""
    if _has_additive_deficits(directive, judge_report):
        logger.info(
            "Document evaluation indicated additive deficits or slide coverage gaps; bypassing bounded patch for full Stage 03 re-extraction"
        )
        enriched_directive, uncovered_units = build_additive_unit_gap_directive(
            source_text, candidate, judge_report, base_directive=directive,
        )
        metadata["document_patch_request"] = None
        metadata["remediation_directive"] = enriched_directive
        metadata["additive_unit_gap_remediation"] = {
            "uncovered_substantive_units": uncovered_units,
            "enriched_directive": enriched_directive,
        }
        return True

    from dual_pipeline.document_pipeline_core.bounded_patch import (
        UnboundPatchScopeError, build_bounded_patch_request,
    )

    try:
        metadata["document_patch_request"] = build_bounded_patch_request(
            candidate, judge_report, source_text, directive,
        )
    except UnboundPatchScopeError as exc:
        _record_unbound_document_patch_handoff(
            output_dir, attempt, source_text, candidate, judge_report, directive, str(exc),
        )
        logger.warning("Document bounded remediation requires review: %s; falling back to full re-extraction", exc)
        metadata["document_patch_request"] = None
        return True
    return True

