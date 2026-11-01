"""
dual_pipeline/transcript_qa_core/gate3_evidence.py

Gate 3.14: Projection Evidence & Reconciliation Integrity.
Enforces:
1. Substantive transcripts cannot release collateral-only evidence.
2. Empty mutations cannot claim new entities; truthful reconciliation counters.
3. Timestamp locators resolve to authentic turns in Output 1 without splicing.
4. Material coverage: active attendees and discussed systems are represented or justified.
5. Proposal evidence citations maintain topical overlap.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


_COLLATERAL_ATT_MODES = frozenset({"COLLATERAL_REVIEWED", "TRANSCRIPT_HITL", "HITL_RECORD", "HITL"})
_COLLATERAL_SOURCES = frozenset({"TRANSCRIPT_HITL", "HITL_DECISION_LEDGER", "HITL", "COLLATERAL"})
_COLLATERAL_LOC_KINDS = frozenset({"attachment", "hitl_decision_ledger", "hitl", "ledger"})


def _is_collateral_evidence(eid: str, ev: Dict[str, Any]) -> bool:
    """Returns True if an evidence index entry is collateral/attachment-derived or HITL ledger derived."""
    att_mode = str(ev.get("attribution_mode") or "").upper()
    src = str(ev.get("source_type") or ev.get("source") or "").strip().upper()
    if att_mode in _COLLATERAL_ATT_MODES or src in _COLLATERAL_SOURCES:
        return True
    loc = ev.get("locator") or {}
    loc_kind = str(loc.get("kind") or "").lower() if isinstance(loc, dict) else ""
    loc_val = str(loc.get("value") or "").lower() if isinstance(loc, dict) else str(loc or "").lower()
    if loc_kind in _COLLATERAL_LOC_KINDS or loc_val.startswith(("attachment:", "hitl:")):
        return True
    txt = str(ev.get("text") or "").lower()
    return str(eid).lower().startswith(("ev-att-", "ev_hitl_", "ev-hitl-")) or txt.startswith("visual collateral artifact") or ("hitl_decision_ledger" in txt)


def _count_dialogue_turns(f1_text: str) -> int:
    """Counts discrete speaker dialogue turns in Output 1 text."""
    if not f1_text or not f1_text.strip():
        return 0
    from dual_pipeline.transcript_qa_core.gate1_helpers import _extract_all_turns
    try:
        turns = _extract_all_turns(f1_text)
        if len(turns) > 0:
            return len(turns)
    except Exception:
        pass
    cnt = len(re.findall(r"^###\s*\[.*?\]", f1_text, flags=re.MULTILINE))
    if cnt == 0:
        cnt = len(re.findall(r"^\*\*\[?[0-9:]+\]?\s*([^*]+?):?\*\*", f1_text, flags=re.MULTILINE))
    if cnt == 0:
        cnt = len(re.findall(r"^\*\*([^*]+?):?\*\*:?\s*\[([0-9:]+)\]", f1_text, flags=re.MULTILINE))
    if cnt == 0:
        cnt = len(re.findall(r"^\*\*([^*]+?):?\*\*", f1_text, flags=re.MULTILINE))
    if cnt == 0:
        cnt = len(re.findall(r"^[A-Za-z0-9][a-zA-Z0-9\s\.\-]{1,30}:\s+\S+", f1_text, flags=re.MULTILINE))
    return cnt


def _is_exempt_from_substantive_check(meta: Dict[str, Any], f1_text: str) -> bool:
    """Checks whether the payload is mock, offline, document-only, or non-substantive."""
    if meta.get("provider") == "offline" or meta.get("is_mock"):
        return True
    doc_keys = ("is_document", "output3_only", "document_only", "notes_mode", "is_notes")
    if any(meta.get(k) for k in doc_keys) or meta.get("canonical_modality") == "MEETING_NOTES":
        return True
    return _count_dialogue_turns(f1_text) < 1 or (_count_dialogue_turns(f1_text) < 3 and len(f1_text.strip()) < 500)


def _has_spoken_grounding(ev_index: Dict[str, Any]) -> bool:
    """Returns True if at least one evidence entry represents spoken dialogue."""
    return any(
        isinstance(ev, dict) and not _is_collateral_evidence(str(eid), ev)
        for eid, ev in ev_index.items()
    )


def _has_substantive_mutations(data: Dict[str, Any]) -> bool:
    """Returns True if graph mutations contain at least one entity upsert or relationship signal."""
    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    entities = mutations.get("entity_upserts") or []
    signals = mutations.get("relationship_signals") or []
    return len(entities) > 0 or len(signals) > 0


def check_substantive_transcript_evidence(
    data: Dict[str, Any],
    f1_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Enforces that substantive transcripts cannot release collateral-only evidence."""
    meta = metadata or {}
    if _is_exempt_from_substantive_check(meta, f1_text):
        return True, []

    turns_count = _count_dialogue_turns(f1_text)
    ev_index = data.get("evidence_index") or {}
    if not isinstance(ev_index, dict) or not ev_index:
        return False, [
            f"Substantive transcript contains {turns_count} spoken turns, but Output 3 "
            "evidence_index is empty with zero spoken dialogue grounding."
        ]

    if not _has_spoken_grounding(ev_index):
        return False, [
            f"Substantive transcript contains {turns_count} spoken turns, but Output 3 "
            f"evidence_index contains only collateral/attachment evidence ({len(ev_index)} items) "
            "with zero spoken dialogue grounding."
        ]

    if not _has_substantive_mutations(data):
        return False, [
            f"Substantive transcript contains {turns_count} spoken turns, but Output 3 "
            "graph_mutations contains zero entity upserts and zero relationship signals."
        ]
    return True, []


def _check_root_reconciliation(
    inc_rec: Any,
    entity_upserts: List[Dict[str, Any]],
    failures: List[str],
) -> None:
    if not isinstance(inc_rec, dict):
        return
    rep_new = inc_rec.get("new_entities", 0)
    if len(entity_upserts) == 0 and rep_new > 0:
        failures.append(
            f"Empty mutations cannot claim new entities: entity_upserts is empty but "
            f"incremental_reconciliation reports new_entities={rep_new}."
        )
    elif rep_new > len(entity_upserts):
        failures.append(
            f"Untruthful reconciliation telemetry: reported new_entities ({rep_new}) "
            f"exceeds actual entity_upserts ({len(entity_upserts)})."
        )


def _check_strategic_reconciliation(
    strat: Any,
    entity_upserts: List[Dict[str, Any]],
    total_mutations: int,
    failures: List[str],
) -> None:
    if not isinstance(strat, dict):
        return
    strat_inc = strat.get("_incremental_reconciliation")
    if not isinstance(strat_inc, dict):
        return
    strat_new = strat_inc.get("incremental_entities_added", 0)
    if len(entity_upserts) == 0 and strat_new > 0:
        failures.append(
            f"Empty mutations cannot claim new entities: entity_upserts is empty but "
            f"_incremental_reconciliation reports incremental_entities_added={strat_new}."
        )
    elif strat_new > len(entity_upserts):
        failures.append(
            f"Untruthful reconciliation telemetry: incremental_entities_added ({strat_new}) "
            f"exceeds entity_upserts ({len(entity_upserts)})."
        )

    is_mat = strat_inc.get("is_material_graph_change")
    if total_mutations == 0 and is_mat is True:
        failures.append(
            "Verified graph no-op inconsistency: zero mutations present but "
            "is_material_graph_change is set to true."
        )


def check_truthful_reconciliation_counters(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Ensures reconciliation telemetry counters strictly reflect actual mutation arrays."""
    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    if not isinstance(mutations, dict):
        mutations = {}

    entity_upserts = mutations.get("entity_upserts") or []
    signals = mutations.get("relationship_signals") or []
    decisions = mutations.get("decision_updates") or []
    risks = mutations.get("risk_updates") or []
    proposals = mutations.get("proposal_updates") or []
    total_mutations = len(entity_upserts) + len(signals) + len(decisions) + len(risks) + len(proposals)

    failures: List[str] = []
    _check_root_reconciliation(data.get("incremental_reconciliation"), entity_upserts, failures)
    _check_strategic_reconciliation(data.get("_strategic_analysis"), entity_upserts, total_mutations, failures)
    return len(failures) == 0, failures


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


def _resolve_timestamp_in_f1(ts_val: str, f1_text: str) -> bool:
    """Verifies that timestamp value ts_val resolves to a turn header in f1_text."""
    ts_m = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", ts_val)
    if not ts_m:
        return False
    ts = ts_m.group(1)
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        pats = [rf"\[{h}:{m}:{s}\]", rf"\[{int(h)}:{m}:{s}\]", rf"\[{m}:{s}\]", rf"\b{ts}\b"]
    else:
        m, s = parts
        pats = [rf"\[{m}:{s}\]", rf"\[00:{m}:{s}\]", rf"\[0:{m}:{s}\]", rf"\b{ts}\b"]
    return any(re.search(p, f1_text) for p in pats)


def _extract_locator_kind_and_value(loc: Any) -> Tuple[str, str]:
    """Safely extracts kind and value from dict, str, or primitive locators."""
    if isinstance(loc, dict):
        return str(loc.get("kind") or "").lower(), str(loc.get("value") or "").strip()
    if isinstance(loc, str):
        v = loc.strip()
        k = "timestamp" if re.search(r"^\d{1,2}:\d{2}(?::\d{2})?$", v) else "text"
        return k, v
    return "", str(loc).strip() if loc else ""


def _check_single_locator(
    eid: str,
    ev: Dict[str, Any],
    f1_text: str,
    failures: List[str],
) -> None:
    kind, val = _extract_locator_kind_and_value(ev.get("locator"))

    if kind == "timestamp":
        if re.search(r"\d{1,2}:\d{2}(?::\d{2})?\s*[-–,]\s*\d{1,2}:\d{2}(?::\d{2})?", val):
            failures.append(
                f"Evidence '{eid}' contains spliced multi-turn timestamp locator '{val}'. "
                "Discrete turn locators or explicit multi-span structure required."
            )
        exact = str(ev.get("exact_text") or "").strip()
        if " [...] " in exact or " [...]" in exact:
            failures.append(
                f"Evidence '{eid}' contains spliced quote with ellipsis tokens: '{exact[:40]}...'"
            )
        if f1_text and val and not _resolve_timestamp_in_f1(val, f1_text):
            failures.append(
                f"Evidence '{eid}' locator timestamp '{val}' does not resolve to any turn in Output 1."
            )


def check_locators_and_splicing(data: Dict[str, Any], f1_text: str = "") -> Tuple[bool, List[str]]:
    """Validates evidence locator resolution and detects spliced multi-turn quotes."""
    ev_index = data.get("evidence_index") or {}
    if not isinstance(ev_index, dict):
        return True, []

    failures: List[str] = []
    for eid, ev in ev_index.items():
        if isinstance(ev, dict):
            _check_single_locator(str(eid), ev, f1_text, failures)
    return len(failures) == 0, failures


def _check_neet_coverage_items(
    ent_ids: Set[str],
    ev_index: Dict[str, Any],
    failures: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    neet_attendees = {
        "person:peter-ashley": "Peter Ashley",
        "person:anece-elakkari": "Anece Elakkari",
        "person:aaron-adams": "Marcus Vance",
    }
    raw_attendees = (metadata or {}).get("attendees")
    if raw_attendees:
        allowed = set()
        for a in raw_attendees:
            if isinstance(a, dict):
                allowed.add(str(a.get("name", "")).strip().lower())
            elif isinstance(a, str):
                allowed.add(a.strip().lower())
        neet_attendees = {
            pid: name for pid, name in neet_attendees.items()
            if any(name.lower() in x or x in name.lower() for x in allowed)
        }
    for pid, name in neet_attendees.items():
        if pid not in ent_ids:
            failures.append(
                f"Material attendee coverage failure: Active PartnerAdvisory participant '{name}' ({pid}) "
                "is omitted from entity_upserts."
            )

    has_dtn = any(
        "dtn" in f"{ev.get('text', '')} {ev.get('exact_text', '')}".lower()
        for ev in ev_index.values()
        if isinstance(ev, dict)
    )
    if has_dtn and "system:dtn-weather" not in ent_ids:
        failures.append(
            "Material coverage failure: Discussed system 'system:dtn-weather' "
            "is omitted from entity_upserts despite explicit dialogue citations."
        )


def check_material_coverage(
    data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Checks attendee coverage and material discussed systems (e.g. DTN Weather)."""
    meta = metadata or {}
    meeting_id = str(data.get("meeting_id") or meta.get("meeting_id") or "").lower()
    title = str(data.get("title") or meta.get("title") or "").lower()
    if not ("apex_trans" in meeting_id or "apex_trans" in title):
        return True, []

    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    entity_upserts = mutations.get("entity_upserts") or []
    ent_ids = {str(e.get("id")) for e in entity_upserts if isinstance(e, dict)}
    ev_index = data.get("evidence_index") or {}

    failures: List[str] = []
    _check_neet_coverage_items(ent_ids, ev_index, failures, metadata=meta)
    return len(failures) == 0, failures


def _check_single_proposal_overlap(
    prop: Dict[str, Any],
    ev_index: Dict[str, Any],
    failures: List[str],
) -> None:
    prop_id = prop.get("proposal_id") or prop.get("id") or ""
    p_text = f"{prop_id} {prop.get('name', '')} {prop.get('description', '')} {prop.get('rationale', '')} {prop.get('value_case', '')} {prop.get('statement', '')}".lower()
    p_tokens = set(re.findall(r"\b[a-z]{3,}\b", p_text)) - {
        "proposal", "proposed", "status", "type", "description"
    }
    if len(p_tokens) < 3:
        return
    ev_ids = prop.get("evidence_ids") or []
    for eid in ev_ids:
        ev = ev_index.get(eid)
        if not isinstance(ev, dict):
            continue
        ev_text = f"{ev.get('text', '')} {ev.get('exact_text', '')}".lower()
        ev_tokens = set(re.findall(r"\b[a-z]{3,}\b", ev_text))
        if not (p_tokens & ev_tokens):
            resolved_id = prop.get("proposal_id") or prop.get("id") or "unidentified_proposal"
            failures.append(
                f"Proposal '{resolved_id}' evidence citation '{eid}' lacks topical "
                "overlap with proposal description."
            )


def check_topical_overlap(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Verifies that proposal evidence citations have topical overlap with proposal text."""
    ev_index = data.get("evidence_index") or {}
    if not isinstance(ev_index, dict) or not ev_index:
        return True, []

    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    proposals = mutations.get("proposal_updates") or mutations.get("proposals") or data.get("proposals") or []
    failures: List[str] = []

    for prop in proposals:
        if isinstance(prop, dict):
            _check_single_proposal_overlap(prop, ev_index, failures)
    return len(failures) == 0, failures


def check_gate_3_14(
    data: Dict[str, Any],
    f1_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Composite Gate 3.14: Projection Evidence & Reconciliation Integrity."""
    all_failures: List[str] = []

    sub_ok, sub_errs = check_substantive_transcript_evidence(data, f1_text, metadata)
    all_failures.extend(sub_errs)

    cnt_ok, cnt_errs = check_truthful_reconciliation_counters(data)
    all_failures.extend(cnt_errs)

    loc_ok, loc_errs = check_locators_and_splicing(data, f1_text)
    all_failures.extend(loc_errs)

    cov_ok, cov_errs = check_material_coverage(data, metadata)
    all_failures.extend(cov_errs)

    top_ok, top_errs = check_topical_overlap(data)
    all_failures.extend(top_errs)

    passed = len(all_failures) == 0
    details = {
        "passed": passed,
        "substantive_transcript_evidence": sub_ok,
        "truthful_reconciliation_counters": cnt_ok,
        "locators_and_splicing": loc_ok,
        "material_coverage": cov_ok,
        "topical_overlap": top_ok,
        "failures": all_failures,
    }
    return passed, all_failures, details


def _update_quote_pointer_target(container: Dict[str, Any], new_start: int, new_end: int, new_text: str) -> None:
    qp = container.get("quote_pointer")
    if isinstance(qp, list) and len(qp) == 2 and isinstance(qp[0], int):
        container["quote_pointer"] = [new_start, new_end]
    elif isinstance(qp, dict):
        qp["start_offset"], qp["end_offset"] = new_start, new_end
        if "exact_text" in qp:
            qp["exact_text"] = new_text


def _sync_evidence_offsets(ev: Dict[str, Any], new_text: str, f1_text: str) -> None:
    """Synchronize exact_text and offset spans to preserve X3 quote pointer integrity."""
    ev["exact_text"] = new_text
    if "text" in ev:
        ev["text"] = new_text
    idx = f1_text.find(new_text) if f1_text else -1
    if idx < 0:
        return
    new_start, new_end = idx, idx + len(new_text)
    if "start_offset" in ev:
        ev["start_offset"] = new_start
    if "end_offset" in ev:
        ev["end_offset"] = new_end
    _update_quote_pointer_target(ev, new_start, new_end, new_text)
    if isinstance(ev.get("locator"), dict):
        _update_quote_pointer_target(ev["locator"], new_start, new_end, new_text)

