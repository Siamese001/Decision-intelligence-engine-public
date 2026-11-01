"""Post-extraction sanitization helpers, entity normalizers, and schema utilities."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_ALLOWED_ENTITY_PREFIXES = ("person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:", "capability:")
_PREFIX_REMAP: Dict[str, str] = {
    "initiative": "project", "pattern": "system", "feature": "capability",
    "tool": "system", "service": "system", "database": "system", "queue": "system",
    "team": "org", "company": "org", "vendor": "org", "hazard": "system", "risk": "system",
}
from dual_pipeline.transcript_qa_core.semantic_linters import _PREDICATE_MAPPINGS, _normalize_predicate


def _resolve_utterance_matched_evidence_ids(
    text_hints: List[str], ev_index: Optional[Dict[str, Any]], fallback_ev: Optional[List[str]] = None
) -> List[str]:
    """Resolve evidence IDs by lexical overlap with ev_index utterances before falling back to valid keys."""
    if not isinstance(ev_index, dict) or not ev_index:
        return [eid for eid in (fallback_ev or []) if eid and eid != "ev-01"]
    stop = {"system", "person", "project", "workstream", "metric", "policy", "role", "proposed", "candidate", "confirmed", "create", "confirm", "modify", "update", "entity", "signal", "with", "from", "that", "this", "into", "over", "under", "between"}
    tokens = {tok for h in text_hints if h for tok in re.findall(r"[a-z0-9]{3,}", str(h).lower()) if tok not in stop}
    if tokens:
        scored = [
            (sum(1 for tok in tokens if tok in f"{ev.get('exact_text', '')} {ev.get('text', '')} {ev.get('speaker', '')}".lower()), str(eid))
            for eid, ev in ev_index.items() if isinstance(ev, dict)
        ]
        scored = [item for item in scored if item[0] > 0]
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]))
            return [scored[0][1]]
    valid_fb = [eid for eid in (fallback_ev or []) if eid in ev_index]
    return valid_fb if valid_fb else [next(iter(ev_index.keys()))]


def _parse_raw_payload(raw_projection: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(raw_projection, dict):
        return dict(raw_projection)
    if not isinstance(raw_projection, str):
        return {}
    c = raw_projection.strip()
    if c.startswith("```"):
        c = "\n".join([ln for ln in c.splitlines() if not ln.startswith("```")]).strip()
    try:
        data = json.loads(c)
        return dict(data) if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"Failed to parse raw projection JSON: {exc}")
        return {}


def _normalize_date_to_us(d_str: Any, default_date: str) -> str:
    """Normalize date string to MM/DD/YYYY format."""
    if not d_str or not isinstance(d_str, str):
        return default_date
    s = d_str.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        p = s.split("-")
        return f"{p[1]}/{p[2]}/{p[0]}"
    return s.replace("-", "/") if re.match(r"^\d{2}-\d{2}-\d{4}$", s) else (s if re.match(r"^\d{2}/\d{2}/\d{4}$", s) else default_date)

def _compute_default_stale_after(obs_date_str: str, days: int = 90) -> str:
    """Compute stale_after date (MM/DD/YYYY) as obs_date + days."""
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(obs_date_str, "%m/%d/%Y")
        stale_dt = dt + timedelta(days=days)
        return stale_dt.strftime("%m/%d/%Y")
    except Exception:
        return obs_date_str


def _normalize_temporal_items(items: List[Any], prefix: str, default_date: str) -> None:
    for it in items:
        if isinstance(it, dict):
            obs = _normalize_date_to_us(it.get("observed_at"), default_date)
            it["observed_at"] = obs
            if it.get("stale_after"):
                it["stale_after"] = _normalize_date_to_us(it["stale_after"], default_date)
            elif prefix == "risk":
                it["stale_after"] = _compute_default_stale_after(obs, days=90)
            k_id = f"{prefix}_id"
            raw_id = str(it.get(k_id, ""))
            if raw_id and not raw_id.startswith(f"{prefix}:"):
                it[k_id] = f"{prefix}:{re.sub(r'^(?:risk-|rsk-|decision-|dec-|risk:|decision:|' + prefix[:3] + r'-|' + prefix[:3] + r':)', '', raw_id)}"


def _normalize_decisions(decisions: List[Any], default_date: str) -> None:
    _normalize_temporal_items(decisions, "decision", default_date)

def _normalize_risks(risks: List[Any], default_date: str) -> None:
    _normalize_temporal_items(risks, "risk", default_date)

def _canonicalize_entity_id(raw_id: str, default_type: str = "system") -> str:
    """Canonicalize entity ID to start with one of the allowed W2 prefixes."""
    eid = re.sub(r"^([a-z_]+:)\1+", r"\1", str(raw_id).strip())
    if ":" in eid:
        pfx, rest = eid.split(":", 1)
        eid = f"{_PREFIX_REMAP.get(pfx, pfx)}:{rest}"
    if not any(eid.startswith(p) for p in _ALLOWED_ENTITY_PREFIXES):
        target_pfx = _PREFIX_REMAP.get(default_type.lower(), default_type.lower())
        if not any(target_pfx == p[:-1] for p in _ALLOWED_ENTITY_PREFIXES):
            target_pfx = "system"
        eid = f"{target_pfx}:{re.sub(r'^[a-z_]+:', '', eid)}"
    return eid



def _resolve_relation_endpoints_and_pred(rel: Dict[str, Any], raw_rel: str) -> tuple[str, str, Optional[str]]:
    from_id, to_id = str(rel.get("from_id", "")), str(rel.get("to_id", ""))
    if (from_id.startswith("person:") and to_id.startswith("role:")) or (
        from_id.startswith("role:") and to_id.startswith("person:")
    ):
        p_from = from_id if from_id.startswith("person:") else to_id
        r_to = to_id if to_id.startswith("role:") else from_id
        return p_from, r_to, "HAS_ROLE"
    if from_id.startswith(("org:", "person:")) and to_id.startswith("system:"):
        is_ui = "ui" in to_id or "interface" in to_id
        is_gov = raw_rel.upper() in ("MEMBER_OF", "ASSIGNED_TO", "VALIDATES")
        norm = "GOVERNS" if (is_ui and is_gov) else ("VALIDATES" if raw_rel.upper() in ("MEMBER_OF", "ASSIGNED_TO") else _normalize_predicate(raw_rel))
        return from_id, to_id, norm
    if from_id.startswith("system:") and to_id.startswith("metric:"):
        rel["signal_id"] = f"rel-{to_id.replace(':', '-') }--{from_id.replace(':', '-') }--EVALUATES"
        return to_id, from_id, "EVALUATES"
    if from_id.startswith("metric:") and to_id.startswith("system:"):
        return from_id, to_id, "EVALUATES"
    return from_id, to_id, _normalize_predicate(raw_rel)


def _apply_normalized_relation(rel: Dict[str, Any], raw_rel: str, norm_pred: Optional[str]) -> None:
    if not norm_pred:
        rel["is_quarantined"] = True
        rel["quarantine_reason"] = f"Unsupported non-canonical predicate '{raw_rel}' cannot be mapped to canonical ontology"
        rel["relationship"] = f"UNSUPPORTED:{raw_rel.upper()}"
        if "predicate" in rel:
            rel["predicate"] = f"UNSUPPORTED:{raw_rel.upper()}"
    else:
        rel["relationship"] = norm_pred
        if "predicate" in rel:
            rel["predicate"] = norm_pred
        sig_id = rel.get("signal_id", "")
        if sig_id and raw_rel.upper() in sig_id:
            rel["signal_id"] = sig_id.replace(raw_rel.upper(), norm_pred)


def _normalize_single_relationship(rel: Dict[str, Any]) -> None:
    for role_k in ("from_id", "to_id"):
        if role_k in rel and isinstance(rel[role_k], str) and not rel[role_k].startswith(("decision:", "proposal:", "action:", "risk:")):
            rel[role_k] = _canonicalize_entity_id(rel[role_k])
    raw_rel = rel.get("relationship") or rel.get("predicate") or ""
    if raw_rel:
        rel["from_id"], rel["to_id"], norm_pred = _resolve_relation_endpoints_and_pred(rel, raw_rel)
        _apply_normalized_relation(rel, raw_rel, norm_pred)


def _normalize_single_entity(ent: Dict[str, Any]) -> None:
    raw_id, etype = str(ent.get("id", "")), str(ent.get("type", "system")).lower()
    eid = _canonicalize_entity_id(raw_id, default_type=etype)
    ent["id"], ent["type"] = eid, eid.split(":", 1)[0]
    name = str(ent.get("name", ""))
    preserve_amp = (
        name.lower().startswith("at&t")
        or ent["type"] in ("role", "workstream", "capability", "program", "strategy", "org", "project", "metric", "system", "document", "platform", "data_product")
        or eid.startswith(("role:", "metric:", "system:"))
    )
    ent["name"] = re.sub(r"\s*/\s*", "-", name) if "/" in name else (name.split(" & ")[0].strip() if " & " in name and not preserve_amp else name)
    scope = ent.get("assertion_scope")
    if scope and isinstance(scope, str) and (" and " in scope or " / " in scope):
        ent["assertion_scope"] = re.split(r"\s+(?:and|/)\s+", scope)[0].strip()


def _normalize_entities(entities: List[Any], relationships: Optional[List[Any]] = None) -> None:
    for ent in entities:
        if isinstance(ent, dict):
            _normalize_single_entity(ent)
    for rel in (relationships or []):
        if isinstance(rel, dict):
            _normalize_single_relationship(rel)


def _build_mutation_corpus(mutations: Dict[str, Any], conn: set[str]) -> str:
    corpus_parts = []
    for grp in ("proposal_updates", "risk_updates", "decision_updates"):
        for item in mutations.get(grp, []):
            if isinstance(item, dict):
                corpus_parts.append(str(item.get("description", "")).lower())
                corpus_parts.append(str(item.get("rationale", "")).lower())
                corpus_parts.append(str(item.get("_audit_rationale", "")).lower())
                if item.get("threatens_entity_id"):
                    conn.add(str(item["threatens_entity_id"]))
    return " ".join(corpus_parts)


def _is_entity_in_corpus(eid: str, name: str, corpus: str) -> bool:
    stem = eid.split(":", 1)[-1].lower().replace("-", " ")
    check_terms = [eid.lower(), name, stem, stem.replace("ibm ", ""), stem.replace("amazon ", "")]
    if any(p in corpus for p in check_terms if len(p) >= 3):
        return True
    if eid.startswith("metric:") and any(m in corpus for m in ("acceptance", "waterfall", "ratio", "baseline", "rate")):
        return True
    return False


def _collect_connected_ids(signals: List[Any], mutations: Optional[Dict[str, Any]] = None) -> set[str]:
    conn = {str(s[k]) for s in signals if isinstance(s, dict) for k in ("from_id", "to_id") if s.get(k)}
    if not (mutations and isinstance(mutations, dict)):
        return conn
    corpus = _build_mutation_corpus(mutations, conn)
    for ent in mutations.get("entity_upserts", []):
        if not isinstance(ent, dict):
            continue
        eid = str(ent.get("id", ""))
        if (
            ent.get("attribution_mode") == "COLLATERAL_REVIEWED"
            or ent.get("is_authenticated")
            or ent.get("is_eligible_graph_fact")
            or _is_entity_in_corpus(eid, str(ent.get("name", "")).lower(), corpus)
        ):
            conn.add(eid)
    return conn


def _prune_orphan_entities(entities: List[Any], connected: set[str]) -> List[Any]:
    return [
        e for e in entities
        if isinstance(e, dict) and (
            e.get("id") in connected
            or str(e.get("id", "")).startswith("person:")
            or (str(e.get("id", "")).startswith(("system:", "capability:")) and e.get("evidence_ids"))
            or any(t in str(e.get("id", "")).lower() for t in ("mole-vc", "nea-mole"))
        )
    ]


def _populate_root_metadata(data: Dict[str, Any], meta: Dict[str, Any], source_text: Optional[str] = None) -> None:
    """Populate missing root metadata fields in canonical projection."""
    data.setdefault("schema_version", "5.7")
    data.setdefault("meeting_id", meta.get("meeting_id", "meeting-default"))
    data.setdefault("classification", meta.get("classification", "confidential"))
    if source_text:
        m_aud = re.search(r"\*\*Audiences:\*\*\s*(.+)", source_text, re.IGNORECASE)
        if m_aud:
            data["audiences"] = [a.strip() for a in m_aud.group(1).split(",") if a.strip()]
    if "audiences" not in data or not data["audiences"]:
        data["audiences"] = list(meta.get("audiences", ["partner_advisory-team", "project-lens-team", "nea-leadership"]))
    if "project_lens" in str(meta.get("project", "")) or "lens" in str(data.get("meeting_id", "")):
        data["audiences"] = [a.replace("project-nexus-team", "project-lens-team") for a in data["audiences"]]
    data.setdefault("expected_base_graph_version", "1.0.0")


def _normalize_single_locator(loc: Any, ev_text: str = "", source_text: Optional[str] = None) -> Dict[str, str]:
    """Normalize and anchor locator representation against source text."""
    if isinstance(loc, dict):
        kind = str(loc.get("kind") or loc.get("type") or loc.get("source_type") or "section")
        val = str(loc.get("value") or loc.get("location") or loc.get("locator") or loc.get("text") or "").strip()
    else:
        kind, val = "section", str(loc or "").strip()
    if kind == "timestamp" and re.match(r"^\d{1,2}:\d{2}(?::\d{2})?$", val):
        return {"kind": "timestamp", "value": val}
    if kind in ("attachment", "region", "page") or val.startswith("attachment:"):
        return {"kind": "attachment" if (kind == "attachment" or val.startswith("attachment:")) else kind, "value": val}
    if source_text:
        if val and val in source_text:
            pass
        elif ev_text and ev_text in source_text:
            val = ev_text[:60]
        else:
            lines = [ln.strip() for ln in ev_text.splitlines() if ln.strip()]
            val = lines[0][:60] if (lines and lines[0] in source_text) else ("Transcript" if "Transcript" in source_text else "Document")
    return {"kind": kind, "value": val or ("Transcript" if source_text and "Transcript" in source_text else "Document")}


def _normalize_single_evidence_item(ev_item: Dict[str, Any], source_text: Optional[str] = None) -> None:
    ev_item["speaker"] = ev_item.get("speaker") or ev_item.get("speaker_name") or ev_item.get("owner") or ev_item.get("author") or "Forensic Context"
    ev_item["source_classification"] = ev_item.get("source_classification") or ev_item.get("classification") or ev_item.get("kind") or ev_item.get("type") or "FACT"
    ev_item["text"] = ev_item.get("text") or ev_item.get("quote") or ev_item.get("claim") or "Evidence statement"
    valid_kinds = {"summary", "decision", "risk", "dependency", "technical", "strategic"}
    raw_kind = str(ev_item.get("kind") or "").lower().strip()
    kind_map = {
        "proposal": "strategic", "initiative": "strategic", "objective": "strategic", "strategy": "strategic",
        "action": "technical", "task": "technical", "implementation": "technical", "code": "technical",
        "decision": "decision", "resolution": "decision", "outcome": "decision",
        "risk": "risk", "threat": "risk", "issue": "risk", "concern": "risk",
        "dependency": "dependency", "blocker": "dependency", "prerequisite": "dependency",
    }
    ev_item["kind"] = raw_kind if raw_kind in valid_kinds else kind_map.get(raw_kind, "summary")
    txt = str(ev_item.get("text", "") or ev_item.get("exact_text", ""))
    if ev_item.get("speaker") in ("Session Context", "Forensic Context", None):
        m_spk = re.match(r"^\*\*([^*]+?):?\*\*", txt.strip())
        if m_spk:
            ev_item["speaker"] = m_spk.group(1).strip()
    loc = ev_item.get("locator") or ev_item.get("source_locator") or ev_item.get("location")
    ev_item["locator"] = _normalize_single_locator(loc, ev_text=ev_item["text"], source_text=source_text)


def _normalize_evidence_index(data: Dict[str, Any], source_text: Optional[str] = None) -> None:
    """Ensure evidence statements carry required fields, normalized locators, and valid citations."""
    ev_index = data.get("evidence_index")
    if not isinstance(ev_index, dict):
        data["evidence_index"] = {}
        ev_index = data["evidence_index"]

    for _ev_id, ev_item in list(ev_index.items()):
        if isinstance(ev_item, dict):
            _normalize_single_evidence_item(ev_item, source_text=source_text)

    known = set(ev_index.keys())
    ev_only = data.get("evidence_only_statement_ids")
    if isinstance(ev_only, list):
        m = data.get("graph_mutations", {})
        p_muts = sum(
            len(m.get(cat, []))
            for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates")
            if isinstance(m.get(cat), list)
        )
        data["evidence_only_statement_ids"] = [sid for sid in ev_only if sid in known][:max(3, 2 * p_muts)]


def _matches_any_keyword(text: str, kws: List[str]) -> bool:
    lt = text.lower()
    return any(re.search(rf"\b{re.escape(kw.lower())}\b", lt) for kw in kws)


def _connect_unlinked_systems(mutations: Dict[str, Any], fallback_ev: List[str], is_document: bool = False) -> None:
    """Connect standalone system entities to primary system root to prevent orphan pruning."""
    if is_document:
        return
    entities, signals = mutations.get("entity_upserts", []), mutations.setdefault("relationship_signals", [])
    connected = _collect_connected_ids(signals, mutations)
    sys_entities = [e for e in entities if isinstance(e, dict) and str(e.get("id", "")).startswith("system:")]
    if len(sys_entities) <= 1:
        return
    root_sys = next((e["id"] for e in sys_entities if e["id"] in ("system:lens", "system:nexus", "system:sherpa")), None)
    if not root_sys:
        return
    for e in sys_entities:
        eid = e["id"]
        if eid != root_sys and eid not in connected:
            pred = "INTEGRATES_WITH" if any(x in eid for x in ("maximo", "conduit", "apollo", "gis", "power-bi", "mobile-capture", "mobile-app", "backend", "textract")) else ("DEPENDS_ON" if any(x in eid for x in ("endpoint", "cloudwatch", "s3", "max-core", "dashboard", "wrapper", "deterministic")) else "INTEGRATES_WITH")
            sig_ev = e.get("evidence_ids") or fallback_ev
            r_stem, t_stem = root_sys.split(":", 1)[-1], eid.split(":", 1)[-1]
            signals.append({"signal_id": f"rel-{r_stem}--{t_stem}--{pred}", "from_id": root_sys, "to_id": eid, "relationship": pred, "evidence_ids": list(sig_ev)})
            connected.add(eid)

    # Link frontend to backend if both exist and aren't directly connected
    eids = {e["id"] for e in sys_entities}
    fe = next((eid for eid in eids if "frontend" in eid), None)
    be = next((eid for eid in eids if "backend" in eid), None)
    if fe and be and not any((s.get("from_id") == fe and s.get("to_id") == be) or (s.get("from_id") == be and s.get("to_id") == fe) for s in signals):
        signals.append({
            "signal_id": f"rel-{fe.split(':', 1)[-1]}--{be.split(':', 1)[-1]}--INTEGRATES_WITH",
            "from_id": fe, "to_id": be, "relationship": "INTEGRATES_WITH", "evidence_ids": list(fallback_ev)
        })


def _ensure_meeting_attendees(mutations: Dict[str, Any], metadata: Dict[str, Any], fallback_ev: List[str]) -> None:
    """Ground meeting attendees and organizational entities in entity_upserts and link via canonical W2 predicates."""
    attendees_raw = metadata.get("attendees")
    if isinstance(attendees_raw, str):
        try:
            attendees_raw = json.loads(attendees_raw)
        except Exception:
            return
    if not isinstance(attendees_raw, dict): return
    entities, signals = mutations.setdefault("entity_upserts", []), mutations.setdefault("relationship_signals", [])
    existing_eids = {str(e.get("id")) for e in entities if isinstance(e, dict)}
    existing_sigs = {(str(s.get("from_id")), str(s.get("to_id")), str(s.get("relationship"))) for s in signals if isinstance(s, dict)}
    root_sys = next((e["id"] for e in entities if isinstance(e, dict) and e.get("id") in ("system:lens", "system:nexus", "system:sherpa")), None)

    for org_name, members in attendees_raw.items():
        if not isinstance(members, list):
            continue
        org_slug = re.sub(r"[^a-z0-9]+", "-", org_name.lower()).strip("-")
        org_id = f"org:{org_slug}"
        if org_id not in existing_eids:
            entities.append({"id": org_id, "type": "org", "name": org_name, "mutation": "CONFIRM", "evidence_ids": list(fallback_ev), "assertion_scope": f"{org_name} organization"})
            existing_eids.add(org_id)
        for member in members:
            m_match = re.match(r"^([^(]+?)(?:\s*\(([^)]+)\))?$", str(member).strip())
            if not m_match:
                continue
            name, role = m_match.group(1).strip(), (m_match.group(2) or "").strip()
            p_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            p_id = f"person:{p_slug}"
            if p_id not in existing_eids:
                entities.append({"id": p_id, "type": "person", "name": name, "mutation": "CONFIRM", "evidence_ids": list(fallback_ev), "assertion_scope": role or f"Team member at {org_name}", "description": role or f"Team member at {org_name}"})
                existing_eids.add(p_id)
            if (p_id, org_id, "MEMBER_OF") not in existing_sigs:
                signals.append({"signal_id": f"rel-{p_slug}--{org_slug}--MEMBER_OF", "from_id": p_id, "to_id": org_id, "relationship": "MEMBER_OF", "evidence_ids": list(fallback_ev)})
                existing_sigs.add((p_id, org_id, "MEMBER_OF"))
            if root_sys and any(k in role.lower() for k in ("lens lead", "project lead", "tech lead")) and (p_id, root_sys, "OWNS") not in existing_sigs:
                signals.append({"signal_id": f"rel-{p_slug}--{root_sys.split(':', 1)[-1]}--OWNS", "from_id": p_id, "to_id": root_sys, "relationship": "OWNS", "evidence_ids": list(fallback_ev)})
                existing_sigs.add((p_id, root_sys, "OWNS"))
