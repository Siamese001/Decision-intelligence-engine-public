"""
dual_pipeline/hitl_governance_core/operator_roster.py

Operator alias roster normalization, operator classification, and decision-latency SLO tracking.
Governed under Wave 10 DLC Hardening (Findings X-25 to X-27).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

# Canonical operator alias mapping
OPERATOR_ROSTER_MAP: dict[str, str] = {
    # Amit Ayer variations
    "amit ayer": "amit_ayer",
    "amit.ayer": "amit_ayer",
    "amit_ayer": "amit_ayer",
    "amitayer": "amit_ayer",
    "operator_amitayer": "amit_ayer",
    # Autonomous engine variations
    "autonomous_context": "autonomous_engine",
    "autonomous_engine": "autonomous_engine",
    "auto_engine": "autonomous_engine",
    "auto": "autonomous_engine",
    "autonomous": "autonomous_engine",
    # Generic human operator variations
    "human_operator": "human_operator",
    "operator": "human_operator",
    "operator_1": "human_operator",
    "user": "human_operator",
    "human": "human_operator",
    # Operator verified marker
    "operator_verified": "operator_verified",
    # Mock / test operator variations
    "mock_operator": "mock_operator",
    "test_operator": "mock_operator",
    "pytest": "mock_operator",
    "unit_test": "mock_operator",
}

# Decision-latency Service Level Objectives (SLOs) in milliseconds
DECISION_LATENCY_SLO_MS: dict[str, int] = {
    "HUMAN": 300_000,  # 5 minutes interactive SLA
    "AUTONOMOUS": 5_000,  # 5 seconds automated decision SLA
    "TEST": 10_000,  # 10 seconds test harness SLA
}


def normalize_operator_id(operator_id: str | None) -> str:
    """Normalize an operator identifier to its canonical roster entry."""
    if not operator_id:
        return "human_operator"
    clean = str(operator_id).strip()
    return OPERATOR_ROSTER_MAP.get(clean.lower(), clean)


def derive_operator_class(operator_id: str, meta: Mapping[str, Any] | None = None) -> str:
    """Determine the operator class (HUMAN, AUTONOMOUS, TEST) from operator ID and metadata."""
    if meta and "operator_class" in meta:
        return str(meta["operator_class"]).strip().upper()

    op_norm = normalize_operator_id(operator_id).lower()
    if meta and meta.get("is_test"):
        return "TEST"
    if op_norm in ("mock_operator", "test_operator") or any(
        k in op_norm for k in ("test", "mock", "pytest")
    ):
        return "TEST"
    if op_norm == "autonomous_engine" or "autonomous" in op_norm or "auto" in op_norm:
        return "AUTONOMOUS"
    return "HUMAN"


def check_decision_latency_slo(operator_class: str, latency_ms: int | float) -> dict[str, Any]:
    """Evaluate decision resolution latency against defined SLO targets."""
    op_class = operator_class.strip().upper()
    target_ms = DECISION_LATENCY_SLO_MS.get(op_class, DECISION_LATENCY_SLO_MS["HUMAN"])
    actual_ms = max(0, int(round(latency_ms)))
    slo_met = actual_ms <= target_ms
    return {
        "operator_class": op_class,
        "target_ms": target_ms,
        "actual_ms": actual_ms,
        "slo_met": slo_met,
        "status": "MET" if slo_met else "EXCEEDED",
    }


def _parse_iso_to_utc(ts_str: str) -> datetime | None:
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def stamp_decision_latency(
    presented_at: str | None,
    resolved_at: str | None,
    operator_class: str,
    explicit_latency_ms: int | None = None,
) -> dict[str, Any]:
    """Stamp presented_at, resolved_at, resolution_latency_ms, and evaluate SLO compliance."""
    now_iso = datetime.now(timezone.utc).isoformat()
    pres = presented_at or now_iso
    res = resolved_at or now_iso

    if explicit_latency_ms is not None:
        latency_ms = max(0, int(explicit_latency_ms))
    else:
        dt_pres = _parse_iso_to_utc(pres)
        dt_res = _parse_iso_to_utc(res)
        if dt_pres and dt_res:
            latency_ms = max(0, int(round((dt_res - dt_pres).total_seconds() * 1000)))
        else:
            latency_ms = 0

    slo = check_decision_latency_slo(operator_class, latency_ms)
    return {
        "presented_at": pres,
        "resolved_at": res,
        "resolution_latency_ms": latency_ms,
        "latency_slo_status": slo["status"],
        "latency_slo": slo,
    }
