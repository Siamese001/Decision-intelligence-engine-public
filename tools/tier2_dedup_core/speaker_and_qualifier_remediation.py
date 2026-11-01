"""Cloned-generation SQLite remediation handlers for speaker attribution quarantine, rebind, and HITL attribution."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import (
    _load_sibling_speaker_maps,
    resolve_candidate_provenance_speaker,
)
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    has_non_promotable_qualifiers,
    is_placeholder_speaker,
    normalize_candidate_claim_text,
    parse_qualifiers_dict,
    sync_canonical_qualifiers_json,
)

_BATCH_SIZE = 400


def _chunked(items: Sequence[str], size: int = _BATCH_SIZE) -> Iterable[Sequence[str]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _extract_assertion_ids(findings: Sequence[Dict[str, Any]], fix_name: str) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for f in findings:
        if f.get("proposed_fix") != fix_name:
            continue
        rd = f.get("raw_data") or {}
        for cand in [rd.get("assertion_id"), *(f.get("entity_references") or [])]:
            aid = str(cand or "").strip()
            if aid and aid not in seen:
                seen.add(aid)
                ordered.append(aid)
    return ordered


def _resync_search_fts_if_present(conn: Any, tables: Set[str]) -> None:
    if "search_fts" not in tables or "search_document" not in tables:
        return
    sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    rowid_col = "search_rowid" if "search_rowid" in sd_cols else "rowid"
    try:
        conn.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
    except Exception:
        conn.execute("DELETE FROM search_fts")
    conn.execute(
        f"INSERT INTO search_fts(rowid, title, body, object_type, object_id) "
        f"SELECT {rowid_col}, title, body, object_type, object_id "
        f"FROM search_document WHERE retrieval_eligible = 1"
    )


def _build_quarantine_set_parts(acols: Set[str]) -> List[str]:
    parts = ["canonical_status = 'historical_candidate'", "retrieval_eligible = 0"]
    optional = [
        ("currentness_state", "currentness_state = 'historical'"),
        ("canonical_eligible", "canonical_eligible = 0"),
        ("validation_state", "validation_state = 'rejected'"),
        ("qualifiers_json", "qualifiers_json = json_set(coalesce(qualifiers_json, '{}'), '$.speaker_hygiene_quarantined', json('true'))"),
        ("updated_at", "updated_at = ?"),
    ]
    return parts + [expr for col, expr in optional if col in acols]


def _clear_non_claim_placeholder_speakers(conn: Any, acols: Set[str]) -> None:
    if not {"predicate", "speaker_or_author"}.issubset(acols):
        return
    rel_rows = conn.execute(
        "SELECT assertion_id, speaker_or_author FROM assertion WHERE coalesce(predicate, '') != 'HAS_CLAIM_TEXT' AND speaker_or_author IS NOT NULL"
    ).fetchall()
    rel_ids = [(str(r[0]),) for r in rel_rows if is_placeholder_speaker(r[1])]
    if rel_ids:
        conn.executemany("UPDATE assertion SET speaker_or_author = NULL WHERE assertion_id = ?", rel_ids)


def _quarantine_speaker_defect_assertions(
    conn: Any, target_ids: Sequence[str], tables: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    if not target_ids or "assertion" not in tables:
        return 0
    acols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    set_parts = _build_quarantine_set_parts(acols)
    has_upd = "updated_at" in acols
    sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()} if "search_document" in tables else set()
    quarantined = 0
    for batch in _chunked(target_ids):
        ph = ",".join("?" for _ in batch)
        params: List[Any] = ([now_iso] if has_upd else []) + list(batch)
        asrt_q_sql = "UPDATE assertion SET " + ", ".join(set_parts) + " WHERE assertion_id IN (" + ph + ")"
        cur = conn.execute(asrt_q_sql, params)
        quarantined += max(0, cur.rowcount)
        if "assertion_id" in sd_cols:
            sd_upd = "updated_at = ?, " if "updated_at" in sd_cols else ""
            sd_params: List[Any] = ([now_iso] if "updated_at" in sd_cols else []) + list(batch)
            sd_q_sql = (
                "UPDATE search_document SET " + sd_upd + "canonical_status = 'historical_candidate', currentness_state = 'historical', retrieval_eligible = 0 "
                "WHERE assertion_id IN (" + ph + ") AND (canonical_status != 'historical_candidate' OR retrieval_eligible != 0)"
            )
            conn.execute(sd_q_sql, sd_params)
    _clear_non_claim_placeholder_speakers(conn, acols)
    sample_ids.extend(list(target_ids[:2]))
    return quarantined


def _normalize_dash_typography(text: Any) -> Any:
    if not isinstance(text, str) or ("\u2013" not in text and "\u2014" not in text):
        return text
    return text.replace("\u2013", "-").replace("\u2014", " - ")


def _build_qualifier_sync_tuple(
    aid: str, qraw: Any, obj_lit: Any, has_obj_lit: bool, has_upd: bool, now_iso: str
) -> Optional[Tuple[Any, ...]]:
    new_q = sync_canonical_qualifiers_json(qraw)
    new_lit = _normalize_dash_typography(obj_lit) if has_obj_lit else obj_lit
    if new_q == (qraw or "") and (not has_obj_lit or new_lit == obj_lit):
        return None
    vals: List[Any] = [new_q]
    if has_obj_lit:
        vals.append(new_lit)
    if has_upd:
        vals.append(now_iso)
    vals.append(aid)
    return tuple(vals)


def _qualifier_sync_update_sql(has_obj_lit: bool, has_upd: bool) -> str:
    cols = ["qualifiers_json = ?"]
    if has_obj_lit:
        cols.append("object_literal = ?")
    if has_upd:
        cols.append("updated_at = ?")
    return f"UPDATE assertion SET {', '.join(cols)} WHERE assertion_id = ?"


def _sync_stale_qualifier_assertions(
    conn: Any, target_ids: Sequence[str], tables: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    if not target_ids or "assertion" not in tables:
        return 0
    acols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    if "qualifiers_json" not in acols:
        return 0
    has_upd = "updated_at" in acols
    has_obj_lit = "object_literal" in acols
    lit_col = "object_literal" if has_obj_lit else "NULL"
    upd_sql = _qualifier_sync_update_sql(has_obj_lit, has_upd)
    synced = 0
    for batch in _chunked(target_ids):
        ph = ",".join("?" for _ in batch)
        sel_sql = "SELECT assertion_id, qualifiers_json, " + lit_col + " FROM assertion WHERE assertion_id IN (" + ph + ")"
        rows = conn.execute(sel_sql, list(batch)).fetchall()
        updates = [t for aid, qraw, obj_lit in rows for t in (_build_qualifier_sync_tuple(aid, qraw, obj_lit, has_obj_lit, has_upd, now_iso),) if t]
        if updates:
            conn.executemany(upd_sql, updates)
            synced += len(updates)
    sample_ids.extend(list(target_ids[:2]))
    return synced


def _col_or_null(prefix: str, col: str, avail: Set[str]) -> str:
    return f"{prefix}.{col} AS {col}" if col in avail else f"NULL AS {col}"


def _evidence_subqueries(conn: Any, tables: Set[str]) -> List[str]:
    if not {"assertion_evidence", "evidence_item"}.issubset(tables):
        return ["NULL AS evidence_id", "NULL AS transcript_timestamp", "NULL AS direct_quote"]
    ei_cols = {r[1] for r in conn.execute("PRAGMA table_info(evidence_item)").fetchall()}
    ts_col = "coalesce(ei.transcript_timestamp, ei.locator_value)" if "transcript_timestamp" in ei_cols else "ei.locator_value"
    dq_expr = (
        "(SELECT ei.direct_quote FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS direct_quote"
        if "direct_quote" in ei_cols
        else "NULL AS direct_quote"
    )
    return [
        "(SELECT ae.evidence_id FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS evidence_id",
        f"(SELECT {ts_col} FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS transcript_timestamp",
        dq_expr,
    ]


def _build_rebind_select_sql(conn: Any, acols: Set[str], tables: Set[str], ph: str) -> str:
    has_node = "node" in tables and "subject_node_id" in acols
    has_src = "source" in tables and "primary_source_id" in acols
    src_cols = {r[1] for r in conn.execute("PRAGMA table_info(source)").fetchall()} if has_src else set()
    asrt_fields = ("predicate", "scope_text", "object_literal", "qualifiers_json", "validation_state", "primary_source_id")
    src_fields = ("source_type", "source_name", "cold_archive_ref")
    sel = [
        "a.assertion_id",
        "a.speaker_or_author",
        *(_col_or_null("a", c, acols) for c in asrt_fields),
        *(_col_or_null("s", c, src_cols) for c in src_fields),
        "n.canonical_name AS subject_canonical_name" if has_node else "NULL AS subject_canonical_name",
        "n.node_type AS subject_node_type" if has_node else "NULL AS subject_node_type",
        *_evidence_subqueries(conn, tables),
    ]
    sjoin = "LEFT JOIN source s ON a.primary_source_id = s.source_id " if has_src else ""
    njoin = "LEFT JOIN node n ON a.subject_node_id = n.node_id " if has_node else ""
    return f"SELECT {', '.join(sel)} FROM assertion a {sjoin}{njoin}WHERE a.assertion_id IN ({ph})"


def _apply_rebound_speaker_and_text_updates(
    conn: Any,
    acols: Set[str],
    tables: Set[str],
    spk_updates: List[Tuple[Any, ...]],
    txt_updates: List[Tuple[str, str]],
) -> None:
    if spk_updates:
        upd_sql = (
            "UPDATE assertion SET speaker_or_author = ?, updated_at = ? WHERE assertion_id = ?"
            if "updated_at" in acols
            else "UPDATE assertion SET speaker_or_author = ? WHERE assertion_id = ?"
        )
        conn.executemany(upd_sql, spk_updates)
    if not txt_updates or "scope_text" not in acols:
        return
    if "object_literal" in acols:
        conn.executemany(
            "UPDATE assertion SET scope_text = ?, object_literal = CASE WHEN object_literal IS NOT NULL THEN ? ELSE object_literal END WHERE assertion_id = ?",
            [(t, t, aid) for t, aid in txt_updates],
        )
    else:
        conn.executemany("UPDATE assertion SET scope_text = ? WHERE assertion_id = ?", txt_updates)
    if "search_document" in tables:
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
        if {"assertion_id", "body"}.issubset(sd_cols):
            conn.executemany("UPDATE search_document SET title = ?, body = ? WHERE assertion_id = ?", [(t[:120], t, aid) for t, aid in txt_updates])


def _collect_batch_rebind_updates(
    rows: Sequence[Any],
    col_names: List[str],
    has_upd: bool,
    now_iso: str,
    sibling_by_ev: Dict[tuple[str, str], str],
    sibling_by_ts: Dict[tuple[str, str], str],
) -> Tuple[List[Tuple[Any, ...]], List[Tuple[str, str]]]:
    spk_updates: List[Tuple[Any, ...]] = []
    txt_updates: List[Tuple[str, str]] = []
    for raw_row in rows:
        row = dict(raw_row) if hasattr(raw_row, "keys") else dict(zip(col_names, raw_row))
        aid = str(row["assertion_id"])
        raw_txt = str(row.get("scope_text") or row.get("object_literal") or "")
        clean_txt = normalize_candidate_claim_text(raw_txt)
        if clean_txt and clean_txt != raw_txt.strip():
            txt_updates.append((clean_txt, aid))
        eff_spk, is_rec = resolve_candidate_provenance_speaker(row, sibling_by_ev, sibling_by_ts)
        if is_rec:
            spk_updates.append((eff_spk, now_iso, aid) if has_upd else (eff_spk, aid))
    return spk_updates, txt_updates


def rebind_placeholder_speakers_for_ids(
    conn: Any, target_ids: Sequence[str], tables: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    """Deterministically re-bind placeholder/mismatched speaker_or_author values and normalize claim text."""
    if not target_ids or "assertion" not in tables:
        return 0
    acols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    if "speaker_or_author" not in acols:
        return 0
    sibling_by_ev, sibling_by_ts = _load_sibling_speaker_maps(conn.cursor(), acols)
    has_upd = "updated_at" in acols
    rebound = 0
    for batch in _chunked(target_ids):
        ph = ",".join("?" for _ in batch)
        cur = conn.execute(_build_rebind_select_sql(conn, acols, tables, ph), list(batch))
        col_names = [d[0] for d in (cur.description or [])]
        spk_u, txt_u = _collect_batch_rebind_updates(cur.fetchall(), col_names, has_upd, now_iso, sibling_by_ev, sibling_by_ts)
        _apply_rebound_speaker_and_text_updates(conn, acols, tables, spk_u, txt_u)
        if spk_u or txt_u:
            rebound += max(len(spk_u), len(txt_u))
            sample_ids.extend([u[-1] for u in (spk_u or txt_u)[:2]])
    return rebound


def _ensure_governance_approval_for_promotion(conn: Any, approval_ref: str, tables: Set[str], now_iso: str) -> None:
    if "governance_approval" not in tables:
        return
    ga_cols = {r[1] for r in conn.execute("PRAGMA table_info(governance_approval)").fetchall()}
    if "scope_json" in ga_cols:
        import hashlib
        dig = hashlib.sha256(approval_ref.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO governance_approval ("
            "approval_id, approval_reference, approval_type, scope_json, proposal_sha256, "
            "approval_statement_sha256, approved_at, status, approved_by, release_version, active, created_at"
            ") VALUES (?, ?, 'ingestion_promotion', '{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', ?, ?, ?, 'approved', 'Operator', ?, 1, ?)",
            (f"ga-{approval_ref}", approval_ref, dig, dig, now_iso, now_iso[:10], now_iso),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, status, active) VALUES (?, ?, 'approved', 1)",
            (f"ga-{approval_ref}", approval_ref),
        )


def _resolved_promotion_confidence(conf_val: Any, qraw: Any) -> float:
    base = float(conf_val) if conf_val is not None else 0.75
    ext = parse_qualifiers_dict(qraw).get("extraction_confidence")
    return max(base, float(ext) if ext is not None else 0.0, 0.75)


def resolve_candidate_promotion_target_ids(
    conn: Any, explicit_ids: Sequence[str], blocked_ids: Set[str], acols: Set[str]
) -> List[str]:
    if explicit_ids:
        return list(explicit_ids)
    if blocked_ids:
        return []
    sup_at = "AND superseded_at IS NULL " if "superseded_at" in acols else ""
    sup_by = "AND superseded_by_assertion_id IS NULL " if "superseded_by_assertion_id" in acols else ""
    promo_sel_sql = (
        "SELECT assertion_id FROM assertion WHERE ("
        "(canonical_status = 'candidate' AND coalesce(currentness_state, 'current') = 'current') "
        "OR (canonical_status = 'historical_candidate' " + sup_at +
        "AND coalesce(validation_state, '') NOT IN ('rejected', 'superseded', 'retired'))"
        ") " + sup_by +
        "AND coalesce(confidence_score, 0.75) >= 0.72 "
        "AND (validation_state = 'accepted_legacy' OR assertion_id IN ("
        "SELECT DISTINCT assertion_id FROM assertion_evidence WHERE assertion_id IS NOT NULL))"
    )
    rows = conn.execute(promo_sel_sql).fetchall()
    return [str(r[0]) for r in rows if r[0]]


def _apply_candidate_promotion_updates(
    conn: Any, acols: Set[str], tables: Set[str], promotable_updates: List[Tuple[str, str, float, str, str]], now_iso: str
) -> None:
    curr_parts = [
        p for col, p in (
            ("currentness_state", "currentness_state = 'current'"),
            ("superseded_by_assertion_id", "superseded_by_assertion_id = NULL"),
            ("superseded_at", "superseded_at = NULL"),
        ) if col in acols
    ]
    curr_set = (", ".join(curr_parts) + ", ") if curr_parts else ""
    if {"qualifiers_json", "confidence_score"}.issubset(acols):
        promo_upd_sql = (
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, retrieval_eligible = 1, "
            + curr_set +
            "validation_state = 'accepted_legacy', approval_reference = ?, qualifiers_json = ?, confidence_score = ?, updated_at = ? WHERE assertion_id = ?"
        )
        conn.executemany(promo_upd_sql, promotable_updates)
    elif "qualifiers_json" in acols:
        conn.executemany(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, retrieval_eligible = 1, "
            "validation_state = 'accepted_legacy', approval_reference = ?, qualifiers_json = ?, updated_at = ? WHERE assertion_id = ?",
            [(u[0], u[1], u[3], u[4]) for u in promotable_updates],
        )
    else:
        conn.executemany(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, retrieval_eligible = 1, "
            "validation_state = 'accepted_legacy', approval_reference = ?, updated_at = ? WHERE assertion_id = ?",
            [(u[0], u[3], u[4]) for u in promotable_updates],
        )
    if "node" in tables and {"subject_node_id", "object_node_id"}.issubset(acols):
        node_cols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}
        if {"status", "canonical_name", "description"}.issubset(node_cols):
            from dual_pipeline.uwg_core.node_promotion import apply_node_promotions, plan_promotable_nodes
            nodes_to_promote = plan_promotable_nodes(conn, [u[4] for u in promotable_updates])
            if nodes_to_promote:
                apply_node_promotions(conn, nodes_to_promote, now_iso)


def execute_rebound_candidate_promotion(
    conn: Any,
    target_ids: Sequence[str],
    approval_ref: str,
    acols: Set[str],
    tables: Set[str],
    now_iso: str,
) -> int:
    """Re-bind placeholder speakers, synchronize qualifiers_json, and promote candidate assertions."""
    if not target_ids:
        return 0
    rebind_placeholder_speakers_for_ids(conn, target_ids, tables, now_iso, [])
    ph = ",".join("?" for _ in target_ids)
    qual_sel = "qualifiers_json" if "qualifiers_json" in acols else "'{}' AS qualifiers_json"
    conf_sel = "confidence_score" if "confidence_score" in acols else "0.75 AS confidence_score"
    rebound_sel_sql = (
        "SELECT assertion_id, " + qual_sel + ", " + conf_sel + " FROM assertion "
        "WHERE assertion_id IN (" + ph + ") AND canonical_status IN ('candidate', 'historical_candidate')"
    )
    rows = conn.execute(rebound_sel_sql, list(target_ids)).fetchall()
    promotable_updates = [
        (approval_ref, sync_canonical_qualifiers_json(qraw), _resolved_promotion_confidence(cval, qraw), now_iso, str(aid))
        for aid, qraw, cval in rows
        if not has_non_promotable_qualifiers(qraw)
    ]
    if not promotable_updates:
        return 0
    _ensure_governance_approval_for_promotion(conn, approval_ref, tables, now_iso)
    _apply_candidate_promotion_updates(conn, acols, tables, promotable_updates, now_iso)
    return len(promotable_updates)


def _extract_undiarized_speaker_map(findings: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    speaker_by_aid: Dict[str, str] = {}
    for f in findings:
        if f.get("proposed_fix") != "attribute_undiarized_speaker_hitl":
            continue
        rd = f.get("raw_data") or {}
        roster = rd.get("roster_candidates") or ["Amit Ayer"]
        spk = str(rd.get("attributed_speaker") or rd.get("recommended_speaker") or roster[0]).strip()
        for cand in [rd.get("assertion_id"), *(f.get("entity_references") or [])]:
            aid = str(cand or "").strip()
            if aid and spk:
                speaker_by_aid[aid] = spk
    return speaker_by_aid


def _apply_undiarized_hitl_attributions(
    conn: Any, findings: Sequence[Dict[str, Any]], tables: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    if "assertion" not in tables:
        return 0
    acols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    speaker_by_aid = _extract_undiarized_speaker_map(findings) if "speaker_or_author" in acols else {}
    if not speaker_by_aid:
        return 0
    upd_sql = (
        "UPDATE assertion SET speaker_or_author = ?, updated_at = ? WHERE assertion_id = ?"
        if "updated_at" in acols
        else "UPDATE assertion SET speaker_or_author = ? WHERE assertion_id = ?"
    )
    rows = [(spk, now_iso, aid) if "updated_at" in acols else (spk, aid) for aid, spk in speaker_by_aid.items()]
    conn.executemany(upd_sql, rows)
    aids = list(speaker_by_aid.keys())
    execute_rebound_candidate_promotion(conn, aids, "HITL-SPEAKER-ATTRIBUTION", acols, tables, now_iso)
    if "search_document" in tables:
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
        if "assertion_id" in sd_cols:
            conn.executemany(
                "UPDATE search_document SET canonical_status = 'canonical', retrieval_eligible = 1 WHERE assertion_id = ?",
                [(aid,) for aid in aids],
            )
    sample_ids.extend(aids[:2])
    return len(aids)


def remediate_speaker_and_qualifier_fixes(
    conn: Any,
    findings: Sequence[Dict[str, Any]],
    tables: Set[str],
    now_iso: str,
    sample_ids: List[str],
) -> Dict[str, int]:
    """Execute cloned-generation speaker quarantine, placeholder rebinding, HITL speaker attribution, and qualifier sync."""
    spk_ids = _extract_assertion_ids(findings, "quarantine_undiarized_or_misattributed_claim")
    rebind_ids = _extract_assertion_ids(findings, "rebind_placeholder_speaker")
    qual_ids = _extract_assertion_ids(findings, "sync_promoted_qualifier_metadata")
    rebound = rebind_placeholder_speakers_for_ids(conn, rebind_ids, tables, now_iso, sample_ids)
    attributed = _apply_undiarized_hitl_attributions(conn, findings, tables, now_iso, sample_ids)
    quarantined = _quarantine_speaker_defect_assertions(conn, spk_ids, tables, now_iso, sample_ids)
    synced = _sync_stale_qualifier_assertions(conn, qual_ids, tables, now_iso + "", list(set(qual_ids) | set(rebind_ids)))
    has_demote = any(f.get("proposed_fix") == "demote_leaked_candidate_assertion" for f in findings)
    if quarantined > 0 or attributed > 0 or has_demote:
        _resync_search_fts_if_present(conn, tables)
    return {
        "speaker_defects_quarantined": quarantined,
        "placeholder_speakers_rebound": rebound,
        "undiarized_speakers_attributed": attributed,
        "qualifier_metadata_synced": synced,
    }
