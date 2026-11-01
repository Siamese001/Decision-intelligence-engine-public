"""Helper utilities for governed graph writes."""
from __future__ import annotations

from typing import Any

from storage.database import sqlite3

from ..core import ZERO_HASH, digest


def _row_digest(connection: sqlite3.Connection, table: str, key: str, value: str) -> str:
    """Computes deterministic digest for a row identified by primary key."""
    row = connection.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?', (value,)).fetchone()
    if row is None:
        return ZERO_HASH
    return digest(dict(row))


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """Checks whether a table exists in the SQLite database."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _chunked_fetch_set(
    connection: sqlite3.Connection,
    query_template: str,
    values: list[str],
    chunk_size: int = 500,
) -> set[str]:
    """Fetches a set of scalar values using chunked IN-clause queries."""
    result: set[str] = set()
    for i in range(0, len(values), chunk_size):
        chunk = values[i : i + chunk_size]
        q = query_template.format(",".join("?" * len(chunk)))
        for r in connection.execute(q, tuple(chunk)):
            result.add(r[0])
    return result


def _chunked_fetch_rows(
    connection: sqlite3.Connection,
    query_template: str,
    values: list[str],
    chunk_size: int = 500,
) -> list[Any]:
    """Fetches rows using chunked IN-clause queries."""
    result: list[Any] = []
    for i in range(0, len(values), chunk_size):
        chunk = values[i : i + chunk_size]
        q = query_template.format(",".join("?" * len(chunk)))
        result.extend(connection.execute(q, tuple(chunk)).fetchall())
    return result


def _find_existing_assertion_for_replacement(
    connection: sqlite3.Connection,
    fingerprint: str,
    payload: dict[str, Any],
    operation_source: dict[str, Any],
) -> Any:
    """Locate an existing assertion matching canonical/legacy fingerprint or exact same-source tuple."""
    cand = payload.get("candidate", {})
    pred = payload.get("predicate", "HAS_CLAIM_TEXT")
    obj_node = None if pred == "HAS_CLAIM_TEXT" else payload.get("object_node_id")
    obj_lit = cand.get("normalized_text") if (pred == "HAS_CLAIM_TEXT" or not obj_node) else None
    legacy_fp = digest(
        {
            "subject": payload["subject_node_id"],
            "predicate": pred,
            "object_node_id": obj_node,
            "object_literal": obj_lit,
            "scope": cand.get("normalized_text"),
            "source_version_id": operation_source.get("source_version_id"),
        }
    )
    row = connection.execute(
        "SELECT assertion_id, confidence_score, currentness_state, superseded_at, superseded_by_assertion_id FROM assertion WHERE assertion_fingerprint IN (?, ?)",
        (fingerprint, legacy_fp),
    ).fetchone()
    if row is not None:
        return row
    return connection.execute(
        """SELECT assertion_id, confidence_score, currentness_state, superseded_at, superseded_by_assertion_id
           FROM assertion
           WHERE primary_source_id=? AND subject_node_id=? AND predicate=?
             AND coalesce(object_node_id, '')=? AND coalesce(object_literal, '')=? AND scope_text=?
           LIMIT 1""",
        (
            operation_source["source_id"],
            payload["subject_node_id"],
            pred,
            obj_node or "",
            obj_lit or "",
            cand.get("normalized_text") or "",
        ),
    ).fetchone()


_DETACHED_VQ_ROWS: dict[tuple[int, str], tuple[list[str], list[tuple[Any, ...]]]] = {}


def _resolve_validation_queue_for_assertion(
    connection: sqlite3.Connection, assertion_id: str, now: str, vq_cols: set[str]
) -> None:
    id_cols = [c for c in ("target_id", "assertion_id", "claim_id") if c in vq_cols]
    if not id_cols:
        return
    where_id = " OR ".join(f"{c}=?" for c in id_cols)
    id_params = tuple(assertion_id for _ in id_cols)
    del_sql = "DELETE FROM validation_queue WHERE " + where_id
    if "status" not in vq_cols:
        connection.execute(del_sql, id_params)
        return
    connection.execute("SAVEPOINT sp_vq_resolve")
    try:
        set_clauses = ["status='resolved'"] + [f"{c}=?" for c in ("resolved_at", "updated_at") if c in vq_cols]
        set_params = [now for c in ("resolved_at", "updated_at") if c in vq_cols]
        upd_sql = (
            "UPDATE validation_queue SET "
            + ", ".join(set_clauses)
            + " WHERE ("
            + where_id
            + ") AND LOWER(COALESCE(status, 'open')) IN ('open', 'pending')"
        )
        connection.execute(upd_sql, (*set_params, *id_params))
        connection.execute("RELEASE SAVEPOINT sp_vq_resolve")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT sp_vq_resolve")
        connection.execute("RELEASE SAVEPOINT sp_vq_resolve")
        connection.execute(del_sql, id_params)


def _evict_fts_and_resolve_validation_queue(
    connection: sqlite3.Connection,
    assertion_id: str,
    now: str,
) -> None:
    """Evicts superseded/retired assertion from search_fts and resolves open validation_queue items."""
    if _table_exists(connection, "search_fts"):
        has_au_trigger = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='search_document_au'"
        ).fetchone()
        if has_au_trigger is None:
            connection.execute("DELETE FROM search_fts WHERE object_id=?", (assertion_id,))
    if _table_exists(connection, "validation_queue"):
        vq_cols = {r[1] for r in connection.execute("PRAGMA table_info(validation_queue)").fetchall()}
        _resolve_validation_queue_for_assertion(connection, assertion_id, now, vq_cols)


def _detach_validation_queue_rows(connection: sqlite3.Connection, old_assertion_id: str) -> None:
    if not _table_exists(connection, "validation_queue"):
        return
    vq_col_list = [r[1] for r in connection.execute("PRAGMA table_info(validation_queue)").fetchall()]
    id_cols = [c for c in ("target_id", "assertion_id", "claim_id") if c in vq_col_list]
    if not id_cols:
        return
    where_id = " OR ".join(f"{c}=?" for c in id_cols)
    id_params = tuple(old_assertion_id for _ in id_cols)
    sel_sql = "SELECT " + ", ".join(vq_col_list) + " FROM validation_queue WHERE " + where_id
    vq_rows = connection.execute(sel_sql, id_params).fetchall()
    if vq_rows:
        _DETACHED_VQ_ROWS[(id(connection), old_assertion_id)] = (vq_col_list, [tuple(r) for r in vq_rows])
        del_sql = "DELETE FROM validation_queue WHERE " + where_id
        connection.execute(del_sql, id_params)


def _rebind_detached_validation_queue_rows(
    connection: sqlite3.Connection, old_assertion_id: str, new_assertion_id: str
) -> None:
    if not _table_exists(connection, "validation_queue"):
        return
    detached = _DETACHED_VQ_ROWS.pop((id(connection), old_assertion_id), None)
    if detached is not None:
        vq_col_list, vq_rows = detached
        ph = ",".join("?" for _ in vq_col_list)
        ins_sql = "INSERT OR IGNORE INTO validation_queue (" + ", ".join(vq_col_list) + ") VALUES (" + ph + ")"
        for row_tuple in vq_rows:
            rebound_vals = [
                new_assertion_id if (col_name in ("target_id", "assertion_id", "claim_id") and val == old_assertion_id) else val
                for col_name, val in zip(vq_col_list, row_tuple)
            ]
            connection.execute(ins_sql, tuple(rebound_vals))
    if old_assertion_id != new_assertion_id:
        vq_cols = {r[1] for r in connection.execute("PRAGMA table_info(validation_queue)").fetchall()}
        for col in [c for c in ("target_id", "assertion_id", "claim_id") if c in vq_cols]:
            upd_sql = "UPDATE validation_queue SET " + col + "=? WHERE " + col + "=?"
            connection.execute(upd_sql, (new_assertion_id, old_assertion_id))


def _prepare_fingerprint_replacement(
    connection: sqlite3.Connection,
    old_assertion_id: str,
) -> tuple[list[Any], list[str]]:
    """Detaches and deletes a prior assertion matching the same fingerprint while preserving its evidence and supersession lineage."""
    prior_ev = connection.execute("SELECT * FROM assertion_evidence WHERE assertion_id=?", (old_assertion_id,)).fetchall()
    sup_rows = connection.execute("SELECT assertion_id FROM assertion WHERE superseded_by_assertion_id=?", (old_assertion_id,)).fetchall()
    superseded_ids = [r[0] for r in sup_rows]
    _detach_validation_queue_rows(connection, old_assertion_id)
    connection.execute(
        "UPDATE assertion SET canonical_status='candidate', canonical_eligible=0, retrieval_eligible=0 WHERE assertion_id=?",
        (old_assertion_id,),
    )
    if _table_exists(connection, "assertion_conflict"):
        connection.execute("DELETE FROM assertion_conflict WHERE assertion_id_1=? OR assertion_id_2=?", (old_assertion_id, old_assertion_id))
    connection.execute("UPDATE assertion SET superseded_by_assertion_id=NULL WHERE superseded_by_assertion_id=?", (old_assertion_id,))
    connection.execute("DELETE FROM assertion_evidence WHERE assertion_id=?", (old_assertion_id,))
    for child_table, del_sql in (
        ("assertion_label_state", "DELETE FROM assertion_label_state WHERE assertion_id=?"),
        ("assertion_audience", "DELETE FROM assertion_audience WHERE assertion_id=?"),
        ("legacy_claim_assertion_map", "DELETE FROM legacy_claim_assertion_map WHERE assertion_id=?"),
        ("legacy_edge_assertion_map", "DELETE FROM legacy_edge_assertion_map WHERE assertion_id=?"),
    ):
        if _table_exists(connection, child_table):
            connection.execute(del_sql, (old_assertion_id,))
    if _table_exists(connection, "object_facet_assignment"):
        connection.execute("DELETE FROM object_facet_assignment WHERE object_type='assertion' AND object_id=?", (old_assertion_id,))
    if _table_exists(connection, "search_fts"):
        connection.execute("DELETE FROM search_fts WHERE object_id=?", (old_assertion_id,))
    connection.execute("DELETE FROM search_document WHERE assertion_id=?", (old_assertion_id,))
    connection.execute("DELETE FROM assertion WHERE assertion_id=?", (old_assertion_id,))
    return prior_ev, superseded_ids


def _restore_fingerprint_replacement(
    connection: sqlite3.Connection,
    new_assertion_id: str,
    prior_ev: list[Any],
    superseded_ids: list[str],
    evidence_ids: list[str],
    existing_evidence: set[str],
    now: str,
    old_assertion_id: str | None = None,
) -> None:
    """Restores prior evidence, supersession references, and validation queue links onto the replacement assertion."""
    if old_assertion_id:
        _rebind_detached_validation_queue_rows(connection, old_assertion_id, new_assertion_id)
    for sup_id in superseded_ids:
        if sup_id != new_assertion_id:
            connection.execute(
                "UPDATE assertion SET superseded_by_assertion_id=? WHERE assertion_id=?",
                (new_assertion_id, sup_id),
            )
    for row in prior_ev:
        ev_id = row[1]
        if ev_id not in evidence_ids:
            ev_rel = row[2]
            ev_indep = int(row[3] or 0)
            ev_created = row[4] if len(row) > 4 and row[4] else now
            connection.execute(
                "INSERT OR IGNORE INTO assertion_evidence VALUES (?,?,?,?,?)",
                (new_assertion_id, ev_id, ev_rel, ev_indep, ev_created),
            )
            existing_evidence.add(ev_id)


def _retire_omitted_source_assertions(
    connection: sqlite3.Connection,
    reingested_sources: set[str],
    touched_by_source: dict[str, set[str]],
    now: str,
) -> None:
    """Mark prior-version current assertions omitted from a source re-ingestion as historical."""
    for src_id in sorted(reingested_sources):
        active_ids = touched_by_source.get(src_id)
        if not active_ids:
            continue
        rows = connection.execute(
            "SELECT assertion_id FROM assertion WHERE primary_source_id=? AND currentness_state='current' AND superseded_at IS NULL",
            (src_id,),
        ).fetchall()
        omitted = [r[0] for r in rows if r[0] not in active_ids]
        for aid in omitted:
            connection.execute(
                "UPDATE assertion SET currentness_state='historical', superseded_at=?, retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
                (now, now, aid),
            )
            connection.execute(
                "UPDATE search_document SET currentness_state='historical', retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
                (now, aid),
            )
            if _table_exists(connection, "assertion_label_state"):
                connection.execute(
                    "UPDATE assertion_label_state SET lifecycle_state='superseded', updated_at=? WHERE assertion_id=?",
                    (now, aid),
                )
            _evict_fts_and_resolve_validation_queue(connection, aid, now)


def _apply_existing_assertion_state(
    writer: Any,
    payload: dict[str, Any],
    target_id: str,
    operation_source: dict[str, Any],
    extraction_run_id: str,
    now: str,
    candidate_val_state: str,
    existing_assertion: Any,
    receipts: list[dict[str, Any]],
) -> tuple[Any, ...]:
    prior_confidence = existing_assertion[1]
    assertion_vals = writer._assertion_values(
        payload, target_id, operation_source,
        extraction_run_id, now, validation_state=candidate_val_state,
        prior_confidence=prior_confidence,
    )
    if existing_assertion[2] == "historical":
        vals_list = list(assertion_vals)
        vals_list[19], vals_list[25], vals_list[26], vals_list[30] = (
            existing_assertion[2], existing_assertion[3], existing_assertion[4], 0
        )
        assertion_vals = tuple(vals_list)
    for r_item in receipts:
        if r_item.get("target_id") == existing_assertion[0]:
            r_item["target_id"] = target_id
    return assertion_vals


def _ensure_source_and_track_reingest(
    writer: Any,
    connection: sqlite3.Connection,
    operation_source: dict[str, Any],
    source_version_id: str,
    now: str,
    reingested_sources: set[str],
    ensured_source_versions: set[str],
    existing_evidence: set[str],
) -> None:
    canon_src = writer.resolve_canonical_source_id(connection, operation_source)
    had_prior_sv = bool(
        connection.execute(
            "SELECT 1 FROM source_version WHERE source_id=? AND source_version_id!=?",
            (canon_src, source_version_id),
        ).fetchone()
    )
    writer._ensure_source(connection, operation_source, now)
    operation_source["source_id"] = canon_src
    if had_prior_sv:
        reingested_sources.add(canon_src)
    ensured_source_versions.add(source_version_id)
    for ev in operation_source.get("evidence", []):
        if isinstance(ev, dict) and "evidence_id" in ev:
            existing_evidence.add(ev["evidence_id"])


def _resolve_evidence_fingerprints(
    source_id: str,
    source_version_id: str,
    locator: dict[str, Any],
    evidence: dict[str, Any],
    a2a_source: bool,
) -> tuple[str, str]:
    legacy_ev_fp = digest({"source_version_id": source_version_id, "locator": locator, "content_digest": evidence.get("content_digest")})
    ev_fp = (
        legacy_ev_fp
        if a2a_source
        else digest({"source_id": source_id, "locator": locator, "content_digest": evidence.get("content_digest")})
    )
    return ev_fp, legacy_ev_fp


def _verify_evidence_row_identity(
    evidence_row: Any,
    evidence_fingerprint: str,
    legacy_ev_fp: str,
    source_id: str,
    source_version_id: str,
    a2a_source: bool,
    direct_quote: str | None,
) -> None:
    if evidence_row is None:
        raise RuntimeError(
            "governed evidence row was not created; evidence metadata violates the active graph contract"
        )
    if (
        evidence_row["evidence_fingerprint"] not in {evidence_fingerprint, legacy_ev_fp}
        or evidence_row["source_id"] != source_id
        or (a2a_source and evidence_row["source_version_id"] != source_version_id)
        or evidence_row["direct_quote"] != direct_quote
    ):
        raise RuntimeError("evidence identity collides with different immutable lineage")


def _sync_search_fts_row(
    connection: sqlite3.Connection,
    search_id: str,
    search_title: str,
    search_body: str,
    target_id: str,
    currentness_state: str = "current",
) -> None:
    """Ensures search_fts contains the search_document row regardless of canonical trigger installation."""
    if not _table_exists(connection, "search_fts"):
        return
    has_ai_trigger = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='search_document_ai'"
    ).fetchone()
    if has_ai_trigger is not None:
        return
    connection.execute("DELETE FROM search_fts WHERE object_id=?", (target_id,))
    if currentness_state == "historical":
        return
    sd_row = connection.execute(
        "SELECT rowid FROM search_document WHERE search_document_id=?",
        (search_id,),
    ).fetchone()
    if sd_row is not None:
        connection.execute("DELETE FROM search_fts WHERE rowid=?", (sd_row[0],))
        connection.execute(
            "INSERT INTO search_fts(rowid,title,body,object_type,object_id) VALUES (?,?,?,?,?)",
            (sd_row[0], search_title, search_body, "assertion", target_id),
        )


def _insert_audit_event(
    connection: sqlite3.Connection,
    change_set_id: str,
    operation_id: str,
    action: str,
    record_type: str,
    record_id: str,
    previous_value_json: str | None,
    new_value_json: str | None,
    source_id: str,
    now: str,
    reason: str,
    validation_ref: str | None,
    approval_ref: str | None,
    run_id: str,
) -> None:
    """Inserts a deterministic audit_event row for a governed graph writer operation."""
    from ..core import stable_id

    audit_id = stable_id("audit", change_set_id, operation_id, length=28)
    connection.execute(
        """INSERT INTO audit_event
           (audit_event_id,actor_or_process,action,record_type,record_id,previous_value_json,
            new_value_json,source_id,event_timestamp,reason,validation_reference,
            approval_reference,migration_or_ingestion_run_id,release_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            audit_id,
            "nee-dual-pipeline",
            action,
            record_type,
            record_id,
            previous_value_json,
            new_value_json,
            source_id,
            now,
            reason,
            validation_ref,
            approval_ref,
            run_id,
            "dual-pipeline-1.0.0",
        ),
    )


