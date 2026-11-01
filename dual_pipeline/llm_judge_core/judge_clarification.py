"""
dual_pipeline/llm_judge_core/judge_clarification.py

Atomic Judge Clarification & Borderline Score Reconciliation Engine.
Intercepts borderline LLM judge evaluations (85 <= composite < 90 or floor missed by <= 1 pt),
formulates atomic HitlRequirementEnvelopes, and allows operators to affirm domain validity
rather than triggering unrecoverable retry loops and terminal pipeline failures.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from dual_pipeline.hitl_core.envelope import create_hitl_envelope
from dual_pipeline.hitl_governance_core.ledger import (
    HITLDecisionStore,
    _find_default_ledger_path,
)
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger(__name__)


def _should_skip_clarification(
    is_borderline: bool,
    status: str,
    verdict: str,
    composite: float,
    eval_key: str,
    output_num: int = 2,
    target_threshold: float = 90.0,
) -> bool:
    if (status == "PASS" and verdict == "PASS") or verdict in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
        return True
    if verdict == "PASS_ADVISORY" and output_num != 1:
        return True
    if composite < 85.0:
        logger.info("Hard judge failure on %s (%s, composite %.1f < 85); clarification not applicable.", eval_key, verdict, composite)
        return True
    if composite >= target_threshold and not is_borderline:
        logger.info(
            "Non-borderline judge failure on %s (%s, composite %.1f >= %.1f); clarification not applicable.",
            eval_key,
            verdict,
            composite,
            target_threshold,
        )
        return True
    return not is_borderline


def evaluate_judge_clarification(
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore] = None,
    interactive_resolver: Optional[Any] = None,
) -> bool:
    """Evaluate borderline judge deductions across Outputs 01-03 and prompt operator.

    If operator affirms validity, retains raw composite score, sets verdict to
    PASS_AUTHORIZED_EXCEPTION, and authorizes release. Returns True if all
    borderline failures were resolved.
    """
    if not isinstance(judge_report, dict) or not isinstance(final_decision, dict):
        return False

    attempt = final_decision.get("attempt") or metadata.get("attempt")
    max_attempts = final_decision.get("max_attempts") or metadata.get("max_attempts")
    if attempt is not None and max_attempts is not None and not metadata.get("resume"):
        try:
            if int(attempt) < int(max_attempts):
                logger.info(
                    "Skipping borderline clarification on attempt %d/%d; allowing automated remediation retry first.",
                    int(attempt),
                    int(max_attempts),
                )
                return False
        except (TypeError, ValueError):
            pass

    decision_store = store or HITLDecisionStore()
    resolver = (
        interactive_resolver
        or metadata.get("hitl_judge_resolver")
        or metadata.get("judge_clarification_resolver")
    )
    any_clarified = False

    output_keys = [
        ("output_1", "output_1_evaluation"),
        ("output_2", "output_2_evaluation"),
        ("output_3", "output_3_evaluation"),
    ]

    for short_key, eval_key in output_keys:
        eval_data = judge_report.get(eval_key)
        if not isinstance(eval_data, dict):
            continue
        if eval_data.get("status") in ("SKIPPED", "NOT_APPLICABLE") or "NOT_APPLICABLE" in str(eval_data.get("reason", "")):
            continue

        status = str(eval_data.get("status", "")).upper()
        verdict = str(eval_data.get("verdict", "")).upper()
        raw_composite = eval_data.get("composite_score")
        if raw_composite is None:
            raw_composite = eval_data.get("score", 0.0)
        composite = float(raw_composite if raw_composite is not None else 0.0)
        output_num = 1 if "1" in short_key else (2 if "2" in short_key else 3)
        has_dims = any(str(k).startswith("dim") for k in eval_data) or "section_scores" in eval_data
        if has_dims:
            from dual_pipeline.release_policy import validate_micro_judge_scorecard
            is_valid_card, val_errs = validate_micro_judge_scorecard(eval_data, output_num=output_num)
            if not is_valid_card:
                logger.warning(
                    "Clarification rejected for %s: scorecard is malformed/invalid (%s). Upholding deduction (fail-closed).",
                    eval_key, val_errs,
                )
                continue

        # Check for per-dimension floor breaches
        from dual_pipeline.prompts.contracts.output_contract import get_dimension_floors_for_output
        eff_mode = (
            eval_data.get("pipeline_mode")
            or eval_data.get("modality")
            or eval_data.get("meeting_archetype")
            or judge_report.get("mode")
            or metadata.get("modality")
        )
        contract_floors = get_dimension_floors_for_output(
            output_num, pipeline_mode=str(eff_mode) if eff_mode else None
        )
        has_multi_point_floor_breach = False
        max_floor_deficit = 0.0
        for dim_k, flr in contract_floors.items():
            short_dim = dim_k.split("_")[0] + "_" + dim_k.split("_")[1]
            val = eval_data.get(dim_k) if dim_k in eval_data else eval_data.get(short_dim)
            if val is not None and isinstance(val, (int, float)) and flr is not None:
                if val < (flr - 1.0):
                    has_multi_point_floor_breach = True
                    break
                if val < flr:
                    deficit = float(flr - val)
                    if deficit > max_floor_deficit:
                        max_floor_deficit = deficit

        if has_multi_point_floor_breach:
            logger.warning(
                "Clarification rejected for %s: per-dimension score is more than 1 point below mandatory floor. "
                "Upholding deduction (fail-closed) without HITL prompt.",
                eval_key,
            )
            continue

        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
            record_judge_override,
        )
        priors_p = metadata.get("priors_path")
        band_floor, band_ceiling = get_calibrated_borderline_band(
            short_key, output_num=output_num, priors_path=priors_p
        )
        target_threshold = band_ceiling
        is_borderline = (band_floor <= composite < target_threshold) or (verdict == "HITL_REQUIRED" and composite >= band_floor)
        if _should_skip_clarification(
            is_borderline, status, verdict, composite, eval_key, output_num=output_num, target_threshold=target_threshold
        ):
            continue

        # Extract deduction rationale
        rationale = (
            eval_data.get("forensic_rationale")
            or eval_data.get("actionable_remediation_diff")
            or eval_data.get("_reasoning", "Borderline interpretive deduction")
        )
        if isinstance(rationale, str):
            rationale_snip = rationale[:100].strip()
        else:
            rationale_snip = "Borderline interpretive deduction"

        needed_pts = max(1.0, round(max(target_threshold - composite, max_floor_deficit), 1))

        # Derive dynamic Bayesian prior-informed base confidence from historical store
        history = decision_store.get_history(category="judge_deduction_clarification")
        total_hist = len(history)
        if total_hist > 0:
            aff_count = sum(
                1 for r in history
                if "AFFIRM" in str(r.get("selected_key") or r.get("selected_option", "")).upper()
            )
            base_affirm = round(min(0.90, max(0.10, (aff_count + 1.0) / (total_hist + 2.0))), 3)
            base_uphold = round(1.0 - base_affirm, 3)
        else:
            base_affirm = 0.50
            base_uphold = 0.50

        options = [
            {
                "key": "AFFIRM_VALID",
                "label": f"Affirm candidate validity (+{needed_pts} pts): Clear deduction and upgrade to PASS",
                "confidence": base_affirm,
            },
            {
                "key": "UPHOLD_DEDUCTION",
                "label": f"Uphold judge deduction (-{needed_pts} pts): Trigger remediation and retry",
                "confidence": base_uphold,
            },
        ]

        envelope = create_hitl_envelope(
            category="judge_deduction_clarification",
            domain="llm_judge_evaluation",
            question=f"Borderline score ({composite:.1f}/100) on {eval_key}: Affirm candidate validity? ({rationale_snip})",
            options=options,
            store=decision_store,
            threshold=get_hitl_ambiguity_margin_threshold(),
        )

        prior_signed = _find_prior_signed_hitl_clarification(metadata, decision_store, short_key)
        if prior_signed is not None and (not callable(resolver) or metadata.get("resume") or metadata.get("prior_signed_honored", True)):
            selected_action = "AFFIRM_VALID"
        elif not callable(resolver):
            logger.warning(
                "FAIL_CLOSED_NO_RESOLVER: Borderline judge score (%.1f/100) on %s requires authentic operator clarification, "
                "but no interactive resolver is bound. Upholding deduction (fail-closed).",
                composite,
                eval_key,
            )
            selected_action = "UPHOLD_DEDUCTION"
        else:
            try:
                from config.request_deadline import suspend_deadline
                with suspend_deadline(max_suspend_seconds=30.0):
                    selected_action = str(resolver(envelope))
            except Exception as e:
                logger.warning("Interactive judge resolver failed (%s); upholding deduction.", e)
                selected_action = "UPHOLD_DEDUCTION"

        if selected_action == "AFFIRM_VALID" and max_floor_deficit > 0.0:
            logger.warning(
                "FLOOR_BREACH_BLOCKS_AFFIRM: %s has per-dimension floor deficit %.1f; "
                "blocking AFFIRM_VALID and upholding deduction (fail-closed).",
                eval_key,
                max_floor_deficit,
            )
            eval_data["clarification_floor_blocked"] = True
            selected_action = "UPHOLD_DEDUCTION"

        if selected_action == "AFFIRM_VALID" and prior_signed is None:
            from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
            op_id = metadata.get("reviewer") or metadata.get("operator_id") or "human_operator"
            approval_src = metadata.get("approval_source") or op_id
            is_valid_origin, origin_reason = validate_approval_origin(
                approval_src,
                prior_user_command=metadata.get("prior_user_command") or metadata.get("user_command"),
            )
            if not is_valid_origin:
                logger.warning(
                    "APPROVAL_REJECTED: Judge clarification AFFIRM_VALID rejected due to unverified origin for %s: %s",
                    eval_key,
                    origin_reason,
                )
                selected_action = "UPHOLD_DEDUCTION"

        op_id = (
            (prior_signed.get("operator_id") if isinstance(prior_signed, dict) else None)
            or metadata.get("reviewer")
            or metadata.get("operator_id")
            or "human_operator"
        )
        receipt = envelope.resolve_and_persist(
            selection=selected_action,
            operator_id=op_id,
            store=decision_store,
        )

        if selected_action == "AFFIRM_VALID":
            record_judge_override(
                judge_name=f"{short_key.capitalize()}Judge",
                candidate_score=composite,
                operator_action="AFFIRM_VALID",
                reason=rationale_snip,
                receipt_id=receipt.get("receipt_digest"),
            )

        if selected_action != "AFFIRM_VALID":
            logger.info("Judge deduction upheld for %s (action=%s): score remains %.1f/100", eval_key, selected_action, composite)
            continue

        # Preserve raw composite score and exact rubric dimensions without artificial padding
        eval_data["raw_composite_score"] = composite
        eval_data["composite_score"] = composite
        eval_data["score"] = composite
        eval_data["verdict"] = "PASS_AUTHORIZED_EXCEPTION"
        eval_data["status"] = "PASS_AUTHORIZED_EXCEPTION"
        eval_data["authorized_exception"] = True
        eval_data["hitl_clarification"] = receipt

        det_pass = bool(final_decision.get(f"{short_key}_deterministic_gates_pass", True))
        final_decision[f"{short_key}_llm_score_pass"] = True
        final_decision[f"{short_key}_pass"] = det_pass
        final_decision["judge_clarification_triggered"] = True

        hitl_key = f"judge_clarification_{short_key}"
        judge_clarifications = metadata.setdefault("judge_clarifications", {})
        judge_clarifications[hitl_key] = {
            "ambiguity": f"Borderline score ({composite:.1f}/100) on {eval_key}",
            "locator": "[Exit Gate Evaluation]",
            "statement_id": f"stmt-judge-{short_key}",
            "resolution": f"Affirmed by operator (authorized exception); raw score {composite:.1f}/100 retained (PASS_AUTHORIZED_EXCEPTION).",
            "basis": f"Operator Confirmed (HITL Judge Clarification, receipt {receipt['receipt_digest'][:8]})",
        }
        metadata.setdefault("hitl_resolutions", {})[hitl_key] = receipt
        if prior_signed is None:
            _record_signed_hitl_clarification(
                metadata=metadata,
                store=store,
                short_key=short_key,
                eval_key=eval_key,
                composite=composite,
                receipt=receipt,
                op_id=op_id,
            )
        any_clarified = True
        logger.info("Clarified borderline deduction on %s: authorized exception granted, retained raw score %.1f/100", eval_key, composite)

    # Re-evaluate final decision status
    all_out_pass = all(
        final_decision.get(f"{k}_pass", True)
        for k in ("output_1", "output_2", "output_3")
    )
    if any_clarified and all_out_pass:
        final_decision["status"] = "PASS"
        final_decision["final_pass"] = True
        final_decision["authoritative_write_authorized"] = True
        final_decision["authorized_exception"] = True
        judge_report["status"] = "PASS"
        judge_report["overall_verdict"] = "PASS_AUTHORIZED_EXCEPTION"
        judge_report["authorized_exception"] = True

    if any_clarified:
        from dual_pipeline.release_policy import (
            compute_decision_seal,
            compute_release_evaluation_digest,
        )
        new_eval_digest = compute_release_evaluation_digest(judge_report)
        judge_report["evaluation_receipt_digest"] = new_eval_digest
        final_decision["judge_evaluation_digest"] = new_eval_digest

        if isinstance(final_decision.get("artifact_manifest"), dict):
            final_decision["artifact_manifest"]["judge_evaluation_digest"] = new_eval_digest
            final_decision["artifact_manifest"]["decision_seal_sha256"] = compute_decision_seal(
                final_decision["artifact_manifest"]
            )

        if all_out_pass:
            logger.info("All borderline judge deductions clarified: upgraded overall status to PASS, re-sealed evaluation digest and decision manifest.")
            return True

    return False


def _find_prior_signed_hitl_clarification(
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore],
    short_key: str,
) -> Optional[Dict[str, Any]]:
    """Lookup a prior cryptographically verified judge clarification approval for this meeting."""
    try:
        from dual_pipeline.hitl_governance_core.crypto import (
            compute_alr_subject_digest,
            verify_hitl_approval_entry,
        )
        roster_names = [r.get("name") for r in metadata.get("roster", []) if isinstance(r, dict)]
        if not roster_names:
            roster_names = [a.strip() for a in str(metadata.get("attendees", "")).split(",") if a.strip()]
        subject_dict = {
            "meeting_name": str(metadata.get("meeting_name") or metadata.get("title") or "meeting"),
            "meeting_date": str(metadata.get("date") or metadata.get("meeting_date") or ""),
            "project": str(metadata.get("project") or "knowledge"),
            "attendees": sorted(roster_names),
        }
        subj_digest = (
            metadata.get("alr_subject_sha256")
            or metadata.get("subject_sha256")
            or compute_alr_subject_digest(subject_dict)
        )
        target_case = f"JUDGE_CLARIFICATION_{short_key.upper()}"
        target_ledger = (
            Path(metadata["hitl_ledger_path"]).resolve()
            if metadata.get("hitl_ledger_path")
            else (
                Path(store.ledger_path).resolve()
                if store and hasattr(store, "ledger_path") and store.ledger_path
                else _find_default_ledger_path().resolve()
            )
        )
        if not target_ledger.is_file():
            return None
        json_store = HITLDecisionStore(ledger_path=target_ledger, use_sqlite=False)
        records = json_store._load_records()
        for rec in reversed(records):
            if not isinstance(rec, dict):
                continue
            rec_subj = rec.get("subject_sha256") or (rec.get("payload", {}).get("subject_sha256") if isinstance(rec.get("payload"), dict) else None)
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            if target_case in (payload.get("case_ids") or []) and payload.get("decision") == "APPROVE":
                if rec_subj == subj_digest and verify_hitl_approval_entry(rec):
                    metadata["subject"] = subject_dict
                    metadata["subject_sha256"] = rec_subj
                    metadata["context_sha256"] = rec_subj
                    metadata["alr_subject_sha256"] = rec_subj
                    return rec
    except Exception as exc:
        logger.debug("Prior signed judge clarification lookup skipped: %s", exc)
    return None


def _record_signed_hitl_clarification(
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore],
    short_key: str,
    eval_key: str,
    composite: float,
    receipt: Dict[str, Any],
    op_id: str,
) -> None:
    """Record cryptographic signed approval to hitl_decision_ledger.json."""
    try:
        from dual_pipeline.hitl_governance_core.crypto import (
            append_signed_approval_to_ledger,
            compute_alr_subject_digest,
            create_signed_hitl_approval_entry,
            get_governed_hitl_keypair,
        )
        roster_names = [r.get("name") for r in metadata.get("roster", []) if isinstance(r, dict)]
        if not roster_names:
            roster_names = [a.strip() for a in str(metadata.get("attendees", "")).split(",") if a.strip()]
        subject_dict = {
            "meeting_name": str(metadata.get("meeting_name") or metadata.get("title") or "meeting"),
            "meeting_date": str(metadata.get("date") or metadata.get("meeting_date") or ""),
            "project": str(metadata.get("project") or "knowledge"),
            "attendees": sorted(roster_names),
        }
        subj_digest = compute_alr_subject_digest(subject_dict)
        metadata["subject"] = subject_dict
        metadata["subject_sha256"] = subj_digest
        metadata["context_sha256"] = subj_digest
        metadata["alr_subject_sha256"] = subj_digest

        priv, pub = get_governed_hitl_keypair()
        signed_entry = create_signed_hitl_approval_entry(
            subject_sha256=subj_digest,
            case_ids=[f"JUDGE_CLARIFICATION_{short_key.upper()}"],
            decision="APPROVE",
            operator_id=op_id,
            private_key=priv,
            public_key=pub,
            meeting_slug=metadata.get("meeting_name") or None,
            meeting_date=metadata.get("date") or None,
            metadata={
                "evaluation_key": eval_key,
                "composite_score": composite,
                "receipt_digest": receipt.get("receipt_digest"),
            },
        )
        target_ledger = (
            Path(metadata["hitl_ledger_path"]).resolve()
            if metadata.get("hitl_ledger_path")
            else (
                Path(store.ledger_path).resolve()
                if store and hasattr(store, "ledger_path") and store.ledger_path
                else _find_default_ledger_path().resolve()
            )
        )
        append_signed_approval_to_ledger(signed_entry, target_ledger)
    except Exception as e:
        logger.warning("Failed to record signed HITL approval for judge clarification: %s", e)


def assign_clarified_graph_verdict(
    parsed: Dict[str, Any],
    dimension_keys: Sequence[str],
    dimension_floors: Mapping[str, int],
) -> Dict[str, Any]:
    """Compute composite score and assign PASS, FAIL, or HITL_REQUIRED."""
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    dim_scores = [parsed.get(k) for k in dimension_keys if parsed.get(k) is not None]
    if len(dim_scores) == len(dimension_keys):
        parsed["composite_score"] = sum(int(s) for s in dim_scores)

    from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT, is_document_pipeline_mode  # noqa: lazy-decouple
    eff_mode = (
        parsed.get("pipeline_mode")
        or parsed.get("modality")
        or parsed.get("meeting_archetype")
        or parsed.get("mode")
    )
    is_doc_mode = is_document_pipeline_mode(eff_mode)
    contract_floors = {
        k: int(v)
        for k, v in OUTPUT3_CONTRACT.get_dimension_floors(
            pipeline_mode="DOCUMENT_MODE" if is_doc_mode else None
        ).items()
    }
    active_floors = contract_floors if is_doc_mode else {
        k: dimension_floors.get(k, contract_floors.get(k, 0))
        for k in dimension_keys
        if k in dimension_floors or k in contract_floors
    }

    floor_failures = []
    for k in dimension_keys:
        val = parsed.get(k)
        floor = active_floors.get(k)
        if val is not None and floor is not None and int(val) < floor:
            floor_failures.append(f"{k}={val} (floor={floor})")

    score = int(parsed.get("composite_score", 0) or 0)

    if floor_failures:
        is_borderline_floor = len(floor_failures) == 1 and all(
            int(parsed.get(k, 0)) >= (active_floors.get(k, 0) - 1)
            for k in dimension_keys if parsed.get(k) is not None and k in active_floors and int(parsed.get(k)) < active_floors.get(k, 0)
        )
        if is_borderline_floor and score >= 85:
            parsed["verdict"] = "HITL_REQUIRED"
            parsed["forensic_rationale"] = f"Borderline floor deduction: {', '.join(floor_failures)}. {parsed.get('forensic_rationale', '')}"
            return parsed

        parsed["verdict"] = "FAIL"
        parsed["status"] = "FAIL"
        parsed["forensic_rationale"] = f"Per-dimension floor failure (Rubric §5.3): {', '.join(floor_failures)}. {parsed.get('forensic_rationale', '')}"
        return parsed

    from dual_pipeline.llm_judge_core.calibration_protocol import get_calibrated_borderline_band
    priors_p = parsed.get("priors_path")
    band_floor, band_ceiling = get_calibrated_borderline_band("graph", output_num=3, priors_path=priors_p)

    if band_floor <= score < band_ceiling or str(parsed.get("verdict")).upper() == "HITL_REQUIRED":
        parsed["verdict"] = "HITL_REQUIRED"
        return parsed

    parsed["verdict"] = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, score).value
    return parsed
