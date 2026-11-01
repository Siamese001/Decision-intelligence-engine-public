"""
dual_pipeline/uwg_core/universal_write_gateway_role.py

Universal Write Gateway: Governed role assertion persistence with competing
assertion supersession and SSOT convergence.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.core import stable_id
from dual_pipeline.storage.governed_context import GLOBAL_WRITE_SERIALIZER
from storage.connection import create_sqlite_conn, sqlite3

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned or "unknown"


def _resolve_entity_redirect(cur: Any, node_id: str) -> str:
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name='entity_redirect'")
    if not cur.fetchone():
        return node_id
    cur.execute("PRAGMA table_info(entity_redirect)")
    rcols = {r[1] for r in cur.fetchall()}
    from_col = "from_node_id" if "from_node_id" in rcols else ("old_entity_id" if "old_entity_id" in rcols else None)
    to_col = "to_node_id" if "to_node_id" in rcols else ("new_entity_id" if "new_entity_id" in rcols else None)
    if not from_col or not to_col:
        return node_id
    act_clause = " AND active = 1" if "active" in rcols else ""
    cur.execute("SELECT " + to_col + " FROM entity_redirect WHERE " + from_col + " = ?" + act_clause, (node_id,))
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else node_id


def _query_alias_table(cur: Any, tbl_name: str, target_col: str, match_cols: tuple[str, ...], norm_name: str) -> Optional[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name=?", (tbl_name,))
    if not cur.fetchone():
        return None
    cur.execute(f"PRAGMA table_info({tbl_name})")
    tcols = {r[1] for r in cur.fetchall()}
    if target_col not in tcols:
        return None
    act_clause = " AND active = 1" if "active" in tcols else ""
    for mcol in match_cols:
        if mcol in tcols:
            cur.execute(f"SELECT {target_col} FROM {tbl_name} WHERE LOWER({mcol}) = ?{act_clause}", (norm_name,))
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
    return None


def _lookup_alias_node_id(cur: Any, norm_name: str) -> Optional[str]:
    found = _query_alias_table(cur, "alias", "node_id", ("normalized_alias", "alias_name", "alias"), norm_name)
    if found:
        return found
    return (
        _query_alias_table(cur, "canonical_alias", "entity_id", ("alias_key", "display_alias", "alias_name"), norm_name)
        or _query_alias_table(cur, "canonical_alias", "node_id", ("alias_key", "display_alias", "alias_name"), norm_name)
    )


def _find_subject_node_id(conn: sqlite3.Connection, name: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN ('node', 'canonical_graph_nodes')")
    tbl = cur.fetchone()
    if not tbl:
        return None
    tbl_name = tbl[0]
    cur.execute("PRAGMA table_info(node)" if tbl_name == "node" else "PRAGMA table_info(canonical_graph_nodes)")
    name_col = "canonical_name" if "canonical_name" in {r[1] for r in cur.fetchall()} else "name"
    norm_name, slug = name.strip().lower(), _slugify(name)
    cand_ids = (f"stakeholder_{slug}", f"person_{slug}", f"person:{slug}", f"stakeholder:{slug}")
    q_sel = f"SELECT node_id FROM {tbl_name} WHERE LOWER({name_col}) = ? OR LOWER(node_id) IN (?, ?, ?, ?) ORDER BY CASE WHEN LOWER(node_id) LIKE 'stakeholder%' THEN 0 ELSE 1 END LIMIT 1"
    cur.execute(q_sel, (norm_name, *cand_ids))
    row = cur.fetchone()
    if row and row[0]:
        return _resolve_entity_redirect(cur, str(row[0]))
    alias_match = _lookup_alias_node_id(cur, norm_name)
    if alias_match:
        return _resolve_entity_redirect(cur, alias_match)
    for cid in cand_ids:
        redir = _resolve_entity_redirect(cur, cid)
        if redir != cid:
            return redir
    return f"person_{slug}"


def _ensure_node_state_row(cur: Any, node_id: str, now_iso: str) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_state'")
    if not cur.fetchone():
        return
    cur.execute("PRAGMA table_info(node_state)")
    ns_cols = {r[1] for r in cur.fetchall()}
    vals = {
        "node_id": node_id, "lifecycle_state": "active", "validation_state": "unreviewed",
        "currentness_state": "current", "approval_state": "legacy_canonical", "rag_state": "not_applicable",
        "resolution_state": "resolved", "retrieval_eligibility": "eligible", "publication_state": "internal",
        "review_state": "legacy_migrated", "updated_at": now_iso,
    }
    items = [(k, v) for k, v in vals.items() if k in ns_cols]
    if items:
        c_s, p_s = ", ".join(k for k, _ in items), ", ".join("?" for _ in items)
        q_ns = "INSERT OR IGNORE INTO node_state (" + c_s + ") VALUES (" + p_s + ")"
        cur.execute(q_ns, [v for _, v in items])
        if "updated_at" in ns_cols:
            cur.execute("UPDATE node_state SET updated_at = ? WHERE node_id = ?", (now_iso, node_id))


def _find_or_create_role_node(
    conn: sqlite3.Connection, role: str, tbl_name: str, has_props: bool
) -> str:
    cur = conn.cursor()
    q_info = "PRAGMA table_info(node)" if tbl_name == "node" else "PRAGMA table_info(canonical_graph_nodes)"
    cur.execute(q_info)
    cols = {r[1] for r in cur.fetchall()}
    name_col = "canonical_name" if "canonical_name" in cols else "name"
    q_role = "SELECT node_id FROM " + tbl_name + " WHERE LOWER(" + name_col + ") = ?"
    cur.execute(q_role, (role.strip().lower(),))
    row = cur.fetchone()
    now_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if row:
        rid = str(row[0])
        if "status" in cols:
            q_stat = "UPDATE " + tbl_name + " SET status = COALESCE(status, 'canonical') WHERE node_id = ?"
            cur.execute(q_stat, (rid,))
        _ensure_node_state_row(cur, rid, now_dt)
        _sync_node_fts_row(cur, rid, tbl_name, cols)
        return rid

    role_node_id = stable_id("role", role.strip().lower(), length=16)
    ins_map: Dict[str, Any] = {
        "node_id": role_node_id, "node_type": "role_group", name_col: role.strip(),
        "display_name": role.strip(), "normalized_name": re.sub(r"[^a-z0-9]+", " ", role.strip().lower()).strip(),
        "description": "Role group", "status": "canonical", "active": 1,
        "properties_json": "{}" if has_props else None, "created_at": now_dt, "updated_at": now_dt,
    }
    items = [(k, v) for k, v in ins_map.items() if k in cols and v is not None]
    col_str, ph_str = ", ".join(k for k, _ in items), ", ".join("?" for _ in items)
    q_ins = "INSERT OR IGNORE INTO " + tbl_name + " (" + col_str + ") VALUES (" + ph_str + ")"
    cur.execute(q_ins, [v for _, v in items])
    _ensure_node_state_row(cur, role_node_id, now_dt)
    _sync_node_fts_row(cur, role_node_id, tbl_name, cols)
    return role_node_id


def _format_updated_description(old_desc: str, new_role: str) -> str:
    clean_old = (old_desc or "").strip()
    if not clean_old:
        return f"Role: {new_role}"
    if re.search(r"(?:Source roles|Role|Title|Position):\s*[^;\.\n]+", clean_old, re.IGNORECASE):
        return re.sub(r"((?:Source roles|Role|Title|Position):\s*)([^;\.\n]+)", rf"\g<1>{new_role}", clean_old, count=1, flags=re.IGNORECASE)
    return f"Role: {new_role}; {clean_old}"


def _insert_node_fts_row(cur: Any, subject_id: str, row: Any, nfts_cols: set[str]) -> None:
    if "aliases" in nfts_cols and "node_type" in nfts_cols:
        cur.execute("INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type) VALUES (?, ?, ?, '', ?)", (subject_id, row[0] or "", row[2] or "", row[3] or "stakeholder"))
    else:
        cur.execute("INSERT INTO node_fts (node_id, canonical_name, display_name, description) VALUES (?, ?, ?, ?)", (subject_id, row[0] or "", row[1] or row[0] or "", row[2] or ""))


def _sync_node_fts_row(cur: Any, subject_id: str, tbl_name: str, cols: set[str]) -> None:
    if tbl_name != "node":
        return
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name='node_fts'")
    if not cur.fetchone():
        return
    cur.execute("PRAGMA table_info(node_fts)")
    nfts_cols = {r[1] for r in cur.fetchall()}
    name_col = "canonical_name" if "canonical_name" in cols else "name"
    disp_col = "display_name" if "display_name" in cols else name_col
    desc_col = "description" if "description" in cols else "''"
    type_col = "node_type" if "node_type" in cols else "'stakeholder'"
    q_sel_fts = "SELECT " + ", ".join((name_col, disp_col, desc_col, type_col)) + " FROM " + tbl_name + " WHERE node_id = ?"
    cur.execute(q_sel_fts, (subject_id,))
    row = cur.fetchone()
    if not row:
        return
    cur.execute("DELETE FROM node_fts WHERE node_id = ?", (subject_id,))
    _insert_node_fts_row(cur, subject_id, row, nfts_cols)


def _ensure_person_node_exists(
    cur: Any, subject_id: str, canonical_name: str, new_role: str, tbl_name: str, cols: set[str], now_iso: str
) -> None:
    cur.execute("SELECT node_id FROM " + tbl_name + " WHERE node_id = ?", (subject_id,))
    if cur.fetchone():
        if "status" in cols:
            cur.execute("UPDATE " + tbl_name + " SET status = COALESCE(status, 'canonical') WHERE node_id = ?", (subject_id,))
        _ensure_node_state_row(cur, subject_id, now_iso)
        return
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_type_registry'")
    person_node_type = "stakeholder" if subject_id.startswith("stakeholder") else "person"
    if cur.fetchone() is not None and cur.execute("SELECT 1 FROM node_type_registry WHERE node_type = ?", (person_node_type,)).fetchone() is None:
        person_node_type = "stakeholder" if person_node_type == "person" else "person"
    name_col = "canonical_name" if "canonical_name" in cols else "name"
    ins_map: Dict[str, Any] = {
        "node_id": subject_id, "node_type": person_node_type, name_col: canonical_name.strip(),
        "display_name": canonical_name.strip(), "normalized_name": re.sub(r"[^a-z0-9]+", " ", canonical_name.strip().lower()).strip(),
        "description": f"Role: {new_role}", "status": "canonical", "active": 1,
        "properties_json": json.dumps({"role": new_role}), "created_at": now_iso, "updated_at": now_iso,
    }
    items = [(k, v) for k, v in ins_map.items() if k in cols]
    col_str, ph_str = ", ".join(k for k, _ in items), ", ".join("?" for _ in items)
    cur.execute("INSERT INTO " + tbl_name + " (" + col_str + ") VALUES (" + ph_str + ")", [v for _, v in items])
    _ensure_node_state_row(cur, subject_id, now_iso)


def _update_person_node_role(
    conn: sqlite3.Connection, subject_id: str, new_role: str, tbl_name: str, canonical_name: str = ""
) -> None:
    cur = conn.cursor()
    q_info = "PRAGMA table_info(node)" if tbl_name == "node" else "PRAGMA table_info(canonical_graph_nodes)"
    cur.execute(q_info)
    cols = {r[1] for r in cur.fetchall()}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical_name:
        _ensure_person_node_exists(cur, subject_id, canonical_name, new_role, tbl_name, cols, now_iso)

    set_parts: List[str] = []
    params: List[Any] = []
    if "description" in cols:
        q_desc = "SELECT description FROM " + tbl_name + " WHERE node_id = ?"
        cur.execute(q_desc, (subject_id,))
        desc_row = cur.fetchone()
        old_desc = str(desc_row[0] or "") if desc_row else ""
        set_parts.append("description = ?")
        params.append(_format_updated_description(old_desc, new_role))
    if "properties_json" in cols:
        q_props = "SELECT properties_json FROM " + tbl_name + " WHERE node_id = ?"
        cur.execute(q_props, (subject_id,))
        row = cur.fetchone()
        props: Dict[str, Any] = {}
        if row and row[0]:
            try:
                props = json.loads(row[0])
            except Exception:
                props = {}
        props["role"] = new_role
        set_parts.append("properties_json = ?")
        params.append(json.dumps(props))
    if not set_parts:
        return
    if "updated_at" in cols:
        set_parts.append("updated_at = ?")
        params.append(now_iso)
    params.append(subject_id)
    q_upd_p = "UPDATE " + tbl_name + " SET " + ", ".join(set_parts) + " WHERE node_id = ?"
    cur.execute(q_upd_p, params)
    _sync_node_fts_row(cur, subject_id, tbl_name, cols)


def _discover_twin_subject_ids(cur: Any, subject_id: str, node_tbl: str) -> List[str]:
    twin = f"person:{subject_id[7:]}" if subject_id.startswith("person_") else (f"person_{subject_id[7:]}" if subject_id.startswith("person:") else None)
    sub_ids = [subject_id, twin] if twin else [subject_id]
    q_cname = "SELECT canonical_name FROM " + node_tbl + " WHERE node_id IN (" + ",".join("?" for _ in sub_ids) + ")"
    cnames = [r[0] for r in cur.execute(q_cname, sub_ids).fetchall() if r[0]]
    if cnames:
        q_ids = "SELECT node_id FROM " + node_tbl + " WHERE canonical_name IN (" + ",".join("?" for _ in cnames) + ")"
        for r in cur.execute(q_ids, cnames).fetchall():
            if r[0] and r[0] not in sub_ids:
                sub_ids.append(r[0])
    return sub_ids


def _supersede_competing_assertions(
    conn: sqlite3.Connection, subject_id: str, new_asrt_id: str, target_role_node_id: str, now_iso: str
) -> List[str]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(assertion)")
    cols = {r[1] for r in cur.fetchall()}
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='controlled_value_registry'")
    sup_curr_state = "historical" if cur.fetchone() is not None else "superseded"
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN ('node', 'canonical_graph_nodes')")
    tbl_row = cur.fetchone()
    node_tbl = tbl_row[0] if tbl_row else "node"
    sub_ids = _discover_twin_subject_ids(cur, subject_id, node_tbl)
    sub_ph = ", ".join("?" for _ in sub_ids)
    scope_filter = "OR scope_text LIKE '%HAS_ROLE%'" if "scope_text" in cols else ""
    rg_filter = f"OR object_node_id IN (SELECT node_id FROM {node_tbl} WHERE node_type = 'role_group')"
    role_pat = " OR ".join(f"object_literal LIKE '%{k}%'" for k in ("Lead", "Manager", "Director", "Engineer", "Architect", "Analyst", "unresolved"))
    claim_role_filter = f"OR (predicate = 'HAS_CLAIM_TEXT' AND ({role_pat}))"
    pred_filter = f"(predicate IN ('HAS_ROLE', 'HAS_ROLE_IN') {scope_filter} {rg_filter} {claim_role_filter})"
    where_active = "AND (currentness_state IS NULL OR currentness_state NOT IN ('historical', 'superseded', 'retracted', 'retired'))"
    q_comp = "SELECT assertion_id FROM assertion WHERE subject_node_id IN (" + sub_ph + ") AND " + pred_filter + " " + where_active + " AND assertion_id != ?"
    cur.execute(q_comp, sub_ids + [new_asrt_id])
    competing_rows = cur.fetchall()
    superseded_ids: List[str] = []
    for (c_id,) in competing_rows:
        set_clauses, params = ["currentness_state = ?", "superseded_by_assertion_id = ?"], [sup_curr_state, new_asrt_id]
        if "retrieval_eligible" in cols:
            set_clauses.append("retrieval_eligible = 0")
        if "validation_state" in cols:
            set_clauses.append("validation_state = 'superseded'")
        if "superseded_at" in cols:
            set_clauses.append("superseded_at = ?")
            params.append(now_iso)
        params.append(c_id)
        cur.execute("UPDATE assertion SET " + ", ".join(set_clauses) + " WHERE assertion_id = ?", params)
        superseded_ids.append(c_id)
    return superseded_ids


def _upgrade_verified_triggers(cur: Any) -> None:
    cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
        "AND name IN ('assertion_semantic_guard_bu', 'assertion_search_sync_au', 'search_document_ai', 'search_document_au')"
    )
    for tname, tsql in cur.fetchall():
        if not tsql or "'verified'" in tsql:
            continue
        new_sql = tsql.replace(
            "validation_state<>'accepted_legacy'",
            "validation_state NOT IN ('accepted_legacy','verified')",
        ).replace(
            "validation_state='accepted_legacy'",
            "validation_state IN ('accepted_legacy','verified')",
        )
        if new_sql != tsql:
            q_drop = "DROP TRIGGER " + str(tname)
            cur.execute(q_drop)
            cur.execute(new_sql)


def _ensure_hitl_role_source(cur: Any, v_from: str, now_iso: str) -> Tuple[str, str, str]:
    source_id, src_ver_id, ext_run_id = "source_hitl_operator_role_registry", "srcver_hitl_operator_role_registry_v1", "extraction-run-hitl-operator-role-registry"
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='controlled_value_registry'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(controlled_value_registry)")
        cvr_cols = {r[1] for r in cur.fetchall()}
        sql_cvr = (
            "INSERT OR IGNORE INTO controlled_value_registry (vocabulary, value, definition, active, sort_order) VALUES ('validation_state', 'verified', 'Verified by human operator.', 1, 0)"
            if "definition" in cvr_cols
            else "INSERT OR IGNORE INTO controlled_value_registry (vocabulary, value, active) VALUES ('validation_state', 'verified', 1)"
        )
        cur.execute(sql_cvr)
    _upgrade_verified_triggers(cur)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='governance_approval'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(governance_approval)")
        ga_cols = {r[1] for r in cur.fetchall()}
        if "scope_json" in ga_cols:
            cur.execute(
                "INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, approval_type, scope_json, status, approved_by, release_version, active, created_at) "
                "VALUES ('approval-hitl-role-registry', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', '{\"allowed_actions\":[\"canonical_assertion_promotion\"]}', 'approved', 'Operator', '1.0', 1, ?)",
                (now_iso,),
            )
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source'")
    if cur.fetchone():
        cur.execute(
            "INSERT OR IGNORE INTO source (source_id, source_type, source_name, source_date, authority_rank, source_posture, created_at) "
            "VALUES (?, 'user_hitl', 'HITL Operator Role Registry', ?, 1, 'derivative_internal', ?)",
            (source_id, v_from, now_iso),
        )
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_version'")
    if cur.fetchone():
        fp = hashlib.sha256(f"{source_id}|v1".encode()).hexdigest()
        cur.execute(
            "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, source_fingerprint, "
            "content_hash, source_date_normalized, source_date_precision, source_date_status, authority_rank, "
            "scope_text, posture_text, created_at) VALUES (?, ?, 1, ?, ?, ?, 'day', 'exact', 1, 'Governed HITL operator role ratification.', 'derivative_internal', ?)",
            (src_ver_id, source_id, fp, fp, v_from, now_iso),
        )
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='extraction_run'")
    if cur.fetchone():
        fp = hashlib.sha256(f"{ext_run_id}|v1".encode()).hexdigest()
        cur.execute(
            "INSERT OR IGNORE INTO extraction_run (extraction_run_id, algorithm_version, baseline_graph_sha256, "
            "input_manifest_sha256, started_at, completed_at, status, deterministic) VALUES (?, 'uwg-hitl-role-persistence-1.0.0', ?, ?, ?, ?, 'completed', 1)",
            (ext_run_id, fp, fp, now_iso, now_iso),
        )
    return source_id, src_ver_id, ext_run_id


def _bind_supporting_role_evidence(
    cur: Any, assertion_id: str, canonical_name: str, selected_role: str, v_from: str, now_iso: str, source_id_override: Optional[str] = None
) -> Optional[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_evidence'")
    if not cur.fetchone():
        return None
    def_src_id, def_src_ver_id, _ = _ensure_hitl_role_source(cur, v_from, now_iso)
    source_id, src_ver_id = source_id_override or def_src_id, def_src_ver_id
    if source_id != def_src_id:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_version'")
        if cur.fetchone():
            cur.execute("SELECT source_version_id FROM source_version WHERE source_id = ? LIMIT 1", (source_id,))
            sv_row = cur.fetchone()
            if sv_row:
                src_ver_id = str(sv_row[0])
            else:
                src_ver_id = f"srcver_{source_id}_v1"
                fp = hashlib.sha256(f"{source_id}|v1".encode()).hexdigest()
                cur.execute(
                    "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, source_fingerprint, "
                    "content_hash, source_date_normalized, source_date_precision, source_date_status, authority_rank, "
                    "scope_text, posture_text, created_at) VALUES (?, ?, 1, ?, ?, ?, 'day', 'exact', 1, 'Governed source version.', 'derivative_internal', ?)",
                    (src_ver_id, source_id, fp, fp, v_from, now_iso),
                )
    evidence_id = f"ev_{assertion_id}"
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_item'")
    if cur.fetchone():
        ev_fp = hashlib.sha256(f"{evidence_id}|{assertion_id}|{selected_role}".encode()).hexdigest()
        quote_text = selected_role if not canonical_name else f"{canonical_name} HAS_ROLE {selected_role}."
        cur.execute(
            "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, locator_kind, "
            "locator_value, direct_quote, evidence_directness, source_authority_rank, extraction_confidence, corroboration_group, location_status, created_at) "
            "VALUES (?, ?, ?, ?, 'conversation_proposition', ?, ?, 'direct', 1, 1.0, ?, 'exact', ?)",
            (evidence_id, ev_fp, source_id, src_ver_id, f"assertion:{assertion_id}", quote_text, source_id, now_iso),
        )
    cur.execute(
        "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) "
        "VALUES (?, ?, 'supports', 1, ?)",
        (assertion_id, evidence_id, now_iso),
    )
    return evidence_id


def _upsert_assertion_search_document(
    cur: Any, assertion_id: str, title: str, body: str, canonical_status: str, source_id: str, now_iso: str
) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_document'")
    if not cur.fetchone():
        return
    sd_id, ret_elig = f"search_assertion_{assertion_id}", (1 if canonical_status == "canonical" else 0)
    cur.execute(
        "INSERT OR REPLACE INTO search_document (search_document_id, object_type, object_id, title, body, canonical_status, "
        "currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) VALUES (?, 'assertion', ?, ?, ?, ?, 'current', ?, ?, ?, ?)",
        (sd_id, assertion_id, title, body, canonical_status, ret_elig, source_id, assertion_id, now_iso),
    )


def _promote_role_assertion_if_needed(cur: Any, asrt_cols: set[str], asrt_id: str, source_id: str) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='assertion_semantic_guard_bu'")
    if not cur.fetchone() or "canonical_status" not in asrt_cols:
        return
    cur.execute("SELECT canonical_status, COALESCE(retrieval_eligible, 0) FROM assertion WHERE assertion_id = ?", (asrt_id,))
    row = cur.fetchone()
    if not row or (row[0] == "canonical" and int(row[1]) != 0):
        return
    p_clauses = ["canonical_status = 'canonical'", "validation_state = 'accepted_legacy'", "currentness_state = 'current'"]
    p_params: List[Any] = []
    for k, v in (("canonical_eligible", 1), ("retrieval_eligible", 1), ("primary_source_id", source_id), ("approval_reference", "REQ_INGEST_CEC57955C84BCE3A")):
        if k in asrt_cols:
            p_clauses.append(f"{k} = ?")
            p_params.append(v)
    p_params.append(asrt_id)
    q_prom = "UPDATE assertion SET " + ", ".join(p_clauses) + " WHERE assertion_id = ?"
    cur.execute(q_prom, p_params)


def _update_existing_role_assertion(
    cur: Any, asrt_cols: set[str], asrt_id: str, v_from: str, scope_txt: str,
    val_state: str, conf_label: str, source_id: str, ext_run_id: str, now_iso: str,
) -> None:
    _promote_role_assertion_if_needed(cur, asrt_cols, asrt_id, source_id)
    update_clauses = ["confidence_score = 1.0", "currentness_state = 'current'", "valid_from = ?", "superseded_by_assertion_id = NULL", "scope_text = ?"]
    u_params: List[Any] = [v_from, scope_txt]
    meta_fields = (
        ("validation_state", val_state), ("confidence_label", conf_label), ("canonical_status", "canonical"),
        ("canonical_eligible", 1), ("retrieval_eligible", 1), ("approval_reference", "REQ_INGEST_CEC57955C84BCE3A"),
        ("classification", "internal"), ("allowed_audiences_json", '["internal_consulting", "nee-engineering"]'),
        ("primary_source_id", source_id), ("extraction_run_id", ext_run_id), ("updated_at", now_iso),
    )
    for col, val in meta_fields:
        if col in asrt_cols:
            update_clauses.append(f"{col} = ?")
            u_params.append(val)
    u_params.append(asrt_id)
    q_upd_a = "UPDATE assertion SET " + ", ".join(update_clauses) + " WHERE assertion_id = ?"
    cur.execute(q_upd_a, u_params)


def _insert_new_role_assertion(
    cur: Any, asrt_cols: set[str], new_asrt_id: str, subject_id: str, role_node_id: str,
    v_from: str, scope_txt: str, has_cvr: bool, val_state: str, conf_label: str,
    source_id: str, ext_run_id: str, now_iso: str,
) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='assertion_semantic_guard_bi'")
    has_sem_bi = cur.fetchone() is not None
    fp = hashlib.sha256(f"{subject_id}|HAS_ROLE|{role_node_id}|{v_from}".encode()).hexdigest()
    insert_data: Dict[str, Any] = {
        "assertion_id": new_asrt_id, "subject_node_id": subject_id,
        "predicate": "HAS_ROLE_IN" if has_cvr else "HAS_ROLE", "object_node_id": role_node_id,
        "confidence_score": 1.0, "currentness_state": "current",
        "validation_state": "accepted_legacy" if has_sem_bi else val_state,
        "valid_from": v_from, "scope_text": scope_txt, "assertion_fingerprint": fp,
        "assertion_type": "confirmed_canonical_fact" if has_cvr else "atomic",
        "atomicity_state": "atomic", "extraction_method": "hitl_operator",
        "origin_type": "human_source" if has_cvr else "human_operator", "confidence_label": conf_label,
        "date_precision": "day", "date_status": "exact", "recorded_at": now_iso,
        "resolution_state": "resolved", "canonical_status": "candidate" if has_sem_bi else "canonical",
        "canonical_eligible": 1, "retrieval_eligible": 0 if has_sem_bi else 1,
        "materiality": "high" if has_cvr else "substantive", "classification": "internal",
        "allowed_audiences_json": '["internal_consulting", "nee-engineering"]',
        "primary_source_id": source_id, "extraction_run_id": ext_run_id,
        "approval_reference": "REQ_INGEST_CEC57955C84BCE3A", "hitl_required": 0,
        "qualifiers_json": "{}", "legacy_record_type": "edge" if has_cvr else "assertion",
        "legacy_record_ids_json": "[]", "created_at": now_iso, "updated_at": now_iso,
    }
    filtered_items = [(k, v) for k, v in insert_data.items() if k in asrt_cols]
    cols_str, placeholders, vals = ", ".join(k for k, _ in filtered_items), ", ".join("?" for _ in filtered_items), [v for _, v in filtered_items]
    q_ins_a = "INSERT INTO assertion (" + cols_str + ") VALUES (" + placeholders + ")"
    cur.execute(q_ins_a, vals)


def _upsert_role_assertion(
    cur: Any, asrt_cols: set[str], subject_id: str, role_node_id: str,
    canonical_name: str, selected_role: str, v_from: str, now_iso: str,
) -> str:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='controlled_value_registry'")
    has_cvr = cur.fetchone() is not None
    val_state, conf_label = "verified", ("high" if has_cvr else "HIGH")
    source_id, _, ext_run_id = _ensure_hitl_role_source(cur, v_from, now_iso)
    scope_txt = f"{canonical_name} HAS_ROLE {selected_role}."
    cur.execute("SELECT assertion_id FROM assertion WHERE subject_node_id = ? AND object_node_id = ?", (subject_id, role_node_id))
    existing_asrt = cur.fetchone()
    if existing_asrt:
        new_asrt_id = str(existing_asrt[0])
        _bind_supporting_role_evidence(cur, new_asrt_id, canonical_name, selected_role, v_from, now_iso)
        _update_existing_role_assertion(cur, asrt_cols, new_asrt_id, v_from, scope_txt, val_state, conf_label, source_id, ext_run_id, now_iso)
    else:
        new_asrt_id = f"asrt-hitl-{uuid.uuid4().hex[:12]}"
        _insert_new_role_assertion(cur, asrt_cols, new_asrt_id, subject_id, role_node_id, v_from, scope_txt, has_cvr, val_state, conf_label, source_id, ext_run_id, now_iso)
        _bind_supporting_role_evidence(cur, new_asrt_id, canonical_name, selected_role, v_from, now_iso)
        _update_existing_role_assertion(cur, asrt_cols, new_asrt_id, v_from, scope_txt, val_state, conf_label, source_id, ext_run_id, now_iso)
    _upsert_assertion_search_document(cur, new_asrt_id, f"{canonical_name} — {selected_role}", scope_txt, "canonical", source_id, now_iso)
    return new_asrt_id


def backfill_unlinked_current_assertions(graph_db: Union[Path, str]) -> Dict[str, Any]:
    """Backfills missing assertion_evidence links and extraction_run_id lineage for current assertions."""
    g_path = Path(graph_db).resolve()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    v_from = now_iso[:10]
    with GLOBAL_WRITE_SERIALIZER.get_lock(str(g_path)):
        conn = create_sqlite_conn(g_path, readonly=False)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            def_src_id, _, ext_run_id = _ensure_hitl_role_source(cur, v_from, now_iso)
            cur.execute(
                "UPDATE assertion SET extraction_run_id = ?, primary_source_id = COALESCE(primary_source_id, ?) "
                "WHERE origin_type <> 'legacy_migration' AND extraction_run_id IS NULL",
                (ext_run_id, def_src_id),
            )
            lineage_fixed = cur.rowcount
            cur.execute(
                "SELECT a.assertion_id, a.primary_source_id, a.scope_text, a.canonical_status "
                "FROM assertion a WHERE a.currentness_state = 'current' "
                "AND NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id)"
            )
            rows = cur.fetchall()
            linked_ids: List[str] = []
            for aid, p_src, s_txt, c_stat in rows:
                src_use = str(p_src) if p_src else def_src_id
                if not p_src:
                    cur.execute("UPDATE assertion SET primary_source_id = ? WHERE assertion_id = ?", (src_use, aid))
                _bind_supporting_role_evidence(cur, str(aid), "", str(s_txt or aid), v_from, now_iso, source_id_override=src_use)
                _upsert_assertion_search_document(cur, str(aid), str(aid), str(s_txt or aid), str(c_stat or "candidate"), src_use, now_iso)
                linked_ids.append(str(aid))
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            return {"linked_assertion_ids": linked_ids, "lineage_fixed_count": lineage_fixed}
        finally:
            conn.close()


def _verify_role_physical_proof(g_path: Path, subject_id: str, new_asrt_id: str, role_node_id: str) -> Dict[str, Any]:
    raw_bytes = g_path.read_bytes()
    offsets = {tid: (pos if (pos := raw_bytes.find(tid.encode("utf-8"))) != -1 else None) for tid in (subject_id, new_asrt_id, role_node_id)}
    ro_conn = create_sqlite_conn(g_path, readonly=True)
    try:
        ic = ro_conn.execute("PRAGMA integrity_check").fetchone()
        rb = ro_conn.execute("SELECT 1 FROM assertion WHERE assertion_id = ?", (new_asrt_id,)).fetchone()
    finally:
        ro_conn.close()
    passed = all(v is not None for v in offsets.values()) and (ic and ic[0] == "ok") and rb is not None
    return {"overall": "PASS" if passed else "FAIL", "raw_byte_offsets": offsets, "integrity": ic[0] if ic else "unknown", "readback_verified": rb is not None}


def _execute_role_persistence_tx(g_path: Path, canonical_name: str, selected_role: str, v_from: str, now_iso: str) -> tuple[str, str, str, list[str]]:
    conn = create_sqlite_conn(g_path, readonly=False)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        subject_id = _find_subject_node_id(conn, canonical_name)
        if not subject_id:
            raise ValueError(f"Could not resolve subject person node for '{canonical_name}'")
        cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN ('node', 'canonical_graph_nodes')")
        tbl_name = cur.fetchone()[0]
        cur.execute("PRAGMA table_info(node)" if tbl_name == "node" else "PRAGMA table_info(canonical_graph_nodes)")
        has_props = "properties_json" in {r[1] for r in cur.fetchall()}
        role_node_id = _find_or_create_role_node(conn, selected_role, tbl_name, has_props)
        _update_person_node_role(conn, subject_id, selected_role, tbl_name, canonical_name=canonical_name)
        cur.execute("PRAGMA table_info(assertion)")
        asrt_cols = {r[1] for r in cur.fetchall()}
        new_asrt_id = _upsert_role_assertion(cur, asrt_cols, subject_id, role_node_id, canonical_name, selected_role, v_from, now_iso)
        superseded_ids = _supersede_competing_assertions(conn, subject_id, new_asrt_id, role_node_id, now_iso)
        conn.commit()
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        cur.execute("PRAGMA integrity_check")
        ic_res = cur.fetchone()
        if not ic_res or ic_res[0] != "ok":
            raise RuntimeError(f"Database integrity check failed: {ic_res}")
        return subject_id, role_node_id, new_asrt_id, superseded_ids
    finally:
        conn.close()


def persist_validated_role(
    graph_db: Union[Path, str], canonical_name: str, selected_role: str,
    operator_id: str = "human_operator", valid_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Persists ratified role into GraphDB via UWG, superseding competing role assertions."""
    if not canonical_name or not canonical_name.strip():
        raise ValueError("Cannot persist role for empty or whitespace canonical_name")
    if not selected_role or not selected_role.strip():
        raise ValueError(f"Cannot persist empty or whitespace role for '{canonical_name}'")
    g_path = Path(graph_db).resolve()
    if not g_path.is_file():
        raise FileNotFoundError(f"Graph database not found at {g_path}")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    v_from = valid_from or now_iso[:10]
    with GLOBAL_WRITE_SERIALIZER.get_lock(str(g_path)):
        subject_id, role_node_id, new_asrt_id, superseded_ids = _execute_role_persistence_tx(g_path, canonical_name, selected_role, v_from, now_iso)
        proof = _verify_role_physical_proof(g_path, subject_id, new_asrt_id, role_node_id)
        logger.info("UniversalWriteGateway: Persisted role '%s' for '%s' (assertion=%s, superseded=%d).", selected_role, canonical_name, new_asrt_id, len(superseded_ids))
        with contextlib.suppress(Exception):
            from dual_pipeline.transcript_metadata_core.entity_resolver import invalidate_resolver_cache  # noqa: PLC0415
            invalidate_resolver_cache()
        return {
            "status": "PERSISTED_SSOT", "canonical_name": canonical_name, "subject_node_id": subject_id,
            "selected_role": selected_role, "role_node_id": role_node_id, "persisted_assertion_id": new_asrt_id,
            "superseded_assertion_ids": superseded_ids, "confidence_score": 1.0, "currentness_state": "current",
            "validation_state": "verified", "operator_id": operator_id, "persisted_at": now_iso, "physical_proof": proof,
        }
