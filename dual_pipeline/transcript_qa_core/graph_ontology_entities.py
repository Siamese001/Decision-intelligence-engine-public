"""
dual_pipeline/transcript_qa_core/graph_ontology_entities.py

Deterministic structural normalization for entities, evidence locators, actions, dates,
and duration-proportional mutation budgets in Output 03 graph projections.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.temporal_ssot import TemporalSSOT
from dual_pipeline.transcript_qa_core.alias_registry import load_learned_aliases
from dual_pipeline.transcript_qa_core.lens_ontology import (
    heal_lens_core_systems as _heal_lens_core_systems,
    reground_single_lens_entity,
)

logger = logging.getLogger(__name__)

GENERIC_PREDICATE_MAP: Dict[str, str] = {
    "CONNECTED_TO": "INTEGRATES_WITH", "LINKS_TO": "INTEGRATES_WITH", "COMPONENT_OF": "INTEGRATES_WITH",
    "PART_OF": "INTEGRATES_WITH", "ASSOCIATED_WITH": "CONTRIBUTES_TO", "DISCUSSED_WITH": "GOVERNS",
    "RELATES_TO": "INTEGRATES_WITH", "RELATED_TO": "INTEGRATES_WITH",
}
RELATIONSHIP_SEMANTIC_GRAVITY: Dict[str, int] = {
    rel: tier for tier, rels in {
        1: ("INTEGRATES_WITH", "DEPENDS_ON", "CONTAINS", "GOVERNS", "CONSTRAINS", "OWNS", "SPONSORS", "DEVELOPS", "ASSIGNED_TO", "VALIDATES", "HAS_RISK", "MEMBER_OF", "LEADS", "ROUTES_TO", "USES", "REPORTS_TO", "EXPLORES"),
        2: ("DELIVERS", "CONTRIBUTES_TO", "PRODUCES", "BLOCKS", "ENABLES"),
        3: ("HYPOTHESIZES", "DISCUSSES", "RELATED_TO", "CONTEXT_FOR", "CANDIDATE_FOR"),
    }.items() for rel in rels
}
INTEGRATION_KEYWORDS = ("api", "sync", "engine", "pipeline", "service", "broker", "hub", "db", "gateway", "sharepoint", "storage", "review", "s3", "bucket", "database", "store")
_RE_PAIR_SPLIT = re.compile(r"[,;]\s*")
_RE_SUB_SPLIT = re.compile(r"[:=]\s*")
_RE_PREFIX_DUP = re.compile(r"^([a-z]+:)\1+")
_RE_SLASH = re.compile(r"\s*/\s*")
TRAILING_FRAGMENT = re.compile(
    r"(?:,\s*and\s*then|\band\s*\.\.\.|\bbut\s*\.\.\.|\band\s+then\s*\.|,\s*or\s+we\s+can\s+just\s+if\s+we\s+have\s+the\s*\.?|,\s*now\s+mind\s+you\s+there'?s\s*\.?|,\s*i\s+don'?t\s+know\s*\.?|\bi\s+don'?t\s+know\s*\.\.\.|\bso\s+yeah\s*\.?|\bso\s+whatever\s*\.?|\bor\s+something\s+like\s+that\s*\.?)$",
    re.IGNORECASE,
)


def _unpack_single_packed_string(val: str) -> Dict[str, str]:
    unpacked: Dict[str, str] = {}
    if not ((":" in val or "=" in val) and ("," in val or ";" in val)):
        return unpacked
    for pair in _RE_PAIR_SPLIT.split(val):
        sub = _RE_SUB_SPLIT.split(pair, maxsplit=1)
        if len(sub) == 2 and sub[0].strip() and sub[1].strip():
            unpacked[sub[0].strip()] = sub[1].strip()
    return unpacked if len(unpacked) >= 2 else {}


def _heal_compound_attributes(attrs: Dict[str, Any], changes: List[str]) -> Dict[str, Any]:
    healed_attrs = dict(attrs)
    for k, v in list(attrs.items()):
        if isinstance(v, str):
            unpacked = _unpack_single_packed_string(v)
            if unpacked:
                healed_attrs.pop(k, None)
                healed_attrs.update(unpacked)
                changes.append(f"Unpacked compound attribute '{k}' -> {list(unpacked.keys())}")
    return healed_attrs


def _canonicalize_entity_prefix(e_id: str) -> str:
    e_id = _RE_PREFIX_DUP.sub(r"\1", e_id)
    for p in ("person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:"):
        if e_id.startswith(f"{p}{p}"):
            return p + e_id[len(f"{p}{p}"):]
    return e_id


def _normalize_entity_id_and_alias(entity: Dict[str, Any], aliases: Dict[str, str], changes: List[str]) -> None:
    raw_id = entity.get("id") or entity.get("entity_id", "")
    e_id = _canonicalize_entity_prefix(raw_id)
    if "id" in entity and entity["id"] != e_id:
        changes.append(f"Normalized duplicate prefix in entity ID '{entity['id']}' -> '{e_id}'")
        entity["id"] = e_id
    if "entity_id" in entity and entity["entity_id"] != e_id:
        entity["entity_id"] = e_id
    if e_id in aliases:
        new_id = aliases[e_id]
        for k in ("id", "entity_id"):
            if k in entity:
                entity[k] = new_id
        changes.append(f"Canonicalized entity ID '{e_id}' -> '{new_id}'")


def _normalize_entity_name_and_attrs(entity: Dict[str, Any], changes: List[str]) -> None:
    name = entity.get("name", "")
    eid_low = str(entity.get("id") or entity.get("entity_id") or "").lower()
    etype = str(entity.get("type", "")).lower()
    preserve_amp = name.lower().startswith("at&t") or eid_low.startswith(("role:", "deliverable:", "policy:")) or etype in ("role", "deliverable", "policy", "workstream", "capability", "program", "strategy", "org", "project")
    if "/" in name:
        entity["name"] = _RE_SLASH.sub("-", name)
        changes.append(f"Normalized slashed compound entity name '{name}' -> '{entity['name']}'")
    elif " & " in name and not preserve_amp:
        entity["name"] = name.split(" & ")[0].strip()
        changes.append(f"Split compound entity name '{name}' -> '{entity['name']}'")
    for attr_key in ("attributes", "properties"):
        val = entity.get(attr_key)
        if isinstance(val, str):
            unpacked = _unpack_single_packed_string(val)
            if unpacked:
                entity[attr_key] = unpacked
                changes.append(f"Unpacked string {attr_key} -> {list(unpacked.keys())}")
        elif isinstance(val, dict):
            entity[attr_key] = _heal_compound_attributes(val, changes)
    if not str(entity.get("description") or "").strip():
        entity["description"] = f"{entity.get('name') or eid_low.split(':')[-1]} component referenced in meeting dialogue."
        changes.append(f"Populated missing description on {eid_low}")


def _heal_entities(entities: List[Dict[str, Any]], changes: List[str]) -> None:
    aliases = load_learned_aliases()
    for entity in (e for e in entities if isinstance(e, dict)):
        _normalize_entity_id_and_alias(entity, aliases, changes)
        _normalize_entity_name_and_attrs(entity, changes)


def _resolve_generic_predicate(norm_pred: str, from_id: str, to_id: str) -> str:
    portfolio_prefixes = ("program:", "product:", "workstream:", "project:", "mission:", "org:", "program_", "product_", "workstream_", "mission_", "org_")
    if norm_pred in {"PART_OF", "CONTAINS", "GOVERNS", "DELIVERS", "OWNS_OR_LEADS", "FUNDS", "TARGETS"} and (
        from_id.lower().startswith(portfolio_prefixes) or to_id.lower().startswith(portfolio_prefixes)
    ):
        return norm_pred
    if norm_pred not in GENERIC_PREDICATE_MAP:
        return norm_pred
    if from_id.startswith("org:") and to_id.startswith("org:"):
        return "PARTNERS_WITH"
    if from_id.startswith("person:") and to_id.startswith("org:"):
        return "MEMBER_OF"
    return GENERIC_PREDICATE_MAP[norm_pred]


def _resolve_canonical_predicate(norm_pred: str, from_id: str, to_id: str, changes: List[str]) -> str:
    norm_pred = _resolve_generic_predicate(norm_pred, from_id, to_id)
    if from_id.startswith("org:") and to_id.startswith("org:") and norm_pred == "INTEGRATES_WITH":
        norm_pred = "PARTNERS_WITH"
    elif from_id.startswith("system:") and to_id.startswith("system:") and norm_pred in {"RELATED_TO", "CONTEXT_FOR"}:
        f_low, t_low = from_id.lower(), to_id.lower()
        norm_pred = "INTEGRATES_WITH" if any(k in f_low or k in t_low for k in INTEGRATION_KEYWORDS) else "DEPENDS_ON"
    if from_id == "org:gis-team" and norm_pred == "GOVERNS":
        norm_pred = "ROUTES_TO"
        changes.append("Replaced ungrounded GIS Team GOVERNS predicate with ROUTES_TO")
    return norm_pred


def _heal_single_signal_dict(sig: Dict[str, Any], aliases: Dict[str, str], changes: List[str]) -> None:
    for role in ("from_id", "to_id", "source_id", "target_id"):
        val = sig.get(role)
        if val and isinstance(val, str):
            val = _canonicalize_entity_prefix(val)
            sig[role] = aliases.get(val, val)
    raw_pred = str(sig.get("relationship") or sig.get("predicate", "")).strip()
    if raw_pred:
        norm_pred = raw_pred.upper().replace("-", "_").replace(" ", "_")
        from_id = str(sig.get("from_id") or sig.get("source_id", "")).strip()
        to_id = str(sig.get("to_id") or sig.get("target_id", "")).strip()
        norm_pred = _resolve_canonical_predicate(norm_pred, from_id, to_id, changes)
        if "relationship" in sig or "predicate" not in sig:
            sig["relationship"] = norm_pred
        if "predicate" in sig:
            sig["predicate"] = norm_pred
    if not sig.get("signal_id"):
        from_c = str(sig.get("from_id", "src")).replace(":", "-")
        to_c = str(sig.get("to_id", "dst")).replace(":", "-")
        pred_c = str(sig.get("predicate") or sig.get("relationship", "rel")).replace(":", "-").upper()
        sig["signal_id"] = f"rel-{from_c}--{to_c}--{pred_c}"
        changes.append(f"Populated missing signal_id '{sig['signal_id']}' on relationship signal")


def _heal_signals(signals: List[Dict[str, Any]], changes: List[str], ev_idx: Optional[Dict[str, Any]] = None) -> None:
    _ = ev_idx
    aliases = load_learned_aliases()
    signals[:] = [s for s in signals if isinstance(s, dict)]
    for sig in signals:
        _heal_single_signal_dict(sig, aliases, changes)


def _heal_actions(actions: List[Dict[str, Any]], changes: List[str]) -> None:
    actions[:] = [a for a in actions if isinstance(a, dict)]
    for act in actions:
        owner_key = "owner_id" if "owner_id" in act else "owner"
        owner = str(act.get(owner_key, ""))
        delim = " / " if " / " in owner else (" & " if " & " in owner else None)
        if delim:
            act[owner_key] = owner.split(delim)[0].strip()
            changes.append(f"Split compound action owner '{owner}' -> '{act[owner_key]}'")


def _normalize_single_item_dates(item: Dict[str, Any], cat: str, changes: List[str]) -> None:
    for date_key in ("observed_at", "stale_after", "due_date"):
        val = item.get(date_key)
        if val and isinstance(val, str) and not TemporalSSOT.validate_human_date(val):
            try:
                item[date_key] = TemporalSSOT.to_human_date(val)
                changes.append(f"Normalized {cat} date '{val}' -> '{item[date_key]}'")
            except Exception:
                pass


def _heal_dates(mutations: Dict[str, Any], changes: List[str]) -> None:
    cats = ("decision_updates", "risk_updates", "proposal_updates", "action_updates", "decisions", "risks", "proposals", "actions")
    for cat in cats:
        for item in mutations.get(cat, []):
            if isinstance(item, dict):
                _normalize_single_item_dates(item, cat, changes)


def _strip_trailing_fragments(txt: str) -> str:
    if not txt or not isinstance(txt, str):
        return txt
    cleaned = txt.strip()
    while TRAILING_FRAGMENT.search(cleaned):
        cleaned = TRAILING_FRAGMENT.sub("", cleaned).rstrip(" ,.")
        if cleaned and not cleaned.endswith((".", "!", "?")):
            cleaned += "."
    return cleaned


def _normalize_evidence_item(eid: str, ev: Dict[str, Any]) -> None:
    ev.setdefault("evidence_id", eid)
    if ev.get("exact_text") and not ev.get("text"):
        ev["text"] = ev["exact_text"]
    elif ev.get("text") and not ev.get("exact_text"):
        ev["exact_text"] = ev["text"]
    if ev.get("kind") not in {"summary", "technical", "quote", "direct_quote", "decision", "risk", "strategic", "factual", "operational"}:
        ev["kind"] = "summary"


def _heal_single_ev_item(
    eid: str, ev: Dict[str, Any], cited_eids: Set[str], changes: List[str], pruned_eids: List[str], ev_idx: Dict[str, Any]
) -> None:
    _normalize_evidence_item(eid, ev)
    for field in ("text", "exact_text"):
        val = ev.get(field)
        if val and isinstance(val, str) and TRAILING_FRAGMENT.search(val):
            ev[field] = _strip_trailing_fragments(val)
            changes.append(f"Stripped trailing conversational fragment from {eid}.{field}")
    if ev.get("text") == "Evidence statement" or not ev.get("text") or ev.get("grounding_status") == "MISSING_QUOTE":
        ev["grounding_status"] = "MISSING_QUOTE"
        if eid not in cited_eids:
            ev_idx.pop(eid, None)
            pruned_eids.append(eid)
        return
    spk = str(ev.get("speaker", "")).strip().lower()
    if spk in ("source references", "references", "metadata", "header", "", "session context"):
        ev["speaker"] = "Forensic Context"
        changes.append(f"Normalized speaker for evidence item '{eid}'")


def _heal_evidence_index(data: Dict[str, Any], changes: List[str]) -> None:
    ev_idx = data.get("evidence_index", {})
    if not isinstance(ev_idx, dict):
        return
    muts = data.get("graph_mutations", {})
    cols = [it for col in ("entity_upserts", "relationship_signals", "decision_signals") for it in muts.get(col, []) if isinstance(it, dict)]
    cited_eids = {str(e) for it in cols for e in it.get("evidence_ids", [])} | {str(it["evidence_id"]) for it in cols if it.get("evidence_id")}
    pruned_eids: List[str] = []
    for eid, ev in list(ev_idx.items()):
        if isinstance(ev, dict):
            _heal_single_ev_item(eid, ev, cited_eids, changes, pruned_eids, ev_idx)


def _normalize_single_evidence_locator(eid: str, item: Dict[str, Any], ch: List[str]) -> None:
    loc = item.get("locator")
    if isinstance(loc, dict) and loc.get("kind") == "timestamp":
        raw_val = str(loc.get("value", ""))
        clean_val = raw_val.strip("[] \t")
        if clean_val != raw_val:
            loc["value"] = clean_val
            ch.append(f"Normalized locator in evidence '{eid}'")
    elif isinstance(loc, str):
        clean_loc = loc.strip("[] \t")
        if clean_loc != loc:
            item["locator"] = clean_loc
            ch.append(f"Normalized string locator in evidence '{eid}'")
    for lk in ("source_locators", "evidence_locators"):
        if isinstance(item.get(lk), list):
            item[lk] = [str(l).strip("[] \t") for l in item[lk] if str(l).strip("[] \t")]


def _prefix_unnormalized_entity_ids(entities: List[Any], ch: List[str]) -> Dict[str, str]:
    ent_id_map: Dict[str, str] = {}
    valid_types = ("person", "org", "system", "concept", "decision", "metric", "role", "entity")
    for ent in (e for e in entities if isinstance(e, dict)):
        ent_id = str(ent.get("id") or ent.get("entity_id") or "")
        if ent_id and ":" not in ent_id:
            ent_type = str(ent.get("type") or "entity").lower().strip()
            clean_type = ent_type if ent_type in valid_types else "entity"
            clean_id = f"{clean_type}:{ent_id.lower().replace(' ', '-')}"
            ent["id"] = clean_id
            if "entity_id" in ent:
                ent["entity_id"] = clean_id
            ch.append(f"Prefixed unnormalized entity ID: '{ent_id}' -> '{clean_id}'")
        eid = str(ent.get("id") or "")
        if ":" in eid:
            ent_id_map[eid.split(":", 1)[-1].lower()] = ent_id_map[eid.lower()] = eid
    return ent_id_map


def _prefix_unnormalized_signal_endpoints(signals: List[Any], ent_id_map: Dict[str, str], ch: List[str]) -> None:
    for sig in (s for s in signals if isinstance(s, dict)):
        for role in ("from_id", "to_id", "source_id", "target_id"):
            r_id = str(sig.get(role) or "")
            if r_id and ":" not in r_id:
                slug_r = r_id.lower().replace(" ", "-")
                sig[role] = ent_id_map.get(slug_r) or ent_id_map.get(r_id.lower()) or f"entity:{slug_r}"
                ch.append(f"Prefixed unnormalized signal endpoint in {role}: '{r_id}' -> '{sig[role]}'")


def _normalize_muts_ids(muts: Any, ch: List[str]) -> None:
    if not isinstance(muts, dict):
        return
    entities = muts.get("entity_upserts") or muts.get("entities")
    ent_id_map = _prefix_unnormalized_entity_ids(entities, ch) if isinstance(entities, list) else {}
    signals = muts.get("relationship_signals") or muts.get("signals")
    if isinstance(signals, list):
        _prefix_unnormalized_signal_endpoints(signals, ent_id_map, ch)


def heal_evidence_locators(data: Dict[str, Any], changes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Normalize evidence locators, timestamp brackets, and un-prefixed entity IDs."""
    ch = changes if changes is not None else []
    ev_idx = data.get("evidence_index")
    if isinstance(ev_idx, dict):
        for eid, item in ev_idx.items():
            if isinstance(item, dict):
                _normalize_single_evidence_locator(eid, item, ch)
    _normalize_muts_ids(data.get("graph_mutations") or data.get("mutations") or data, ch)
    return data


def _heal_dangling_evidence_citations(data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str]) -> None:
    ev_idx = data.setdefault("evidence_index", {})
    cols = ("relationship_signals", "signals", "decision_updates", "decisions", "proposal_updates", "proposals", "risk_updates", "risks", "entity_upserts", "entities")
    for col in cols:
        for it in (x for x in mutations.get(col, []) if isinstance(x, dict)):
            ev = it.get("evidence")
            if isinstance(ev, dict) and ev.get("statement_id") and ev.get("statement_id") not in ev_idx:
                sid = str(ev["statement_id"]).strip()
                txt = ev.get("direct_quote") or ev.get("text", "")
                ev_idx[sid] = {"statement_id": sid, "text": txt, "exact_text": txt, "locator": ev.get("locator") or {"kind": "timestamp", "value": "00:00:00"}}
                changes.append(f"Reconciled inline evidence '{sid}' into evidence_index")
    valid_eids = set(ev_idx.keys())
    for col in cols:
        for it in (x for x in mutations.get(col, []) if isinstance(x, dict) and isinstance(x.get("evidence_ids"), list)):
            it["evidence_ids"] = list(dict.fromkeys(e for e in it["evidence_ids"] if e in valid_eids))


def heal_entities_and_evidence(
    data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str], metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Heals entity upserts, descriptions, classifications, and evidence index in Output 03."""
    _ = metadata
    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    evidence_index = data.get("evidence_index") or {}
    norm_src = " ".join(str(v.get("exact_text") or v.get("text") or "") for v in evidence_index.values() if isinstance(v, dict)).lower()
    entities[:] = [e for e in entities if str(e.get("id", "")).lower() not in ("system:08", "system:8")]
    _heal_lens_core_systems(entities, evidence_index, changes, norm_src)
    for ent in entities:
        reground_single_lens_entity(ent, evidence_index, changes)


def _extract_connected_signal_ids(signals: List[Dict[str, Any]]) -> Set[str]:
    return {sig[role] for sig in signals for role in ("from_id", "to_id") if isinstance(sig, dict) and sig.get(role)}


def _partition_entities_by_connectivity(entities: List[Dict[str, Any]], connected_ids: Set[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    retained, pruned = [], []
    for ent in entities:
        ent_id = ent.get("id") or ent.get("entity_id", "")
        (pruned if (ent_id and ent_id not in connected_ids) else retained).append(ent_id if (ent_id and ent_id not in connected_ids) else ent)
    return retained, pruned


def _relegate_pruned_evidence(data: Dict[str, Any], pruned_ids: List[str]) -> None:
    evidence_only = data.setdefault("evidence_only_statement_ids", [])
    evidence_index = data.get("evidence_index", {})
    if isinstance(evidence_index, dict):
        pruned_tokens = {pid.split(":")[-1] for pid in pruned_ids}
        for eid, ev in evidence_index.items():
            if isinstance(ev, dict) and eid not in evidence_only and any(tok in (ev.get("text") or "").lower() for tok in pruned_tokens):
                evidence_only.append(eid)


def _signal_priority(s: Dict[str, Any], protected_ids: Set[str]) -> Tuple[int, int]:
    f = str(s.get("from_id") or s.get("source_id", ""))
    t = str(s.get("to_id") or s.get("target_id", ""))
    rel_str = str(s.get("relationship", "")).upper()
    if f.startswith("person:") and ((rel_str == "MEMBER_OF" and t.startswith("org:")) or (rel_str == "HAS_ROLE" and t.startswith("role:"))):
        return (-2, 0)
    tier = 0 if (f in protected_ids and t in protected_ids) else (1 if (f in protected_ids or t in protected_ids) else 2)
    return (tier, RELATIONSHIP_SEMANTIC_GRAVITY.get(rel_str, 2))


def _trim_excess_signals(
    data: Dict[str, Any], entities: List[Any], signals: List[Any], static_count: int, max_budget: int, is_doc: bool = False
) -> int:
    from .graph_healer import CORE_PROTECTED_ENTITY_IDS
    _ = is_doc
    total_items = len(entities) + len(signals) + static_count
    allowable = max(4, len(signals) - (total_items - max_budget))
    if total_items <= max_budget or len(signals) <= 4 or allowable >= len(signals):
        return 0
    trimmed = len(signals) - allowable
    signals[:] = sorted(signals, key=lambda s: _signal_priority(s, CORE_PROTECTED_ENTITY_IDS))[:allowable]
    entities[:], pruned = _partition_entities_by_connectivity(entities, _extract_connected_signal_ids(signals) | CORE_PROTECTED_ENTITY_IDS)
    if pruned:
        _relegate_pruned_evidence(data, pruned)
    return trimmed


def _cap_sub_collections(mutations: Dict[str, Any], scale: float, is_doc: bool, changes: List[str]) -> int:
    base_caps = (("proposal_updates", "proposals", 8, 4), ("strategic_intent_signals", "strategic_signals", 10, 3), ("decision_updates", "decisions", 8, 4), ("risk_updates", "risks", 8, 3), ("action_updates", "actions", 8, 4))
    total = 0
    for k, a, doc_c, mtg_c in base_caps:
        base_c = doc_c if is_doc else mtg_c
        cap_val = max(base_c, int(base_c * scale))
        target_k = k if k in mutations else a
        items = mutations.get(target_k) or []
        if len(items) > cap_val:
            mutations[target_k] = items[:cap_val]
            changes.append(f"Capped {k} to {cap_val} items ({len(items) - cap_val} trimmed) to preserve mutation budget")
        total += len(mutations.get(target_k) or [])
    return total


def _trim_excess_entities_for_budget(
    mutations: Dict[str, Any], entities: List[Any], signals: List[Any], allowed_e: int, eff_ceiling: int, changes: List[str]
) -> None:
    from .graph_healer import CORE_PROTECTED_ENTITY_IDS
    sig_ends = _extract_connected_signal_ids(signals)
    entities[:] = sorted(
        entities,
        key=lambda e: (
            0 if ((e.get("id") or e.get("entity_id")) in CORE_PROTECTED_ENTITY_IDS or "workstream:" in str(e.get("id") or e.get("entity_id"))) else 1,
            0 if (e.get("id") or e.get("entity_id")) in sig_ends else 1,
        ),
    )[:allowed_e]
    mutations["entity_upserts" if "entity_upserts" in mutations else "entities"] = entities
    surviving = {str(e.get("id") or e.get("entity_id")) for e in entities if isinstance(e, dict)}
    signals[:] = [
        s for s in signals
        if isinstance(s, dict) and str(s.get("from_id") or s.get("source_id") or "") in surviving and str(s.get("to_id") or s.get("target_id") or "") in surviving
    ]
    mutations["relationship_signals" if "relationship_signals" in mutations else "signals"] = signals
    changes.append(f"Trimmed non-essential entities to satisfy mutation budget <= {eff_ceiling}")


def _heal_mutation_budget(
    data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str],
    max_budget: int = 30, is_doc: bool = False, metadata: Optional[Dict[str, Any]] = None, f1_text: str = ""
) -> None:
    """Enforce Gate 3.10 duration-proportional mutation budget by scaling sub-caps and trimming secondary edges."""
    from dual_pipeline.transcript_duration_helper import count_roster_scaffolding_mutations, resolve_duration_mutation_budget
    meta = metadata or data.get("metadata") or data
    eff_budget = resolve_duration_mutation_budget(meta, f1_text or str(data.get("raw_transcript") or ""), base_budget=max_budget) if max_budget == 30 else max_budget
    static_count = _cap_sub_collections(mutations, max(1.0, eff_budget / 30.0), is_doc, changes)
    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    signals = mutations.get("relationship_signals") or mutations.get("signals") or []
    eff_ceiling = eff_budget + count_roster_scaffolding_mutations(mutations)
    if len(entities) + len(signals) + static_count > eff_ceiling:
        trimmed = _trim_excess_signals(data, entities, signals, static_count, eff_ceiling, is_doc=is_doc)
        mutations["relationship_signals" if "relationship_signals" in mutations else "signals"] = signals
        mutations["entity_upserts" if "entity_upserts" in mutations else "entities"] = entities
        if trimmed > 0:
            changes.append(f"Trimmed {trimmed} secondary relationship signals to satisfy mutation budget <= {eff_ceiling}")
    allowed_e = max(2, eff_ceiling - len(signals) - static_count)
    if len(entities) + len(signals) + static_count > eff_ceiling and len(entities) > allowed_e:
        _trim_excess_entities_for_budget(mutations, entities, signals, allowed_e, eff_ceiling, changes)
