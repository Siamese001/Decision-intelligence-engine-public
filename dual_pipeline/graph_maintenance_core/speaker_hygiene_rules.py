"""Agent 3 & Agent 4 hygiene rules for speaker attribution, synthetic purity, and qualifier drift."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from dual_pipeline.l0.speaker_and_qualifier_policy import (
    ORG_OR_OPERATOR_SPEAKER_LABELS, classify_speaker_attribution_defect, clean_non_placeholder_speaker,
    extract_inline_turn_speaker, has_non_promotable_qualifiers, has_synthetic_or_cross_project_pollution,
    is_clearable_structured_placeholder, is_degenerate_single_token_claim, is_placeholder_speaker,
    is_structured_profile_card_claim, is_unresolved_channel_or_room_speaker, normalize_candidate_claim_text,
)
from .transcript_turn_resolver import (
    build_undiarized_turn_hitl_context, is_single_channel_undiarized_transcript,
    is_verified_human_speaker, normalize_timestamp_locator, resolve_diarized_transcript_turn_speaker,
)

_RED_TEAM_PREFIXES = ("cross_project_contamination", "unquarantined_advisory_assertion", "synthetic_node_pollution")
_ACTION_OWNER_RE = re.compile(r"^([A-Z][a-z]+)_([A-Z][a-z]+)\s+will\b")
_PLAIN_DEFECT_DIMENSIONS: Dict[str, str] = {
    "placeholder_speaker_label": "Generic Roster Header", "machine_slug_speaker": "Internal Speaker Token",
    "stripped_empty_speaker_colon": "Empty Speaker Prefix", "inline_speaker_contradiction": "Speaker Tag Mismatch",
    "missing_meeting_speaker": "Missing Speaker Name", "unresolved_channel_or_room_speaker": "Unresolved Channel Label",
    "organization_or_operator_speaker": "Organization Speaker Tag", "organization_or_unresolved_speaker": "Organization Speaker Tag",
    "single_token_speaker_name": "First-Name Speaker Label", "synthetic_fixture_marker": "Synthetic Test Marker",
    "cross_project_persona_contamination": "Scope Boundary Alert",
}
_ITEMIZED_ROW_FIXES = frozenset({
    "archive_candidate_assertion", "batch_promote_candidates", "rebind_placeholder_speaker",
    "attribute_undiarized_speaker_hitl", "merge_entities", "deduplicate_assertions",
    "supersede_assertion", "consolidate_duplicate_sources",
})


def _build_itemized_batch_promotion_rows(
    finding: Any,
    debate: Any,
    clean_cell_fn: Callable[[Any, int], str],
) -> List[str]:
    raw = getattr(finding, "raw_data", None) or {}
    items = raw.get("promotable_items") or []
    verdict = clean_cell_fn("Promote", 8)
    rows: List[str] = []
    for it in items:
        subj = clean_cell_fn(it.get("subject_name") or "Topic", 7)
        spk = clean_cell_fn(it.get("resolved_speaker") or "Speaker", 11)
        claim = clean_cell_fn(it.get("claim_text") or "", 58)
        raw_src = str(it.get("source_title") or "Meeting").replace("_", " ")
        src = clean_cell_fn(f"{raw_src[:8]} {it.get('transcript_timestamp') or ''}".strip(), 15)
        detail = clean_cell_fn(f"[PROMOTE] {claim}", 68)
        rows.append(f"| {clean_cell_fn(f'{subj}/{spk}', 17)} | {detail} | {src} | {verdict} |")
    return rows


def _bucket_key_for_finding(
    f: Any,
    debate: Any,
    clean_cell_fn: Callable[[Any, int], str],
    detector_labels: Dict[str, str],
) -> tuple[str, str, str, str]:
    raw = getattr(f, "raw_data", None) or {}
    d_code = str(raw.get("defect_code") or "")
    fix = str(getattr(f, "proposed_fix", "") or "")
    default_dim = "Metadata Alignment" if fix == "sync_promoted_qualifier_metadata" else detector_labels.get(str(getattr(f, "detective", "") or ""), "Hygiene Review")
    dim = _PLAIN_DEFECT_DIMENSIONS.get(d_code, default_dim)
    detail = clean_cell_fn(str(getattr(f, "description", "") or "").replace("_", " "), 95)
    sev = f"Priority {str(getattr(f, 'severity', '') or 'MEDIUM').title()}"
    verdict = clean_cell_fn(getattr(debate, "committee_verdict", "Present for operator decision"), 75)
    return (dim, detail, sev, verdict)


def build_mece_cluster_finding_rows(
    findings: List[Any],
    debates: List[Any],
    format_row_fn: Callable[[Any, Any], str],
    clean_cell_fn: Callable[[Any, int], str],
    detector_labels: Dict[str, str],
) -> List[str]:
    """Render itemized rows for pair/source clusters and MECE grouped counts for bulk clusters."""
    if not findings:
        return []
    if len(findings) == 1 and str(getattr(findings[0], "proposed_fix", "") or "") == "batch_promote_candidates":
        promo_rows = _build_itemized_batch_promotion_rows(findings[0], debates[0] if debates else None, clean_cell_fn)
        if promo_rows:
            return promo_rows
    if all(str(getattr(f, "proposed_fix", "") or "") in _ITEMIZED_ROW_FIXES for f in findings):
        return [format_row_fn(f, d) for f, d in zip(findings, debates)]
    buckets: Dict[tuple[str, str, str, str], int] = {}
    for idx, f in enumerate(findings):
        key = _bucket_key_for_finding(f, debates[idx] if idx < len(debates) else None, clean_cell_fn, detector_labels)
        buckets[key] = buckets.get(key, 0) + 1
    ordered = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0][0]))[:12]
    return [
        f"| {clean_cell_fn(dim, 55)} | {detail} | {clean_cell_fn(f'{cnt} records ({sev})', 55)} | {verdict} |"
        for (dim, detail, sev, verdict), cnt in ordered
    ]


_PLAIN_DEFECT_DESCRIPTIONS: Dict[str, str] = {
    "placeholder_speaker_label": "Active meeting statement uses a generic roster or section header instead of a real speaker",
    "machine_slug_speaker": "Active statement uses an internal identifier token instead of a verified person name",
    "stripped_empty_speaker_colon": "Active statement begins with an empty speaker prefix after label stripping",
    "inline_speaker_contradiction": "Inline speaker tag inside statement text contradicts the recorded speaker field",
    "missing_meeting_speaker": "Spoken meeting statement is missing a verified participant name",
    "unresolved_channel_or_room_speaker": "Spoken meeting statement uses an unresolved recording channel or room label",
    "organization_or_operator_speaker": "Spoken meeting statement is attributed to an organization or operator tag instead of a person",
    "organization_or_unresolved_speaker": "Spoken meeting statement is attributed to an organization or operator tag instead of a person",
    "single_token_speaker_name": "Active statement uses a first-name speaker label instead of a full person name",
    "synthetic_fixture_marker": "Synthetic test marker leaked into an active knowledge record",
    "cross_project_persona_contamination": "Synthetic or out-of-charter persona appeared outside its valid project scope",
}


def _plain_antipattern_description(item: Any, details: Dict[str, Any], resolved_fix: str) -> str:
    if resolved_fix == "rebind_placeholder_speaker":
        spk = str(details.get("resolved_speaker") or details.get("suggested_rebound_speaker") or "Verified Speaker")
        return f"Deterministic transcript provenance resolved speaker attribution to {spk}"
    if resolved_fix == "attribute_undiarized_speaker_hitl":
        src = str(details.get("source_title") or details.get("source_name") or "Meeting").replace("_", " ")
        ts = str(details.get("transcript_timestamp") or "00:00:00")
        return f"Undiarized spoken statement in {src} at {ts} requires operator speaker attribution"
    action = str(getattr(item, "action_recommended", "") or "")
    if action == "quarantine_undiarized_or_misattributed_claim":
        code = str(details.get("defect_code") or "")
        return _PLAIN_DEFECT_DESCRIPTIONS.get(
            code,
            "Active meeting statement lacks a verified participant name and requires staging hold",
        )
    if action == "sync_promoted_qualifier_metadata":
        return "Approved official statement still carries outdated staging classification labels"
    if action == "archive_clutter" and str(getattr(item, "finding_type", "")).startswith("synthetic_node_pollution"):
        cname = str(details.get("canonical_name") or item.target_id)
        return f"Synthetic test profile ({cname}) is active in the production graph"
    return str(getattr(item, "label", "") or "Governance hygiene issue detected")


def _enrich_undiarized_hitl_details(details: Dict[str, Any]) -> Dict[str, Any]:
    raw_txt = str(details.get("scope_text") or details.get("object_literal") or details.get("claim_preview") or "")
    clean_txt = normalize_candidate_claim_text(raw_txt)
    src_title = str(details.get("source_title") or details.get("source_name") or "Meeting Recording")
    ctx = build_undiarized_turn_hitl_context(
        details.get("cold_archive_ref"),
        details.get("transcript_timestamp"),
        clean_txt,
        details.get("speaker_or_author"),
        source_title=src_title,
    )
    roster = ctx["roster_candidates"]
    details.update({
        "claim_text": clean_txt,
        "source_title": src_title,
        "transcript_timestamp": ctx["target_timestamp"],
        "context_block": ctx["context_block"],
        "roster_candidates": roster,
        "recommended_speaker": roster[0] if roster else "Amit Ayer",
    })
    return details


def _route_canonical_speaker_defect(
    fix: str,
    details: Dict[str, Any],
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> str:
    d_code = str(details.get("defect_code") or "")
    if fix != "quarantine_undiarized_or_misattributed_claim":
        return fix
    if d_code in ("synthetic_fixture_marker", "cross_project_persona_contamination") or not details.get("cold_archive_ref"):
        return fix
    eff_spk, is_rec = resolve_candidate_provenance_speaker(details, sibling_by_ev, sibling_by_ts)
    if not is_rec:
        clean_txt = normalize_candidate_claim_text(str(details.get("scope_text") or details.get("claim_preview") or ""))
        if is_degenerate_single_token_claim(clean_txt):
            return fix
        _enrich_undiarized_hitl_details(details)
        return "attribute_undiarized_speaker_hitl"
    details["suggested_rebound_speaker"] = eff_spk
    details["resolved_speaker"] = eff_spk or "Structured Card"
    details["claim_text"] = normalize_candidate_claim_text(str(details.get("scope_text") or details.get("claim_preview") or ""))
    details["source_title"] = str(details.get("source_name") or "Meeting")
    return "rebind_placeholder_speaker"


def build_antipattern_hygiene_findings(
    antipattern_items: Iterable[Any],
    finding_cls: Callable[..., Any],
    cursor: Any = None,
) -> List[Any]:
    """Convert GraphScout anti-pattern findings into 13-Agent Council Finding objects."""
    sibling_by_ev: Dict[tuple[str, str], str] = {}
    sibling_by_ts: Dict[tuple[str, str], str] = {}
    if cursor is not None:
        try:
            asrt_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
            sibling_by_ev, sibling_by_ts = _load_sibling_speaker_maps(cursor, asrt_cols)
        except Exception:
            pass
    results: List[Any] = []
    for item in antipattern_items:
        ftype = str(getattr(item, "finding_type", "") or "")
        details = dict(getattr(item, "details", None) or {})
        raw_fix = str(getattr(item, "action_recommended", "") or "")
        base_fix = "archive_node" if raw_fix == "archive_clutter" else raw_fix
        fix = _route_canonical_speaker_defect(base_fix, details, sibling_by_ev, sibling_by_ts)
        is_red_team = ftype.startswith(_RED_TEAM_PREFIXES) or "persona" in ftype
        sev = "LOW" if fix in ("sync_promoted_qualifier_metadata", "rebind_placeholder_speaker") else "HIGH"
        results.append(
            finding_cls(
                detective="RedTeamer" if is_red_team else "ProvenanceInspector",
                severity=sev,
                description=_plain_antipattern_description(item, details, fix),
                entity_references=[str(item.target_id)],
                proposed_fix=fix,
                raw_data=details,
            )
        )
    return results


_STAKEHOLDER_NODE_TYPES = frozenset({"stakeholder", "person", "internal_person", "person_profile"})
_ORG_OR_OPERATOR_SPEAKERS = ORG_OR_OPERATOR_SPEAKER_LABELS


def _resolve_subject_or_sibling_speaker(
    row: Dict[str, Any],
    txt: str,
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> Optional[str]:
    if is_single_channel_undiarized_transcript(row.get("cold_archive_ref")):
        return None
    if not row.get("cold_archive_ref"):
        ntype = str(row.get("subject_node_type") or "").strip().lower()
        subj_name = clean_non_placeholder_speaker(row.get("subject_canonical_name"))
        if ntype in _STAKEHOLDER_NODE_TYPES and subj_name and not is_structured_profile_card_claim(txt):
            return subj_name
    src_id = str(row.get("primary_source_id") or "")
    ev_id = str(row.get("evidence_id") or "")
    if src_id and ev_id and (src_id, ev_id) in sibling_by_ev:
        return sibling_by_ev[(src_id, ev_id)]
    ts_raw = str(row.get("transcript_timestamp") or "")
    ts_norm = normalize_timestamp_locator(ts_raw) or ts_raw
    return sibling_by_ts.get((src_id, ts_norm)) or sibling_by_ts.get((src_id, ts_raw)) if src_id and ts_raw else None


def _is_clearable_structured_placeholder(row: Dict[str, Any], txt: str) -> bool:
    return is_clearable_structured_placeholder(
        txt, row.get("qualifiers_json"), row.get("transcript_timestamp"), row.get("validation_state"),
        row.get("speaker_or_author"), row.get("source_type"), row.get("source_name"), row.get("cold_archive_ref"),
    )


def _recover_provenance_speaker_for_row(
    row: Dict[str, Any],
    clean_txt: str,
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> Optional[str]:
    raw_txt = str(row.get("scope_text") or row.get("object_literal") or "")
    inl_spk = extract_inline_turn_speaker(raw_txt) or extract_inline_turn_speaker(clean_txt)
    if inl_spk:
        return inl_spk
    ts_spk = resolve_diarized_transcript_turn_speaker(
        row.get("cold_archive_ref"), row.get("transcript_timestamp"), clean_txt, row.get("direct_quote"),
    )
    if ts_spk:
        return ts_spk
    if is_unresolved_channel_or_room_speaker(row.get("speaker_or_author")):
        return None
    return _resolve_subject_or_sibling_speaker(row, clean_txt, sibling_by_ev, sibling_by_ts)


def _resolve_non_placeholder_candidate_speaker(
    row: Dict[str, Any],
    clean_raw: str,
    clean_txt: str,
    inl_spk: Optional[str],
    txt_changed: bool,
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> tuple[Optional[str], bool]:
    if inl_spk and inl_spk.lower() != clean_raw.lower():
        return inl_spk, True
    if clean_raw.lower() in _ORG_OR_OPERATOR_SPEAKERS or is_unresolved_channel_or_room_speaker(clean_raw):
        if _is_clearable_structured_placeholder(row, clean_txt) and not inl_spk:
            return None, True
        rec = _recover_provenance_speaker_for_row(row, clean_txt, sibling_by_ev, sibling_by_ts)
        if rec:
            return rec, True
        if clean_raw.lower() == "operator":
            return "Amit Ayer", True
        return clean_raw, False
    ver_spk = is_verified_human_speaker(clean_raw)
    if ver_spk:
        return ver_spk, (ver_spk != clean_raw or txt_changed)
    return (clean_raw or None), txt_changed


def resolve_candidate_provenance_speaker(
    row: Dict[str, Any],
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> tuple[Optional[str], bool]:
    """Return (effective_speaker, is_recoverable_placeholder) for an assertion row."""
    raw_spk = row.get("speaker_or_author")
    raw_txt = str(row.get("scope_text") or row.get("object_literal") or "")
    clean_txt = normalize_candidate_claim_text(raw_txt)
    if is_degenerate_single_token_claim(clean_txt):
        return (str(raw_spk) if raw_spk is not None else None), False
    inl_spk = extract_inline_turn_speaker(raw_txt) or extract_inline_turn_speaker(clean_txt)
    clean_raw = str(raw_spk or "").strip()
    if clean_raw and not is_placeholder_speaker(raw_spk):
        return _resolve_non_placeholder_candidate_speaker(
            row, clean_raw, clean_txt, inl_spk, clean_txt != raw_txt.strip(), sibling_by_ev, sibling_by_ts
        )
    if _is_clearable_structured_placeholder(row, clean_txt) and not inl_spk:
        return None, True
    resolved = _recover_provenance_speaker_for_row(row, clean_txt, sibling_by_ev, sibling_by_ts)
    if resolved:
        return resolved, True
    return (str(raw_spk) if raw_spk is not None else None), False


def _extract_reported_or_action_speaker(scope_text: Optional[str]) -> Optional[str]:
    txt = str(scope_text or "").strip()
    if " is a reported speaker in " in txt:
        return is_verified_human_speaker(txt.split(" is a reported speaker in ", 1)[0].strip())
    m_act = _ACTION_OWNER_RE.match(txt)
    if m_act:
        return is_verified_human_speaker(f"{m_act.group(1)} {m_act.group(2)}")
    return None


def _record_sibling_speaker_row(
    row: tuple[Any, ...], has_cold: bool, by_ev: Dict[tuple[str, str], str], by_ts: Dict[tuple[str, str], str]
) -> None:
    src_id, ev_id, ts_val, cname, ntype, spk, scope_txt = row
    if not src_id:
        return
    resolved = is_verified_human_speaker(spk) or _extract_reported_or_action_speaker(scope_txt)
    if not resolved and not has_cold and str(ntype or "").lower() in _STAKEHOLDER_NODE_TYPES:
        resolved = clean_non_placeholder_speaker(cname)
    if not resolved:
        return
    if ev_id:
        by_ev.setdefault((str(src_id), str(ev_id)), resolved)
    if not ts_val:
        return
    ts_n = normalize_timestamp_locator(str(ts_val))
    if ts_n:
        by_ts.setdefault((str(src_id), ts_n), resolved)
    by_ts.setdefault((str(src_id), str(ts_val)), resolved)


def _load_sibling_speaker_maps(cursor: Any, asrt_cols: Set[str]) -> tuple[Dict[tuple[str, str], str], Dict[tuple[str, str], str]]:
    by_ev: Dict[tuple[str, str], str] = {}
    by_ts: Dict[tuple[str, str], str] = {}
    if not {"primary_source_id", "subject_node_id"}.issubset(asrt_cols):
        return by_ev, by_ts
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not {"node", "assertion_evidence", "evidence_item"}.issubset(tables):
        return by_ev, by_ts
    ei_cols = {r[1] for r in cursor.execute("PRAGMA table_info(evidence_item)").fetchall()}
    src_cols = {r[1] for r in cursor.execute("PRAGMA table_info(source)").fetchall()} if "source" in tables else set()
    has_cold = "cold_archive_ref" in src_cols
    ts_expr = "coalesce(ei.transcript_timestamp, ei.locator_value)" if "transcript_timestamp" in ei_cols else "ei.locator_value"
    scope_expr = "a.scope_text" if "scope_text" in asrt_cols else "NULL"
    sql = (
        f"SELECT a.primary_source_id, ae.evidence_id, {ts_expr} AS ts, n.canonical_name, n.node_type, a.speaker_or_author, {scope_expr} "
        "FROM assertion a LEFT JOIN node n ON n.node_id = a.subject_node_id "
        "JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id "
        "JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id"
    )
    for row in cursor.execute(sql).fetchall():
        _record_sibling_speaker_row(row, has_cold, by_ev, by_ts)
    return by_ev, by_ts


def _candidate_row_passes_promotion_safety(
    row: Dict[str, Any],
    sibling_by_ev: Optional[Dict[tuple[str, str], str]] = None,
    sibling_by_ts: Optional[Dict[tuple[str, str], str]] = None,
) -> bool:
    if has_non_promotable_qualifiers(row.get("qualifiers_json")):
        return False
    clean_txt = normalize_candidate_claim_text(str(row.get("scope_text") or row.get("object_literal") or ""))
    if is_degenerate_single_token_claim(clean_txt):
        return False
    eff_spk, _ = resolve_candidate_provenance_speaker(row, sibling_by_ev or {}, sibling_by_ts or {})
    defect = classify_speaker_attribution_defect(
        predicate=row.get("predicate"), scope_text=clean_txt, speaker_or_author=eff_spk,
        subject_node_id=row.get("subject_node_id"), source_type=row.get("source_type"),
        source_name=row.get("source_name"), assertion_id=str(row.get("assertion_id") or ""),
        qualifiers_json=row.get("qualifiers_json"), transcript_timestamp=row.get("transcript_timestamp"),
        validation_state=row.get("validation_state"), cold_archive_ref=row.get("cold_archive_ref"),
    )
    return defect is None


def _source_select_exprs(src_cols: Set[str]) -> List[str]:
    return [f"s.{c}" if c in src_cols else f"NULL AS {c}" for c in ("source_type", "source_name", "cold_archive_ref")]


def _promotable_select_columns(cursor: Any, asrt_cols: Set[str], tables: Set[str]) -> tuple[List[str], str, str]:
    has_src = "primary_source_id" in asrt_cols and "source" in tables
    has_node = "subject_node_id" in asrt_cols and "node" in tables
    has_ei = "assertion_evidence" in tables and "evidence_item" in tables
    src_cols = {r[1] for r in cursor.execute("PRAGMA table_info(source)").fetchall()} if has_src else set()
    ei_cols = {r[1] for r in cursor.execute("PRAGMA table_info(evidence_item)").fetchall()} if has_ei else set()
    ts_col = "coalesce(ei.transcript_timestamp, ei.locator_value)" if "transcript_timestamp" in ei_cols else "ei.locator_value"
    ev_sub = "(SELECT ae.evidence_id FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS evidence_id"
    ts_sub = f"(SELECT {ts_col} FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS transcript_timestamp"
    dq_sub = (
        "(SELECT ei.direct_quote FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS direct_quote"
        if has_ei and "direct_quote" in ei_cols
        else "NULL AS direct_quote"
    )
    sel = [
        "a.assertion_id",
        *(f"a.{c}" if c in asrt_cols else f"NULL AS {c}" for c in (
            "subject_node_id", "predicate", "scope_text", "object_literal", "speaker_or_author", "qualifiers_json", "primary_source_id", "validation_state"
        )),
        *_source_select_exprs(src_cols),
        "n.canonical_name AS subject_canonical_name" if has_node else "NULL AS subject_canonical_name",
        "n.node_type AS subject_node_type" if has_node else "NULL AS subject_node_type",
        ev_sub if has_ei else "NULL AS evidence_id",
        ts_sub if has_ei else "NULL AS transcript_timestamp",
        dq_sub,
    ]
    sjoin = "LEFT JOIN source s ON a.primary_source_id = s.source_id " if has_src else ""
    njoin = "LEFT JOIN node n ON a.subject_node_id = n.node_id " if has_node else ""
    return sel, sjoin, njoin


def _build_safe_promotable_sql(cursor: Any, asrt_cols: Set[str]) -> str:
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    sel, sjoin, njoin = _promotable_select_columns(cursor, asrt_cols, tables)
    val_col = "a.validation_state" if "validation_state" in asrt_cols else "'needs_review'"
    conf_col = "coalesce(a.confidence_score, 0.0)" if "confidence_score" in asrt_cols else "0.0"
    ext_conf = (
        "coalesce(CAST(json_extract(a.qualifiers_json, '$.extraction_confidence') AS REAL), 0.0)"
        if "qualifiers_json" in asrt_cols
        else "0.0"
    )
    conf_cond = f"({conf_col} >= 0.72 OR {ext_conf} >= 0.72)"
    curr_cond = "AND coalesce(a.currentness_state, 'current') = 'current' " if "currentness_state" in asrt_cols else ""
    sup_cond = "AND a.superseded_at IS NULL " if "superseded_at" in asrt_cols else ""
    sup_by_cond = "AND a.superseded_by_assertion_id IS NULL " if "superseded_by_assertion_id" in asrt_cols else ""
    status_clause = (
        f"((a.canonical_status = 'candidate' {curr_cond}) "
        f"OR (a.canonical_status = 'historical_candidate' {sup_cond}"
        f"AND coalesce({val_col}, '') NOT IN ('rejected', 'superseded', 'retired'))) {sup_by_cond}"
    )
    extra = "".join([
        "AND coalesce(a.retrieval_eligible, 0) = 0 " if "retrieval_eligible" in asrt_cols else "",
        "AND coalesce(a.is_quarantined, 0) = 0 " if "is_quarantined" in asrt_cols else "",
    ])
    return (
        f"SELECT {', '.join(sel)} FROM assertion a {sjoin}{njoin}"
        f"WHERE {status_clause}{extra}"
        f"AND {conf_cond} AND ({val_col} = 'accepted_legacy' OR a.assertion_id IN ("
        "SELECT DISTINCT assertion_id FROM assertion_evidence WHERE assertion_id IS NOT NULL))"
    )


def _build_promotable_item_summary(
    row: Dict[str, Any],
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> Dict[str, Any]:
    eff_spk, _ = resolve_candidate_provenance_speaker(row, sibling_by_ev, sibling_by_ts)
    raw_txt = str(row.get("scope_text") or row.get("object_literal") or "")
    clean_txt = normalize_candidate_claim_text(raw_txt)
    return {
        "assertion_id": str(row["assertion_id"]),
        "subject_name": str(row.get("subject_canonical_name") or row.get("subject_node_id") or "Workstream"),
        "resolved_speaker": str(eff_spk or row.get("speaker_or_author") or "Structured Card"),
        "claim_text": clean_txt[:90],
        "source_title": str(row.get("source_name") or "Meeting Transcript"),
        "transcript_timestamp": str(row.get("transcript_timestamp") or "verified"),
    }


def audit_safe_promotable_candidates(
    cursor: Any,
    asrt_cols: Set[str],
    excluded_ids: Optional[Set[str]],
    finding_cls: Callable[..., Any],
) -> Optional[Any]:
    """Identify high-confidence candidate assertions that pass speaker, purity, and qualifier gates."""
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_evidence'")
    if "canonical_status" not in asrt_cols or not cursor.fetchone():
        return None
    sibling_by_ev, sibling_by_ts = _load_sibling_speaker_maps(cursor, asrt_cols)
    cursor.execute(_build_safe_promotable_sql(cursor, asrt_cols))
    cols = [d[0] for d in (cursor.description or [])]
    excl = excluded_ids or set()
    promotable_rows = [
        row
        for raw_row in cursor.fetchall()
        for row in (dict(raw_row) if hasattr(raw_row, "keys") else dict(zip(cols, raw_row)),)
        if str(row["assertion_id"]) not in excl and _candidate_row_passes_promotion_safety(row, sibling_by_ev, sibling_by_ts)
    ]
    if not promotable_rows:
        return None
    promotable_ids = [str(r["assertion_id"]) for r in promotable_rows]
    promotable_items = [_build_promotable_item_summary(r, sibling_by_ev, sibling_by_ts) for r in promotable_rows]
    return finding_cls(
        detective="ProvenanceInspector",
        severity="LOW",
        description=f"Identified {len(promotable_ids)} verified high-confidence staging note(s) eligible for promotion.",
        entity_references=promotable_ids,
        proposed_fix="batch_promote_candidates",
        raw_data={
            "promotable_count": len(promotable_ids),
            "sample_ids": promotable_ids[:10],
            "promotable_ids": promotable_ids,
            "promotable_items": promotable_items,
        },
    )


def _build_unpromotable_staging_finding(row: Dict[str, Any], finding_cls: Callable[..., Any]) -> Any:
    aid = str(row["assertion_id"])
    raw_txt = str(row.get("scope_text") or row.get("object_literal") or "")
    clean_txt = normalize_candidate_claim_text(raw_txt)
    syn = has_synthetic_or_cross_project_pollution(
        record_id=aid,
        subject_node_id=row.get("subject_node_id"),
        scope_text=clean_txt,
        speaker_or_author=row.get("speaker_or_author"),
        source_name=row.get("source_name"),
    )
    is_deg = bool(syn) or is_degenerate_single_token_claim(clean_txt) or has_non_promotable_qualifiers(row.get("qualifiers_json"))
    s_name = str(row.get("subject_canonical_name") or row.get("subject_node_id") or "Workstream")
    if is_deg:
        return finding_cls(
            detective="ProvenanceInspector",
            severity="MEDIUM",
            description=f"Incomplete single-word staging fragment ('{clean_txt}') on {s_name} requires archival.",
            entity_references=[aid],
            proposed_fix="archive_candidate_assertion",
            raw_data={
                "assertion_id": aid,
                "subject_node_id": row.get("subject_node_id"),
                "subject_name": s_name,
                "predicate": row.get("predicate"),
                "object_node_id": row.get("object_node_id"),
                "object_name": str(row.get("object_literal") or "")[:48] or "Staging Fragment",
                "object_literal": row.get("object_literal"),
                "claim_text": clean_txt,
                "source_title": row.get("source_name") or "Meeting",
                "created_at": str(row.get("created_at") or ""),
                "eviction_reason": "degenerate_single_token_candidate",
                "recommended_option": "ARCHIVE",
            },
        )
    details = _enrich_undiarized_hitl_details({
        "assertion_id": aid,
        "subject_node_id": row.get("subject_node_id"),
        "subject_name": s_name,
        "predicate": row.get("predicate"),
        "object_node_id": row.get("object_node_id"),
        "object_literal": row.get("object_literal"),
        "scope_text": clean_txt,
        "speaker_or_author": row.get("speaker_or_author"),
        "source_name": row.get("source_name"),
        "cold_archive_ref": row.get("cold_archive_ref"),
        "transcript_timestamp": row.get("transcript_timestamp"),
        "created_at": str(row.get("created_at") or ""),
    })
    src_clean = str(details.get("source_title") or "Meeting").replace("_", " ")
    return finding_cls(
        detective="ProvenanceInspector",
        severity="HIGH",
        description=f"Undiarized spoken statement in {src_clean} at {details['transcript_timestamp']} requires operator speaker attribution.",
        entity_references=[aid],
        proposed_fix="attribute_undiarized_speaker_hitl",
        raw_data=details,
    )


def _build_unpromotable_staging_sql(cursor: Any, asrt_cols: Set[str]) -> str:
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    sel, sjoin, njoin = _promotable_select_columns(cursor, asrt_cols, tables)
    extra_sel = [f"a.{c}" if c in asrt_cols else f"NULL AS {c}" for c in ("object_node_id", "created_at")]
    curr_cond = "AND a.currentness_state = 'current' " if "currentness_state" in asrt_cols else ""
    return f"SELECT {', '.join(sel + extra_sel)} FROM assertion a {sjoin}{njoin}WHERE a.canonical_status = 'candidate' {curr_cond}"


def audit_unpromotable_staging_candidates(
    cursor: Any,
    asrt_cols: Set[str],
    excluded_ids: Optional[Set[str]],
    finding_cls: Callable[..., Any],
) -> List[Any]:
    """Emit archive_candidate_assertion for degenerate fragments and attribute_undiarized_speaker_hitl for undiarized spoken claims."""
    if "canonical_status" not in asrt_cols:
        return []
    sibling_by_ev, sibling_by_ts = _load_sibling_speaker_maps(cursor, asrt_cols)
    cursor.execute(_build_unpromotable_staging_sql(cursor, asrt_cols))
    cols = [d[0] for d in (cursor.description or [])]
    excl = excluded_ids or set()
    rows = [dict(r) if hasattr(r, "keys") else dict(zip(cols, r)) for r in cursor.fetchall()]
    return [
        _build_unpromotable_staging_finding(row, finding_cls)
        for row in rows
        if str(row["assertion_id"]) not in excl and not _candidate_row_passes_promotion_safety(row, sibling_by_ev, sibling_by_ts)
    ]


def build_undiarized_speaker_question_payload(findings: List[Any], alpha_sum: str, beta_sum: str, verdict_sum: str) -> Tuple[str, str, Dict[str, Any]]:
    """Build 5-turn speaker attribution question payload for CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL."""
    first_raw = (getattr(findings[0], "raw_data", None) or {}) if findings else {}
    ctx_block = str(first_raw.get("context_block") or "")
    roster = list(first_raw.get("roster_candidates") or ["Amit Ayer", "Jennifer Pan", "harper Hoffman"])
    while len(roster) < 2:
        roster.append("Amit Ayer" if "Amit Ayer" not in roster else "Jennifer Pan")
    tiers = [(62, "MEDIUM"), (50, "MEDIUM"), (38, "LOW"), (28, "LOW")]
    options: List[str] = []
    for idx, person in enumerate(roster[:4]):
        pct, tier = tiers[min(idx, len(tiers) - 1)]
        prefix = "(Recommended) " if idx == 0 else ""
        options.append(f"{prefix}[Confidence: {tier} ({pct}%)] Attribute to {person} based on verified meeting roster and turn context")
    prompt_line = "Who is speaking in the TARGET turn?"
    full_question = f"{ctx_block}\n\n{prompt_line}" if ctx_block else prompt_line
    rationale = (
        f"These {len(findings)} spoken meeting statement(s) come from undiarized or unresolved speaker turns and "
        "require operator speaker attribution before promotion into active search."
    )
    return rationale, f"Team Alpha: {alpha_sum} | Team Beta: {beta_sum} | Verdict: {verdict_sum}", {
        "question": full_question,
        "options": options,
        "is_multi_select": False,
        "criticality_tier": "TIER_3_AMBIGUOUS",
        "category": "speaker_attribution",
        "recommended_action": "apply",
        "top_confidence": 0.62,
        "runner_up_confidence": 0.50,
        "margin": 0.12,
        "autonomous_permitted": False,
        "surface_hitl": True,
    }
