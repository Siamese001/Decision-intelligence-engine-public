"""Additive union signal merger and Gate C1 structural conservation validator (Wave 1)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_AUXILIARY_MUTATION_KEYS = (
    "relationship_signals",
    "decision_updates",
    "proposal_updates",
    "risk_updates",
    "action_updates",
    "strategic_intent_signals",
)


class AdditiveMergeViolationError(ValueError):
    """Raised when Gate C1 detects signal loss during specialized extractor reconciliation."""


def _is_offline_mock_item(item: Dict[str, Any], ev_idx: Dict[str, Any]) -> bool:
    ename = str(item.get("name") or item.get("description") or "").lower()
    if "synthetic" in ename or "fixture" in ename:
        return True
    eids = item.get("evidence_ids") or []
    if not eids:
        return False
    return all(str(x).startswith("ev-offline") or x not in ev_idx for x in eids)


def _merge_evidence_id_lists(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    cur_eids = list(target.get("evidence_ids") or [])
    for x in incoming.get("evidence_ids") or []:
        if x not in cur_eids:
            cur_eids.append(x)
    if cur_eids:
        target["evidence_ids"] = cur_eids


def _merge_single_entity(
    existing_by_id: Dict[str, Dict[str, Any]],
    existing_by_slug: Dict[str, str],
    upserts: List[Dict[str, Any]],
    ent: Any,
    prefer_incoming: bool = False,
) -> None:
    if not isinstance(ent, dict) or not ent.get("id"):
        return
    eid = str(ent["id"])
    slug = eid.split(":", 1)[-1]
    target_id = eid if eid in existing_by_id else existing_by_slug.get(slug)
    if not target_id or target_id not in existing_by_id:
        upserts.append(ent)
        existing_by_id[eid] = ent
        existing_by_slug[slug] = eid
        return
    target = existing_by_id[target_id]
    _merge_evidence_id_lists(target, ent)
    inc_desc = str(ent.get("description") or "").strip()
    cur_desc = str(target.get("description") or "").strip()
    if inc_desc and (prefer_incoming or len(inc_desc) > len(cur_desc)):
        target["description"] = inc_desc


def _item_dedup_key(collection_key: str, item: Dict[str, Any]) -> str:
    if collection_key == "relationship_signals":
        rel = str(item.get("relationship") or item.get("predicate") or "").upper()
        return f"{item.get('from_id', '')}->{item.get('to_id', '')}:{rel}"
    for id_k in ("signal_id", "decision_id", "proposal_id", "risk_id", "action_id", "id"):
        if item.get(id_k):
            return str(item[id_k])
    desc = str(item.get("description") or item.get("statement") or item.get("title") or item.get("rationale") or "").strip().lower()
    return f"{collection_key}:{desc[:120]}"


def _upsert_collection_item(
    merged: List[Dict[str, Any]],
    seen_keys: Dict[str, Dict[str, Any]],
    collection_key: str,
    item: Any,
    ev_idx: Dict[str, Any],
    strip_offline: bool,
) -> None:
    if not isinstance(item, dict) or (strip_offline and _is_offline_mock_item(item, ev_idx)):
        return
    k = _item_dedup_key(collection_key, item)
    if k not in seen_keys:
        seen_keys[k] = item
        merged.append(item)
    else:
        _merge_evidence_id_lists(seen_keys[k], item)


def _merge_collection_items(
    existing_items: List[Dict[str, Any]],
    incoming_items: List[Dict[str, Any]],
    collection_key: str,
    ev_idx: Dict[str, Any],
    strip_offline: bool,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen_keys: Dict[str, Dict[str, Any]] = {}
    for item in existing_items:
        _upsert_collection_item(merged, seen_keys, collection_key, item, ev_idx, strip_offline)
    for item in incoming_items:
        _upsert_collection_item(merged, seen_keys, collection_key, item, ev_idx, False)
    return merged


def count_grounded_mutations(data: Dict[str, Any]) -> Dict[str, int]:
    """Count non-offline grounded entities and auxiliary signals in a projection dictionary."""
    ev_idx = data.get("evidence_index") or {}
    muts = data.get("graph_mutations") or {}
    ent_cnt = sum(
        1 for e in (muts.get("entity_upserts") or [])
        if isinstance(e, dict) and e.get("id") and not _is_offline_mock_item(e, ev_idx)
    )
    sig_cnt = sum(
        1
        for k in _AUXILIARY_MUTATION_KEYS
        for item in (muts.get(k) or [])
        if isinstance(item, dict) and not _is_offline_mock_item(item, ev_idx)
    )
    return {"grounded_entities": ent_cnt, "grounded_signals": sig_cnt}


def validate_structural_census_conservation(
    pre_counts: Dict[str, int],
    post_data: Dict[str, Any],
    census: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate C1: Verify additive merge conservation and structural unit representation."""
    post_counts = count_grounded_mutations(post_data)
    if post_counts["grounded_signals"] < pre_counts.get("grounded_signals", 0):
        raise AdditiveMergeViolationError(
            f"ERR_ADDITIVE_MERGE_VIOLATION: Grounded signals decreased from "
            f"{pre_counts['grounded_signals']} to {post_counts['grounded_signals']}"
        )
    ev_idx = post_data.get("evidence_index") or {}
    if census and census.get("substantive_unit_count", 0) > 0 and len(ev_idx) == 0:
        raise AdditiveMergeViolationError(
            f"ERR_STRUCTURAL_UNIT_DROP: Document has {census['substantive_unit_count']} substantive units but 0 evidence items"
        )
    return {
        "gate": "Gate_C1_Structural_Census_Conservation",
        "status": "PASS",
        "pre_merge": pre_counts,
        "post_merge": post_counts,
        "evidence_count": len(ev_idx),
    }


def _merge_evidence_indices(ev_idx: Dict[str, Any], sp_ev_idx: Dict[str, Any], prefer_specialized: bool) -> bool:
    has_sp_ev = bool(sp_ev_idx)
    if prefer_specialized and has_sp_ev:
        for k in [eid for eid in list(ev_idx.keys()) if str(eid).startswith("ev-offline")]:
            ev_idx.pop(k, None)
    for eid, ev in sp_ev_idx.items():
        if prefer_specialized or eid not in ev_idx:
            ev_idx[eid] = ev
    return has_sp_ev


def _merge_entity_batches(
    muts: Dict[str, Any], sp_muts: Dict[str, Any], ev_idx: Dict[str, Any], prefer_specialized: bool, has_sp_ev: bool
) -> None:
    existing_ents = [
        e for e in (muts.get("entity_upserts") or [])
        if isinstance(e, dict) and not (prefer_specialized and has_sp_ev and _is_offline_mock_item(e, ev_idx))
    ]
    sp_ents = [e for e in (sp_muts.get("entity_upserts") or []) if isinstance(e, dict)]
    merged_upserts: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, str] = {}
    first_batch, second_batch = (sp_ents, existing_ents) if prefer_specialized else (existing_ents, sp_ents)
    for ent in first_batch:
        _merge_single_entity(by_id, by_slug, merged_upserts, ent, prefer_incoming=True)
    for ent in second_batch:
        _merge_single_entity(by_id, by_slug, merged_upserts, ent, prefer_incoming=False)
    muts["entity_upserts"] = merged_upserts


def merge_specialized_mutations_additive(
    final_data: Dict[str, Any],
    special_proj: Dict[str, Any],
    *,
    prefer_specialized: bool = False,
    census: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perform an additive union merge of specialized archetype projections without wiping LLM signals."""
    pre_counts = count_grounded_mutations(final_data)
    if special_proj.get("max_mutation_budget"):
        cur_b = int(final_data.get("max_mutation_budget") or 0)
        final_data["max_mutation_budget"] = max(cur_b, int(special_proj["max_mutation_budget"]))

    ev_idx = final_data.setdefault("evidence_index", {})
    has_sp_ev = _merge_evidence_indices(ev_idx, special_proj.get("evidence_index") or {}, prefer_specialized)
    muts = final_data.setdefault("graph_mutations", {})
    sp_muts = special_proj.get("graph_mutations") or {}
    _merge_entity_batches(muts, sp_muts, ev_idx, prefer_specialized, has_sp_ev)

    for key in _AUXILIARY_MUTATION_KEYS:
        cur_list = list(muts.get(key) or [])
        sp_list = list(sp_muts.get(key) or [])
        first_l, second_l = (sp_list, cur_list) if prefer_specialized else (cur_list, sp_list)
        if sp_list or key in muts:
            muts[key] = _merge_collection_items(
                first_l, second_l, key, ev_idx, strip_offline=(prefer_specialized and bool(sp_list))
            )

    receipt = validate_structural_census_conservation(pre_counts, final_data, census=census)
    muts["_gate_c1_conservation_receipt"] = receipt
    return receipt
