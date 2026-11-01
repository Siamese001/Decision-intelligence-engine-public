"""
dual_pipeline/graph_consolidation_core/repoint.py

Governed repointing routines for entity merges.
Safely re-routes assertions, claims, edges, routes, and aliases to survivor nodes.
"""
from __future__ import annotations

from typing import Any, Optional

from storage.connection import sqlite3


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _replace_node_names_in_search_doc(conn: sqlite3.Connection, retired_id: str, survivor_id: str, now: str, aid: str) -> None:
    if not _table_exists(conn, "node"):
        return
    ncols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}
    name_col = "canonical_name" if "canonical_name" in ncols else ("name" if "name" in ncols else None)
    if not name_col:
        return
    r_row = conn.execute("SELECT " + name_col + " FROM node WHERE node_id=?", (retired_id,)).fetchone()
    s_row = conn.execute("SELECT " + name_col + " FROM node WHERE node_id=?", (survivor_id,)).fetchone()
    r_name = str(r_row[0] or "").strip() if r_row else ""
    s_name = str(s_row[0] or "").strip() if s_row else ""
    if r_name and s_name and r_name != s_name:
        conn.execute(
            "UPDATE search_document SET title=replace(coalesce(title,''),?,?), body=replace(coalesce(body,''),?,?), updated_at=? WHERE assertion_id=?",
            (r_name, s_name, r_name, s_name, now, aid),
        )


def _update_search_doc_on_repoint(
    conn: sqlite3.Connection, retired_id: str, survivor_id: str, now: str, aid: str
) -> None:
    if not _table_exists(conn, "search_document"):
        return
    scols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    if {"title", "body", "updated_at", "assertion_id"}.issubset(scols):
        conn.execute(
            "UPDATE search_document SET title=replace(coalesce(title,''),?,?), body=replace(coalesce(body,''),?,?), updated_at=? WHERE assertion_id=?",
            (retired_id, survivor_id, retired_id, survivor_id, now, aid),
        )
        _replace_node_names_in_search_doc(conn, retired_id, survivor_id, now, aid)


def _check_domain_range(
    conn: sqlite3.Connection,
    edge_type: Optional[str],
    source_type: Optional[str],
    target_type: Optional[str],
) -> bool:
    if not edge_type or not source_type or not target_type:
        return True
    if not _table_exists(conn, "edge_type_domain_range"):
        return True
    row = conn.execute(
        "SELECT 1 FROM edge_type_domain_range WHERE edge_type_id=? AND source_node_type=? AND target_node_type=? AND active=1",
        (edge_type, source_type, target_type),
    ).fetchone()
    return row is not None


def _repoint_single_subject_assertion(
    conn: sqlite3.Connection,
    row: Any,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    aid = row["assertion_id"]
    obj_id = row["object_node_id"]
    pred = row["predicate"]
    leg_pred = row["legacy_predicate"]

    if obj_id == survivor_id:
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', canonical_status='historical', retrieval_eligible=0, superseded_at=?, updated_at=? WHERE assertion_id=?",
            (now, now, aid),
        )
        return

    if obj_id:
        ot = conn.execute("SELECT node_type FROM node WHERE node_id=?", (obj_id,)).fetchone()
        ot_type = ot[0] if ot else None
        if not _check_domain_range(conn, leg_pred, s_type, ot_type):
            conn.execute(
                "UPDATE assertion SET currentness_state='historical', canonical_status='historical', retrieval_eligible=0, superseded_at=?, updated_at=? WHERE assertion_id=?",
                (now, now, aid),
            )
            return

    coll = conn.execute(
        "SELECT assertion_id FROM assertion WHERE subject_node_id=? AND ifnull(predicate,'')=ifnull(?,'') AND ifnull(legacy_predicate,'')=ifnull(?,'') AND ifnull(object_node_id,'')=ifnull(?,'') AND currentness_state='current' AND superseded_by_assertion_id IS NULL AND ifnull(valid_from,'')=? AND ifnull(valid_to,'')=? AND ifnull(scope_text,'')=ifnull(?,'')",
        (survivor_id, pred, leg_pred, obj_id, row["vf"], row["vt"], row["scope_text"]),
    ).fetchone()

    if coll:
        w_aid = coll[0]
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', canonical_status='historical', validation_state='superseded', retrieval_eligible=0, superseded_at=?, superseded_by_assertion_id=?, updated_at=? WHERE assertion_id=?",
            (now, w_aid, now, aid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) SELECT ?, evidence_id, evidence_relationship, independent_corroboration, ? FROM assertion_evidence WHERE assertion_id=?",
            (w_aid, now, aid),
        )
    else:
        conn.execute(
            "UPDATE assertion SET subject_node_id=?, updated_at=? WHERE assertion_id=?",
            (survivor_id, now, aid),
        )
        _update_search_doc_on_repoint(conn, retired_id, survivor_id, now, aid)


def _repoint_single_object_assertion(
    conn: sqlite3.Connection,
    row: Any,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    aid = row["assertion_id"]
    sub_id = row["subject_node_id"]
    pred = row["predicate"]
    leg_pred = row["legacy_predicate"]

    if sub_id == survivor_id:
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', canonical_status='historical', retrieval_eligible=0, superseded_at=?, updated_at=? WHERE assertion_id=?",
            (now, now, aid),
        )
        return

    st = conn.execute("SELECT node_type FROM node WHERE node_id=?", (sub_id,)).fetchone()
    st_type = st[0] if st else None
    if not _check_domain_range(conn, leg_pred, st_type, s_type):
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', canonical_status='historical', retrieval_eligible=0, superseded_at=?, updated_at=? WHERE assertion_id=?",
            (now, now, aid),
        )
        return

    coll = conn.execute(
        "SELECT assertion_id FROM assertion WHERE subject_node_id=? AND ifnull(predicate,'')=ifnull(?,'') AND ifnull(legacy_predicate,'')=ifnull(?,'') AND object_node_id=? AND currentness_state='current' AND superseded_by_assertion_id IS NULL AND ifnull(valid_from,'')=? AND ifnull(valid_to,'')=? AND ifnull(scope_text,'')=ifnull(?,'')",
        (sub_id, pred, leg_pred, survivor_id, row["vf"], row["vt"], row["scope_text"]),
    ).fetchone()

    if coll:
        w_aid = coll[0]
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', canonical_status='historical', validation_state='superseded', retrieval_eligible=0, superseded_at=?, superseded_by_assertion_id=?, updated_at=? WHERE assertion_id=?",
            (now, w_aid, now, aid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) SELECT ?, evidence_id, evidence_relationship, independent_corroboration, ? FROM assertion_evidence WHERE assertion_id=?",
            (w_aid, now, aid),
        )
    else:
        conn.execute(
            "UPDATE assertion SET object_node_id=?, updated_at=? WHERE assertion_id=?",
            (survivor_id, now, aid),
        )
        _update_search_doc_on_repoint(conn, retired_id, survivor_id, now, aid)


def repoint_node_assertions(
    conn: sqlite3.Connection,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    """Repoints all assertions connected to retired node to survivor node."""
    conn.execute(
        "UPDATE assertion SET currentness_state='historical', canonical_status='historical', retrieval_eligible=0, superseded_at=?, updated_at=? WHERE ((subject_node_id=? AND object_node_id=?) OR (subject_node_id=? AND object_node_id=?)) AND currentness_state='current'",
        (now, now, survivor_id, retired_id, retired_id, survivor_id),
    )

    subj_rows = conn.execute(
        "SELECT assertion_id, object_node_id, predicate, legacy_predicate, ifnull(valid_from,'') AS vf, ifnull(valid_to,'') AS vt, scope_text "
        "FROM assertion WHERE subject_node_id=? AND currentness_state='current'",
        (retired_id,),
    ).fetchall()
    for row in subj_rows:
        _repoint_single_subject_assertion(conn, row, retired_id, survivor_id, s_type, now)

    obj_rows = conn.execute(
        "SELECT assertion_id, subject_node_id, predicate, legacy_predicate, ifnull(valid_from,'') AS vf, ifnull(valid_to,'') AS vt, scope_text "
        "FROM assertion WHERE object_node_id=? AND currentness_state='current'",
        (retired_id,),
    ).fetchall()
    for row in obj_rows:
        _repoint_single_object_assertion(conn, row, retired_id, survivor_id, s_type, now)


def _repoint_from_edge(
    conn: sqlite3.Connection,
    e: Any,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    eid, to_nid, etype = e["edge_id"], e["to_node_id"], e["edge_type"]
    tot = conn.execute("SELECT node_type FROM node WHERE node_id=?", (to_nid,)).fetchone()
    tot_type = tot[0] if tot else None
    if not _check_domain_range(conn, etype, s_type, tot_type):
        te = conn.execute(
            "SELECT edge_id FROM edge WHERE from_node_id=? AND edge_id!=? AND superseded_by IS NULL LIMIT 1",
            (survivor_id, eid),
        ).fetchone()
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=?", (te[0] if te else None, now, eid))
        return

    coll = conn.execute(
        "SELECT edge_id FROM edge WHERE from_node_id=? AND edge_type=? AND to_node_id=? AND ifnull(description,'')=ifnull(?,'') AND evidence_posture=? AND confidence=? AND materiality=? AND ifnull(validation_priority,'')=ifnull(?,'') AND ifnull(validation_owner,'')=ifnull(?,'') AND hitl_required=? AND source_id=? AND superseded_by IS NULL",
        (survivor_id, etype, to_nid, e["description"], e["evidence_posture"], e["confidence"], e["materiality"], e["validation_priority"], e["validation_owner"], e["hitl_required"], e["source_id"]),
    ).fetchone()
    if coll:
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=?", (coll[0], now, eid))
    else:
        conn.execute("UPDATE edge SET from_node_id=?, updated_at=? WHERE edge_id=?", (survivor_id, now, eid))


def _repoint_to_edge(
    conn: sqlite3.Connection,
    e: Any,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    eid, from_nid, etype = e["edge_id"], e["from_node_id"], e["edge_type"]
    fnt = conn.execute("SELECT node_type FROM node WHERE node_id=?", (from_nid,)).fetchone()
    fnt_type = fnt[0] if fnt else None
    if not _check_domain_range(conn, etype, fnt_type, s_type):
        te = conn.execute(
            "SELECT edge_id FROM edge WHERE to_node_id=? AND edge_id!=? AND superseded_by IS NULL LIMIT 1",
            (survivor_id, eid),
        ).fetchone()
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=?", (te[0] if te else None, now, eid))
        return

    coll = conn.execute(
        "SELECT edge_id FROM edge WHERE from_node_id=? AND edge_type=? AND to_node_id=? AND ifnull(description,'')=ifnull(?,'') AND evidence_posture=? AND confidence=? AND materiality=? AND ifnull(validation_priority,'')=ifnull(?,'') AND ifnull(validation_owner,'')=ifnull(?,'') AND hitl_required=? AND source_id=? AND superseded_by IS NULL",
        (from_nid, etype, survivor_id, e["description"], e["evidence_posture"], e["confidence"], e["materiality"], e["validation_priority"], e["validation_owner"], e["hitl_required"], e["source_id"]),
    ).fetchone()
    if coll:
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=?", (coll[0], now, eid))
    else:
        conn.execute("UPDATE edge SET to_node_id=?, updated_at=? WHERE edge_id=?", (survivor_id, now, eid))


def repoint_node_edges(
    conn: sqlite3.Connection,
    retired_id: str,
    survivor_id: str,
    s_type: str,
    now: str,
) -> None:
    """Repoints all edge entries connected to retired node to survivor node."""
    if not _table_exists(conn, "edge"):
        return

    # Handle self-loops between survivor and retired
    for sle in conn.execute(
        "SELECT edge_id FROM edge WHERE ((from_node_id=? AND to_node_id=?) OR (from_node_id=? AND to_node_id=?)) AND superseded_by IS NULL",
        (survivor_id, retired_id, retired_id, survivor_id),
    ).fetchall():
        te = conn.execute(
            "SELECT edge_id FROM edge WHERE from_node_id=? AND edge_id!=? AND superseded_by IS NULL LIMIT 1",
            (survivor_id, sle["edge_id"]),
        ).fetchone()
        conn.execute("UPDATE edge SET superseded_by=?, updated_at=? WHERE edge_id=?", (te[0] if te else None, now, sle["edge_id"]))

    for e in conn.execute("SELECT * FROM edge WHERE from_node_id=? AND superseded_by IS NULL", (retired_id,)).fetchall():
        _repoint_from_edge(conn, e, retired_id, survivor_id, s_type, now)

    for e in conn.execute("SELECT * FROM edge WHERE to_node_id=? AND superseded_by IS NULL", (retired_id,)).fetchall():
        _repoint_to_edge(conn, e, retired_id, survivor_id, s_type, now)


def _repoint_node_auxiliary_tables(
    conn: sqlite3.Connection, retired_id: str, survivor_id: str, now: str
) -> None:
    if _table_exists(conn, "object_facet_assignment"):
        ofa_cols = {r[1] for r in conn.execute("PRAGMA table_info(object_facet_assignment)").fetchall()}
        if {"assignment_id", "object_type", "object_id", "active", "retired_at"}.issubset(ofa_cols):
            conn.execute(
                """INSERT OR IGNORE INTO object_facet_assignment (
                       assignment_id, object_type, object_id, vocabulary, value,
                       assignment_method, review_state, derivation_rule_id, derivation_rule_version,
                       rationale, approval_reference, active, created_at
                   )
                   SELECT assignment_id || '-surv', object_type, ?, vocabulary, value,
                          assignment_method, review_state, derivation_rule_id, derivation_rule_version,
                          rationale, approval_reference, 1, ?
                     FROM object_facet_assignment
                    WHERE object_type='node' AND object_id=? AND active=1""",
                (survivor_id, now, retired_id),
            )
            conn.execute(
                "UPDATE object_facet_assignment SET active=0, retired_at=? WHERE object_type='node' AND object_id=? AND active=1",
                (now, retired_id),
            )
    if _table_exists(conn, "validation_queue"):
        vq_cols = {r[1] for r in conn.execute("PRAGMA table_info(validation_queue)").fetchall()}
        if "node_id" in vq_cols:
            conn.execute("UPDATE OR IGNORE validation_queue SET node_id=? WHERE node_id=?", (survivor_id, retired_id))
            conn.execute("DELETE FROM validation_queue WHERE node_id=?", (retired_id,))
    if _table_exists(conn, "search_document"):
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
        if {"currentness_state", "retrieval_eligible", "object_type", "object_id"}.issubset(sd_cols):
            conn.execute(
                "UPDATE search_document SET canonical_status='historical', currentness_state='historical', retrieval_eligible=0, updated_at=? WHERE object_type='node' AND object_id=?",
                (now, retired_id),
            )


def _collect_retired_alias_names(conn: sqlite3.Connection, retired_id: str, survivor_id: str) -> list[str]:
    if not _table_exists(conn, "node"):
        return []
    ncols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}
    if "canonical_name" not in ncols:
        return []
    disp_expr = "display_name" if "display_name" in ncols else "canonical_name"
    nrow = conn.execute("SELECT canonical_name, " + disp_expr + " FROM node WHERE node_id=?", (retired_id,)).fetchone()
    srow = conn.execute("SELECT canonical_name FROM node WHERE node_id=?", (survivor_id,)).fetchone()
    surv_cname = str(srow[0] if srow and srow[0] else "").strip().lower()
    if not nrow:
        return []
    raw_names = [str(nm).strip() for nm in (nrow[0], nrow[1]) if nm]
    return list(dict.fromkeys(s for s in raw_names if s and s.lower() != surv_cname))


def _insert_preserved_aliases(conn: sqlite3.Connection, retired_id: str, survivor_id: str, names: list[str], now: str) -> None:
    acols = {r[1] for r in conn.execute("PRAGMA table_info(alias)").fetchall()}
    full_cols = {"normalized_alias", "alias_type", "active", "created_at"}.issubset(acols)
    if {"alias_id", "node_id", "alias_name"}.issubset(acols):
        for idx, nm in enumerate(names):
            aid = f"alias-merged-{retired_id}-{idx}"
            if full_cols:
                conn.execute("INSERT OR IGNORE INTO alias (alias_id, node_id, alias_name, normalized_alias, alias_type, active, created_at) VALUES (?, ?, ?, ?, 'merged_canonical', 1, ?)", (aid, survivor_id, nm, nm.lower(), now))
            else:
                conn.execute("INSERT OR IGNORE INTO alias (alias_id, node_id, alias_name) VALUES (?, ?, ?)", (aid, survivor_id, nm))
    conn.execute("UPDATE OR IGNORE alias SET node_id=? WHERE node_id=?", (survivor_id, retired_id))
    conn.execute("DELETE FROM alias WHERE node_id=?", (retired_id,))


def _insert_preserved_canonical_aliases(conn: sqlite3.Connection, retired_id: str, survivor_id: str, names: list[str], now: str) -> None:
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(canonical_alias)").fetchall()}
    if {"alias_id", "entity_id", "alias_key", "display_alias", "alias_type", "active", "created_at"}.issubset(ccols):
        for idx, nm in enumerate(names):
            cid = f"calias-merged-{retired_id}-{idx}"
            conn.execute("INSERT OR IGNORE INTO canonical_alias (alias_id, entity_id, alias_key, display_alias, alias_type, active, created_at) VALUES (?, ?, ?, ?, 'merged_canonical', 1, ?)", (cid, survivor_id, nm.lower(), nm, now))
    conn.execute("UPDATE OR IGNORE canonical_alias SET entity_id=? WHERE entity_id=?", (survivor_id, retired_id))
    conn.execute("DELETE FROM canonical_alias WHERE entity_id=?", (retired_id,))


def _preserve_retired_names_as_aliases(
    conn: sqlite3.Connection, retired_id: str, survivor_id: str, now: str
) -> None:
    names = _collect_retired_alias_names(conn, retired_id, survivor_id)
    if _table_exists(conn, "alias"):
        _insert_preserved_aliases(conn, retired_id, survivor_id, names, now)
    if _table_exists(conn, "canonical_alias"):
        _insert_preserved_canonical_aliases(conn, retired_id, survivor_id, names, now)


def _fetch_survivor_alias_str(conn: sqlite3.Connection, survivor_id: str) -> str:
    if not _table_exists(conn, "alias"):
        return ""
    acols = {r[1] for r in conn.execute("PRAGMA table_info(alias)").fetchall()}
    acol = "alias_name" if "alias_name" in acols else ("alias" if "alias" in acols else None)
    if not acol:
        return ""
    arows = conn.execute("SELECT " + acol + " FROM alias WHERE node_id=?", (survivor_id,)).fetchall()
    return " ".join(str(r[0]) for r in arows if r and r[0])


def _insert_updated_node_fts_row(conn: sqlite3.Connection, survivor_id: str, row: Any, alias_str: str, nfts_cols: set[str]) -> None:
    conn.execute("DELETE FROM node_fts WHERE node_id=?", (survivor_id,))
    if "aliases" in nfts_cols and "node_type" in nfts_cols:
        conn.execute(
            "INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type) VALUES (?, ?, ?, ?, ?)",
            (survivor_id, row[0] or "", row[2] or "", alias_str, row[3] or "stakeholder"),
        )
    elif "display_name" in nfts_cols:
        conn.execute(
            "INSERT INTO node_fts (node_id, canonical_name, display_name, description) VALUES (?, ?, ?, ?)",
            (survivor_id, row[0] or "", row[1] or row[0] or "", row[2] or ""),
        )


def _sync_node_fts_after_merge(conn: sqlite3.Connection, retired_id: str, survivor_id: str) -> None:
    if not _table_exists(conn, "node_fts"):
        return
    conn.execute("DELETE FROM node_fts WHERE node_id=?", (retired_id,))
    if not _table_exists(conn, "node"):
        return
    nfts_cols = {r[1] for r in conn.execute("PRAGMA table_info(node_fts)").fetchall()}
    ncols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}
    name_col = "canonical_name" if "canonical_name" in ncols else "name"
    disp_col = "display_name" if "display_name" in ncols else name_col
    desc_col = "description" if "description" in ncols else "''"
    type_col = "node_type" if "node_type" in ncols else "'stakeholder'"
    row = conn.execute(
        "SELECT " + ", ".join((name_col, disp_col, desc_col, type_col)) + " FROM node WHERE node_id=?",
        (survivor_id,),
    ).fetchone()
    if row:
        _insert_updated_node_fts_row(conn, survivor_id, row, _fetch_survivor_alias_str(conn, survivor_id), nfts_cols)


def repoint_node_entities_and_routes(
    conn: sqlite3.Connection,
    retired_id: str,
    survivor_id: str,
    now: str,
) -> None:
    """Repoints claims, routes, aliases, and lifecycle states for merged nodes."""
    if _table_exists(conn, "claim"):
        conn.execute("UPDATE claim SET subject_node_id=?, updated_at=? WHERE subject_node_id=?", (survivor_id, now, retired_id))
    if _table_exists(conn, "canonical_route"):
        if conn.execute("SELECT 1 FROM canonical_route WHERE object_type='node' AND object_id=? AND active=1 AND is_primary=1", (survivor_id,)).fetchone():
            conn.execute("DELETE FROM canonical_route WHERE object_type='node' AND object_id=?", (retired_id,))
        else:
            conn.execute("UPDATE canonical_route SET object_id=? WHERE object_type='node' AND object_id=?", (survivor_id, retired_id))
    _preserve_retired_names_as_aliases(conn, retired_id, survivor_id, now)
    conn.execute("UPDATE node SET active=0, status='merged', updated_at=? WHERE node_id=?", (now, retired_id))
    if _table_exists(conn, "node_state"):
        conn.execute(
            "UPDATE node_state SET lifecycle_state='inactive', currentness_state='historical', retrieval_eligibility='historical_only', resolution_state='superseded', updated_at=? WHERE node_id=?",
            (now, retired_id),
        )
    _repoint_node_auxiliary_tables(conn, retired_id, survivor_id, now)
    _sync_node_fts_after_merge(conn, retired_id, survivor_id)
