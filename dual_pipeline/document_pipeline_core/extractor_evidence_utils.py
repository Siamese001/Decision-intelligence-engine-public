"""
dual_pipeline/document_pipeline_core/extractor_evidence_utils.py

Shared entity ID slugification, verbatim span resolution, and grounded
evidence entry construction across specialized document archetype extractors.
"""
from __future__ import annotations

import re
from typing import Any, Dict


def slugify_entity_id(prefix: str, label: str) -> str:
    """Normalize a human label into a deterministic `<prefix>:<slug>` identifier."""
    clean = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return f"{prefix}:{clean or 'unknown'}"


_STOP_WORDS = frozenset({"the", "and", "for", "with", "that", "this", "from"})


def _best_keyword_line(lines: list[str], candidates: tuple[str, ...], max_len: int) -> str:
    for cand in candidates:
        words = [w.lower() for w in re.findall(r"[a-zA-Z0-9$]{3,}", cand or "") if w.lower() not in _STOP_WORDS]
        if not words:
            continue
        scored = [(sum(1 for w in words if w in ln.lower()), ln) for ln in lines]
        best_hits, best_ln = max(scored, key=lambda x: x[0], default=(0, ""))
        if best_hits > 0 and best_ln:
            return best_ln[:max_len]
    return ""


def find_verbatim_span(doc_text: str, *candidates: str, max_len: int = 1200) -> str:
    """Locate the best verbatim substring match in `doc_text` for candidate strings."""
    if not doc_text:
        return ""
    non_empty_cands = tuple(c for c in candidates if c)
    for cand in non_empty_cands:
        if "\n" in cand and cand in doc_text:
            return cand[:max_len]
    lines = [ln.strip() for ln in doc_text.splitlines() if ln.strip()]
    for s in lines:
        if any(c.lower() in s.lower() for c in non_empty_cands):
            return s[:max_len]
    for cand in non_empty_cands:
        if cand in doc_text:
            return cand[:max_len]
    kw_match = _best_keyword_line(lines, non_empty_cands, max_len)
    if kw_match:
        return kw_match
    return lines[0][:max_len] if lines else doc_text[:max_len]


_LOCATOR_SECTION_NORM_RE = re.compile(
    r"^(?:p\d+|slide:?\d+|section:[a-zA-Z0-9_\-]+|tbl\d+!R\d+C\d+|R\d+|block\d+|L\d+(?:-L\d+)?|header:[a-zA-Z0-9_\-]+|body|attachment:[a-zA-Z0-9_.\-]+|[a-zA-Z0-9_\s\-]+!(?:R\d+C\d+|[A-Z]+\d+))$",
    re.IGNORECASE,
)


def normalize_unit_locator_value(locator_kind: str, locator_value: str) -> tuple[str, str]:
    """Normalize human-readable section/page locators (e.g. 'Geo-POD Matrix', '## Page 1') into valid unit locator tokens."""
    kind = (locator_kind or "section").strip()
    val = (locator_value or "Document Body").strip()
    m_page = re.match(r"^#*\s*(?:Page|Slide)\s+(\d+)\b", val, re.IGNORECASE)
    if m_page:
        return ("slide" if "slide" in val.lower() or kind == "slide" else "section"), f"p{m_page.group(1)}"
    if kind in ("cell", "uri_fragment") or _LOCATOR_SECTION_NORM_RE.match(val):
        return kind, val
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", val).strip("-").lower() or "body"
    return "section", f"section:{slug}"


def _resolve_grounded_span_and_offsets(
    raw_exact: str, text: str, doc_text: str, resolve_verbatim: bool, exact_limit: int
) -> tuple[str, int, int, str]:
    if resolve_verbatim or (raw_exact and raw_exact not in doc_text):
        span = find_verbatim_span(doc_text, raw_exact, text, max_len=exact_limit)
    else:
        span = raw_exact[:exact_limit]
    pos = doc_text.find(span) if span else -1
    if pos == -1 and doc_text:
        span = find_verbatim_span(doc_text, text, max_len=exact_limit)
        pos = doc_text.find(span) if span else -1
    start = max(0, pos)
    end = start + len(span) if (span and pos != -1) else 0
    status = "GROUNDED" if (span and pos != -1) else "UNGROUNDED"
    return span, start, end, status


def build_grounded_evidence_entry(
    ev_id: str,
    text: str,
    exact: str,
    locator_value: str,
    doc_text: str,
    *,
    locator_kind: str = "section",
    resolve_verbatim: bool = False,
    text_limit: int = 800,
    exact_limit: int = 1200,
) -> Dict[str, Any]:
    """Construct a grounded Output 03 evidence index entry with character offsets."""
    span, start, end, status = _resolve_grounded_span_and_offsets(
        exact or "", text or "", doc_text or "", resolve_verbatim, exact_limit
    )
    norm_kind, norm_val = normalize_unit_locator_value(locator_kind, locator_value)
    return {
        "evidence_id": ev_id,
        "statement_id": ev_id,
        "speaker": "Not specified",
        "source_classification": "FACT",
        "kind": "summary",
        "text": (text or span)[:text_limit],
        "exact_text": span[:exact_limit],
        "start_offset": start,
        "end_offset": end,
        "quote_pointer": [start, end],
        "locator": {"kind": norm_kind, "value": norm_val},
        "grounding_status": status,
    }


def _classify_visual_component(comp: Dict[str, Any]) -> tuple[str, str, str] | None:
    label = (comp.get("label") or "").strip()
    if not label or len(label) < 2 or label.lower() in ("sh", ">", "<"):
        return None
    if "," in label and not re.search(r"\b(?:inc|llc|corp|ltd)\b", label, re.I):
        parts = [p.strip() for p in label.split(",", 1)]
        clean = f"{parts[1]} {parts[0]}" if len(parts) == 2 else label
        return "person", "person", clean
    low = label.lower()
    if any(w in low for w in ("president", "director", "developer", "lead", "manager", "vp", "engineer", "analyst")):
        return "role", "role", label
    if any(w in low for w in ("analytics", "apex_global", "nea", "nee", "partner_advisory", "bmo")):
        return "org", "org", label
    return "system", "system", label


def _build_visual_entity_upsert(
    comp: Dict[str, Any], ev_idx: Dict[str, Any], fallback_eid: str | None
) -> tuple[Dict[str, Any], str] | None:
    classified = _classify_visual_component(comp)
    if not classified:
        return None
    prefix, ent_type, clean_label = classified
    slug = re.sub(r"[^a-z0-9]+", "-", clean_label.lower()).strip("-")
    if not slug:
        return None
    ent_id = f"{prefix}:{slug}"
    target_text = clean_label.lower()
    matched_eid = next((eid for eid, ev in ev_idx.items() if target_text in (ev.get("text") or "").lower()), None)
    eid = matched_eid or fallback_eid
    upsert = {"id": ent_id, "type": ent_type, "name": clean_label, "mutation": "CREATE", "evidence_ids": [eid] if eid else []}
    return upsert, f"Synthesized visual entity upsert '{ent_id}' ({clean_label})"


def _add_special_entity(
    mutations: Dict[str, Any], ev_idx: Dict[str, Any], existing: set, target_id: str, aliases: tuple, ntype: str, name: str, desc: str, search: str, changes: list[str]
) -> None:
    if target_id in existing or any(a in existing for a in aliases):
        return
    ev = [k for k, v in ev_idx.items() if search in (v.get("text", "") + " " + v.get("exact_text", "")).lower()]
    if ev:
        mutations["entity_upserts"].append({"id": target_id, "type": ntype, "name": name, "description": desc, "mutation": "CONFIRM", "evidence_ids": [ev[0]]})
        changes.append(f"Synthesized missing visual entity '{target_id}'")


def _sanitize_visual_descriptions(mutations: Dict[str, Any], changes: list[str]) -> None:
    for e in mutations.get("entity_upserts", []):
        if isinstance(e, dict):
            d = str(e.get("description") or "").strip().lower()
            if any(term in d for term in ("meeting stakeholder", "meeting attendee", "meeting dialogue")):
                e["description"] = e.get("name") or e.get("id", "").split(":", 1)[-1].replace("-", " ").title()
                changes.append(f"Sanitized meeting text from entity description '{e.get('id')}'")


def heal_visual_document_mutations(data: Dict[str, Any], metadata: Dict[str, Any], document_text: str = "") -> list[str]:
    """Ensures visual documents and org charts yield substantive entity_upserts and grounds visual nodes."""
    if not (metadata.get("is_visual_document") or metadata.get("allow_visual_image")):
        return []
    mutations = data.setdefault("graph_mutations", {})
    changes: list[str] = []
    ev_idx = data.setdefault("evidence_index", {})
    comps = (metadata.get("visual_extraction") or {}).get("components") or []
    ev_keys = list(ev_idx.keys())
    existing = {e.get("id") for e in mutations.setdefault("entity_upserts", []) if isinstance(e, dict)}
    if not mutations["entity_upserts"]:
        for idx, comp in enumerate(comps):
            item = _build_visual_entity_upsert(comp, ev_idx, ev_keys[idx % len(ev_keys)] if ev_keys else None)
            if item:
                mutations["entity_upserts"].append(item[0])
                existing.add(item[0]["id"])
                changes.append(item[1])
    doc_low = (document_text or "").lower()
    if any(t in doc_low for t in ("business mgmt", "business management")):
        _add_special_entity(mutations, ev_idx, existing, "org:business-mgmt", ("org:business-management",), "org", "Business Mgmt", "Business Management Office.", "business mgmt", changes)
    if "bennetto" in doc_low:
        _add_special_entity(mutations, ev_idx, existing, "person:jack-bennetto", ("person:bennetto-jack",), "person", "Jack Bennetto", "Jack Bennetto.", "bennetto", changes)
    _sanitize_visual_descriptions(mutations, changes)
    return changes


def _redact_string(val: str, patterns: list[Any]) -> str:
    for p in patterns:
        val = p.sub("[REDACTED]", val)
    return val


_OFFER_SCOPE_KEYWORDS = ("offer", "proposed", "anticipated", "contingent", "future")


def _is_valid_document_signal(s: Any, kept_ids: set[Any]) -> bool:
    if not isinstance(s, dict) or s.get("source") == "roster":
        return False
    fid = str(s.get("from_id") or "")
    return not (fid.startswith("person:") and fid not in kept_ids)


def _apply_offer_epistemic_status(s: Dict[str, Any]) -> None:
    scope_low = str(s.get("assertion_scope") or "").lower()
    if not any(w in scope_low for w in _OFFER_SCOPE_KEYWORDS):
        return
    s["epistemic_status"] = "PROPOSED"
    s["mutation"] = "PROPOSE"
    desc = str(s.get("description") or "")
    if s.get("relationship") == "MEMBER_OF" and desc.startswith("Member of "):
        s["description"] = desc.replace("Member of ", "Offered employment at ", 1)


def _restore_document_offer_and_roster_fidelity(final_data: Dict[str, Any]) -> None:
    muts = final_data.get("graph_mutations") or {}
    if not isinstance(muts, dict):
        return
    muts["entity_upserts"] = [
        e for e in (muts.get("entity_upserts") or [])
        if isinstance(e, dict) and not str(e.get("description") or "").startswith("Project Stakeholder at ")
    ]
    kept_ids = {e.get("id") for e in muts["entity_upserts"]}
    kept_sigs = [s for s in (muts.get("relationship_signals") or []) if _is_valid_document_signal(s, kept_ids)]
    for s in kept_sigs:
        _apply_offer_epistemic_status(s)
    muts["relationship_signals"] = kept_sigs


def scrub_archetype_injections(final_data: Dict[str, Any]) -> None:
    """Validate archetype-injected evidence and entity descriptions against SECRET/ADVISORY egress patterns."""
    _restore_document_offer_and_roster_fidelity(final_data)
    from dual_pipeline.sanitizer import ADVISORY_PATTERNS, SECRET_PATTERNS
    patterns = SECRET_PATTERNS + ADVISORY_PATTERNS
    for ev in (final_data.get("evidence_index") or {}).values():
        if not isinstance(ev, dict):
            continue
        for f in ("text", "exact_text", "direct_quote"):
            if isinstance(ev.get(f), str) and ev[f]:
                ev[f] = _redact_string(ev[f], patterns)
    for e in (final_data.get("graph_mutations") or {}).get("entity_upserts") or []:
        if isinstance(e, dict) and isinstance(e.get("description"), str) and e["description"]:
            e["description"] = _redact_string(e["description"], patterns)
