"""
dual_pipeline/hitl_governance_core/pre_surfacing.py

Pre-surfacing autonomous gate and anti-leak audit receipt generator.
Evaluates candidate options under confidence spread (Delta >= 15%), epistemic entropy,
and criticality tier boundaries to determine whether operator interruption is necessary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.core import digest, stable_id, write_json
from dual_pipeline.hitl_criticality import CriticalityTier

from .surfacing import (
    DEFAULT_MARGIN_THRESHOLD,
    DEFAULT_SURFACING_MODE,
    HitlSurfacingMode,
    evaluate_hitl_surfacing_gate,
)


def evaluate_pre_surfacing_gate(
    options: list[Any],
    decision_id: str | None = None,
    category: str | None = None,
    margin_threshold: float | None = None,
    surfacing_mode: HitlSurfacingMode | str = DEFAULT_SURFACING_MODE,
    criticality_tier: CriticalityTier | int | None = None,
    context: Mapping[str, Any] | None = None,
    entropy_threshold: float | None = None,
    min_confidence_floor: float | None = None,
    normalized_entropy_threshold: float | None = None,
    decision_store: Any | None = None,
    audit_receipt_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Evaluate candidate options through the pre-surfacing autonomous gate.

    If margin >= margin_threshold (e.g. 15%) and not overridden by criticality or entropy,
    the gate autonomously resolves the decision without operator interruption and emits
    an audit receipt conforming to urn:nee:aidlc:hitl-autonomous-audit-receipt:1.0.0.
    """
    dec_id = decision_id or stable_id("dec-auto", category or "gen", str(options)[:64])
    gate_res = evaluate_hitl_surfacing_gate(
        options=options,
        margin_threshold=margin_threshold,
        surfacing_mode=surfacing_mode,
        criticality_tier=criticality_tier,
        context=context,
        entropy_threshold=entropy_threshold,
        min_confidence_floor=min_confidence_floor,
        normalized_entropy_threshold=normalized_entropy_threshold,
        decision_store=decision_store,
        category=category,
    )

    should_surface = bool(gate_res.get("should_surface", True))
    resolved_autonomously = not should_surface
    now_iso = datetime.now(timezone.utc).isoformat()

    receipt = {
        "contract_id": "urn:nee:aidlc:hitl-autonomous-audit-receipt:1.0.0",
        "schema_version": "1.0.0",
        "decision_id": dec_id,
        "category": category or "general",
        "resolved_autonomously": resolved_autonomously,
        "should_surface": should_surface,
        "top_option": gate_res.get("top_option"),
        "top_confidence": gate_res.get("top_confidence"),
        "runner_up_confidence": gate_res.get("runner_up_confidence"),
        "margin": gate_res.get("margin"),
        "margin_threshold": gate_res.get("margin_threshold"),
        "epistemic_entropy": gate_res.get("epistemic_entropy"),
        "reason": gate_res.get("reason"),
        "evaluated_at": now_iso,
    }
    receipt["receipt_digest"] = digest(receipt)

    receipt_path = None
    if audit_receipt_dir:
        out_dir = Path(audit_receipt_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = out_dir / f"audit_receipt_{dec_id}.json"
        write_json(receipt_path, receipt)

    return {
        "should_surface": should_surface,
        "resolved_autonomously": resolved_autonomously,
        "top_option": gate_res.get("top_option"),
        "margin": gate_res.get("margin"),
        "audit_receipt": receipt,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "gate_result": gate_res,
    }
