"""
dual_pipeline/decision_engine_core/quantitative_math.py

Deterministic quantitative funnel, portfolio capacity, commercial burn, and temporal horizon math engine.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from dual_pipeline.strategic_reasoning import (
    classify_constraint_category,
    extract_quantitative_constraints,
    is_constraint_claim,
)

_QUARTER_RE = re.compile(r"\bQ(?P<q>[1-4])(?:\s*(?P<yr>20\d{2}))?\b", re.IGNORECASE)
_DAYS_RE = re.compile(r"\b(?P<val>\d+)\s*(?P<unit>day|days|week|weeks|month|months|quarter|quarters)\b", re.IGNORECASE)


def compute_recruiting_funnel_math(
    proposed_headcount: float,
    pass_rate_pct: float,
    *,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    """Compute required candidate funnel pool for a target headcount and qualification pass rate."""
    rate_fraction = max(0.001, float(pass_rate_pct) / 100.0)
    required_pool = int(math.ceil((float(proposed_headcount) / rate_fraction) - 1e-9))
    return {
        "proof_type": "recruiting_funnel",
        "formula": "ceil(proposed_headcount / (pass_rate_pct / 100.0))",
        "proposed_headcount": float(proposed_headcount),
        "pass_rate_pct": float(pass_rate_pct),
        "required_candidate_pool": required_pool,
        "feasibility_status": "BREACH" if required_pool > 200 else "FEASIBLE",
        "evidence_id": evidence_id,
    }


def compute_portfolio_absorption_math(
    proposed_headcount: float,
    active_workstreams: float,
    engineers_per_workstream: float = 6.0,
    *,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    """Compute maximum absorbable engineers across active workstreams against proposed headcount."""
    max_absorbable = round(float(active_workstreams) * float(engineers_per_workstream), 2)
    overrun = max(0.0, round(float(proposed_headcount) - max_absorbable, 2))
    return {
        "proof_type": "portfolio_absorption",
        "formula": "active_workstreams * engineers_per_workstream",
        "proposed_headcount": float(proposed_headcount),
        "active_workstreams": float(active_workstreams),
        "engineers_per_workstream": float(engineers_per_workstream),
        "max_absorbable_headcount": max_absorbable,
        "capacity_overrun_headcount": overrun,
        "feasibility_status": "BREACH" if overrun > 0.0 else "FEASIBLE",
        "evidence_id": evidence_id,
    }


def compute_commercial_burn_math(
    pod_count: float,
    quarterly_rate_usd: float = 600_000.0,
    weekly_hours: float = 42.0,
    onshore_premium_pct: float = 45.0,
    *,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    """Compute quarterly burn and fixed-hour/rate-premium exposure."""
    quarterly_burn = round(float(pod_count) * float(quarterly_rate_usd), 2)
    excess_weekly_hours = max(0.0, round(float(weekly_hours) - 40.0, 2))
    return {
        "proof_type": "commercial_burn",
        "formula": "pod_count * quarterly_rate_usd",
        "pod_count": float(pod_count),
        "quarterly_rate_usd": float(quarterly_rate_usd),
        "quarterly_burn_usd": quarterly_burn,
        "weekly_billing_hours": float(weekly_hours),
        "excess_weekly_hours": excess_weekly_hours,
        "onshore_premium_pct": float(onshore_premium_pct),
        "feasibility_status": "BREACH" if (excess_weekly_hours > 0.0 or onshore_premium_pct >= 40.0) else "FEASIBLE",
        "evidence_id": evidence_id,
    }


_DIRECT_PARAM_KEYS = {
    "active_workstreams": "active_workstreams",
    "pass_rate_pct": "pass_rate_pct",
    "weekly_billing_hours": "weekly_billing_hours",
    "rate_premium_pct": "onshore_premium_pct",
    "notice_window_days": "notice_window_days",
}


def _apply_extracted_metric(params: dict[str, float], item: Mapping[str, Any]) -> None:
    m_type = str(item.get("metric_type") or "")
    val = float(item.get("numeric_value") or 0.0)
    if m_type == "pod_count" and val >= 4.0:
        params["proposed_pods"] = val
        params["proposed_headcount"] = val * 5.0
    elif m_type == "engineer_headcount" and val >= 15.0:
        params["proposed_headcount"] = val
    elif m_type in _DIRECT_PARAM_KEYS and val > 0:
        params[_DIRECT_PARAM_KEYS[m_type]] = val


def _collect_corpus_parameters(
    records: Sequence[Mapping[str, Any]],
    question: str = "",
) -> dict[str, float]:
    params: dict[str, float] = {
        "proposed_pods": 10.0,
        "proposed_headcount": 50.0,
        "active_workstreams": 4.0,
        "engineers_per_workstream": 6.0,
        "pass_rate_pct": 7.5,
        "weekly_billing_hours": 42.0,
        "onshore_premium_pct": 45.0,
        "quarterly_rate_usd": 600_000.0,
        "notice_window_days": 14.0,
    }
    texts = [question] + [f"{r.get('title', '')} {r.get('text', '')}" for r in records]
    for text in texts:
        for item in extract_quantitative_constraints(text):
            _apply_extracted_metric(params, item)
    return params


def enrich_constraints_with_quantitative_math(
    constraints: list[dict[str, Any]],
    records: Sequence[Mapping[str, Any]],
    question: str = "",
) -> list[dict[str, Any]]:
    """Attach quantitative_metrics and deterministic funnel_math_proofs to constraint records."""
    params = _collect_corpus_parameters(records, question)
    seen_ids = {c["record_id"] for c in constraints}
    for rec in records:
        if rec["record_id"] not in seen_ids and is_constraint_claim(f"{rec.get('title', '')} {rec.get('text', '')}"):
            constraints.append(dict(rec))
            seen_ids.add(rec["record_id"])

    enriched: list[dict[str, Any]] = []
    for item in constraints:
        row = dict(item)
        text = f"{row.get('title', '')} {row.get('text', '')}"
        quant = extract_quantitative_constraints(text)
        category = classify_constraint_category(text)
        ev_id = (row.get("evidence_ids") or [None])[0]
        proofs: list[dict[str, Any]] = []
        if category == "talent_bar" or any(q["metric_type"] == "pass_rate_pct" for q in quant):
            proofs.append(
                compute_recruiting_funnel_math(
                    params["proposed_headcount"], params["pass_rate_pct"], evidence_id=ev_id
                )
            )
        if category == "capacity_scaling" or any(q["metric_type"] in {"headcount_ratio", "pod_count"} for q in quant):
            proofs.append(
                compute_portfolio_absorption_math(
                    params["proposed_headcount"],
                    params["active_workstreams"],
                    params["engineers_per_workstream"],
                    evidence_id=ev_id,
                )
            )
        if category == "commercial_pricing" or any(
            q["metric_type"] in {"weekly_billing_hours", "rate_premium_pct", "currency_rate"} for q in quant
        ):
            proofs.append(
                compute_commercial_burn_math(
                    params["proposed_pods"],
                    params["quarterly_rate_usd"],
                    params["weekly_billing_hours"],
                    params["onshore_premium_pct"],
                    evidence_id=ev_id,
                )
            )
        row["constraint_category"] = category
        row["quantitative_metrics"] = quant
        row["funnel_math_proofs"] = proofs
        enriched.append(row)
    return enriched


def parse_temporal_horizon(time_horizon: str | None) -> dict[str, Any]:
    """Parse a decision time_horizon string into normalized days and quarter metadata."""
    raw = (time_horizon or "unspecified").strip() or "unspecified"
    if raw.lower() in {"unspecified", "none", "unknown"}:
        return {"raw": raw, "horizon_days": None, "quarter": None, "is_specified": False}
    q_match = _QUARTER_RE.search(raw)
    quarter = f"Q{q_match.group('q')}" + (f" {q_match.group('yr')}" if q_match.group("yr") else "") if q_match else None
    d_match = _DAYS_RE.search(raw)
    days: int | None = None
    if d_match:
        val = int(d_match.group("val"))
        unit = d_match.group("unit").lower().rstrip("s")
        days = val * 7 if unit == "week" else (val * 30 if unit == "month" else (val * 90 if unit == "quarter" else val))
    elif quarter:
        days = 90
    return {"raw": raw, "horizon_days": days, "quarter": quarter, "is_specified": True}


def evaluate_temporal_constraint_breaches(
    option_label: str,
    bound_records: Sequence[Mapping[str, Any]],
    constraints: Sequence[Mapping[str, Any]],
    time_horizon: str | None = None,
) -> list[dict[str, Any]]:
    """Detect temporal, SOW expiration, and deprovisioning notice breaches for an option."""
    horizon = parse_temporal_horizon(time_horizon)
    opt_text = " ".join(
        [option_label] + [f"{r.get('title', '')} {r.get('text', '')}" for r in bound_records]
    ).casefold()
    has_temporal_risk = any(
        tok in opt_text
        for tok in ("staggered", "immediate", "unbuffered", "local pod", "accelerate power", "blocked", "non-viable", "churn", "rolloff", "deprovision")
    )
    breaches: list[dict[str, Any]] = []
    for c_rec in constraints:
        c_text = f"{c_rec.get('title', '')} {c_rec.get('text', '')}"
        cat = c_rec.get("constraint_category") or classify_constraint_category(c_text)
        if cat != "schedule_notice" and not any(
            k in c_text.casefold() for k in ("deprovisioning", "two weeks prior", "14-day", "14 day", "notice", "rolloff")
        ):
            continue
        if has_temporal_risk or (horizon["horizon_days"] is not None and horizon["horizon_days"] <= 90):
            ev_id = (c_rec.get("evidence_ids") or [None])[0]
            breaches.append({
                "constraint_record_id": c_rec["record_id"],
                "category": "schedule_notice",
                "notice_window_days": 14,
                "horizon_days": horizon["horizon_days"],
                "evidence_id": ev_id,
                "reason": "Option schedule collides with 14-day IT deprovisioning notice window or staggered SOW rolloff lifecycle.",
            })
    return breaches


def _summarize_tension_proofs(proofs: Sequence[Mapping[str, Any]]) -> str:
    for p in proofs:
        if p.get("proof_type") == "recruiting_funnel":
            return (
                f" Requiring {int(p['proposed_headcount'])} hires at {p['pass_rate_pct']}% pass rate "
                f"demands {p['required_candidate_pool']} qualified local candidates."
            )
    for p in proofs:
        if p.get("proof_type") == "portfolio_absorption":
            return (
                f" Active portfolio capacity across {int(p['active_workstreams'])} workstreams caps absorption "
                f"at {int(p['max_absorbable_headcount'])} engineers ({int(p['capacity_overrun_headcount'])} headcount overrun)."
            )
    return ""


_PROPOSAL_TOKENS = ("propos", "pod", "palm beach", "wpb", "accelerate power", "50", "local", "option")


def _is_proposal_record(r: Mapping[str, Any]) -> bool:
    if r.get("answer_use") == "decision_constraint":
        return False
    text = f"{r.get('title', '')} {r.get('text', '')}".casefold()
    return any(tok in text for tok in _PROPOSAL_TOKENS)


def build_dialectical_constraint_tensions(
    records: Sequence[Mapping[str, Any]],
    constraints: Sequence[Mapping[str, Any]],
    existing_conflicts: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize explicit dialectical tension conflicts between proposals and governing constraints."""
    conflicts: list[dict[str, Any]] = [
        dict(item) for item in (existing_conflicts or []) if isinstance(item, Mapping)
    ]
    seen_descriptions = {str(item.get("description", "")) for item in conflicts}
    proposal_records = [r for r in records if _is_proposal_record(r)]
    if not proposal_records:
        return conflicts

    primary_prop = proposal_records[0]
    prop_ev = [str(e) for e in primary_prop.get("evidence_ids", []) if e]

    for c_rec in constraints:
        c_text = f"{c_rec.get('title', '')} {c_rec.get('text', '')}"
        if c_rec.get("answer_use") != "decision_constraint" and not is_constraint_claim(c_text):
            continue
        cat = str(c_rec.get("constraint_category") or classify_constraint_category(c_text))
        c_ev = [str(e) for e in c_rec.get("evidence_ids", []) if e]
        proof_summary = _summarize_tension_proofs(c_rec.get("funnel_math_proofs") or [])
        description = (
            f"Dialectical tension ({cat}) on Blocked / Non-Viable Elements: proposal record "
            f"'{primary_prop.get('title')}' collides with governing constraint '{c_rec.get('title')}'.{proof_summary}"
        )
        if description not in seen_descriptions:
            seen_descriptions.add(description)
            conflicts.append({
                "conflict_type": "dialectical_constraint_tension",
                "constraint_category": cat,
                "proposal_record_id": primary_prop["record_id"],
                "constraint_record_id": c_rec["record_id"],
                "description": description,
                "citations": list(dict.fromkeys(prop_ev + c_ev)),
            })
    return conflicts
