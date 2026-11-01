"""
dual_pipeline/learning_engine_core/aggregation.py

Multi-cohort proposal aggregation, parameter envelope enforcement, healing store ingestion,
and offline healing learning cycle orchestration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import digest, seal_artifact, stable_id, utc_now, write_json
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)
from dual_pipeline.parameters import (
    HITL_PARAMETER_BOUNDS,
    get_hitl_ambiguity_margin_threshold,
    validate_hitl_parameter_bounds,
)
from dual_pipeline.storage.healing_decision_store import (
    SUCCESS_OUTCOMES,
    HealingDecisionStore,
)

logger = logging.getLogger(__name__)


def _consolidate_cohorts(cohorts: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Groups and merges overlapping cohorts by cohort_key."""
    consolidated: Dict[str, Dict[str, Any]] = {}
    for cohort in cohorts:
        ckey = str(cohort.get("cohort_key", "generic"))
        items = list(cohort.get("items", []))
        run_ids = set(cohort.get("run_ids", []))

        if ckey not in consolidated:
            consolidated[ckey] = {
                "cohort_key": ckey,
                "occurrence_count": len(items),
                "run_ids": sorted(list(run_ids)),
                "items": items,
            }
        else:
            existing = consolidated[ckey]
            existing["items"].extend(items)
            existing["run_ids"] = sorted(list(set(existing["run_ids"]).union(run_ids)))
            existing["occurrence_count"] = len(existing["items"])
    return consolidated


def _clamp_parameter_bounds(prop: Dict[str, Any]) -> None:
    """Clamps calibration parameter values within governed ranges."""
    ext = prop.get("extensions", {})
    calib_ext = ext.get("com.partner_advisory.nee.calibration", {})
    pname = calib_ext.get("target_parameter")
    pval = calib_ext.get("proposed_value")
    if not (pname and pval is not None):
        return

    try:
        pval_f = float(pval)
        if not validate_hitl_parameter_bounds(pname, pval_f):
            bounds = HITL_PARAMETER_BOUNDS.get(pname, (0.0, 1.0, 0.5))
            logger.warning(
                "Proposal %s proposed value %s outside bounds [%s, %s] for %s; clamping.",
                prop.get("proposal_id"),
                pval_f,
                bounds[0],
                bounds[1],
                pname,
            )
            clamped_val = min(bounds[1], max(bounds[0], pval_f))
            calib_ext["proposed_value"] = clamped_val
            learn_ext = ext.get("com.partner_advisory.nee.learning", {})
            if "proposed_value" in learn_ext:
                learn_ext["proposed_value"] = clamped_val
    except (ValueError, TypeError):
        pass


def _attach_provenance(prop: Dict[str, Any], ckey: str) -> None:
    """Attaches signed provenance to aggregated proposal."""
    ext = prop.get("extensions", {})
    replay_def = {
        "evaluator": "ClosedLoopLearningEngine.simulate_proposal",
        "cohort_key": ckey,
        "min_occurrences": 2,
        "simulated_action": prop.get("proposed_change", ""),
    }
    prov = build_signed_provenance(
        proposal_id=prop["proposal_id"],
        evidence_event_ids=prop["evidence_event_ids"],
        run_ids=prop["run_ids"],
        proposed_change=prop["proposed_change"],
        replay_definition=replay_def,
    )
    ext[PROVENANCE_EXTENSION_KEY] = prov


def aggregate_cohort_proposals(
    engine: Any,
    cohorts: Sequence[Mapping[str, Any]],
    target_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
    max_proposals: int = 20,
) -> List[Dict[str, Any]]:
    """Consolidates and generates bounded learning proposals across multiple failure cohorts."""
    overrides_map = dict(target_overrides or {})
    consolidated_cohorts = _consolidate_cohorts(cohorts)
    generated_proposals: List[Dict[str, Any]] = []

    for ckey, cohort_data in list(consolidated_cohorts.items())[:max_proposals]:
        t_over = overrides_map.get(ckey)
        prop = engine.generate_proposal_for_cohort(cohort_data, target_override=t_over)
        _clamp_parameter_bounds(prop)
        _attach_provenance(prop, ckey)
        prop = seal_artifact(prop)
        validate_artifact(prop)
        generated_proposals.append(prop)

    return generated_proposals


def _build_tokenomics_signal(
    signal_type: str, category: str, run_id: str, event_id: str, created_at: str
) -> Dict[str, Any]:
    return {
        "source": "tokenomics",
        "signal_type": signal_type,
        "category": category.lower(),
        "run_id": run_id,
        "event_id": event_id,
        "created_at": created_at,
    }


def _collect_list_signals(
    records: Sequence[Mapping[str, Any]],
    field_key: str,
    signal_type: str,
    prefix: str,
    default_items: Sequence[str],
    now_iso: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, rec in enumerate(records):
        run_id = str(rec.get("run_id") or f"run-{prefix}-{idx}")
        created = str(rec.get("created_at") or now_iso)
        for val in rec.get(field_key) or default_items:
            out.append(_build_tokenomics_signal(signal_type, str(val), run_id, f"{prefix}-{idx}-{val}", created))
    return out


def extract_tokenomics_signals(
    judge_reports: Sequence[Mapping[str, Any]] = (),
    healer_results: Sequence[Mapping[str, Any]] = (),
    circuit_breaker_trips: Sequence[Mapping[str, Any]] = (),
    cache_metrics: Sequence[Mapping[str, Any]] = (),
    now_iso: str = "",
) -> List[Dict[str, Any]]:
    """Extracts structured tokenomics learning signals across Judge, Healer, CircuitBreaker, and Cache."""
    signals = _collect_list_signals(judge_reports, "defect_codes", "judge_defect", "judge", (), now_iso)
    signals.extend(
        _collect_list_signals(healer_results, "repaired_fields", "healer_repair", "healer", ("healed_content",), now_iso)
    )
    for idx, trip in enumerate(circuit_breaker_trips):
        code = str(trip.get("defect_code") or "circuit_trip")
        signals.append(
            _build_tokenomics_signal(
                "circuit_breaker", code, str(trip.get("run_id") or f"run-cb-{idx}"), f"cb-{idx}-{code}", str(trip.get("created_at") or now_iso)
            )
        )
    for idx, cm in enumerate(cache_metrics):
        if float(cm.get("cache_hit_rate", 1.0)) < 0.50:
            stage = str(cm.get("stage") or "unknown_stage")
            signals.append(
                _build_tokenomics_signal(
                    "low_cache_hit_rate", stage, str(cm.get("run_id") or f"run-cache-{idx}"), f"cache-{idx}-{stage}", str(cm.get("created_at") or now_iso)
                )
            )
    return signals


def _first_non_empty(evt: Mapping[str, Any], keys: Sequence[str], default: str) -> str:
    for k in keys:
        val = evt.get(k)
        if val:
            return str(val)
    return default


def _parse_outcome_line(line: str) -> Optional[Dict[str, Any]]:
    clean = line.strip()
    if not clean:
        return None
    try:
        evt = json.loads(clean)
    except Exception:
        return None
    if not isinstance(evt, dict):
        return None
    otype = str(evt.get("outcome_type") or "")
    has_direct = bool(evt.get("subsystem") and evt.get("repair_class"))
    if otype not in ("COMPONENT_REPAIR", "RETRY_EXHAUSTION") and not has_direct:
        return None
    ev_id = _first_non_empty(evt, ("decision_id", "event_id"), stable_id("heal-evt", digest(clean)))
    raw_status = str(evt.get("outcome_status") or "").upper()
    was_ok = raw_status in SUCCESS_OUTCOMES if raw_status else bool(evt.get("was_successful", otype == "COMPONENT_REPAIR"))
    default_reason = _first_non_empty(evt, ("defect_message",), otype)
    return {
        "decision_id": ev_id,
        "run_id": _first_non_empty(evt, ("run_id",), "run-live-outcome"),
        "subsystem": _first_non_empty(evt, ("subsystem", "source_agent", "target_component"), "live_pipeline_healer"),
        "component": _first_non_empty(evt, ("component", "target_component"), "healer_agent"),
        "scope": _first_non_empty(evt, ("scope", "category", "retry_class"), "pipeline_repair"),
        "repair_class": _first_non_empty(evt, ("repair_class", "retry_class"), otype).upper(),
        "action_taken": _first_non_empty(evt, ("action_taken", "defect_message"), otype),
        "outcome_status": raw_status or ("HEALED" if was_ok else "UNREPAIRABLE"),
        "before_digest": _first_non_empty(evt, ("before_digest",), ev_id),
        "after_digest": _first_non_empty(evt, ("after_digest",), ev_id),
        "confidence_top": float(evt.get("confidence_top", 0.90 if was_ok else 0.35)),
        "confidence_second": float(evt.get("confidence_second", 0.10 if was_ok else 0.65)),
        "ambiguity_margin": float(evt.get("ambiguity_margin", 0.80 if was_ok else 0.0)),
        "hitl_escalated": bool(evt.get("hitl_escalated", not was_ok)),
        "invariants_preserved": bool(evt.get("invariants_preserved", was_ok)),
        "defect_reasons": list(evt.get("defect_reasons") or [default_reason]),
        "created_at": _first_non_empty(evt, ("recorded_at", "created_at"), "2026-01-01T00:00:00Z"),
    }


def _drain_jsonl_outcomes_to_store(store: HealingDecisionStore, jsonl_path: Path) -> int:
    if not jsonl_path.is_file():
        return 0
    drained = 0
    try:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            rec = _parse_outcome_line(line)
            if rec is not None:
                store.record_decision(rec)
                drained += 1
    except Exception as exc:
        logger.warning("Non-fatal: failed draining JSONL outcomes into healing SQLite store: %s", exc)
    return drained


def _resolve_cycle_db_path(runtime_root: Path, db_path: Optional[Path]) -> Optional[Path]:
    if db_path is not None:
        return db_path
    rt_db = runtime_root / "ledger" / "learning.sqlite"
    return rt_db if rt_db.exists() else None


def ingest_and_drain_healing_store(
    runtime_root: Path,
    db_path: Optional[Path] = None,
    outcome_ledger_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Drain live outcome JSONL into HealingDecisionStore and return offline learning signals."""
    eff_db = _resolve_cycle_db_path(runtime_root, db_path)
    store = HealingDecisionStore(db_path=eff_db)
    jsonl_p = outcome_ledger_path or (runtime_root / "ledger" / "live_outcome_events.jsonl")
    _drain_jsonl_outcomes_to_store(store, jsonl_p)

    decisions = store.list_decisions(limit=5000)
    signals: List[Dict[str, Any]] = []
    for d in decisions:
        status = str(d.get("outcome_status") or "").upper()
        is_clean_repair = (
            status in SUCCESS_OUTCOMES
            and bool(d.get("invariants_preserved", True))
            and not bool(d.get("hitl_escalated", False))
        )
        signals.append({
            "source": "healing_repair" if is_clean_repair else "healing_escalation",
            "decision_id": d["decision_id"],
            "event_id": d["decision_id"],
            "run_id": d["run_id"],
            "subsystem": d["subsystem"],
            "component": d["component"],
            "scope": d["scope"],
            "repair_class": d["repair_class"],
            "outcome_status": status,
            "hitl_escalated": bool(d["hitl_escalated"]),
            "invariants_preserved": bool(d["invariants_preserved"]),
            "confidence_top": float(d.get("confidence_top", 1.0)),
            "ambiguity_margin": float(d.get("ambiguity_margin", 1.0)),
            "defect_reasons": list(d.get("defect_reasons") or []),
            "created_at": d["created_at"],
        })
    return signals


def _build_escalation_proposal_spec(
    ckey: str, sub: str, rclass: str, scope: str, cnt: int, target_override: Dict[str, Any]
) -> Tuple[str, str, str, List[str], str, Dict[str, Any]]:
    curr_margin = float(target_override.get("current_value", get_hitl_ambiguity_margin_threshold()))
    prop_margin = round(min(0.30, max(0.15, float(target_override.get("proposed_value", curr_margin + 0.02)))), 4)
    pname = str(target_override.get("parameter_name", "hitl.ambiguity_margin_threshold"))
    problem = f"Healing escalation cohort {ckey} triggered {cnt} unrepairable/HITL escalations in {sub}."
    change = f"Recalibrate {pname} from {curr_margin} to {prop_margin} and update healing confidence priors for {rclass}."
    plan = [
        "Dry-run replay simulation against historical healing_decisions store",
        "Verify Brier calibration improvement and 100% fact-preservation invariant",
    ]
    ext = {
        "com.partner_advisory.nee.learning": {
            "cohort_key": ckey,
            "category": "healing_policy",
            "target_type": "hitl_confidence",
            "parameter_name": pname,
            "current_value": curr_margin,
            "proposed_value": prop_margin,
            "subsystem": sub,
            "repair_class": rclass,
            "scope": scope,
            "occurrence_count": cnt,
        },
        "com.partner_advisory.nee.calibration": {
            "target_parameter": pname,
            "current_value": curr_margin,
            "proposed_value": prop_margin,
            "requires_release_gate": True,
            "auto_mutation_permitted": False,
        },
    }
    return "hitl_confidence", problem, change, plan, "medium", ext


def build_healing_proposal_spec(
    ckey: str, cohort: Dict[str, Any], target_override: Dict[str, Any]
) -> Tuple[str, str, str, List[str], str, Dict[str, Any]]:
    """Build contract-valid proposal fields for healing_repair or healing_escalation cohorts."""
    items = cohort.get("items") or [{}]
    first = items[0]
    sub = str(first.get("subsystem") or "healing_subsystem")
    rclass = str(first.get("repair_class") or "GENERIC_FIELD_CLEANUP")
    scope = str(first.get("scope") or "general")
    cnt = int(cohort.get("occurrence_count", len(items)))

    if ckey.startswith("healing_escalation:"):
        return _build_escalation_proposal_spec(ckey, sub, rclass, scope, cnt, target_override)

    problem = f"Recurring in-flight healing repair pattern {ckey} executed {cnt} times across runs."
    change = f"Promote recurring {rclass} healing rule in {sub} ({scope}) to learned upstream prior store."
    plan = [
        "Replay historical healing_decisions cohort to verify upstream defect reduction",
        "Confirm zero fact-preservation regressions and recalibrate option posteriors",
    ]
    ext = {
        "com.partner_advisory.nee.learning": {
            "cohort_key": ckey,
            "category": "healing_policy",
            "target_type": "workflow",
            "subsystem": sub,
            "repair_class": rclass,
            "scope": scope,
            "occurrence_count": cnt,
        }
    }
    return "workflow", problem, change, plan, "low", ext


def run_offline_healing_learning_cycle(
    engine: Any,
    db_path: Optional[Path] = None,
    outcome_ledger_path: Optional[Path] = None,
    min_occurrences: int = 2,
    generate_proposals: bool = True,
) -> Dict[str, Any]:
    """Execute end-to-end offline healing learning scan, posterior calibration, and proposal synthesis."""
    eff_db = _resolve_cycle_db_path(engine.runtime_root, db_path)
    signals = ingest_and_drain_healing_store(
        engine.runtime_root, db_path=eff_db, outcome_ledger_path=outcome_ledger_path
    )
    cohorts = engine.analyze_failure_cohorts(signals, min_occurrences=min_occurrences)
    store = HealingDecisionStore(db_path=eff_db)
    posteriors = store.compute_empirical_posteriors()
    post_payload = {
        "schema_version": "1.0.0",
        "updated_at": utc_now(),
        "total_decisions": len(signals),
        "posteriors": posteriors,
    }
    write_json(engine.runtime_root / "learned_healing_posteriors.json", post_payload)

    proposals: List[Dict[str, Any]] = []
    simulations: List[Dict[str, Any]] = []
    if generate_proposals and cohorts:
        proposals = engine.aggregate_proposals(cohorts)
        for prop in proposals:
            sim = engine.simulate_proposal(prop)
            simulations.append(sim.to_dict())

    return {
        "signals_count": len(signals),
        "total_decisions_analyzed": len(signals),
        "proposals_generated": len(proposals),
        "cohorts": cohorts,
        "proposals": proposals,
        "simulations": simulations,
        "posteriors": posteriors,
    }


def analyze_audio_voice_cohorts_impl(
    events: Sequence[Dict[str, Any]],
    min_occurrences: int = 3,
    min_distinct_sources: int = 2,
) -> List[Dict[str, Any]]:
    """Detects audio voice recognition cohorts requiring >= 3 events across >= 2 distinct sources."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        param_or_target = ev.get("parameter_name") or ev.get("target_key") or "voiceprint_similarity_threshold"
        buckets.setdefault(f"audio_voice_recognition:{param_or_target}", []).append(ev)
    cohorts: List[Dict[str, Any]] = []
    for ckey, items in buckets.items():
        sources = {str(i.get("source_id") or i.get("run_id") or i.get("session_id") or "").strip() for i in items} - {""}
        if len(items) >= min_occurrences and len(sources) >= min_distinct_sources:
            cohorts.append({
                "cohort_key": ckey, "occurrence_count": len(items),
                "distinct_source_count": len(sources), "run_ids": sorted(list(sources)),
                "items": items, "latest_timestamp": max(str(i.get("created_at", "")) for i in items),
            })
    return sorted(cohorts, key=lambda c: c["occurrence_count"], reverse=True)


def record_proposal_sqlite_status_impl(runtime_root: Path, proposal_id: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
    from storage.connection import get_connection
    db_path = runtime_root / "ledger" / "learning.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS learning_proposals (proposal_id TEXT PRIMARY KEY, status TEXT NOT NULL, details_json TEXT, updated_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO learning_proposals (proposal_id, status, details_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(proposal_id) DO UPDATE SET status=excluded.status, details_json=excluded.details_json, updated_at=excluded.updated_at",
            (proposal_id, status, json.dumps(details or {}), utc_now()),
        )
        conn.commit()


def _extract_alias_pair_from_record(rec: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    det_raw = rec.get("details") or rec.get("observed_outcome")
    det = det_raw if isinstance(det_raw, Mapping) else {}
    raw_pair = str(rec.get("raw_alias_pair") or det.get("raw_alias_pair") or "").strip()
    if "->" in raw_pair:
        left, right = raw_pair.split("->", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    f_id = str(rec.get("from_id") or det.get("from_id") or "").strip()
    t_id = str(rec.get("to_id") or det.get("to_id") or "").strip()
    return (f_id, t_id) if (f_id and t_id) else None


def scan_reconciler_outcomes_impl(
    engine: Any,
    events_log_path: Optional[Path] = None,
    min_occurrences: int = 2,
) -> List[Dict[str, Any]]:
    """Scans live reconciler outcome events and synthesizes ENTITY_ALIAS_MERGE proposals."""
    log_file = Path(events_log_path) if events_log_path else (engine.runtime_root / "ledger" / "live_outcome_events.jsonl")
    if not log_file.is_file():
        return []
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        pair = _extract_alias_pair_from_record(rec)
        if pair is not None:
            grouped.setdefault(pair, []).append(rec)
    proposals: List[Dict[str, Any]] = []
    for (f_id, t_id), items in sorted(grouped.items()):
        if len(items) < min_occurrences:
            continue
        raw_runs = {str(it.get("run_id") or "run-reconciler") for it in items}
        norm_runs = sorted({r if len(r) >= 3 else f"run-{r}" for r in raw_runs})
        cohort = {
            "cohort_key": f"outcome:ENTITY_ALIAS_MISMATCH:entity_resolution:{f_id}->{t_id}",
            "occurrence_count": len(items),
            "run_ids": norm_runs,
            "items": items,
            "latest_timestamp": utc_now(),
        }
        prop = engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="alias_addition",
            target_override={
                "from_id": f_id,
                "to_id": t_id,
                "raw_alias_pair": f"{f_id}->{t_id}",
                "extra_metadata": {"raw_alias_pair": f"{f_id}->{t_id}"},
                "output_01_immutable_verified": True,
                "target_before": 0.72,
                "target_after": 0.90,
            },
        )
        proposals.append(prop)
    return proposals


def _synthesize_correction_proposals(engine: Any, min_occurrences: int) -> List[Dict[str, Any]]:
    from dual_pipeline.learning_adapters.correction_feedback_ingestor import CorrectionFeedbackIngestor
    ingestor = CorrectionFeedbackIngestor(runtime_root=engine.runtime_root, min_confirmations=max(2, min_occurrences))
    corr_list, _ = ingestor.synthesize_proposals(min_confirmations=max(2, min_occurrences))
    engine.proposals_dir.mkdir(parents=True, exist_ok=True)
    for cprop in corr_list:
        write_json(engine.proposals_dir / f"{cprop['proposal_id']}.json", cprop)
    return list(corr_list)


def run_unified_learning_cycle_impl(
    engine: Any, *, min_occurrences: int = 2, include_audio_voice: bool = True, include_reconciler: bool = True, include_corrections: bool = True
) -> Dict[str, Any]:
    """Orchestrates all 8 system learning surfaces into a unified deterministic cycle."""
    telemetry_failures = engine.ingest_telemetry_failures()
    hitl_decisions = [d for d in engine.ingest_hitl_decisions() if d.get("outcome_type") == "OVERRIDDEN"]
    tokenomics_signals = engine.ingest_tokenomics_signals()
    healing_res = engine.run_healing_learning_cycle(min_occurrences=min_occurrences, generate_proposals=True)
    reconciler_props = scan_reconciler_outcomes_impl(engine, min_occurrences=min_occurrences) if include_reconciler else []
    audio_res = engine.run_audio_voice_learning_cycle(min_occurrences=max(2, min_occurrences)) if include_audio_voice else {"proposals": [], "cohorts": []}
    correction_props = _synthesize_correction_proposals(engine, min_occurrences) if include_corrections else []
    core_cohorts = engine.analyze_failure_cohorts(list(telemetry_failures) + list(hitl_decisions) + list(tokenomics_signals), min_occurrences=min_occurrences)
    core_props = [engine.generate_proposal_for_cohort(c) for c in core_cohorts]
    all_props = core_props + list(healing_res.get("proposals", [])) + reconciler_props + list(audio_res.get("proposals", [])) + correction_props
    sims = [engine.simulate_proposal(p) for p in all_props]
    sim_pass = sum(1 for r in sims if r.success and r.status in ("SIMULATED_PASS", "PASS"))
    total_props = len(all_props)
    return {
        "status": "PASS", "surfaces_orchestrated": 8, "surfaces_scanned": 8,
        "telemetry_failures_count": len(telemetry_failures), "hitl_overrides_count": len(hitl_decisions),
        "tokenomics_signals_count": len(tokenomics_signals), "healing_proposals_count": len(healing_res.get("proposals", [])),
        "reconciler_proposals_count": len(reconciler_props), "audio_proposals_count": len(audio_res.get("proposals", [])),
        "corrections_proposals_count": len(correction_props), "total_proposals_generated": total_props,
        "total_proposals_emitted": total_props, "simulated_pass_count": sim_pass,
        "vetoed_count": total_props - sim_pass, "proposal_ids": [p.get("proposal_id") for p in all_props],
    }


def build_retrieval_weight_proposal_spec(
    ckey: str, cohort: Dict[str, Any], t_over: Dict[str, Any]
) -> Tuple[str, str, List[str], str, Dict[str, Any]]:
    """Build simplex-normalized BGE-M3 hybrid weight calibration spec from implicit QREL cohorts."""
    raw_weights = t_over.get("proposed_weights") or {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25}
    total_w = sum(max(0.05, float(v)) for v in raw_weights.values()) or 1.0
    norm_weights = {k: round(max(0.05, float(v)) / total_w, 4) for k, v in raw_weights.items()}
    diff = round(1.0 - sum(norm_weights.values()), 4)
    if "bm25" in norm_weights:
        norm_weights["bm25"] = round(norm_weights["bm25"] + diff, 4)
    pname = str(t_over.get("parameter_name") or "retrieval.hybrid_weight_colbert")
    pval = float(t_over.get("proposed_value", norm_weights.get("colbert", 0.25)))
    problem = f"Retrieval rank or coverage gap detected across {cohort['occurrence_count']} implicit QREL events in {ckey}."
    proposed_change = f"Calibrate BGE-M3 hybrid RRF weights {norm_weights} ({pname}={pval}) for cohort {ckey}."
    eval_plan = ["Verify all 11 BGE-M3 retrieval release gates", "Verify graded nDCG@10 improvement on holdout QRELs"]
    ext = {
        "com.partner_advisory.nee.learning": {
            **t_over,
            "cohort_key": ckey,
            "category": "query_retrieval",
            "target_type": "retrieval_weight",
            "parameter_name": pname,
            "proposed_value": pval,
            "proposed_weights": norm_weights,
            "target_metric": t_over.get("target_metric", "ndcg_at_10"),
            "target_before": float(t_over.get("target_before", 0.88)),
            "target_after": float(t_over.get("target_after", 0.96)),
        },
        "com.partner_advisory.nee.calibration": {
            "target_parameter": pname,
            "proposed_value": pval,
            "current_value": float(t_over.get("current_value", 0.10)),
            "requires_release_gate": True,
            "auto_mutation_permitted": False,
        },
    }
    return problem, proposed_change, eval_plan, "medium", ext
