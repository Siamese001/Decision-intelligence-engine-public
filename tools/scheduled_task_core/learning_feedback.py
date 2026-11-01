"""Closed-loop system learning and persistent-store confidence calibration for automation tasks.

Implements INV-AUTOMATION-PERSISTENT-LEARNING-LOOP across all scheduled and
interactive automation agents by:
1. Loading historical operator outcomes (AFFIRMED, OVERRIDDEN, AUTONOMOUS) from
   the persistent store (HITLDecisionStore / HITLSQLiteStore) before surfacing
   or resolving task decisions.
2. Dynamically calibrating candidate decision option confidences via Beta-binomial
   conjugate posterior updates (Laplace smoothing alpha=1, clamped to [0.05, 0.95])
   and evaluating the 20% SSOT ambiguity margin (needs_operator_decision).
3. Persisting resolved decision outcomes back into hitl_decision_ledger.sqlite,
   hitl_decision_ledger.json, and telemetry_ledger.sqlite (learning_event) to
   improve future decision confidence.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.parameters import needs_operator_decision
from tools.scheduled_task_core.authority import ApprovalRecord


def _resolve_persistent_store_paths(run_dir: Path, store_dir: Path | None = None) -> tuple[Path, Path]:
    """Resolves isolated or persistent HITL ledger and SQLite paths."""
    base_dir = store_dir if store_dir is not None else (run_dir / "ledger")
    base_dir.mkdir(parents=True, exist_ok=True)
    return (
        base_dir / "hitl_decision_ledger.json",
        base_dir / "hitl_decision_ledger.sqlite",
    )


def _extract_observation_count(finding: Any) -> int:
    """Extracts observation or recurrence count from a finding dict or object."""
    if isinstance(finding, dict):
        return int(finding.get("observation_count", finding.get("recurrence_count", 1)) or 1)
    return int(getattr(finding, "observation_count", 1) or 1)


def _opt_confidence(opt: dict[str, Any], default: float) -> float:
    """Extracts normalized float confidence from a calibrated option dict."""
    val = opt.get("confidence_score", opt.get("confidence", default))
    return float(val if val is not None else default)


def calibrate_task_decision_options(
    task_name: str,
    findings: list[Any],
    run_dir: Path,
    store_dir: Path | None = None,
) -> dict[str, Any]:
    """Calibrates task remediation options using persistent store feedback priors."""
    ledger_path, sqlite_path = _resolve_persistent_store_paths(run_dir, store_dir)
    store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)
    category = f"automation.{task_name}"

    max_obs = max((_extract_observation_count(f) for f in findings), default=1)
    recurrence_boost = min(0.03, 0.01 * max(0, max_obs - 1))
    base_primary = round(min(0.95, 0.92 + recurrence_boost), 4)
    base_secondary = round(max(0.05, 0.65 - recurrence_boost), 4)

    raw_options = [
        {
            "key": "approve_wave_remediation",
            "option_id": "approve_wave_remediation",
            "label": "Approve Wave-1 Remediation & Execute Verification Gate",
            "confidence_score": base_primary,
            "confidence": base_primary,
            "recommended": True,
        },
        {
            "key": "defer_remediation_keep_open",
            "option_id": "defer_remediation_keep_open",
            "label": "Defer Remediation & Preserve Finding Open in Ledger",
            "confidence_score": base_secondary,
            "confidence": base_secondary,
            "recommended": False,
        },
    ]

    calibrated = store.calibrate_options(options=raw_options, category=category)
    sorted_opts = sorted(
        calibrated,
        key=lambda item: _opt_confidence(item, 0.5),
        reverse=True,
    )
    top_opt = sorted_opts[0]
    second_opt = sorted_opts[1] if len(sorted_opts) > 1 else {"confidence_score": 0.0}

    c_top = _opt_confidence(top_opt, base_primary)
    c_second = _opt_confidence(second_opt, base_secondary)
    margin = round(c_top - c_second, 4)
    sample_count = int(
        top_opt.get("confidence_sample_count", top_opt.get("sample_count", 0)) or 0
    )
    requires_operator = bool(needs_operator_decision(c_top, c_second))

    top_pct = int(round(c_top * 100))
    second_pct = int(round(c_second * 100))
    top_tier = str(
        top_opt.get("confidence_tier")
        or ("HIGH" if c_top >= 0.85 else ("MEDIUM" if c_top >= 0.50 else "LOW"))
    )
    second_tier = str(
        second_opt.get("confidence_tier")
        or ("HIGH" if c_second >= 0.85 else ("MEDIUM" if c_second >= 0.50 else "LOW"))
    )

    return {
        "invariant_id": "INV-AUTOMATION-PERSISTENT-LEARNING-LOOP",
        "domain": category,
        "ledger_path": str(ledger_path),
        "sqlite_path": str(sqlite_path),
        "prior_sample_count": sample_count,
        "calibrated_top_confidence": c_top,
        "calibrated_second_confidence": c_second,
        "calibrated_top_label": f"(Confidence: {top_tier} ({top_pct}%))",
        "calibrated_second_label": f"(Confidence: {second_tier} ({second_pct}%))",
        "margin_at_decision": margin,
        "requires_operator_decision": requires_operator,
        "calibrated_options": calibrated,
    }


def record_task_decision_feedback(
    task_name: str,
    findings: list[Any],
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
    status: str = "COMPLETE",
    store_dir: Path | None = None,
) -> dict[str, Any]:
    """Calibrates options from persistent store and writes resolved decision feedback."""
    calibration = calibrate_task_decision_options(
        task_name=task_name,
        findings=findings,
        run_dir=run_dir,
        store_dir=store_dir,
    )
    ledger_path = Path(calibration["ledger_path"])
    sqlite_path = Path(calibration["sqlite_path"])
    store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

    calibrated_opts = calibration["calibrated_options"]
    rec_opt = next((o for o in calibrated_opts if o.get("recommended")), calibrated_opts[0])
    alt_opt = next((o for o in calibrated_opts if not o.get("recommended")), calibrated_opts[-1])

    feedback_recorded = False
    outcome_type = "AWAITING_OPERATOR"
    selected_option_id = str(rec_opt.get("key") or rec_opt.get("option_id") or "approve_wave_remediation")
    decision_id = f"dec-{task_name}-{uuid.uuid4().hex[:8]}"

    if approval is not None:
        approval_dict = approval.to_dict() if hasattr(approval, "to_dict") else dict(approval)
        override_flag = bool(approval_dict.get("override_recommended", False))
        chosen_opt = alt_opt if override_flag else rec_opt
        selected_option_id = str(chosen_opt.get("key") or chosen_opt.get("option_id"))
        record = store.record_decision(
            decision_id=decision_id,
            category=calibration["domain"],
            prompt=f"HITL Decision: Wave Remediation Gate for {task_name}",
            options=calibrated_opts,
            selected_option=chosen_opt,
            rationale=f"Operator resolved {task_name} wave decision via persistent feedback loop.",
            operator_id="human_operator",
            metadata={
                "task_name": task_name,
                "findings_count": len(findings),
                "status": status,
                "outcome_type": "OVERRIDDEN" if override_flag else "AFFIRMED",
            },
        )
        feedback_recorded = True
        outcome_type = str(record.get("outcome_type", "OVERRIDDEN" if override_flag else "AFFIRMED"))
    elif status in {"COMPLETE", "COMPLETED", "COMPLETE_NOOP", "NO_ACTION", "PASS"} and not calibration["requires_operator_decision"]:
        record = store.record_decision(
            decision_id=decision_id,
            category=calibration["domain"],
            prompt=f"Autonomous Verification Gate for {task_name}",
            options=calibrated_opts,
            selected_option=rec_opt,
            rationale=f"Autonomous verification completed for {task_name} (margin > 20% SSOT).",
            operator_id="autonomous_agent",
            metadata={
                "task_name": task_name,
                "findings_count": len(findings),
                "status": status,
                "autonomous": True,
                "outcome_type": "AUTONOMOUS",
            },
        )
        feedback_recorded = True
        outcome_type = str(record.get("outcome_type", "AUTONOMOUS"))

    post_calibration = calibrate_task_decision_options(
        task_name=task_name,
        findings=findings,
        run_dir=run_dir,
        store_dir=store_dir,
    )

    receipt_payload = {
        **calibration,
        "feedback_recorded": feedback_recorded,
        "outcome_type": outcome_type,
        "selected_option_id": selected_option_id,
        "post_feedback_top_confidence": post_calibration["calibrated_top_confidence"],
        "post_feedback_sample_count": post_calibration["prior_sample_count"],
    }
    receipt_path = run_dir / "learning_feedback_receipt.json"
    receipt_path.write_text(json.dumps(receipt_payload, indent=2, sort_keys=True), encoding="utf-8")
    receipt_payload["receipt_path"] = str(receipt_path)
    return receipt_payload
