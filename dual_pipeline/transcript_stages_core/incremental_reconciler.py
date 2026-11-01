"""
incremental_reconciler.py — Deterministic Incremental Graph Mutation Reconciler.

Enforces Feature 2 (Part B):
When re-running updated inputs (new audio file, changes to raw transcript, etc.),
only incremental additions (if anything material) are reflected in the graph mutations
to prevent the knowledge graph from becoming cluttered and low signal.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


def _parse_projection_dict(proj: Optional[Union[Dict[str, Any], str]]) -> Optional[Dict[str, Any]]:
    """Safely parse a projection payload into a dictionary."""
    if not proj:
        return None
    if isinstance(proj, dict):
        return proj
    if isinstance(proj, str):
        try:
            parsed = json.loads(proj.strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            logger.debug(f"Could not parse prior projection JSON: {e}")
    return None


def _extract_prior_entities(
    prior_mutations: Dict[str, Any], prior_dict: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Index prior entities from mutations and evidence index."""
    prior_entities: Dict[str, Dict[str, Any]] = {}
    for ent in prior_mutations.get("entity_upserts", []):
        if isinstance(ent, dict) and "id" in ent:
            prior_entities[ent["id"]] = ent

    for ev_item in prior_dict.get("evidence_index", {}).values():
        if isinstance(ev_item, dict) and "entity_id" in ev_item:
            prior_entities.setdefault(ev_item["entity_id"], {"id": ev_item["entity_id"]})
    return prior_entities


def _reconcile_entity_upserts(
    current_entities: List[Any], prior_entities: Dict[str, Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Reconcile entity upserts by confirming prior entities and marking net-new as CREATE."""
    reconciled: List[Dict[str, Any]] = []
    incremental_added = 0
    confirmed_count = 0
    seen_entity_ids: Set[str] = set()

    for ent in current_entities:
        if not isinstance(ent, dict) or "id" not in ent:
            continue
        eid = ent["id"]
        if eid in seen_entity_ids:
            continue
        seen_entity_ids.add(eid)

        ent_copy = dict(ent)
        if eid in prior_entities:
            ent_copy["mutation"] = "CONFIRM"
            confirmed_count += 1
        else:
            ent_copy["mutation"] = "CREATE"
            incremental_added += 1
        reconciled.append(ent_copy)

    return reconciled, confirmed_count, incremental_added


def _extract_prior_signals(prior_mutations: Dict[str, Any]) -> Set[Tuple[str, str, str]]:
    """Index prior relationship signals as (from_id, rel, to_id) tuples."""
    signals: Set[Tuple[str, str, str]] = set()
    for sig in prior_mutations.get("relationship_signals", []):
        if isinstance(sig, dict):
            fid = sig.get("from_id", "")
            rel = sig.get("relationship", "")
            tid = sig.get("to_id", "")
            if fid and rel and tid:
                signals.add((fid, rel, tid))
    return signals


def _reconcile_signals(
    current_signals: List[Any], prior_signals: Set[Tuple[str, str, str]]
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Filter duplicate relationship signals already present in prior projection."""
    reconciled: List[Dict[str, Any]] = []
    incremental_added = 0
    pruned_count = 0
    seen_signals: Set[Tuple[str, str, str]] = set()

    for sig in current_signals:
        if not isinstance(sig, dict):
            continue
        fid = sig.get("from_id", "")
        rel = sig.get("relationship", "")
        tid = sig.get("to_id", "")
        if not (fid and rel and tid):
            continue

        sig_tuple = (fid, rel, tid)
        if sig_tuple in seen_signals:
            pruned_count += 1
            continue
        seen_signals.add(sig_tuple)

        if sig_tuple in prior_signals:
            logger.debug(f"Pruning redundant relationship edge {sig_tuple} already in prior graph.")
            pruned_count += 1
            continue

        incremental_added += 1
        reconciled.append(sig)

    return reconciled, incremental_added, pruned_count


def _reconcile_decisions(
    current_decisions: List[Any], prior_decisions: List[Any]
) -> List[Dict[str, Any]]:
    """Prune unchanged decisions matching prior decision ID and statement."""
    prior_by_id = {
        d.get("decision_id"): d
        for d in prior_decisions
        if isinstance(d, dict) and "decision_id" in d
    }
    prior_texts = {
        d.get("statement", "").strip().lower()
        for d in prior_decisions
        if isinstance(d, dict) and d.get("statement")
    }

    reconciled: List[Dict[str, Any]] = []
    for dec in current_decisions:
        if not isinstance(dec, dict):
            continue
        did = dec.get("decision_id")
        stmt = dec.get("statement", "").strip().lower()
        if did in prior_by_id and stmt in prior_texts:
            continue
        reconciled.append(dec)
    return reconciled


def _reconcile_risks(
    current_risks: List[Any], prior_risks: List[Any]
) -> List[Dict[str, Any]]:
    """Prune unchanged risks matching prior risk ID and risk statement."""
    prior_by_id = {
        r.get("risk_id"): r
        for r in prior_risks
        if isinstance(r, dict) and "risk_id" in r
    }

    reconciled: List[Dict[str, Any]] = []
    for rsk in current_risks:
        if not isinstance(rsk, dict):
            continue
        rid = rsk.get("risk_id")
        if rid in prior_by_id and rsk.get("risk_statement") == prior_by_id[rid].get("risk_statement"):
            continue
        reconciled.append(rsk)
    return reconciled


def _record_reconciliation_telemetry(
    current_projection: Dict[str, Any],
    prior_entities_count: int,
    prior_signals_count: int,
    confirmed_count: int,
    incremental_entities: int,
    incremental_signals: int,
    reconciled_decisions_count: int,
    pruned_signals_count: int = 0,
) -> None:
    """Record structured telemetry in _strategic_analysis._incremental_reconciliation."""
    strat = current_projection.setdefault("_strategic_analysis", {})
    if not isinstance(strat, dict):
        strat = {"description": str(strat)}
        current_projection["_strategic_analysis"] = strat

    is_material = (
        incremental_entities > 0
        or incremental_signals > 0
        or reconciled_decisions_count > 0
    )
    strat["_incremental_reconciliation"] = {
        "status": "INCREMENTAL_RECONCILED",
        "prior_entities_count": prior_entities_count,
        "prior_signals_count": prior_signals_count,
        "confirmed_entities_count": confirmed_count,
        "incremental_entities_added": incremental_entities,
        "incremental_signals_added": incremental_signals,
        "is_material_graph_change": is_material,
    }
    current_projection["incremental_reconciliation"] = {
        "confirmed_entities": confirmed_count,
        "new_entities": incremental_entities,
        "pruned_duplicate_relationships": pruned_signals_count,
        "prior_projection_detected": True,
    }


def _filter_clean_entities(entity_upserts: List[Any]) -> List[Dict[str, Any]]:
    return [
        e for e in entity_upserts
        if isinstance(e, dict) and str(e.get("type", "")).lower() not in ("action", "task", "chore")
    ]


def _update_strategic_reconciliation(
    strat: Any,
    new_entities: int,
    confirmed_entities: int,
    is_material: bool,
) -> None:
    if isinstance(strat, dict):
        s_inc = strat.setdefault("_incremental_reconciliation", {})
        if isinstance(s_inc, dict):
            s_inc["status"] = "INCREMENTAL_RECONCILED"
            s_inc["incremental_entities_added"] = new_entities
            s_inc["confirmed_entities_count"] = confirmed_entities
            s_inc["is_material_graph_change"] = is_material


def sync_reconciliation_counters(current_projection: Dict[str, Any]) -> None:
    """Synchronizes incremental reconciliation telemetry strictly from actual final mutation arrays."""
    curr_mut = current_projection.get("graph_mutations") or current_projection.get("mutations") or {}
    entity_upserts = curr_mut.get("entity_upserts") or []
    signals = curr_mut.get("relationship_signals") or []
    decisions = curr_mut.get("decision_updates") or []
    risks = curr_mut.get("risk_updates") or []

    clean_entities = _filter_clean_entities(entity_upserts)
    if len(clean_entities) != len(entity_upserts):
        curr_mut["entity_upserts"] = clean_entities
        entity_upserts = clean_entities

    new_entities = sum(
        1 for e in entity_upserts
        if isinstance(e, dict) and str(e.get("mutation", "create")).lower() in ("create", "new", "upsert")
    )
    confirmed_entities = sum(
        1 for e in entity_upserts
        if isinstance(e, dict) and str(e.get("mutation", "")).lower() == "confirm"
    )

    is_material = (new_entities > 0 or len(signals) > 0 or len(decisions) > 0 or len(risks) > 0)
    _update_strategic_reconciliation(
        current_projection.setdefault("_strategic_analysis", {}),
        new_entities,
        confirmed_entities,
        is_material,
    )

    inc_rec = current_projection.get("incremental_reconciliation")
    if isinstance(inc_rec, dict):
        inc_rec["new_entities"] = new_entities
        inc_rec["confirmed_entities"] = confirmed_entities


def reconcile_incremental_projection(
    current_projection: Dict[str, Any],
    prior_projection: Optional[Union[Dict[str, Any], str]] = None,
) -> Dict[str, Any]:
    """Reconciles a new canonical signal projection against prior meeting projection state."""
    prior_dict = _parse_projection_dict(prior_projection)
    if not prior_dict:
        sync_reconciliation_counters(current_projection)
        return current_projection

    prior_mut = prior_dict.get("graph_mutations", {})
    curr_mut = current_projection.setdefault("graph_mutations", {})

    prior_entities = _extract_prior_entities(prior_mut, prior_dict)
    reconciled_entities, confirmed_count, inc_entities = _reconcile_entity_upserts(
        curr_mut.get("entity_upserts", []), prior_entities
    )

    prior_signals = _extract_prior_signals(prior_mut)
    reconciled_signals, inc_signals, pruned_signals = _reconcile_signals(
        curr_mut.get("relationship_signals", []), prior_signals
    )

    reconciled_decisions = _reconcile_decisions(
        curr_mut.get("decision_updates", []), prior_mut.get("decision_updates", [])
    )
    reconciled_risks = _reconcile_risks(
        curr_mut.get("risk_updates", []), prior_mut.get("risk_updates", [])
    )

    curr_mut["entity_upserts"] = reconciled_entities
    curr_mut["relationship_signals"] = reconciled_signals
    curr_mut["decision_updates"] = reconciled_decisions
    curr_mut["risk_updates"] = reconciled_risks

    _record_reconciliation_telemetry(
        current_projection,
        len(prior_entities),
        len(prior_signals),
        confirmed_count,
        inc_entities,
        inc_signals,
        len(reconciled_decisions),
        pruned_signals_count=pruned_signals,
    )
    sync_reconciliation_counters(current_projection)

    logger.info(
        f"Incremental Graph Reconciliation Complete: "
        f"{confirmed_count} confirmed, {inc_entities} new entities, {inc_signals} new signals."
    )
    return current_projection

