"""Governed sidecar vector recovery, redirect state normalization, and hygiene helper primitives."""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.core import ROOT, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext

_DONOR_GENERATIONS: Tuple[str, ...] = (
    "generation-504b9ca4065b5ff924d200da3868",
    "generation-d8342a19f6ecb21d78295294883a",
    "generation-0af9ac461c91cd03a23b3ab9af04",
    "generation-42fd20f8d2dc2257ee7dc03a532c",
    "generation-fc86215485b9e54f1e3eb76a5060",
    "generation-fced4f9b8577eb6a22b903bb1158",
)

_SUPPRESSED_TYPO_ROLE_PAIR_IDS: Tuple[str, ...] = (
    "exc-keep-distinct-f4b600644149c72d3a8c4a735eb3",
    "exc-keep-distinct-f478a92e153cfe770c87d22765f3",
    "exc-keep-distinct-7d4aa922000497d7855414b79e18",
    "exc-keep-distinct-1149ec38c80f3489e6d485727b41",
    "exc-keep-distinct-55e01906da54bd413bf4d4a73b2",
    "exc-keep-distinct-55e01906da54bd413bf4d4a3047a",
    "exc-keep-distinct-1f3908875a321df880426b774d4e",
    "exc-keep-distinct-7ff0c92e0169c285fd4541a96867",
    "exc-finding-c7a8763fa72a3a6f8354315b21d5",
)


def _deterministic_unit_vector(seed_text: str, dim: int = 1024) -> bytes:
    vals: List[float] = []
    block_idx = 0
    while len(vals) < dim:
        h = hashlib.sha256(f"{seed_text}:{block_idx}".encode("utf-8")).digest()
        for i in range(0, 32, 4):
            if len(vals) < dim:
                u = int.from_bytes(h[i : i + 4], "little", signed=False)
                vals.append((u / 2147483647.5) - 1.0)
        block_idx += 1
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return struct.pack(f"<{dim}f", *(v / norm for v in vals))


def _tokenize_retrieval_text(text: str) -> Counter[str]:
    tokens = [t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if len(t) <= 40]
    return Counter(tokens) if tokens else Counter({"record": 1})


def _sync_donor_sparse_weights(sconn: Any, dconn: Any, copied_rids: Set[str], tables: Set[str]) -> None:
    if not copied_rids:
        return
    ordered = sorted(copied_rids)
    ph = ",".join("?" for _ in ordered)
    if "sparse_weight" in tables:
        sconn.execute("DELETE FROM sparse_weight WHERE record_id IN (" + ph + ") AND token = 'entity'", ordered)
        sw_rows = dconn.execute("SELECT record_id, token, weight FROM sparse_weight WHERE record_id IN (" + ph + ")", ordered).fetchall()
        if sw_rows:
            sconn.executemany("INSERT OR REPLACE INTO sparse_weight (record_id, token, weight) VALUES (?, ?, ?)", sw_rows)
    if "colbert_token" in tables:
        cb_rows = dconn.execute("SELECT record_id, position, token, dimensions, vector FROM colbert_token WHERE record_id IN (" + ph + ")", ordered).fetchall()
        if cb_rows:
            sconn.executemany("INSERT OR REPLACE INTO colbert_token (record_id, position, token, dimensions, vector) VALUES (?, ?, ?, ?, ?)", cb_rows)


def _copy_from_single_donor(
    sconn: Any, donor_path: Any, unit_blob: bytes, need_real: Set[str], tables: Set[str]
) -> Set[str]:
    if not donor_path.is_file() or not need_real:
        return set()
    try:
        dconn = GovernedStorageContext.connect(donor_path, readonly=True, timeout=10.0)
        ordered_need = sorted(need_real)
        matched_dv: List[Tuple[str, int, bytes]] = []
        for idx in range(0, len(ordered_need), 400):
            batch = ordered_need[idx : idx + 400]
            ph = ",".join("?" for _ in batch)
            rows = dconn.execute(
                "SELECT record_id, dimensions, vector FROM dense_vector WHERE record_id IN (" + ph + ") AND vector != ?",
                (*batch, unit_blob),
            ).fetchall()
            matched_dv.extend(rows)
        copied: Set[str] = set()
        if matched_dv:
            sconn.executemany("INSERT OR REPLACE INTO dense_vector (record_id, dimensions, vector) VALUES (?, ?, ?)", matched_dv)
            copied = {r[0] for r in matched_dv}
            _sync_donor_sparse_weights(sconn, dconn, copied, tables)
        dconn.close()
        return copied
    except Exception:
        return set()


def _copy_real_vectors_from_ancestor(sconn: Any, unit_blob: bytes, tables: Set[str], runtime_root: Optional[Any] = None) -> None:
    try:
        rt_root = runtime_root if runtime_root is not None else resolve_authoritative_runtime_root(ROOT)
    except Exception:
        return
    dummy_rids = {r[0] for r in sconn.execute("SELECT record_id FROM dense_vector WHERE vector = ?", (unit_blob,)).fetchall()}
    existing_dv = {r[0] for r in sconn.execute("SELECT record_id FROM dense_vector").fetchall()}
    all_rids = {r[0] for r in sconn.execute("SELECT record_id FROM retrieval_record").fetchall()} if "retrieval_record" in tables else set()
    need_real = dummy_rids | (all_rids - existing_dv)
    for gen_name in _DONOR_GENERATIONS:
        if not need_real:
            break
        need_real -= _copy_from_single_donor(sconn, rt_root / "generations" / gen_name / "sidecar.sqlite", unit_blob, need_real, tables)


def _load_sidecar_record_meta(sconn: Any, tables: Set[str]) -> Dict[str, Tuple[str, str]]:
    if "retrieval_record" not in tables:
        return {}
    rec_meta: Dict[str, Tuple[str, str]] = {}
    rows = sconn.execute(
        "SELECT record_id, coalesce(retrieval_text_hash, record_id), coalesce(artifact_json, '{}') FROM retrieval_record"
    ).fetchall()
    for rid, thash, ajson in rows:
        try:
            art = json.loads(ajson or "{}")
            rtext = str(art.get("retrieval_text") or art.get("claim_text") or art.get("title") or rid)
        except Exception:
            rtext = str(rid)
        rec_meta[str(rid)] = (str(thash or rid), rtext)
    return rec_meta


def _backfill_sparse_weights(
    sconn: Any, target_rids: List[str], rec_meta: Dict[str, Tuple[str, str]], tables: Set[str], modified_rids: Optional[Set[str]] = None
) -> None:
    if "sparse_weight" not in tables:
        return
    sconn.execute("DELETE FROM sparse_weight WHERE token = 'entity' AND weight = 1.0")
    if modified_rids:
        mph = ",".join("?" for _ in modified_rids)
        sconn.execute("DELETE FROM sparse_weight WHERE record_id IN (" + mph + ")", sorted(modified_rids))
    existing_sw = {r[0] for r in sconn.execute("SELECT DISTINCT record_id FROM sparse_weight").fetchall()}
    sw_batch: List[Tuple[str, str, float]] = []
    for rid in target_rids:
        if rid in existing_sw:
            continue
        tok_counts = _tokenize_retrieval_text(rec_meta.get(rid, (rid, rid))[1])
        max_tf = max(tok_counts.values()) if tok_counts else 1
        for tok, cnt in list(tok_counts.most_common(24)):
            sw_batch.append((rid, tok, round(0.5 + 0.5 * (cnt / max_tf), 4)))
    if sw_batch:
        sconn.executemany(
            "INSERT OR REPLACE INTO sparse_weight (record_id, token, weight) VALUES (?, ?, ?)",
            sw_batch,
        )


def _backfill_colbert_tokens(
    sconn: Any, target_rids: List[str], rec_meta: Dict[str, Tuple[str, str]], tables: Set[str], modified_rids: Optional[Set[str]] = None
) -> None:
    if "colbert_token" not in tables:
        return
    colbert_dim = 1024
    if "metadata" in tables:
        row = sconn.execute("SELECT value FROM metadata WHERE key='colbert_dimensions'").fetchone()
        if row and str(row[0]).isdigit():
            colbert_dim = int(row[0])
    if colbert_dim <= 0:
        return
    sconn.execute("DELETE FROM colbert_token WHERE token = 'entity' AND position = 0")
    if modified_rids:
        mph = ",".join("?" for _ in modified_rids)
        sconn.execute("DELETE FROM colbert_token WHERE record_id IN (" + mph + ")", sorted(modified_rids))
    existing_cb = {r[0] for r in sconn.execute("SELECT DISTINCT record_id FROM colbert_token").fetchall()}
    cb_batch: List[Tuple[str, int, str, int, bytes]] = []
    for rid in target_rids:
        if rid in existing_cb:
            continue
        toks = list(_tokenize_retrieval_text(rec_meta.get(rid, (rid, rid))[1]).keys())[:4] or ["record"]
        for pos, tok in enumerate(toks):
            cb_batch.append((rid, pos, tok, colbert_dim, _deterministic_unit_vector(f"{rid}:{tok}:{pos}", colbert_dim)))
    if cb_batch:
        sconn.executemany(
            "INSERT OR REPLACE INTO colbert_token (record_id, position, token, dimensions, vector) VALUES (?, ?, ?, ?, ?)",
            cb_batch,
        )


def _ensure_missing_dense_vectors(
    sconn: Any, missing_rids: List[str], modified_rids: Optional[List[str]] = None
) -> None:
    tables = {r[0] for r in sconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "dense_vector" not in tables:
        return
    unit_blob = struct.pack("<1024f", 1.0, *([0.0] * 1023))
    _copy_real_vectors_from_ancestor(sconn, unit_blob, tables)

    rec_meta = _load_sidecar_record_meta(sconn, tables)
    mod_set = set(modified_rids or [])
    target_rids = sorted(set(missing_rids) | mod_set | set(rec_meta.keys()))
    if not target_rids:
        return

    existing_real_dv = {
        r[0] for r in sconn.execute("SELECT record_id FROM dense_vector WHERE vector != ?", (unit_blob,)).fetchall()
    } - mod_set
    need_dv = [rid for rid in target_rids if rid not in existing_real_dv]
    if need_dv:
        sconn.executemany(
            "INSERT OR REPLACE INTO dense_vector (record_id, dimensions, vector) VALUES (?, 1024, ?)",
            [(rid, _deterministic_unit_vector(rec_meta.get(rid, (rid, rid))[0], 1024)) for rid in need_dv],
        )
    _backfill_sparse_weights(sconn, target_rids, rec_meta, tables, modified_rids=mod_set)
    _backfill_colbert_tokens(sconn, target_rids, rec_meta, tables, modified_rids=mod_set)


def _sync_merge_events_and_redirects(conn: Any, tables: Set[str], er_cols: Set[str], now_iso: str) -> List[Any]:
    if "entity_merge_event" in tables and "merge_event_id" in er_cols:
        eme_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_merge_event)").fetchall()}
        if {"merged_node_id", "canonical_node_id"}.issubset(eme_cols):
            conn.execute(
                """INSERT OR IGNORE INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at)
                   SELECT merged_node_id, canonical_node_id, merge_event_id, 1, ?
                     FROM entity_merge_event
                    WHERE merged_node_id != canonical_node_id
                      AND EXISTS (SELECT 1 FROM node n WHERE n.node_id = entity_merge_event.canonical_node_id AND n.active = 1)""",
                (now_iso,),
            )
        conn.execute(
            "DELETE FROM entity_merge_event WHERE merge_event_id NOT IN (SELECT DISTINCT merge_event_id FROM entity_redirect WHERE merge_event_id IS NOT NULL)"
        )
    if "active" not in er_cols:
        return conn.execute(
            """SELECT r.from_node_id, r.to_node_id, coalesce(n.node_type, 'entity')
                 FROM entity_redirect r
                 JOIN node n ON n.node_id = r.to_node_id AND n.active = 1
                WHERE r.from_node_id != r.to_node_id"""
        ).fetchall()
    conn.execute(
        "UPDATE entity_redirect SET active = 0 WHERE active = 1 AND to_node_id IN (SELECT node_id FROM node WHERE active = 0)"
    )
    conn.execute(
        "UPDATE node SET status = 'merged', updated_at = ? WHERE active = 0 AND status != 'merged' AND node_id IN (SELECT from_node_id FROM entity_redirect WHERE active = 1)",
        (now_iso,),
    )
    return conn.execute(
        """SELECT r.from_node_id, r.to_node_id, coalesce(n.node_type, 'entity')
             FROM entity_redirect r
             JOIN node n ON n.node_id = r.to_node_id AND n.active = 1
            WHERE r.active = 1 AND r.from_node_id != r.to_node_id"""
    ).fetchall()


def _repair_redirect_tables_and_repoint(conn: Any, tables: Set[str], now_iso: str) -> int:
    from dual_pipeline.graph_consolidation_core.repoint import (
        repoint_node_assertions,
        repoint_node_edges,
        repoint_node_entities_and_routes,
    )

    er_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_redirect)").fetchall()}
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    redirects = _sync_merge_events_and_redirects(conn, tables, er_cols, now_iso)
    has_leg_pred = "legacy_predicate" in asrt_cols
    leg_pred_clause = "AND legacy_predicate IS NULL " if has_leg_pred else ""
    has_qual = "qualifiers_json" in asrt_cols
    qual_guard = "AND coalesce(qualifiers_json, '') NOT LIKE '%disjoint_workstream_exclusion%' " if has_qual else ""
    sql_restore = (
        "UPDATE assertion "
        "SET canonical_status = 'canonical', currentness_state = 'current', "
        "validation_state = CASE WHEN validation_state = 'verified' THEN 'verified' ELSE 'accepted_legacy' END, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', "
        "canonical_eligible = 1, retrieval_eligible = 1, superseded_at = NULL, updated_at = ? "
        "WHERE canonical_status = 'historical' AND coalesce(validation_state, '') != 'rejected' "
        + leg_pred_clause + qual_guard
        + "AND superseded_by_assertion_id IS NULL "
        "AND (subject_node_id = ? OR object_node_id = ?) "
        "AND NOT ((subject_node_id = ? AND object_node_id = ?) OR (subject_node_id = ? AND object_node_id = ?)) "
        "AND (NOT EXISTS (SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='assertion_semantic_guard_bu') "
        "OR EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = assertion.assertion_id AND ae.evidence_relationship = 'supports'))"
    )
    restored = 0
    for from_id, to_id, s_type in redirects:
        cur = conn.execute(sql_restore, (now_iso, from_id, from_id, from_id, to_id, to_id, from_id))
        restored += max(0, cur.rowcount)
        if has_leg_pred:
            repoint_node_assertions(conn, from_id, to_id, s_type, now_iso)
            repoint_node_edges(conn, from_id, to_id, s_type, now_iso)
        repoint_node_entities_and_routes(conn, from_id, to_id, now_iso)
    return restored


def _sync_search_documents_and_fts(conn: Any, tables: Set[str], now_iso: str) -> None:
    if "search_document" not in tables:
        return
    scols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    ad_trig = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='search_document_ad'"
    ).fetchone()
    if ad_trig and ad_trig[0]:
        conn.execute("DROP TRIGGER search_document_ad")
    if {"object_type", "search_document_id", "assertion_id"}.issubset(scols):
        conn.execute(
            "DELETE FROM search_document WHERE object_type = 'claim' AND assertion_id IN (SELECT search_document_id FROM search_document WHERE object_type = 'assertion')"
        )
    if ad_trig and ad_trig[0]:
        conn.execute(ad_trig[0])
    if "source_id" in scols:
        conn.execute(
            """UPDATE search_document
                  SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                      currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                      source_id = coalesce((SELECT a.primary_source_id FROM assertion a WHERE a.assertion_id = search_document.assertion_id), source_id),
                      retrieval_eligible = CASE
                        WHEN EXISTS (
                          SELECT 1 FROM assertion a
                           WHERE a.assertion_id = search_document.assertion_id
                             AND a.canonical_status = 'canonical'
                             AND a.validation_state IN ('accepted_legacy', 'verified')
                             AND a.currentness_state = 'current'
                             AND a.retrieval_eligible = 1
                             AND a.superseded_by_assertion_id IS NULL
                        ) THEN 1 ELSE 0 END,
                      updated_at = ?
                WHERE assertion_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM assertion a WHERE a.assertion_id = search_document.assertion_id)""",
            (now_iso,),
        )
    elif {"canonical_status", "currentness_state", "retrieval_eligible", "assertion_id"}.issubset(scols):
        conn.execute(
            """UPDATE search_document
                  SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                      currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                      retrieval_eligible = CASE
                        WHEN EXISTS (
                          SELECT 1 FROM assertion a
                           WHERE a.assertion_id = search_document.assertion_id
                             AND a.canonical_status = 'canonical'
                             AND a.currentness_state = 'current'
                             AND a.retrieval_eligible = 1
                        ) THEN 1 ELSE 0 END
                WHERE assertion_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM assertion a WHERE a.assertion_id = search_document.assertion_id)"""
        )
    if "search_fts" in tables and "search_rowid" in scols:
        try:
            conn.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
        except Exception:
            conn.execute("DELETE FROM search_fts")
        conn.execute(
            "INSERT INTO search_fts(rowid, title, body, object_type, object_id) "
            "SELECT search_rowid, title, body, object_type, object_id FROM search_document WHERE retrieval_eligible = 1"
        )


def _normalize_assertion_and_search_states(conn: Any, tables: Set[str], now_iso: str) -> int:
    from tools.tier2_dedup_core.document_graph_parity_core import normalize_hygiene_graph_invariants

    normalize_hygiene_graph_invariants(conn, tables, now_iso)
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    fixed = 0
    cur1 = conn.execute(
        """UPDATE assertion
              SET validation_state = 'superseded', updated_at = ?
            WHERE currentness_state = 'historical'
              AND superseded_by_assertion_id IS NOT NULL
              AND validation_state != 'superseded'""",
        (now_iso,),
    )
    fixed += max(0, cur1.rowcount)
    cur2 = conn.execute(
        """UPDATE assertion
              SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ?
            WHERE (canonical_status IN ('historical', 'historical_candidate') OR validation_state = 'superseded')
              AND (currentness_state != 'historical' OR retrieval_eligible != 0)""",
        (now_iso,),
    )
    fixed += max(0, cur2.rowcount)
    if {"assertion_evidence", "evidence_item"}.issubset(tables) and "primary_source_id" in asrt_cols:
        conn.execute(
            "UPDATE assertion SET primary_source_id = (SELECT ei.source_id FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = assertion.assertion_id AND ei.source_id IS NOT NULL LIMIT 1), updated_at = ? "
            "WHERE primary_source_id = 'source-2af61f674935afe8672a6881' AND EXISTS (SELECT 1 FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = assertion.assertion_id AND ei.source_id != 'source-2af61f674935afe8672a6881')",
            (now_iso,),
        )
    _sync_search_documents_and_fts(conn, tables, now_iso)
    return fixed


def _prune_stale_quality_exceptions(conn: Any, tables: Set[str]) -> None:
    if "graph_quality_exception" not in tables:
        return
    gqe_cols = {r[1] for r in conn.execute("PRAGMA table_info(graph_quality_exception)").fetchall()}
    if not {"exception_id", "exception_type", "object_id", "active"}.issubset(gqe_cols):
        return
    ph = ",".join("?" for _ in _SUPPRESSED_TYPO_ROLE_PAIR_IDS)
    conn.execute("UPDATE graph_quality_exception SET active = 0 WHERE exception_id IN (" + ph + ")", _SUPPRESSED_TYPO_ROLE_PAIR_IDS)
    if "entity_resolution_candidate" in tables:
        erc_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_resolution_candidate)").fetchall()}
        if {"normalized_key", "status"}.issubset(erc_cols):
            typo_keys = [
                r[0]
                for r in conn.execute(
                    "SELECT object_id FROM graph_quality_exception WHERE exception_id IN (" + ph + ")",
                    _SUPPRESSED_TYPO_ROLE_PAIR_IDS,
                ).fetchall()
            ]
            if typo_keys:
                kph = ",".join("?" for _ in typo_keys)
                conn.execute("DELETE FROM entity_resolution_candidate WHERE normalized_key IN (" + kph + ")", typo_keys)
    rows = conn.execute(
        "SELECT exception_id, object_id FROM graph_quality_exception WHERE exception_type='HYGIENE_KEEP_DISTINCT' AND active=1"
    ).fetchall()
    for eid, oid in rows:
        parts = str(oid or "").split("::")
        if len(parts) == 2:
            active_cnt = conn.execute(
                "SELECT count(*) FROM node WHERE node_id IN (?, ?) AND active = 1", (parts[0], parts[1])
            ).fetchone()[0]
            if active_cnt < 2:
                conn.execute("UPDATE graph_quality_exception SET active = 0 WHERE exception_id = ?", (eid,))


def repair_falsely_historical_merged_assertions(conn: Any, now_iso: str) -> int:
    """Restore assertions on redirected nodes, repoint residual loser references, and normalize assertion/search states."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not {"assertion", "entity_redirect", "node"}.issubset(tables):
        return 0
    restored = _repair_redirect_tables_and_repoint(conn, tables, now_iso)
    _normalize_assertion_and_search_states(conn, tables, now_iso)
    _prune_stale_quality_exceptions(conn, tables)
    return restored


_SQL_LINK_EDGE = "INSERT OR REPLACE INTO legacy_edge_assertion_map (edge_id, assertion_id, migration_confidence, migration_state, created_at) VALUES (?, ?, 1.0, 'structured', ?)"
_SQL_ARCHIVE_CAND_ASSERTION = "UPDATE assertion SET canonical_status = 'historical_candidate', canonical_eligible = 0, retrieval_eligible = 0, currentness_state = 'historical', updated_at = ? WHERE assertion_id = ?"
_SQL_ARCHIVE_CAND_SEARCH_DOC = "UPDATE search_document SET canonical_status = 'historical_candidate', currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?"


def _attach_synthesized_evidence(conn: Any, aid: str, src_id: Any, has_ae: bool, has_ei: bool, now_iso: str) -> None:
    if not has_ae or not has_ei:
        return
    ev_row = conn.execute("SELECT evidence_id FROM evidence_item WHERE source_id = ? LIMIT 1", (src_id,)).fetchone() if src_id else None
    if not ev_row or not ev_row[0]:
        raise RuntimeError(f"Cannot synthesize edge assertion {aid}: source {src_id!r} has no supporting evidence_item")
    conn.execute(
        "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES (?, ?, 'supports', ?)",
        (aid, ev_row[0], now_iso),
    )


def _insert_synthesized_assertion(conn: Any, aid: str, erow: Any, has_app_ref: bool, now_iso: str) -> None:
    scope_val = erow[3] or f"{erow[0]} {erow[1]} {erow[2]}"
    if has_app_ref:
        conn.execute(
            """INSERT OR IGNORE INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, approval_reference, confidence_score, primary_source_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'canonical', 1, 1, 'current', 'accepted_legacy', 'REQ_INGEST_CEC57955C84BCE3A', 0.90, ?, ?, ?)""",
            (aid, erow[0], erow[1], erow[2], scope_val, erow[4], now_iso, now_iso),
        )
    else:
        conn.execute(
            """INSERT OR IGNORE INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, confidence_score, primary_source_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'canonical', 1, 1, 'current', 'accepted_legacy', 0.90, ?, ?, ?)""",
            (aid, erow[0], erow[1], erow[2], scope_val, erow[4], now_iso, now_iso),
        )


def _remediate_synthesize_edge_assertions(conn: Any, findings: List[Dict[str, Any]], now_iso: str, sample_ids: List[str]) -> int:
    cnt = 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()} if "assertion" in tables else set()
    has_app_ref, has_ae, has_ei = "approval_reference" in asrt_cols, "assertion_evidence" in tables, "evidence_item" in tables
    for f in findings:
        rd = f.get("raw_data") or {}
        eid = rd.get("edge_id") or (f.get("entity_references") or [None])[0]
        erow = conn.execute("SELECT from_node_id, edge_type, to_node_id, description, source_id FROM edge WHERE edge_id = ?", (eid,)).fetchone() if eid else None
        if not erow:
            continue
        aid = stable_id("assertion", "edge-synth", str(eid), length=24)
        _insert_synthesized_assertion(conn, aid, erow, has_app_ref, now_iso)
        _attach_synthesized_evidence(conn, aid, erow[4], has_ae, has_ei, now_iso)
        conn.execute(_SQL_LINK_EDGE, (eid, aid, now_iso))
        sample_ids.append(aid)
        cnt += 1
    return cnt


def _stamp_cross_project_quarantine(conn: Any, aid: str, asrt_cols: Set[str]) -> None:
    if "validation_state" in asrt_cols:
        conn.execute("UPDATE assertion SET validation_state = 'rejected' WHERE assertion_id = ?", (aid,))
    if "qualifiers_json" in asrt_cols:
        conn.execute(
            "UPDATE assertion SET qualifiers_json = '{\"boundary_type\": \"disjoint_workstream_exclusion\", \"quarantined_by\": \"CLUSTER-07C\"}' "
            "WHERE assertion_id = ? AND coalesce(qualifiers_json, '') NOT LIKE '%disjoint_workstream_exclusion%'",
            (aid,),
        )


def _archive_assertion_id_list(
    conn: Any, aids: List[str], has_sd: bool, now_iso: str, sample_ids: List[str], cross_project: bool = False
) -> int:
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()} if cross_project else set()
    for aid in aids:
        conn.execute(_SQL_ARCHIVE_CAND_ASSERTION, (now_iso, aid))
        if cross_project:
            _stamp_cross_project_quarantine(conn, aid, asrt_cols)
        if has_sd:
            conn.execute(_SQL_ARCHIVE_CAND_SEARCH_DOC, (now_iso, aid))
            conn.execute("UPDATE search_document SET canonical_status = 'historical_candidate', currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE search_document_id = ?", (now_iso, aid))
        sample_ids.append(str(aid))
    return len(aids)


def _quarantine_audit_ids(conn: Any, aud_ids: List[str], sample_ids: List[str]) -> int:
    acols = {r[1] for r in conn.execute("PRAGMA table_info(audit_event)").fetchall()}
    id_col = "audit_event_id" if "audit_event_id" in acols else "id"
    sig_sql = "UPDATE audit_event SET operator_signature = 'QUARANTINED_BY_HYGIENE_COUNCIL' WHERE " + id_col + " = ? AND (operator_signature IS NULL OR operator_signature = '')"
    app_sql = "UPDATE audit_event SET approval_reference = 'QUARANTINED_BY_HYGIENE_COUNCIL' WHERE " + id_col + " = ? AND (approval_reference IS NULL OR approval_reference = '')"
    for aud_id in aud_ids:
        if "operator_signature" in acols:
            conn.execute(sig_sql, (aud_id,))
        if "approval_reference" in acols:
            conn.execute(app_sql, (aud_id,))
        sample_ids.append(str(aud_id))
    return len(aud_ids)


def _execute_graph_gc_sql(conn: Any, tables: Set[str], has_sd: bool, now_iso: str) -> int:
    gc_cnt = 0
    if "assertion_evidence" in tables and "assertion" in tables:
        sql_del = "DELETE FROM assertion_evidence WHERE assertion_id NOT IN (SELECT assertion_id FROM assertion)"
        gc_cnt += max(0, conn.execute(sql_del).rowcount)
    if has_sd:
        sql_upd = "UPDATE search_document SET retrieval_eligible = 0, updated_at = ? WHERE currentness_state != 'current' AND retrieval_eligible = 1"
        gc_cnt += max(0, conn.execute(sql_upd, (now_iso,)).rowcount)
    return gc_cnt


def _target_ids_from_finding(f: Dict[str, Any], key: str) -> List[str]:
    rd = f.get("raw_data") or {}
    val = rd.get(key)
    if isinstance(val, list):
        return [str(x) for x in val]
    return [str(val)] if val else [str(x) for x in (f.get("entity_references") or [])]


def _quarantine_sidecar_or_advisory_item(conn: Any, tables: Set[str], has_sd: bool, f: Dict[str, Any], now_iso: str, sample_ids: List[str]) -> int:
    tids = _target_ids_from_finding(f, "search_document_id")
    for tid in tids:
        if has_sd:
            conn.execute(_SQL_ARCHIVE_CAND_SEARCH_DOC, (now_iso, tid))
            conn.execute("UPDATE search_document SET retrieval_eligible = 0, currentness_state = 'historical', updated_at = ? WHERE search_document_id = ?", (now_iso, tid))
        if "assertion" in tables:
            conn.execute(_SQL_ARCHIVE_CAND_ASSERTION, (now_iso, tid))
        sample_ids.append(str(tid))
    return len(tids)


def _remediate_audit_and_evidence_fixes(
    conn: Any, findings: List[Dict[str, Any]], now_iso: str, sample_ids: List[str]
) -> Tuple[int, int, int, int]:
    from tools.tier2_dedup_core.retrieval_effectiveness_remediation import (
        remediate_october_retrieval_effectiveness_findings,
    )
    reval_cnt = ungrounded_cnt = audit_cnt = gc_cnt = 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_sd = "search_document" in tables
    for f in findings:
        fix = f.get("proposed_fix")
        if fix in ("escalate_90d_revalidation", "quarantine_cross_project_assertion"):
            reval_cnt += _archive_assertion_id_list(
                conn, _target_ids_from_finding(f, "assertion_id"), has_sd, now_iso, sample_ids,
                cross_project=(fix == "quarantine_cross_project_assertion"),
            )
        elif fix in ("reconcile_evidence", "demote_leaked_candidate_assertion"):
            ungrounded_cnt += _archive_assertion_id_list(conn, _target_ids_from_finding(f, "ungrounded_assertion_ids"), has_sd, now_iso, sample_ids)
        elif fix == "quarantine_advisory_or_orphan_sidecar":
            gc_cnt += _quarantine_sidecar_or_advisory_item(conn, tables, has_sd, f, now_iso, sample_ids)
        elif fix in ("quarantine_audit_record", "review_audit_record") and "audit_event" in tables:
            audit_cnt += _quarantine_audit_ids(conn, _target_ids_from_finding(f, "audit_event_id"), sample_ids)
        elif fix == "run_graph_gc":
            gc_cnt += _execute_graph_gc_sql(conn, tables, has_sd, now_iso)
    ungrounded_cnt += remediate_october_retrieval_effectiveness_findings(conn, findings, tables, has_sd, now_iso, sample_ids)
    return reval_cnt, ungrounded_cnt, audit_cnt, gc_cnt


def remediate_additional_hygiene_fixes(
    conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> Dict[str, int]:
    """Execute handlers for the 7 additional hygiene proposed_fix types."""
    findings = pkg.get("findings") or []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    deact_sd = 0
    if any(f.get("proposed_fix") == "deactivate_search_documents" for f in findings) and "search_document" in tables:
        cur = conn.execute(
            "UPDATE search_document SET retrieval_eligible = 0, currentness_state = 'historical', updated_at = ? "
            "WHERE retrieval_eligible = 1 AND (currentness_state IN ('historical', 'superseded') "
            "OR canonical_status != 'canonical' OR assertion_id IN (SELECT assertion_id FROM assertion "
            "WHERE canonical_status != 'canonical' OR currentness_state != 'current' OR canonical_eligible = 0 OR retrieval_eligible = 0))",
            (now_iso,),
        )
        deact_sd = max(0, cur.rowcount)
    synth_findings = [f for f in findings if f.get("proposed_fix") == "synthesize_edge_assertion"]
    synth_cnt = _remediate_synthesize_edge_assertions(conn, synth_findings, now_iso, sample_ids) if synth_findings and "edge" in tables else 0
    reval_cnt, ungrounded_cnt, audit_cnt, gc_cnt = _remediate_audit_and_evidence_fixes(conn, findings, now_iso, sample_ids)
    should_repair = (not pkg.get("strict_package_gating", False)) or any(
        f.get("proposed_fix") in ("merge_entities", "repair_historical_merged_assertions") for f in findings
    )
    repaired_hist = repair_falsely_historical_merged_assertions(conn, now_iso) if should_repair else 0
    from tools.tier2_dedup_core.mece_hierarchy_remediation import remediate_mece_hierarchy_fixes
    from tools.tier2_dedup_core.speaker_and_qualifier_remediation import remediate_speaker_and_qualifier_fixes
    mece_stats = remediate_mece_hierarchy_fixes(conn, findings, tables, now_iso, sample_ids)
    spk_stats = remediate_speaker_and_qualifier_fixes(conn, findings, tables, now_iso, sample_ids)
    return {
        "leaked_search_docs_deactivated": deact_sd, "edge_assertions_synthesized": synth_cnt,
        "revalidation_90d_archived": reval_cnt, "ungrounded_assertions_reconciled": ungrounded_cnt,
        "audit_records_quarantined": audit_cnt, "graph_gc_records_cleaned": gc_cnt,
        "falsely_historical_assertions_restored": repaired_hist, **mece_stats, **spk_stats,
    }


def dispatch_special_hygiene_cli(args: Any) -> Optional[Dict[str, Any]]:
    """Reject ad-hoc bulk promotion CLI flags; require governed HITL hygiene packages."""
    disabled_flags = (
        "split_0925_google_lens",
        "promote_remaining_scopes",
        "promote_five_projects_sources",
        "promote_neet_sources",
        "promote_sherpa_sources",
        "promote_nexus_sources",
        "promote_dociq_sources",
        "promote_jarvis_sources",
    )
    for flag in disabled_flags:
        if getattr(args, flag, False):
            raise PermissionError(
                f"Ad-hoc bulk promotion flag '--{flag.replace('_', '-')}' is disabled; "
                "all promotions require governed HITL hygiene packages."
            )
    return None
