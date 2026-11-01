"""Cross-document entity canonicalizer (`INV-ENTITY-CROSS-DOC-CANONICALIZATION`)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_CANONICAL_NAME_MAP: Dict[str, str] = {
    "matt barrows": "Matthew Barrows", "matthew barrows": "Matthew Barrows", "m. barrows": "Matthew Barrows",
    "christopher nunalee": "Chris Nunalee", "chris nunalee": "Chris Nunalee", "c. nunalee": "Chris Nunalee",
    "nicholas krivov": "Nick Krivov", "nick krivov": "Nick Krivov", "n. krivov": "Nick Krivov",
    "kurt hopmann": "Curt Hopmann", "curt hopmann": "Curt Hopmann",
    "dianna valencia": "Diana Valencia", "diana valencia": "Diana Valencia",
    "ray laffer": "Ray Lauffer", "ray lauffer": "Ray Lauffer",
    "reid shenk": "Reid Schenk", "reid schenk": "Reid Schenk",
    "lori bartlet": "Lori Bartlett", "lori bartlett": "Lori Bartlett",
    "amit a": "Amit Ayer", "amit a.": "Amit Ayer", "amit ayer": "Amit Ayer",
    "eduardo ed juan more": "Eduardo Juan More", "eduardo juan more": "Eduardo Juan More", "ed more": "Eduardo Juan More",
    "medina jose": "Jose Medina", "jose medina": "Jose Medina",
    "clement hamel": "Clement Hamel", "jack o shea": "Jack O'Shea", "jack oshea": "Jack O'Shea",
    "Cameron Mercer": "Cameron Mercer", "miles erickson": "Miles Erickson",
    "skyler carlson": "Skyler Carlson", "diana chen": "Diana Chen", "Drew Oakley": "Drew Oakley",
}
_CONTEXT_SCOPED_BARE_NAMES: Dict[str, Tuple[str, str]] = {
    "dan": ("person:dan-okray", "Drew Oakley"), "tyler": ("person:tyler", "Tyler"),
}
_CANONICAL_ORG_DISPLAY_MAP: Dict[str, str] = {
    "next era energy": "Apex Global Enterprises", "next era-energy": "Apex Global Enterprises",
    "apex_global energy": "Apex Global Enterprises", "apex_global energy inc": "Apex Global Enterprises", "apex_global": "Apex Global Enterprises",
    "next era analytics": "Apex Global Analytics", "apex_global analytics": "Apex Global Analytics", "nea": "Apex Global Analytics",
    "partner_advisory": "PartnerAdvisory", "partner_advisory consulting": "PartnerAdvisory", "partner_advisory llc": "PartnerAdvisory",
    "florida power light": "Apex Power & Grid", "florida power and light": "Apex Power & Grid", "apex": "Apex Power & Grid",
}
_NICKNAME_EQUIVALENTS: Dict[str, str] = {
    "matt": "matthew", "matthew": "matthew", "chris": "christopher", "christopher": "christopher",
    "nick": "nicholas", "nicholas": "nicholas", "kurt": "curt", "curt": "curt", "dianna": "diana", "diana": "diana",
}
_NEA_partner_advisory_CONTEXT_TOKENS = (
    "apex_global", "next era", "partner_advisory", "apex", "florida power", "okray", "eduardo", "juan more", "ed more",
    "barrows", "nunalee", "krivov", "hopmann", "valencia", "lauffer", "schenk", "bartlett", "amit ayer",
    "clement hamel", "o'shea", "oshea", "marceau", "erickson", "carlson", "dociq", "hr360", "grid_os", "forge",
    "project_lens", "project_nexus", "hrit", "nea",
)
_ENTITY_REF_FIELDS = (
    "entity_id", "owner_id", "subject_id", "target_id", "from_id", "to_id",
    "actor_id", "stakeholder_id", "subject_node_id", "object_node_id", "assigned_to",
)
_ENTITY_LIST_FIELDS = ("entity_ids", "affected_entities", "related_entities", "participants", "stakeholders")
_SECONDARY_MUTATION_KEYS = (
    "decision_updates", "decision_signals", "proposal_updates", "risk_updates",
    "action_updates", "action_items", "strategic_intent_signals", "assertions",
)


def _org_id_from_name(display_name: str) -> str:
    clean = display_name.replace("&", "and").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    return f"org:{slug or 'unknown'}"


def _person_id_from_name(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    return f"person:{slug or 'unknown'}"


CANONICAL_PERSON_ALIASES: Dict[str, Tuple[str, str]] = {
    alias: (_person_id_from_name(canon_name), canon_name)
    for alias, canon_name in _CANONICAL_NAME_MAP.items()
}


def _normalize_person_key(raw: str) -> str:
    clean = re.sub(r"^person:", "", raw.strip().lower())
    clean = re.sub(r'["\'\(\)\[\]]', " ", clean)
    clean = re.sub(r"\beduardo\s+ed\s+juan\s+more\b", "eduardo juan more", clean)
    clean = re.sub(r"[^a-z0-9\s\.]", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _levenshtein_le_2(a: str, b: str) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(curr) > 2:
            return 3
        prev = curr
    return prev[-1]


def _extract_first_last_tokens(tokens: List[str]) -> Optional[Tuple[str, str]]:
    if len(tokens) == 2:
        return tokens[0].rstrip("."), tokens[1].rstrip(".")
    if len(tokens) < 3:
        return None
    filtered = [t.rstrip(".") for t in tokens if len(t.rstrip(".")) > 1]
    pool = filtered if len(filtered) >= 2 else [t.rstrip(".") for t in tokens]
    return pool[0], pool[-1]


def _is_fuzzy_person_pair_match(first: str, last: str, a_first: str, a_last: str) -> bool:
    last_dist = _levenshtein_le_2(last, a_last)
    if last_dist > 2:
        return False
    if _NICKNAME_EQUIVALENTS.get(first, first) == _NICKNAME_EQUIVALENTS.get(a_first, a_first):
        return True
    if len(first) == 1 and a_first.startswith(first):
        return last == a_last
    return min(len(first), len(a_first)) >= 4 and _levenshtein_le_2(first, a_first) <= 1 and last_dist <= 1


def _match_fuzzy_canonical_person(
    norm_key: str,
    allow_bare_context: bool = False,
    custom_aliases: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Optional[Tuple[str, str]]:
    alias_pool = {**CANONICAL_PERSON_ALIASES, **(custom_aliases or {})}
    no_dot = norm_key.rstrip(".")
    for probe in (norm_key, no_dot):
        if probe in alias_pool:
            return alias_pool[probe]
        if allow_bare_context and probe in _CONTEXT_SCOPED_BARE_NAMES:
            return _CONTEXT_SCOPED_BARE_NAMES[probe]
    fl = _extract_first_last_tokens(no_dot.split())
    if fl is None:
        return None
    for alias_key, target in alias_pool.items():
        a_fl = _extract_first_last_tokens(alias_key.rstrip(".").split())
        if a_fl is not None and _is_fuzzy_person_pair_match(fl[0], fl[1], a_fl[0], a_fl[1]):
            return target
    return None


def resolve_canonical_person(
    entity_id_or_name: str,
    display_name: Optional[str] = None,
    *,
    allow_bare_context: bool = False,
    custom_aliases: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Tuple[str, str]:
    """Return `(canonical_entity_id, canonical_display_name)` for a person identifier or name."""
    probe = display_name or entity_id_or_name
    norm = _normalize_person_key(probe)
    for key in (norm, _normalize_person_key(entity_id_or_name)):
        matched = _match_fuzzy_canonical_person(key, allow_bare_context=allow_bare_context, custom_aliases=custom_aliases)
        if matched is not None:
            return matched
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    canon_id = f"person:{slug or 'unknown'}"
    clean_disp = re.sub(r'\s*["\(][a-zA-Z]+["\)]\s*', " ", display_name or "").strip()
    if not clean_disp or "_" in clean_disp or clean_disp.startswith("person:"):
        canon_name = " ".join(w.capitalize() for w in norm.split())
    else:
        canon_name = re.sub(r"\s+", " ", clean_disp)
    return canon_id, canon_name


def _match_canonical_org_probe(
    probe: str, custom_org_aliases: Optional[Dict[str, Tuple[str, str]]],
) -> Optional[Tuple[str, str]]:
    if custom_org_aliases and probe in custom_org_aliases:
        return custom_org_aliases[probe]
    if probe in _CANONICAL_ORG_DISPLAY_MAP:
        canon_disp = _CANONICAL_ORG_DISPLAY_MAP[probe]
        return _org_id_from_name(canon_disp), canon_disp
    return None


def resolve_canonical_non_person_entity(
    raw_id: str,
    ent_type: str,
    display_name: Optional[str] = None,
    *,
    custom_org_aliases: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Tuple[str, Optional[str]]:
    """Canonicalize non-person entity IDs (hyphenated slugs, org alias normalization)."""
    prefix = raw_id.split(":", 1)[0].lower() if ":" in raw_id else (ent_type.lower() or "system")
    body = raw_id.split(":", 1)[-1] if ":" in raw_id else raw_id
    norm_body = re.sub(r"[^a-z0-9]+", " ", body.lower()).strip()
    norm_name = re.sub(r"[^a-z0-9]+", " ", (display_name or "").lower()).strip()
    if prefix == "org":
        for probe in (norm_body, norm_name):
            matched = _match_canonical_org_probe(probe, custom_org_aliases)
            if matched is not None:
                return matched
    slug = re.sub(r"[^a-z0-9]+", "-", norm_body or norm_name).strip("-")
    return f"{prefix}:{slug or 'unknown'}", display_name


def _merge_evidence_list(base_ids: List[Any], incoming_ids: List[Any]) -> List[Any]:
    merged = list(base_ids)
    for eid in incoming_ids:
        if eid not in merged:
            merged.append(eid)
    return merged


def _collect_entity_aliases(
    existing_aliases: List[Any], incoming: Dict[str, Any], canon_name: str, canon_id: str = ""
) -> List[str]:
    excluded = {"", canon_name, canon_id}
    aliases = [str(a) for a in existing_aliases if a and str(a) not in excluded]
    inc_attrs = incoming.get("attributes") if isinstance(incoming.get("attributes"), dict) else {}
    inc_aliases = inc_attrs.get("aliases") if isinstance(inc_attrs.get("aliases"), list) else []
    for candidate in [incoming.get("name"), incoming.get("label"), incoming.get("id")] + list(inc_aliases):
        if isinstance(candidate, str) and candidate not in excluded and candidate not in aliases:
            aliases.append(candidate)
    return aliases


def _merge_entity_into(existing: Dict[str, Any], incoming: Dict[str, Any], canon_name: str) -> None:
    existing["name"] = canon_name
    if "label" in existing:
        existing["label"] = canon_name
    existing["evidence_ids"] = _merge_evidence_list(existing.get("evidence_ids") or [], incoming.get("evidence_ids") or [])
    attrs = dict(existing.get("attributes") or {})
    inc_attrs = dict(incoming.get("attributes") or {})
    attrs.update({k: v for k, v in inc_attrs.items() if k != "aliases" and attrs.get(k) is None})
    aliases = _collect_entity_aliases(
        attrs.get("aliases") or [], incoming, canon_name, str(existing.get("id") or "")
    )
    if aliases:
        attrs["aliases"] = aliases
    if attrs:
        existing["attributes"] = attrs


def _finalize_entity_upsert(
    ent_copy: Dict[str, Any], old_id: str, raw_name: str, canon_id: str,
    effective_name: str, id_remap: Dict[str, str], merged_upserts: Dict[str, Dict[str, Any]],
) -> None:
    id_remap[old_id] = canon_id
    ent_copy["id"] = canon_id
    inc_snapshot = {**ent_copy, "id": old_id, "name": raw_name}
    if canon_id in merged_upserts:
        _merge_entity_into(merged_upserts[canon_id], inc_snapshot, effective_name)
        return
    attrs = dict(ent_copy.get("attributes") or {})
    aliases = _collect_entity_aliases(attrs.get("aliases") or [], inc_snapshot, effective_name, canon_id)
    if aliases:
        attrs["aliases"] = aliases
    if attrs:
        ent_copy["attributes"] = attrs
    ent_copy["name"] = effective_name
    if "label" in ent_copy:
        ent_copy["label"] = effective_name
    merged_upserts[canon_id] = ent_copy


def _resolve_endpoint_id(
    raw_id: str, id_remap: Dict[str, str], allow_bare_context: bool = False,
    custom_person: Optional[Dict[str, Tuple[str, str]]] = None,
    custom_org: Optional[Dict[str, Tuple[str, str]]] = None,
) -> str:
    if raw_id in id_remap:
        return id_remap[raw_id]
    if raw_id.startswith("person:"):
        return resolve_canonical_person(raw_id, allow_bare_context=allow_bare_context, custom_aliases=custom_person)[0]
    if ":" in raw_id:
        return resolve_canonical_non_person_entity(raw_id, raw_id.split(":", 1)[0], custom_org_aliases=custom_org)[0]
    return raw_id


def _merge_colliding_rel_fields(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    existing["evidence_ids"] = _merge_evidence_list(existing.get("evidence_ids") or [], incoming.get("evidence_ids") or [])
    for conf_key in ("confidence", "epistemic_confidence"):
        if conf_key in existing or conf_key in incoming:
            existing[conf_key] = max(float(existing.get(conf_key) or 0.0), float(incoming.get(conf_key) or 0.0))
    if "CONFIRMED" in (incoming.get("epistemic_status"), existing.get("epistemic_status")):
        existing["epistemic_status"] = "CONFIRMED"
    if "CONFIRM" in (incoming.get("mutation"), existing.get("mutation")):
        existing["mutation"] = "CONFIRM"


def _recompute_remapped_sig_id(sig_id: str, f_id: str, pred: str, t_id: str, remapped: bool) -> str:
    if not (remapped and (sig_id.startswith("sig-auto-") or sig_id.startswith("sig-"))):
        return sig_id
    digest8 = hashlib.sha256(f"{f_id}|{pred}|{t_id}".encode("utf-8")).hexdigest()[:8]
    slug_f = re.sub(r"[^a-z0-9]+", "-", f_id.lower()).strip("-")[:24]
    slug_t = re.sub(r"[^a-z0-9]+", "-", t_id.lower()).strip("-")[:24]
    return f"sig-auto-{slug_f}-{pred.lower().replace('_', '-')}-{slug_t}-{digest8}"


def _rewrite_rel_endpoints(rel_copy: Dict[str, Any], f_id: str, t_id: str) -> None:
    rel_copy["from_id"], rel_copy["to_id"] = f_id, t_id
    for k, val in (("from", f_id), ("to", t_id)):
        if k in rel_copy:
            rel_copy[k] = val


def _canonicalize_single_rel(
    rel_copy: Dict[str, Any], id_remap: Dict[str, str], dedup_rels: Dict[Tuple[str, str, str], Dict[str, Any]],
    allow_bare_context: bool = False, custom_person: Optional[Dict[str, Tuple[str, str]]] = None,
    custom_org: Optional[Dict[str, Tuple[str, str]]] = None,
) -> None:
    orig_from = str(rel_copy.get("from_id") or rel_copy.get("from") or "")
    orig_to = str(rel_copy.get("to_id") or rel_copy.get("to") or "")
    f_id = _resolve_endpoint_id(orig_from, id_remap, allow_bare_context, custom_person, custom_org)
    t_id = _resolve_endpoint_id(orig_to, id_remap, allow_bare_context, custom_person, custom_org)
    if not f_id or not t_id or f_id == t_id:
        return
    _rewrite_rel_endpoints(rel_copy, f_id, t_id)
    pred = str(rel_copy.get("relationship") or rel_copy.get("predicate") or "RELATED_TO")
    if rel_copy.get("signal_id"):
        rel_copy["signal_id"] = _recompute_remapped_sig_id(
            str(rel_copy["signal_id"]), f_id, pred, t_id, (f_id, t_id) != (orig_from, orig_to)
        )
    key = (f_id, t_id, pred)
    if key in dedup_rels:
        _merge_colliding_rel_fields(dedup_rels[key], rel_copy)
    else:
        dedup_rels[key] = rel_copy


def _register_context_alias_entry(
    raw_alias: str, cid: str, cname: str,
    person_map: Dict[str, Tuple[str, str]], org_map: Dict[str, Tuple[str, str]],
) -> None:
    if cid.startswith("person:"):
        person_map[_normalize_person_key(raw_alias)] = (cid, cname)
    elif cid.startswith("org:"):
        org_map[re.sub(r"[^a-z0-9]+", " ", raw_alias.lower()).strip()] = (cid, cname)


def _parse_context_entity(
    ent: Any, person_map: Dict[str, Tuple[str, str]], org_map: Dict[str, Tuple[str, str]],
) -> None:
    if not isinstance(ent, dict) or not ent.get("id"):
        return
    cid = str(ent["id"])
    cname = str(ent.get("canonical_name") or ent.get("name") or cid)
    for al in [cname, cid] + list(ent.get("aliases") or []):
        _register_context_alias_entry(str(al), cid, cname, person_map, org_map)


def _parse_context_source(
    src: Any, person_map: Dict[str, Tuple[str, str]], org_map: Dict[str, Tuple[str, str]],
) -> None:
    if not isinstance(src, dict):
        return
    alias_dict = src.get("graph_alias_map")
    if isinstance(alias_dict, dict):
        for k, v in alias_dict.items():
            is_seq = isinstance(v, (list, tuple))
            cid = str(v[0] if is_seq else v)
            cname = str(v[1] if is_seq and len(v) > 1 else cid)
            _register_context_alias_entry(str(k), cid, cname, person_map, org_map)
    for ent in src.get("known_entities") or []:
        _parse_context_entity(ent, person_map, org_map)


def _extract_context_aliases(
    projection: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, Tuple[str, str]]]:
    person_map: Dict[str, Tuple[str, str]] = {}
    org_map: Dict[str, Tuple[str, str]] = {}
    meta = metadata or {}
    for src in (projection, meta, projection.get("context_pack"), meta.get("context_pack")):
        _parse_context_source(src, person_map, org_map)
    return person_map, org_map


def _extract_doc_context_blob(projection: Dict[str, Any], meta: Dict[str, Any]) -> str:
    parts = [str(projection.get(k) or "") for k in ("project", "meeting_id", "slug")] + [
        str(meta.get(k) or "") for k in ("project", "slug", "title", "document_title")
    ]
    ev_idx = projection.get("evidence_index")
    if isinstance(ev_idx, dict):
        for ev in ev_idx.values():
            if isinstance(ev, dict):
                parts.append(str(ev.get("exact_text") or ev.get("text") or ""))
    for e in (projection.get("graph_mutations") or {}).get("entity_upserts") or []:
        if isinstance(e, dict):
            parts.append(f"{e.get('id') or ''} {e.get('name') or ''}")
    return " ".join(parts).lower()


def _has_nea_partner_advisory_context(projection: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> bool:
    blob = _extract_doc_context_blob(projection, metadata or {})
    return any(tok in blob for tok in _NEA_partner_advisory_CONTEXT_TOKENS)


def _remap_single_secondary_item(
    item: Dict[str, Any], id_remap: Dict[str, str], allow_bare: bool,
    custom_person: Dict[str, Tuple[str, str]], custom_org: Dict[str, Tuple[str, str]],
) -> None:
    for field in _ENTITY_REF_FIELDS:
        val = item.get(field)
        if isinstance(val, str):
            item[field] = _resolve_endpoint_id(val, id_remap, allow_bare, custom_person, custom_org)
    for lfield in _ENTITY_LIST_FIELDS:
        lval = item.get(lfield)
        if isinstance(lval, list):
            item[lfield] = [
                _resolve_endpoint_id(x, id_remap, allow_bare, custom_person, custom_org)
                if isinstance(x, str) else x for x in lval
            ]


def _remap_secondary_mutations(
    mutations: Dict[str, Any], id_remap: Dict[str, str], allow_bare: bool,
    custom_person: Dict[str, Tuple[str, str]], custom_org: Dict[str, Tuple[str, str]],
) -> None:
    for col_key in _SECONDARY_MUTATION_KEYS:
        for item in mutations.get(col_key) or []:
            if isinstance(item, dict):
                _remap_single_secondary_item(item, id_remap, allow_bare, custom_person, custom_org)


def _remap_eid_list(eids: List[Any], ev_remap: Dict[str, str]) -> List[str]:
    remapped: List[str] = []
    for old_e in eids:
        new_e = ev_remap.get(str(old_e), str(old_e))
        if new_e not in remapped:
            remapped.append(new_e)
    return remapped


def _rewrite_projection_evidence_pointers(mutations: Dict[str, Any], ev_remap: Dict[str, str]) -> None:
    for col in ("entity_upserts", "relationship_signals") + _SECONDARY_MUTATION_KEYS:
        for item in mutations.get(col) or []:
            if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
                item["evidence_ids"] = _remap_eid_list(item["evidence_ids"], ev_remap)


def _ev_signature_key(ev: Dict[str, Any]) -> Tuple[str, str]:
    raw_txt = str(ev.get("exact_text") or ev.get("text") or ev.get("direct_quote") or "")
    norm_txt = " ".join(unicodedata.normalize("NFKC", raw_txt).lower().split())
    loc = ev.get("locator") or ev.get("source_locator") or ev.get("unit_locator") or ""
    loc_str = f"{loc.get('kind')}:{loc.get('value')}" if isinstance(loc, dict) else str(loc).strip()
    return loc_str.lower(), norm_txt


def _record_seen_ev(
    eid: str, ev: Any, seen_ev: Dict[Tuple[str, str], str], ev_remap: Dict[str, str], dedup_ev: Dict[str, Any]
) -> None:
    sig_key = _ev_signature_key(ev) if isinstance(ev, dict) else ("", "")
    if not sig_key[1]:
        dedup_ev[eid] = ev
        return
    if sig_key in seen_ev:
        ev_remap[eid] = seen_ev[sig_key]
        return
    seen_ev[sig_key] = eid
    dedup_ev[eid] = ev


def deduplicate_projection_evidence_and_signals(projection: Dict[str, Any]) -> Dict[str, Any]:
    """Merge duplicate evidence_index entries sharing identical (locator, normalized_text) and rewrite pointers."""
    ev_idx = projection.get("evidence_index")
    if not isinstance(ev_idx, dict) or len(ev_idx) <= 1:
        return projection
    seen_ev: Dict[Tuple[str, str], str] = {}
    ev_remap: Dict[str, str] = {}
    dedup_ev: Dict[str, Any] = {}
    for eid, ev in ev_idx.items():
        _record_seen_ev(eid, ev, seen_ev, ev_remap, dedup_ev)
    if ev_remap:
        projection["evidence_index"] = dedup_ev
        if isinstance(projection.get("graph_mutations"), dict):
            _rewrite_projection_evidence_pointers(projection["graph_mutations"], ev_remap)
    return projection


def _upsert_single_projection_entity(
    ent_copy: Dict[str, Any], id_remap: Dict[str, str], merged_upserts: Dict[str, Dict[str, Any]],
    allow_bare: bool, custom_person: Dict[str, Tuple[str, str]], custom_org: Dict[str, Tuple[str, str]],
) -> None:
    old_id = str(ent_copy.get("id") or "")
    raw_name = str(ent_copy.get("name") or ent_copy.get("label") or old_id)
    if str(ent_copy.get("type") or "").lower() == "person" or old_id.startswith("person:"):
        cid, cname = resolve_canonical_person(old_id, raw_name, allow_bare_context=allow_bare, custom_aliases=custom_person)
        _finalize_entity_upsert(ent_copy, old_id, raw_name, cid, cname, id_remap, merged_upserts)
        return
    ent_type = str(ent_copy.get("type") or (old_id.split(":", 1)[0] if ":" in old_id else "system"))
    cid, cname = resolve_canonical_non_person_entity(old_id, ent_type, raw_name, custom_org_aliases=custom_org)
    _finalize_entity_upsert(ent_copy, old_id, raw_name, cid, cname or raw_name, id_remap, merged_upserts)


def _upsert_projection_entities(
    upserts: List[Any], id_remap: Dict[str, str], allow_bare: bool,
    custom_person: Dict[str, Tuple[str, str]], custom_org: Dict[str, Tuple[str, str]],
) -> List[Dict[str, Any]]:
    merged_upserts: Dict[str, Dict[str, Any]] = {}
    for ent in upserts:
        if isinstance(ent, dict):
            _upsert_single_projection_entity(dict(ent), id_remap, merged_upserts, allow_bare, custom_person, custom_org)
    return list(merged_upserts.values())


def canonicalize_projection_entities(
    projection: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Canonicalize person and organization/system entity upserts and relationship signals in-place."""
    if not isinstance(projection, dict):
        return projection
    deduplicate_projection_evidence_and_signals(projection)
    mutations = projection.get("graph_mutations")
    if not isinstance(mutations, dict):
        return projection
    allow_bare = _has_nea_partner_advisory_context(projection, metadata)
    custom_person, custom_org = _extract_context_aliases(projection, metadata)
    id_remap: Dict[str, str] = {}
    mutations["entity_upserts"] = _upsert_projection_entities(
        mutations.get("entity_upserts") or [], id_remap, allow_bare, custom_person, custom_org
    )
    dedup_rels: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for rel in mutations.get("relationship_signals") or []:
        if isinstance(rel, dict):
            _canonicalize_single_rel(dict(rel), id_remap, dedup_rels, allow_bare, custom_person, custom_org)
    mutations["relationship_signals"] = list(dedup_rels.values())
    _remap_secondary_mutations(mutations, id_remap, allow_bare, custom_person, custom_org)
    return projection
