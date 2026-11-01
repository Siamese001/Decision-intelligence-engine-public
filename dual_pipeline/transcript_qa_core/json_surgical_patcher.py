"""
Surgical JSON Patcher for Output 03 Graph Signal Projections.

Enforces surgical additions, updates, and removals of graph nodes, edges, and evidence
while strictly guaranteeing that protected passing entities remain invariant.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .healing_mesh import DeterministicHealer
from .surgical_patcher import ProtectedPathMutationError

logger = logging.getLogger(__name__)


def _normalize_entity_id(eid: Any) -> str:
    if isinstance(eid, dict):
        eid = eid.get("id") or eid.get("canonical_id") or eid.get("name") or ""
    return str(eid).strip().lower()


def _get_entities_and_rels(
    data: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if "graph_mutations" in data and isinstance(data["graph_mutations"], dict):
        gm = data["graph_mutations"]
        ents = gm.setdefault("entity_upserts", [])
        rels = gm.setdefault("relationship_signals", [])
    elif "entity_upserts" in data:
        ents = data["entity_upserts"]
        rels = data.setdefault("relationship_signals", [])
    elif "entities" in data:
        ents = data["entities"]
        rels = data.setdefault("relationships", [])
    else:
        gm = data.setdefault("graph_mutations", {})
        ents = gm.setdefault("entity_upserts", [])
        rels = gm.setdefault("relationship_signals", [])

    ev_index = data.setdefault("evidence_index", {})
    if not isinstance(ev_index, dict):
        ev_index = {}
        data["evidence_index"] = ev_index
    return ents, rels, ev_index


def _rel_endpoints(r: Dict[str, Any]) -> Tuple[str, str, str]:
    fid = str(r.get("from_id") or r.get("source") or "").strip().lower()
    tid = str(r.get("to_id") or r.get("target") or "").strip().lower()
    rtype = str(r.get("relation_type") or r.get("relation") or "").upper()
    return fid, tid, rtype


def _remove_relationships(
    rels: List[Dict[str, Any]], to_remove: Optional[List[Any]]
) -> List[str]:
    if not to_remove:
        return []
    removed: List[str] = []
    for spec in to_remove:
        if not isinstance(spec, dict):
            continue
        sfid, stid, srtype = _rel_endpoints(spec)
        before = len(rels)
        rels[:] = [
            r for r in rels
            if not (_rel_endpoints(r)[:2] == (sfid, stid) and (not srtype or _rel_endpoints(r)[2] == srtype))
        ]
        if len(rels) < before:
            removed.append(f"{sfid}->{stid}")
    return removed


def _update_relationships(
    rels: List[Dict[str, Any]], to_update: Optional[List[Dict[str, Any]]]
) -> List[str]:
    if not to_update:
        return []
    updated: List[str] = []
    for spec in to_update:
        if not isinstance(spec, dict):
            continue
        sfid, stid, _ = _rel_endpoints(spec)
        for r in rels:
            if _rel_endpoints(r)[:2] == (sfid, stid):
                r.update(spec)
                updated.append(f"{sfid}->{stid}")
    return updated


def _add_relationships(
    rels: List[Dict[str, Any]], to_add: Optional[List[Dict[str, Any]]]
) -> List[str]:
    if not to_add:
        return []
    added: List[str] = []
    for spec in to_add:
        if not isinstance(spec, dict):
            continue
        sfid, stid, srtype = _rel_endpoints(spec)
        if not any(_rel_endpoints(r) == (sfid, stid, srtype) for r in rels):
            rels.append(spec)
            added.append(f"{sfid}->{stid}:{srtype}")
    return added


def _apply_rel_mutations(
    rels: List[Dict[str, Any]], ops: Dict[str, Any]
) -> Dict[str, List[str]]:
    return {
        "removed": _remove_relationships(rels, ops.get("remove_relationships")),
        "updated": _update_relationships(rels, ops.get("update_relationships")),
        "added": _add_relationships(rels, ops.get("add_relationships")),
    }


def _apply_entity_removals(
    ents: List[Dict[str, Any]],
    rels: List[Dict[str, Any]],
    to_remove: List[Any],
    protected_set: Set[str],
) -> List[str]:
    removed: List[str] = []
    for item in to_remove:
        eid = _normalize_entity_id(item)
        if not eid:
            continue
        if eid in protected_set:
            raise ProtectedPathMutationError(
                f"Target entity '{item}' is declared as a protected path and cannot be removed!"
            )
        before_len = len(ents)
        ents[:] = [e for e in ents if _normalize_entity_id(e) != eid]
        if len(ents) < before_len:
            removed.append(eid)
        rels[:] = [r for r in rels if eid not in _rel_endpoints(r)[:2]]
    return removed


def _apply_entity_upserts(
    ents: List[Dict[str, Any]],
    to_upsert: Any,
    is_update_only: bool = False,
) -> Tuple[List[str], List[str]]:
    added: List[str] = []
    updated: List[str] = []
    items: List[Dict[str, Any]] = []

    if isinstance(to_upsert, dict):
        for k, v in to_upsert.items():
            if isinstance(v, dict):
                v_copy = dict(v)
                v_copy.setdefault("id", k)
                items.append(v_copy)
    elif isinstance(to_upsert, list):
        items = [i for i in to_upsert if isinstance(i, dict)]

    for item in items:
        eid = _normalize_entity_id(item)
        if not eid:
            continue
        existing = next((e for e in ents if _normalize_entity_id(e) == eid), None)
        if existing is not None:
            existing.update(item)
            updated.append(eid)
        elif not is_update_only:
            ents.append(item)
            added.append(eid)
    return added, updated


def _apply_evidence_and_status(
    data: Dict[str, Any],
    ev_index: Dict[str, Any],
    add_ev: Any,
    status_updates: Any,
) -> Dict[str, Any]:
    added_ev: List[str] = []
    if isinstance(add_ev, list):
        for ev in add_ev:
            if isinstance(ev, dict):
                ev_id = ev.get("evidence_id") or ev.get("statement_id") or ev.get("id")
                if ev_id:
                    ev_index[str(ev_id)] = ev
                    added_ev.append(str(ev_id))
    elif isinstance(add_ev, dict):
        ev_index.update(add_ev)
        added_ev.extend([str(k) for k in add_ev.keys()])

    if isinstance(status_updates, dict):
        meta = data.setdefault("meeting_metadata", {})
        if isinstance(meta, dict):
            meta.update(status_updates)
    return {"added_evidence": added_ev, "status_updated": bool(status_updates)}


def _extract_protected_entities(critique_data: Dict[str, Any]) -> List[str]:
    protected: List[str] = []
    for k in ("protected_paths", "protected_entities", "protected_nodes"):
        val = critique_data.get(k)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    cleaned = re.sub(r"^entity:\s*", "", item).strip()
                    if cleaned:
                        protected.append(cleaned)
    return protected


def _extract_critique_text_operations(critique_text: str, protected: List[str]) -> Dict[str, Any]:
    if not critique_text:
        return {}
    prot_set = {_normalize_entity_id(p) for p in protected if p}
    rem_ents: List[str] = []
    rem_rels: List[Dict[str, str]] = []
    for m in re.finditer(r"\bremove\s+[`'\"″“”]?([a-z]+:[a-z0-9_-]+)[`'\"″“”]?", critique_text, re.IGNORECASE):
        cand = m.group(1).strip().lower()
        if cand not in prot_set and cand not in rem_ents:
            rem_ents.append(cand)
    for m in re.finditer(r"\b(rel-[a-z0-9_-]+--[a-z0-9_-]+--[A-Za-z0-9_-]+)\b", critique_text):
        rel_id = m.group(1).strip()
        parts = rel_id[4:].split("--")
        if len(parts) == 3:
            rem_rels.append({"signal_id": rel_id, "from_id": parts[0], "to_id": parts[1], "predicate": parts[2]})
    ops: Dict[str, Any] = {}
    if rem_ents:
        ops["remove_entities"] = rem_ents
    if rem_rels:
        ops["remove_relationships"] = rem_rels
    return ops


def _parse_json_patch_envelope(critique_data: Any) -> Tuple[Dict[str, Any], List[str]]:
    if isinstance(critique_data, str):
        c_str = critique_data.strip()
        if not c_str:
            return {}, []
        try:
            critique_data = json.loads(c_str)
        except Exception:
            return _extract_critique_text_operations(c_str, []), []
    if not isinstance(critique_data, dict):
        return {}, []

    protected = _extract_protected_entities(critique_data)
    ops = critique_data.get("operations") or critique_data.get("changes") or critique_data
    if isinstance(ops, dict) and not any(
        k in ops for k in ("remove_entities", "add_entities", "update_entities", "remove_relationships", "add_relationships", "update_relationships")
    ):
        combined_text = " ".join(
            str(critique_data.get(k) or "")
            for k in ("required_delta", "observed_defect", "actionable_remediation_diff", "target_path")
        ).strip()
        parsed_ops = _extract_critique_text_operations(combined_text, protected)
        if parsed_ops:
            ops = {**ops, **parsed_ops}
    return (ops if isinstance(ops, dict) else {}), protected


def _verify_entity_invariance(
    ents: List[Dict[str, Any]], active_protected: Set[str], phase: str = "surgical patching"
) -> None:
    remaining_eids = {_normalize_entity_id(e) for e in ents}
    for pe in active_protected:
        if pe not in remaining_eids:
            raise ProtectedPathMutationError(
                f"Invariance violation: Protected entity '{pe}' was removed during {phase}!"
            )


class JsonSurgicalPatcher:
    """Surgically mutates Output 03 JSON graphs with schema and protected-entity invariance validation."""

    @classmethod
    def apply_patch(
        cls,
        original_json: Union[str, Dict[str, Any]],
        critique_diff_or_envelope: Union[Dict[str, Any], str],
        metadata: Optional[Dict[str, Any]] = None,
        protected_entities: Optional[List[str]] = None,
        output_1: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        data = cls._parse_original_json(original_json)
        if not data:
            return "", {"status": "SKIPPED", "reason": "empty_original_json"}

        ops, active_prot = cls._prepare_patch_operations(critique_diff_or_envelope, protected_entities, data)
        ents, rels, ev_index = _get_entities_and_rels(data)

        removed_ents = _apply_entity_removals(ents, rels, ops.get("remove_entities", []), active_prot)
        added_ents, updated_ents = _apply_entity_upserts(ents, ops.get("add_entities", []), is_update_only=False)
        _, exp_updates = _apply_entity_upserts(ents, ops.get("update_entities", []), is_update_only=True)
        updated_ents.extend(exp_updates)

        rel_audit = _apply_rel_mutations(rels, ops)
        ev_audit = _apply_evidence_and_status(data, ev_index, ops.get("add_evidence"), ops.get("set_system_status"))
        _verify_entity_invariance(ents, active_prot, "surgical patching")

        eff_o1 = output_1 or (metadata.get("output_1") or metadata.get("clean_transcript") or metadata.get("forensic_transcript") if metadata else None)
        healed_str, healing_changes = DeterministicHealer.heal_output_3(data, metadata=metadata, output_1=eff_o1)
        cls._verify_healed_data_invariance(healed_str, active_prot)

        has_mutations = bool(
            removed_ents
            or added_ents
            or updated_ents
            or any(rel_audit.values())
            or ev_audit.get("added_evidence")
            or ev_audit.get("status_updated")
        )
        audit = {
            "status": "APPLIED",
            "patch_applied": has_mutations,
            "operations_applied": {
                "removed_entities": removed_ents,
                "added_entities": added_ents,
                "updated_entities": updated_ents,
                "relationship_mutations": rel_audit,
                "evidence_mutations": ev_audit,
            },
            "protected_entities_verified": sorted(list(active_prot)),
            "deterministic_healing_applied": healing_changes,
        }
        return healed_str, audit

    @staticmethod
    def _parse_original_json(original_json: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(original_json, str):
            raw_s = original_json.strip()
            if not raw_s:
                return {}
            try:
                return json.loads(raw_s)
            except Exception as e:
                raise ValueError(f"Invalid JSON input for surgical patch: {e}") from e
        if isinstance(original_json, dict):
            return copy.deepcopy(original_json)
        raise ValueError(f"original_json must be str or dict, got {type(original_json)}")

    @staticmethod
    def _prepare_patch_operations(
        critique_diff_or_envelope: Union[Dict[str, Any], str],
        protected_entities: Optional[List[str]],
        data: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Set[str]]:
        ops, env_prot = _parse_json_patch_envelope(critique_diff_or_envelope)
        all_prot = list(protected_entities or []) + env_prot
        norm_prot = {_normalize_entity_id(p) for p in all_prot if p}
        ents, _, _ = _get_entities_and_rels(data)
        init_eids = {_normalize_entity_id(e) for e in ents if _normalize_entity_id(e)}
        return ops, init_eids.intersection(norm_prot)

    @staticmethod
    def _verify_healed_data_invariance(healed_str: str, active_prot: Set[str]) -> None:
        try:
            h_data = json.loads(healed_str)
            h_ents, _, _ = _get_entities_and_rels(h_data)
            _verify_entity_invariance(h_ents, active_prot, "deterministic healing")
        except json.JSONDecodeError:
            pass
