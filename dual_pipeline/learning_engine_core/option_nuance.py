"""
dual_pipeline/learning_engine_core/option_nuance.py

Synthesizes sharper HITL option nuance templates and widened posterior margins from the HITL ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from dual_pipeline.core import write_json
from dual_pipeline.hitl_governance_core.bayesian import (
    canonicalize_pattern_key,
    compute_bayesian_calibrated_score,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore


def _is_eligible_human_record(rec: Any) -> bool:
    if not isinstance(rec, dict):
        return False
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    if str(meta.get("operator_class") or "").upper() == "AUTONOMOUS":
        return False
    op_id = str(rec.get("operator_id") or "").lower()
    return op_id not in ("autonomous_engine", "auto", "system_auto")


def _record_selected_option(
    option_stats: Dict[str, Dict[str, Any]],
    sel_key: str,
    cat: str,
    rationale: str,
) -> None:
    if not sel_key:
        return
    entry = option_stats.setdefault(
        sel_key,
        {
            "canonical_key": sel_key,
            "category": cat,
            "affirmed_count": 0,
            "rejected_count": 0,
            "rationales": [],
        },
    )
    entry["affirmed_count"] += 1
    if rationale and rationale not in entry["rationales"]:
        entry["rationales"].append(rationale)


def _record_unselected_options(
    option_stats: Dict[str, Dict[str, Any]],
    options: List[Any],
    sel_key: str,
    cat: str,
    outcome: str,
) -> None:
    for opt in options:
        opt_str = str(opt.get("label") or opt.get("text") or "") if isinstance(opt, dict) else str(opt)
        opt_k = canonicalize_pattern_key(opt_str)
        if not opt_k or opt_k == sel_key:
            continue
        r_entry = option_stats.setdefault(
            opt_k,
            {
                "canonical_key": opt_k,
                "category": cat,
                "affirmed_count": 0,
                "rejected_count": 0,
                "rationales": [],
            },
        )
        r_entry["rejected_count"] += 1
        if outcome == "OVERRIDDEN":
            r_entry["overridden_from"] = sel_key


def _collect_option_stats(history: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    option_stats: Dict[str, Dict[str, Any]] = {}
    for rec in history:
        if not _is_eligible_human_record(rec):
            continue
        meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
        cat = str(rec.get("category") or "general").strip().lower()
        sel_raw = str(rec.get("selected_key") or rec.get("selected_option") or "").strip()
        sel_key = canonicalize_pattern_key(sel_raw)
        outcome = str(meta.get("outcome_type") or rec.get("outcome_type") or "AFFIRMED").upper()
        rationale = str(rec.get("rationale") or "").strip()

        _record_selected_option(option_stats, sel_key, cat, rationale)
        _record_unselected_options(option_stats, rec.get("options") or [], sel_key, cat, outcome)
    return option_stats


def _build_synthesized_entry(key: str, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aff = int(st["affirmed_count"])
    rej = int(st["rejected_count"])
    total = aff + rej
    if total == 0:
        return None
    cal = compute_bayesian_calibrated_score(
        base_score=0.55 if aff >= rej else 0.45,
        affirmed_weight=float(aff) * 1.5,
        rejected_weight=float(rej) * 1.5,
        prior_strength=2.0,
        sample_count=total,
    )
    win_rate = round((aff / total) * 100.0)
    if aff > rej:
        summary = f"Operator-preferred choice affirmed in {aff} of {total} prior reviews ({win_rate}% selection rate)"
        impact = "Ensures execution follows the proven workflow pattern that consistently passed operator review without rework"
    else:
        summary = f"Alternative choice bypassed in {rej} of {total} prior operator reviews"
        impact = "Preserves the secondary fallback path when standard conditions do not apply"
    return {
        "canonical_key": key,
        "category": st["category"],
        "affirmed_count": aff,
        "rejected_count": rej,
        "sample_count": total,
        "posterior_confidence": float(cal.point_estimate),
        "posterior_tier": cal.tier,
        "auto_apply": False,
        "approval_required": True,
        "plain_english_summary": summary,
        "impact_statement": impact,
    }


def _format_healing_option_entry(canon_k: str, stats: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    total = int(stats.get("total_count") or stats.get("total") or 0)
    if not canon_k or total == 0:
        return None
    healed = int(stats.get("success_count") or stats.get("healed") or 0)
    unrep = int(stats.get("failure_count") or 0)
    post_mean = float(stats.get("posterior_confidence") or stats.get("posterior_mean") or 0.50)
    tier = "HIGH" if post_mean >= 0.85 else ("MEDIUM" if post_mean >= 0.50 else "LOW")
    win_rate = round((healed / total) * 100.0)
    summary = (
        f"Empirically verified healing path with {healed} of {total} successful repairs ({win_rate}% success rate)"
        if healed >= unrep
        else f"Healing path required escalation or failed in {unrep} of {total} prior runs"
    )
    impact = (
        "Ensures reliable repair execution and preserves verified document sections across runs"
        if healed >= unrep
        else "Prevents unreliable automated edits and ensures operator review before modifying content"
    )
    return {
        "canonical_key": canon_k,
        "category": "healing_policy",
        "affirmed_count": healed,
        "rejected_count": unrep,
        "sample_count": total,
        "posterior_confidence": post_mean,
        "posterior_tier": tier,
        "plain_english_summary": summary,
        "impact_statement": impact,
    }


def _extract_healing_option_name(raw_key: str) -> Optional[str]:
    for prefix in ("repair_class:", "subsystem:"):
        if raw_key.startswith(prefix):
            return raw_key.split(prefix, 1)[1]
    return None


def _collect_healing_option_entries(healing_db_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if healing_db_path is not None and not Path(healing_db_path).exists():
        return {}
    try:
        from dual_pipeline.storage.healing_decision_store import HealingDecisionStore

        posteriors = HealingDecisionStore(db_path=healing_db_path).compute_empirical_posteriors()
        entries: Dict[str, Dict[str, Any]] = {}
        for raw_key, stats in posteriors.items():
            clean_name = _extract_healing_option_name(raw_key) if isinstance(stats, dict) else None
            if not clean_name:
                continue
            canon_k = canonicalize_pattern_key(clean_name)
            if canon_k and canon_k not in entries:
                entry = _format_healing_option_entry(canon_k, stats)
                if entry is not None:
                    entries[canon_k] = entry
        return entries
    except Exception:
        return {}


def synthesize_hitl_option_nuance(
    target_ledger: Path,
    dest_store_path: Path,
    healing_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mine HITL decision ledger and HealingDecisionStore to synthesize sharper option nuance templates and widened posterior margins."""
    store = HITLDecisionStore(ledger_path=target_ledger)
    history = store.get_history(None)
    option_stats = _collect_option_stats(history)

    synthesized_options: Dict[str, Dict[str, Any]] = {}
    for key, st in option_stats.items():
        entry = _build_synthesized_entry(key, st)
        if entry is not None:
            synthesized_options[key] = entry

    for h_key, h_entry in _collect_healing_option_entries(healing_db_path).items():
        synthesized_options.setdefault(h_key, h_entry)

    repo_root = Path(__file__).resolve().parents[2]
    try:
        rel_ledger = str(target_ledger.resolve().relative_to(repo_root.resolve()))
    except Exception:
        rel_ledger = f".local/ledger/{target_ledger.name}"
    payload = {
        "schema_version": "1.0.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_ledger": rel_ledger,
        "auto_apply": False,
        "approval_required": True,
        "total_learned_options": len(synthesized_options),
        "options": synthesized_options,
    }
    write_json(dest_store_path, payload)
    return payload


def recalibrate_post_learning_confidences_core(
    runtime_root: Path,
    proposals_dir: Path,
    hitl_ledger_path: Optional[Path] = None,
    healing_db_path: Optional[Path] = None,
    output_store_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Recalibrate HITL and healing option confidences post-learning and persist updated option store."""
    from dual_pipeline.hitl_governance import resolve_hitl_ledger_path
    from dual_pipeline.storage.healing_decision_store import HealingDecisionStore

    target_store = output_store_path or (runtime_root / "learned_hitl_option_store.json")
    eff_healing_db = healing_db_path or (runtime_root / "ledger" / "learning.sqlite")
    nuance_payload = synthesize_hitl_option_nuance(
        hitl_ledger_path or resolve_hitl_ledger_path(),
        target_store,
        healing_db_path=eff_healing_db if eff_healing_db.exists() else None,
    )
    cfg_rt = runtime_root / "config" / "runtime"
    if output_store_path is None and cfg_rt.is_dir():
        write_json(cfg_rt / "learned_hitl_option_store.json", nuance_payload)
    healing_posteriors: Dict[str, Any] = {}
    if eff_healing_db.exists():
        h_store = HealingDecisionStore(db_path=eff_healing_db)
        healing_posteriors = h_store.compute_empirical_posteriors()
        write_json(
            runtime_root / "learned_healing_posteriors.json",
            {
                "schema_version": "1.0.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_decisions": len(h_store.list_decisions(limit=10000)),
                "posteriors": healing_posteriors,
            },
        )
    return {
        "status": "RECALIBRATED",
        "learned_option_store_path": str(target_store),
        "total_learned_options": nuance_payload.get("total_learned_options", 0),
        "options": nuance_payload.get("options", {}),
        "healing_posteriors": healing_posteriors,
    }
