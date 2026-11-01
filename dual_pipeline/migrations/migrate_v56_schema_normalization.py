"""
dual_pipeline/migrations/migrate_v56_schema_normalization.py

Wave 1 Migration: Schema Normalization, Referential Integrity & Traversal Indexing.
Retires legacy persona/project tables into canonical assertion primitives,
prunes unreferenced orphaned nodes, and provisions composite traversal and JSON indexes.
"""
from __future__ import annotations

import hashlib
import json
from storage.connection import sqlite3
from typing import Any, Dict, List, Tuple

LEGACY_BLOAT_TABLES = [
    "dan_next_best_action",
    "gemini_enablement_answer_packet",
    "gemini_enablement_guardrail",
    "gemini_enablement_raci_question",
    "gemini_enablement_track_model",
    "rewire_amwe_answer_packet",
    "account_team_next_best_action",
]

NEW_PREDICATES = [
    (
        "RECOMMENDS_NEXT_BEST_ACTION",
        "literal",
        "many_to_many",
        0,
        "optional",
        "never",
        0,
        "explicit_only",
        1,
        "Next best action recommendation",
    ),
    (
        "PROVIDES_ANSWER_PACKET",
        "literal",
        "many_to_many",
        0,
        "optional",
        "never",
        0,
        "explicit_only",
        1,
        "Strategic answer packet",
    ),
    (
        "ENFORCES_GUARDRAIL",
        "literal",
        "many_to_many",
        0,
        "optional",
        "never",
        0,
        "explicit_only",
        1,
        "Enablement guardrail item",
    ),
    (
        "RAISES_RACI_QUESTION",
        "literal",
        "many_to_many",
        0,
        "optional",
        "never",
        0,
        "explicit_only",
        1,
        "RACI question",
    ),
    (
        "DEFINES_ENABLEMENT_TRACK",
        "literal",
        "many_to_many",
        0,
        "optional",
        "never",
        0,
        "explicit_only",
        1,
        "Enablement track definition",
    ),
]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Checks whether a table exists in sqlite_master."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def register_candidate_predicates(conn: sqlite3.Connection) -> List[str]:
    """Registers new literal predicates into assertion_predicate_registry if table exists."""
    if not table_exists(conn, "assertion_predicate_registry"):
        return []
    registered = []
    cur = conn.cursor()
    for pred_def in NEW_PREDICATES:
        cur.execute(
            """
            INSERT OR IGNORE INTO assertion_predicate_registry 
            (predicate, object_kind, cardinality, evidence_required, temporal_requirement,
             symmetric_mode, acyclic_flag, conflict_policy, active, definition, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            pred_def,
        )
        registered.append(pred_def[0])
    return registered


def ensure_subject_nodes(conn: sqlite3.Connection) -> None:
    """Ensures subject anchor nodes exist in the node table."""
    node_table = "node" if table_exists(conn, "node") else "canonical_graph_nodes"
    if not table_exists(conn, node_table):
        return
    anchors = [
        ("person_dan", "Dan", "stakeholder"),
        ("workstream_gemini_enablement", "Gemini Enablement", "workstream"),
        ("workstream_rewire_amwe", "REWIRE AM&WE", "workstream"),
    ]
    cur = conn.cursor()
    for nid, name, ntype in anchors:
        exists = cur.execute(
            f"SELECT 1 FROM {node_table} WHERE node_id=?", (nid,)
        ).fetchone()
        if not exists:
            cur.execute(
                f"""
                INSERT INTO {node_table} (node_id, node_type, canonical_name, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                """,
                (nid, ntype, name),
            )


def build_assertion_record(
    table: str, data: Dict[str, Any], ast_cols: Dict[str, Any], has_cvr: bool = False
) -> Dict[str, Any]:
    """Constructs a normalized assertion dict from a legacy table row."""
    row_id = (
        data.get("action_rank")
        or data.get("packet_id")
        or data.get("guardrail_id")
        or data.get("raci_question_id")
        or data.get("track_id")
        or hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]
    )
    ast_id = f"ast_migrated_{table}_{row_id}"
    is_dan = "dan" in table or "account_team" in table
    subject_id = "person_dan" if is_dan else ("workstream_rewire_amwe" if "rewire" in table else "workstream_gemini_enablement")
    predicate = (
        "RECOMMENDS_NEXT_BEST_ACTION" if "next_best_action" in table
        else ("ENFORCES_GUARDRAIL" if "guardrail" in table
        else ("RAISES_RACI_QUESTION" if "raci" in table
        else ("DEFINES_ENABLEMENT_TRACK" if "track" in table else "PROVIDES_ANSWER_PACKET")))
    )
    obj_lit = (
        data.get("action")
        or data.get("answer_summary")
        or data.get("item")
        or data.get("question")
        or data.get("track")
        or ""
    )
    qualifiers = dict(data)
    qualifiers["legacy_source_table"] = table
    qual_json = json.dumps(qualifiers, sort_keys=True)
    fp = hashlib.sha256(f"{ast_id}:{subject_id}:{predicate}:{obj_lit}".encode("utf-8")).hexdigest()
    created_at = data.get("created_at", "2026-07-09T15:30:00Z")

    if has_cvr:
        ast_type = "recommendation" if "next_best_action" in table else "extracted_claim"
    else:
        ast_type = "STRATEGIC"

    fields = {
        "assertion_id": ast_id,
        "assertion_fingerprint": fp,
        "subject_node_id": subject_id,
        "predicate": predicate,
        "object_literal": obj_lit,
        "object_value": obj_lit,
        "literal_value": obj_lit,
        "assertion_type": ast_type,
        "status": "CONFIRMED",
        "atomicity_state": "atomic",
        "scope_text": f"Migrated from {table}",
        "extraction_method": "legacy_table_migration",
        "origin_type": "legacy_migration",
        "confidence_label": "medium",
        "confidence_score": 0.70,
        "validation_state": "unreviewed",
        "currentness_state": "current",
        "recorded_at": created_at,
        "date_precision": "day",
        "date_status": "exact",
        "resolution_state": "resolved",
        "canonical_status": "candidate",
        "canonical_eligible": 0,
        "retrieval_eligible": 0,
        "materiality": "medium",
        "classification": "internal",
        "allowed_audiences_json": json.dumps(["internal_consulting"]),
        "hitl_required": 0,
        "qualifiers_json": qual_json,
        "legacy_record_type": "edge",
        "legacy_record_ids_json": json.dumps([f"{table}_{row_id}"]),
        "created_at": created_at,
        "updated_at": created_at,
    }
    return {k: v for k, v in fields.items() if k in ast_cols}


def migrate_persona_tables(conn: sqlite3.Connection) -> Tuple[int, List[str]]:
    """Migrates rows from legacy tables into assertions and drops legacy tables."""
    if not table_exists(conn, "assertion"):
        return 0, []
    cur = conn.cursor()
    ast_cols = {r[1]: r[2] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    has_cvr = table_exists(conn, "controlled_value_registry")
    ensure_subject_nodes(conn)
    total_migrated = 0
    dropped = []

    for table in LEGACY_BLOAT_TABLES:
        if not table_exists(conn, table):
            continue
        table_info_sql = f"PRAGMA table_info({table})"
        cols = [r[1] for r in cur.execute(table_info_sql).fetchall()]
        select_table_sql = f"SELECT * FROM {table}"
        rows = cur.execute(select_table_sql).fetchall()
        for row in rows:
            data = dict(zip(cols, row))
            record = build_assertion_record(table, data, ast_cols, has_cvr=has_cvr)
            placeholders = ", ".join("?" for _ in record)
            col_names = ", ".join(record.keys())
            insert_ast_sql = f"INSERT INTO assertion ({col_names}) VALUES ({placeholders})"
            cur.execute(insert_ast_sql, list(record.values()))
            total_migrated += 1
        drop_table_sql = f"DROP TABLE {table}"
        cur.execute(drop_table_sql)
        dropped.append(table)

    return total_migrated, dropped


def prune_unreferenced_nodes(conn: sqlite3.Connection) -> int:
    """Garbage collects unreferenced orphaned nodes and dependent rowsets."""
    if not table_exists(conn, "node"):
        return 0
    cur = conn.cursor()
    has_edge = table_exists(conn, "edge")
    has_claim = table_exists(conn, "claim")
    edge_clause = (
        "AND n.node_id NOT IN (SELECT DISTINCT from_node_id FROM edge WHERE from_node_id IS NOT NULL) "
        "AND n.node_id NOT IN (SELECT DISTINCT to_node_id FROM edge WHERE to_node_id IS NOT NULL) "
        if has_edge else ""
    )
    claim_clause = (
        "AND n.node_id NOT IN (SELECT DISTINCT subject_node_id FROM claim WHERE subject_node_id IS NOT NULL) "
        if has_claim else ""
    )
    find_orphans_sql = f"""
        SELECT n.node_id FROM node n
        WHERE n.node_id NOT IN (SELECT DISTINCT subject_node_id FROM assertion WHERE subject_node_id IS NOT NULL)
          AND n.node_id NOT IN (SELECT DISTINCT object_node_id FROM assertion WHERE object_node_id IS NOT NULL)
          {edge_clause}
          {claim_clause}
    """
    orphans = [r[0] for r in cur.execute(find_orphans_sql).fetchall()]
    if not orphans:
        return 0

    has_wc = table_exists(conn, "write_control")
    if has_wc:
        cur.execute("UPDATE write_control SET allow_legacy_write=1, active_transaction_id='tx_v56' WHERE control_id=1;")

    dependents = [
        ("node_state", "node_id"),
        ("canonical_route", "object_id"),
        ("node_card_map", "node_id"),
        ("alias", "node_id"),
        ("canonical_alias", "entity_id"),
        ("entity_redirect", "from_node_id"),
        ("entity_redirect", "to_node_id"),
        ("entity_resolution_candidate_member", "node_id"),
        ("validation_queue", "node_id"),
        ("entity_merge_event", "canonical_node_id"),
        ("candidate_entity", "matched_node_id"),
    ]
    for o in orphans:
        for tbl, col in dependents:
            if table_exists(conn, tbl):
                delete_dep_sql = f"DELETE FROM {tbl} WHERE {col}=?"
                cur.execute(delete_dep_sql, (o,))
        cur.execute("DELETE FROM node WHERE node_id=?", (o,))

    if has_wc:
        cur.execute("UPDATE write_control SET allow_legacy_write=0, active_transaction_id=NULL WHERE control_id=1;")
    return len(orphans)


def create_traversal_indexes(conn: sqlite3.Connection) -> List[str]:
    """Provisions composite traversal and JSON expression indexes."""
    if not table_exists(conn, "assertion"):
        return []
    cur = conn.cursor()
    indexes = [
        ("idx_assertion_subj_pred", "CREATE INDEX IF NOT EXISTS idx_assertion_subj_pred ON assertion(subject_node_id, predicate);"),
        (
            "idx_assertion_qualifiers_card",
            "CREATE INDEX IF NOT EXISTS idx_assertion_qualifiers_card ON assertion(json_extract(qualifiers_json, '$.card_id')) WHERE qualifiers_json IS NOT NULL;",
        ),
        (
            "idx_assertion_allowed_audiences",
            "CREATE INDEX IF NOT EXISTS idx_assertion_allowed_audiences ON assertion(json_extract(allowed_audiences_json, '$[0]')) WHERE allowed_audiences_json IS NOT NULL;",
        ),
    ]
    created = []
    for name, sql in indexes:
        cur.execute(sql)
        created.append(name)
    return created


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


def migrate_v56_schema_normalization(
    conn: sqlite3.Connection, prune_orphans: bool = True
) -> Dict[str, Any]:
    """Executes the full Wave 1 schema normalization migration transaction."""
    conn.execute("BEGIN TRANSACTION;")
    try:
        registered_preds = register_candidate_predicates(conn)
        migrated_rows, dropped_tables = migrate_persona_tables(conn)
        pruned_count = prune_unreferenced_nodes(conn) if prune_orphans else 0
        indexes = create_traversal_indexes(conn)
        verification = verify_integrity(conn)

        if verification["foreign_key_violations"] > 0:
            raise RuntimeError(f"Foreign key violations detected: {verification['fk_errors']}")
        if verification["integrity_check"] != "ok":
            raise RuntimeError(f"Database integrity failure: {verification['integrity_check']}")

        conn.commit()
        return {
            "status": "COMPLETE",
            "migrated_assertions": migrated_rows,
            "dropped_tables": dropped_tables,
            "pruned_orphans": pruned_count,
            "registered_predicates": registered_preds,
            "created_indexes": indexes,
            "integrity": verification,
        }
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"Migration v56 failed: {exc}") from exc
