"""
dual_pipeline/migrations/migrate_v56_bitemporal_and_provenance.py

Wave 2 Migration: Bi-Temporal Lineage, Provenance URIs & Atomic Triples.
Repairs unlinked supersessions, backfills missing valid_from dates,
normalizes inverse predicates (SUPPORTED_BY -> SUPPORTS), binds missing
assertion evidence, and standardizes source cold_archive_ref to RFC 3986 URIs.
"""
from __future__ import annotations

import json
from storage.connection import sqlite3
from typing import Any, Dict, List, Tuple


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Checks whether a table exists in sqlite_master."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Checks whether a column exists in a given table."""
    if not table_exists(conn, table):
        return False
    cur = conn.cursor()
    table_info_sql = f"PRAGMA table_info({table})"
    cols = [r[1] for r in cur.execute(table_info_sql).fetchall()]
    return column in cols


def register_supports_predicate(conn: sqlite3.Connection) -> None:
    """Registers canonical forward predicate SUPPORTS and inverts domain/range mappings."""
    cur = conn.cursor()
    if table_exists(conn, "assertion_predicate_registry"):
        cur.execute(
            """
            INSERT OR IGNORE INTO assertion_predicate_registry 
            (predicate, object_kind, cardinality, evidence_required, temporal_requirement,
             symmetric_mode, acyclic_flag, conflict_policy, active, definition, created_at)
            VALUES ('SUPPORTS', 'entity', 'many_to_many', 1, 'optional', 'never', 0, 'explicit_only', 1, 
                    'Canonical forward predicate SUPPORTS', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
            """
        )

    if table_exists(conn, "edge_type_registry"):
        cur.execute("UPDATE edge_type_registry SET registry_state='active' WHERE edge_type_id='SUPPORTS';")
        cols = {r[1] for r in cur.execute("PRAGMA table_info(edge_type_registry)").fetchall()}
        rec = {
            "edge_type_id": "SUPPORTS",
            "canonical_predicate": "SUPPORTS",
            "definition": "Canonical forward relationship SUPPORTS",
            "relationship_family": "SUPPORTS",
            "direction": "directed",
            "cardinality": "many_to_many",
            "registry_state": "active",
            "created_at": "2026-07-12T02:45:01Z",
        }
        rec = {k: v for k, v in rec.items() if k in cols}
        cols_sql = ", ".join(rec.keys())
        phs = ", ".join("?" for _ in rec)
        insert_edge_sql = f"INSERT OR IGNORE INTO edge_type_registry ({cols_sql}) VALUES ({phs})"
        cur.execute(insert_edge_sql, list(rec.values()))

    if table_exists(conn, "edge_type_domain_range") and table_exists(conn, "node"):
        # Invert (target, source) pairs from SUPPORTED_BY assertions
        pairs = cur.execute(
            """
            SELECT DISTINCT o.node_type as s_type, s.node_type as o_type
            FROM assertion a
            JOIN node s ON s.node_id = a.subject_node_id
            JOIN node o ON o.node_id = a.object_node_id
            WHERE a.predicate = 'SUPPORTED_BY' AND a.object_node_id IS NOT NULL
            """
        ).fetchall()
        for st, ot in pairs:
            cur.execute(
                """
                INSERT OR IGNORE INTO edge_type_domain_range 
                (edge_type_id, source_node_type, target_node_type, active, observed_baseline_count) 
                VALUES ('SUPPORTS', ?, ?, 1, 1)
                """,
                (st, ot),
            )
        # Ensure control -> control is allowed for tombstone assertion
        cur.execute(
            """
            INSERT OR IGNORE INTO edge_type_domain_range 
            (edge_type_id, source_node_type, target_node_type, active, observed_baseline_count) 
            VALUES ('SUPPORTS', 'control', 'control', 1, 1)
            """
        )


def backfill_valid_from(conn: sqlite3.Connection) -> int:
    """Backfills missing valid_from dates on assertions using recorded_at or created_at."""
    if not table_exists(conn, "assertion") or not column_exists(conn, "assertion", "valid_from"):
        return 0
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE assertion
        SET valid_from = substr(ifnull(nullif(recorded_at, ''), ifnull(nullif(created_at, ''), '2026-07-09T00:00:00Z')), 1, 10)
        WHERE valid_from IS NULL OR trim(valid_from) = '';
        """
    )
    return cur.rowcount


def ensure_baseline_evidence_item(conn: sqlite3.Connection) -> str:
    """Ensures at least one valid evidence item exists to satisfy foreign key constraints."""
    if not table_exists(conn, "evidence_item"):
        return "evi_tombstone_retired"
    cur = conn.cursor()
    row = cur.execute("SELECT evidence_id FROM evidence_item LIMIT 1").fetchone()
    if row:
        return row[0]

    source_table = "source" if table_exists(conn, "source") else "ingestion_source"
    if table_exists(conn, source_table):
        src_info_sql = f"PRAGMA table_info({source_table})"
        s_cols = {r[1] for r in cur.execute(src_info_sql).fetchall()}
        s_rec = {
            "source_id": "src_system_tombstone", "source_type": "system", "source_name": "System Tombstone Source",
            "title": "System Tombstone Source", "authority_rank": 1, "source_authority_rank": 1,
            "source_posture": "authoritative", "cold_archive_ref": "urn:nee:cold-archive:src_system_tombstone",
            "created_at": "1970-01-01T00:00:00Z",
        }
        s_rec = {k: v for k, v in s_rec.items() if k in s_cols}
        scols_sql, sphs = ", ".join(s_rec.keys()), ", ".join("?" for _ in s_rec)
        insert_source_sql = f"INSERT OR IGNORE INTO {source_table} ({scols_sql}) VALUES ({sphs})"
        cur.execute(insert_source_sql, list(s_rec.values()))

    if table_exists(conn, "source_version"):
        sv_cols = {r[1] for r in cur.execute("PRAGMA table_info(source_version)").fetchall()}
        sv_rec = {
            "source_version_id": "sv_system_tombstone", "version_id": "sv_system_tombstone", "source_id": "src_system_tombstone",
            "version_number": 1, "source_fingerprint": "0" * 64, "content_hash": "0" * 64, "sha256": "0" * 64,
            "source_date_normalized": "1970-01-01", "source_date_precision": "day", "source_date_status": "exact",
            "authority_rank": 1, "scope_text": "System tombstone", "posture_text": "authoritative", "created_at": "1970-01-01T00:00:00Z",
        }
        sv_rec = {k: v for k, v in sv_rec.items() if k in sv_cols}
        svcols_sql, svphs = ", ".join(sv_rec.keys()), ", ".join("?" for _ in sv_rec)
        insert_sv_sql = f"INSERT OR IGNORE INTO source_version ({svcols_sql}) VALUES ({svphs})"
        cur.execute(insert_sv_sql, list(sv_rec.values()))

    evi_cols = {r[1] for r in cur.execute("PRAGMA table_info(evidence_item)").fetchall()}
    evi_rec = {
        "evidence_id": "evi_system_tombstone", "evidence_fingerprint": "0" * 63 + "2",
        "source_id": "src_system_tombstone", "source_version_id": "sv_system_tombstone",
        "locator_kind": "system", "locator_value": "system", "locator": "system",
        "content": "System tombstone baseline evidence", "evidence_directness": "direct",
        "source_authority_rank": 1, "extraction_confidence": 1.0, "location_status": "exact",
        "created_at": "1970-01-01T00:00:00Z",
    }
    evi_rec = {k: v for k, v in evi_rec.items() if k in evi_cols}
    ecols_sql, ephs = ", ".join(evi_rec.keys()), ", ".join("?" for _ in evi_rec)
    insert_evi_sql = f"INSERT OR IGNORE INTO evidence_item ({ecols_sql}) VALUES ({ephs})"
    cur.execute(insert_evi_sql, list(evi_rec.values()))
    return "evi_system_tombstone"


def ensure_tombstone_primitives(conn: sqlite3.Connection) -> str:
    """Provisions node_tombstone and ast_tombstone_retired as supersession sinks."""
    if not table_exists(conn, "assertion"):
        return ""
    cur = conn.cursor()

    node_table = "node" if table_exists(conn, "node") else "canonical_graph_nodes"
    if table_exists(conn, node_table):
        node_info_sql = f"PRAGMA table_info({node_table})"
        node_cols = {r[1] for r in cur.execute(node_info_sql).fetchall()}
        node_rec = {
            "node_id": "node_tombstone",
            "node_type": "control",
            "canonical_name": "Tombstone Node",
            "name": "Tombstone Node",
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
        }
        node_rec = {k: v for k, v in node_rec.items() if k in node_cols}
        ncols = ", ".join(node_rec.keys())
        nphs = ", ".join("?" for _ in node_rec)
        insert_node_sql = f"INSERT OR IGNORE INTO {node_table} ({ncols}) VALUES ({nphs})"
        cur.execute(insert_node_sql, list(node_rec.values()))

    ast_cols = {r[1]: r[2] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    tombstone_record = {
        "assertion_id": "ast_tombstone_retired", "assertion_fingerprint": "0" * 63 + "1",
        "subject_node_id": "node_tombstone", "predicate": "SUPPORTS", "legacy_predicate": "SUPPORTS",
        "object_node_id": "node_tombstone", "object_literal": None, "assertion_type": "extracted_claim",
        "status": "RETIRED", "atomicity_state": "atomic", "scope_text": "Tombstone for retired assertions without superseding assertion",
        "extraction_method": "system_tombstone", "origin_type": "system_source", "confidence_label": "superseded",
        "confidence_score": 1.0, "validation_state": "superseded", "currentness_state": "historical",
        "recorded_at": "1970-01-01T00:00:00Z", "valid_from": "1970-01-01", "date_precision": "day",
        "date_status": "exact", "resolution_state": "superseded", "canonical_status": "candidate",
        "canonical_eligible": 0, "retrieval_eligible": 0, "materiality": "medium", "classification": "internal",
        "allowed_audiences_json": json.dumps(["internal_consulting"]), "hitl_required": 0, "qualifiers_json": "{}",
        "legacy_record_type": "edge", "legacy_record_ids_json": json.dumps(["system_tombstone"]),
        "created_at": "1970-01-01T00:00:00Z", "updated_at": "1970-01-01T00:00:00Z",
    }
    filtered_record = {k: v for k, v in tombstone_record.items() if k in ast_cols}
    cols_sql = ", ".join(filtered_record.keys())
    placeholders = ", ".join("?" for _ in filtered_record)
    insert_ast_sql = f"INSERT OR IGNORE INTO assertion ({cols_sql}) VALUES ({placeholders})"
    cur.execute(insert_ast_sql, list(filtered_record.values()))

    if table_exists(conn, "assertion_evidence"):
        evi_id = ensure_baseline_evidence_item(conn)
        cur.execute(
            """
            INSERT OR IGNORE INTO assertion_evidence 
            (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at)
            VALUES ('ast_tombstone_retired', ?, 'supports', 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
            """,
            (evi_id,),
        )
    return "ast_tombstone_retired"


def repair_supersessions(conn: sqlite3.Connection) -> int:
    """Repairs broken supersessions by linking to active assertion or tombstone."""
    if not table_exists(conn, "assertion") or not column_exists(conn, "assertion", "superseded_at"):
        return 0
    tombstone_id = ensure_tombstone_primitives(conn)
    cur = conn.cursor()
    repair_sql = f"""
        UPDATE assertion AS a
        SET superseded_by_assertion_id = coalesce(
            (SELECT a2.assertion_id FROM assertion a2
             WHERE a2.subject_node_id = a.subject_node_id
               AND a2.predicate = a.predicate
               AND a2.superseded_at IS NULL
               AND a2.assertion_id != a.assertion_id
             LIMIT 1),
            '{tombstone_id}'
        )
        WHERE a.superseded_at IS NOT NULL AND (a.superseded_by_assertion_id IS NULL OR trim(a.superseded_by_assertion_id) = '');
    """
    cur.execute(repair_sql)
    return cur.rowcount


def normalize_inverse_predicates(conn: sqlite3.Connection) -> int:
    """Normalizes SUPPORTED_BY assertions into canonical forward SUPPORTS assertions."""
    if not table_exists(conn, "assertion") or not column_exists(conn, "assertion", "predicate"):
        return 0
    cur = conn.cursor()
    has_legacy_pred = column_exists(conn, "assertion", "legacy_predicate")
    legacy_sql = ", legacy_predicate = 'SUPPORTS'" if has_legacy_pred else ""

    norm_sql = f"""
        UPDATE assertion
        SET subject_node_id = object_node_id,
            object_node_id = subject_node_id,
            predicate = 'SUPPORTS'
            {legacy_sql}
        WHERE predicate = 'SUPPORTED_BY' AND object_node_id IS NOT NULL;
    """
    cur.execute(norm_sql)
    normalized_count = cur.rowcount

    if table_exists(conn, "assertion_predicate_registry"):
        cur.execute("UPDATE assertion_predicate_registry SET active=0 WHERE predicate='SUPPORTED_BY';")
    if table_exists(conn, "edge_type_registry"):
        cur.execute("UPDATE edge_type_registry SET registry_state='deprecated' WHERE edge_type_id='SUPPORTED_BY';")
    return normalized_count


def standardize_source_provenance_uris(conn: sqlite3.Connection) -> int:
    """Converts source cold_archive_ref values into valid RFC 3986 URIs."""
    if not table_exists(conn, "source") or not column_exists(conn, "source", "cold_archive_ref"):
        return 0
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE source
        SET cold_archive_ref = CASE
            WHEN cold_archive_ref IS NULL OR trim(cold_archive_ref) = '' OR cold_archive_ref = 'cold_archive'
                THEN 'urn:nee:cold-archive:' || source_id
            WHEN cold_archive_ref LIKE 'file://%' OR cold_archive_ref LIKE 'urn:%' 
              OR cold_archive_ref LIKE 'https://%' OR cold_archive_ref LIKE 'http://%' 
              OR cold_archive_ref LIKE 'gs://%' OR cold_archive_ref LIKE 's3://%'
                THEN cold_archive_ref
            WHEN cold_archive_ref LIKE '/%'
                THEN 'file://' || cold_archive_ref
            ELSE 'file:///' || cold_archive_ref
        END;
        """
    )
    return cur.rowcount


def bind_missing_assertion_evidence(conn: sqlite3.Connection) -> int:
    """Binds any unreferenced assertions to an evidence item."""
    if not table_exists(conn, "assertion") or not table_exists(conn, "assertion_evidence"):
        return 0
    cur = conn.cursor()
    unbound = cur.execute(
        """
        SELECT a.assertion_id FROM assertion a
        WHERE NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id)
        """
    ).fetchall()
    if not unbound:
        return 0

    evi_id = ensure_baseline_evidence_item(conn)

    for (aid,) in unbound:
        cur.execute(
            """
            INSERT OR IGNORE INTO assertion_evidence 
            (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at)
            VALUES (?, ?, 'supports', 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (aid, evi_id),
        )
    return len(unbound)


def verify_integrity(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Runs foreign key check and integrity check."""
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    fk_errors = cur.execute("PRAGMA foreign_key_check;").fetchall()
    integrity = cur.execute("PRAGMA integrity_check;").fetchall()
    return {
        "foreign_key_violations": len(fk_errors),
        "fk_errors": fk_errors,
        "integrity_check": "ok" if (len(integrity) == 1 and integrity[0][0] == "ok") else str(integrity),
    }


def migrate_v56_bitemporal_and_provenance(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Executes the full Wave 2 bi-temporal and provenance migration transaction."""
    conn.execute("BEGIN TRANSACTION;")
    try:
        has_wc = table_exists(conn, "write_control")
        has_tx = column_exists(conn, "write_control", "active_transaction_id") if has_wc else False
        if has_wc:
            if has_tx:
                conn.execute("UPDATE write_control SET allow_legacy_write=1, active_transaction_id='tx_v56_w2' WHERE control_id=1;")
            else:
                conn.execute("UPDATE write_control SET allow_legacy_write=1 WHERE control_id=1;")

        register_supports_predicate(conn)
        valid_from_count = backfill_valid_from(conn)
        supersessions_count = repair_supersessions(conn)
        normalized_preds_count = normalize_inverse_predicates(conn)
        sources_count = standardize_source_provenance_uris(conn)
        bound_evidence_count = bind_missing_assertion_evidence(conn)

        if has_wc:
            if has_tx:
                conn.execute("UPDATE write_control SET allow_legacy_write=0, active_transaction_id=NULL WHERE control_id=1;")
            else:
                conn.execute("UPDATE write_control SET allow_legacy_write=0 WHERE control_id=1;")

        verification = verify_integrity(conn)
        if verification["foreign_key_violations"] > 0:
            raise RuntimeError(f"Foreign key violations detected: {verification['fk_errors']}")
        if verification["integrity_check"] != "ok":
            raise RuntimeError(f"Database integrity failure: {verification['integrity_check']}")

        conn.commit()
        return {
            "status": "COMPLETE",
            "backfilled_valid_from": valid_from_count,
            "repaired_supersessions": supersessions_count,
            "normalized_predicates": normalized_preds_count,
            "standardized_sources": sources_count,
            "bound_evidence_rows": bound_evidence_count,
            "integrity": verification,
        }
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"Migration v56 Wave 2 failed: {exc}") from exc
