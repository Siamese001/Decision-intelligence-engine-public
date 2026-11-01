"""
dual_pipeline/hitl_governance_core/telemetry.py

Enriched telemetry derivation and canonical ledger path resolution for HITL governance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bayesian import _match_keys
from .surfacing import _option_key, parse_confidence_score


def resolve_hitl_ledger_path(repo_root: Path | str | None = None) -> Path:
    """Locate the single canonical governed decision ledger across workspace, worktrees, and git common dir."""
    from .decision_record_writer import resolve_ledger_paths

    _, j_path = resolve_ledger_paths(repo_root=repo_root)
    return j_path


def _derive_operator_class(operator_id: str, meta: dict[str, Any]) -> str:
    if "operator_class" in meta:
        return str(meta["operator_class"]).strip().upper()
    op_lower = str(operator_id).lower().strip()
    if any(k in op_lower for k in ("test", "mock", "pytest", "unit_test")) or meta.get("is_test"):
        return "TEST"
    if "autonomous" in op_lower or op_lower in ("auto_engine", "auto", "autonomous_context"):
        return "AUTONOMOUS"
    return "HUMAN"


def _find_recommended_option(options: list[Any]) -> Any | None:
    for opt in options:
        if isinstance(opt, dict) and (opt.get("recommended") or opt.get("is_recommended")):
            return opt
    return options[0] if options else None


def _derive_outcome_type(
    operator_class: str,
    meta: dict[str, Any],
    selected_key: str,
    options: list[Any],
) -> str:
    if "outcome_type" in meta:
        return str(meta["outcome_type"])
    if operator_class == "AUTONOMOUS":
        return "AUTONOMOUS"
    rec_opt = _find_recommended_option(options)
    if rec_opt is not None:
        rec_key = _option_key(rec_opt)
        return "AFFIRMED" if _match_keys(selected_key, rec_key) else "OVERRIDDEN"
    return "AFFIRMED"


def _derive_margin(meta: dict[str, Any], options: list[Any]) -> float:
    if "margin_at_decision" in meta:
        return float(meta["margin_at_decision"])
    if len(options) >= 2:
        try:
            scores = sorted([parse_confidence_score(o, require_confidence=False) for o in options], reverse=True)
            if any(s > 0.0 for s in scores):
                return round(scores[0] - scores[1], 4)
        except Exception:
            pass
    return 1.0


def _derive_confidence_interval(meta: dict[str, Any], selected_option: Any) -> list[float]:
    if "confidence_interval_at_decision" in meta:
        return meta["confidence_interval_at_decision"]
    if isinstance(selected_option, dict):
        if "confidence_interval" in selected_option:
            return selected_option["confidence_interval"]
        if "confidence_lower" in selected_option and "confidence_upper" in selected_option:
            return [selected_option["confidence_lower"], selected_option["confidence_upper"]]
    try:
        s_score = parse_confidence_score(selected_option, require_confidence=False)
    except Exception:
        s_score = 0.0
    return [max(0.05, round(s_score - 0.20, 4)), min(0.99, round(s_score + 0.20, 4))]


def _derive_sample_count(
    meta: dict[str, Any],
    selected_option: Any,
    records: list[dict[str, Any]],
    category: str,
    selected_key: str,
) -> int:
    if "sample_count_at_presentation" in meta:
        return int(meta["sample_count_at_presentation"])
    if isinstance(selected_option, dict) and "confidence_sample_count" in selected_option:
        return int(selected_option["confidence_sample_count"])
    from .learning_eligibility import deduplicate_records_for_learning, is_test_record

    deduped = deduplicate_records_for_learning(records)
    count = 0
    for r in deduped:
        if is_test_record(r):
            continue
        if str(r.get("category", "")).strip() != category:
            continue
        if _match_keys(str(r.get("selected_key", "")), selected_key):
            count += 1
    return count


def _derive_enriched_telemetry(
    operator_id: str,
    selected_key: str,
    selected_option: Any,
    options: list[Any],
    category: str,
    metadata: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = dict(metadata or {})
    operator_class = _derive_operator_class(operator_id, meta)
    meta["operator_class"] = operator_class
    meta["outcome_type"] = _derive_outcome_type(operator_class, meta, selected_key, options)
    meta["margin_at_decision"] = _derive_margin(meta, options)
    meta["confidence_interval_at_decision"] = _derive_confidence_interval(
        meta, selected_option
    )
    meta["sample_count_at_presentation"] = _derive_sample_count(
        meta, selected_option, records, category, selected_key
    )
    return meta
