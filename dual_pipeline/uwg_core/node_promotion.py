"""Helper for promoting candidate endpoint nodes during Universal Write Gateway candidate promotion."""
from __future__ import annotations

from typing import Any

from dual_pipeline.core import stable_id


def _index_promoted_node(connection: Any, linked_node_id: str, now: str) -> None:
    node_cols = {r[1] for r in connection.execute("PRAGMA table_info(node)").fetchall()}
    if not ({"node_id", "node_type", "canonical_name", "description"} <= node_cols):
        return
    node_row = connection.execute(
        "SELECT node_id, node_type, canonical_name, description FROM node WHERE node_id=?",
        (linked_node_id,),
    ).fetchone()
    if not node_row:
        return
    sd_cols = {r[1] for r in connection.execute("PRAGMA table_info(search_document)").fetchall()}
    required = {
        "search_document_id", "object_type", "object_id", "title", "body",
        "canonical_status", "currentness_state", "retrieval_eligible", "updated_at",
    }
    if not (required <= sd_cols):
        return
    title = str(node_row["canonical_name"] or linked_node_id)
    body = f"{title}: {node_row['description'] or ''}".strip()
    existing_sd = connection.execute(
        "SELECT search_document_id FROM search_document WHERE object_type='node' AND object_id=?",
        (linked_node_id,),
    ).fetchone()
    if existing_sd:
        connection.execute(
            """UPDATE search_document
               SET title=?, body=?, canonical_status='canonical',
                   currentness_state='current', retrieval_eligible=1, updated_at=?
               WHERE object_type='node' AND object_id=?""",
            (title, body, now, linked_node_id),
        )
    else:
        sd_id = stable_id("search-node", linked_node_id, length=28)
        connection.execute(
            """INSERT OR IGNORE INTO search_document
               (search_document_id, object_type, object_id, title, body,
                canonical_status, currentness_state, retrieval_eligible, updated_at)
               VALUES (?, 'node', ?, ?, ?, 'canonical', 'current', 1, ?)""",
            (sd_id, linked_node_id, title, body, now),
        )
    has_fts = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone() is not None
    has_ai_trig = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='search_document_ai'"
    ).fetchone() is not None
    if has_fts and not has_ai_trig:
        connection.execute(
            """INSERT INTO search_fts (rowid, object_id, object_type, title, body)
               SELECT sd.rowid, sd.object_id, sd.object_type, sd.title, sd.body
               FROM search_document sd
               WHERE sd.object_type='node' AND sd.object_id=?
                 AND sd.rowid NOT IN (SELECT rowid FROM search_fts)""",
            (linked_node_id,),
        )


_UNVERIFIED_ID_TOKENS = ("speaker_", "speaker-", "unresolved", "undiarized", "session_context", "session-context")
_UNVERIFIED_MARKERS = ("UNVERIFIED", "UNDIARIZED_QUARANTINED", "SPEAKER_COLLAPSE")


def _has_unverified_assertion_fields(before: Any) -> bool:
    keys = before.keys() if hasattr(before, "keys") else ()
    for k in ("confidence_reason", "provenance_summary", "canonical_status", "attribution_provenance_tier"):
        if k in keys:
            val_up = str(before[k] or "").upper()
            if any(bad in val_up for bad in _UNVERIFIED_MARKERS):
                return True
    return False


def _has_unverified_node_fields(connection: Any, linked_node_id: str) -> bool:
    row = connection.execute(
        "SELECT canonical_name, description FROM node WHERE node_id=?",
        (linked_node_id,),
    ).fetchone()
    if not row:
        return False
    cname = str(row["canonical_name"] or "").lower()
    desc = str(row["description"] or "").upper()
    return cname.startswith(("speaker ", "speaker_", "voice ")) or "UNVERIFIED_SPEAKER" in desc or "UNDIARIZED_QUARANTINED" in desc


def _has_unverified_speaker_provenance(connection: Any, linked_node_id: str, before: Any, has_node: bool) -> bool:
    """Returns True if a person: node or its backing assertion carries unverified/undiarized speaker attribution."""
    if not str(linked_node_id).startswith("person:"):
        return False
    low_id = str(linked_node_id).lower()
    if any(tok in low_id for tok in _UNVERIFIED_ID_TOKENS):
        return True
    if _has_unverified_assertion_fields(before):
        return True
    return bool(has_node and _has_unverified_node_fields(connection, linked_node_id))


def _node_has_status_columns(connection: Any, has_node: bool) -> bool:
    if not has_node:
        return False
    node_cols = {r[1] for r in connection.execute("PRAGMA table_info(node)").fetchall()}
    return {"status", "updated_at"} <= node_cols


def _node_state_columns(connection: Any, has_state: bool) -> set[str]:
    if not has_state:
        return set()
    return {str(r[1]) for r in connection.execute("PRAGMA table_info(node_state)").fetchall()}


def _update_single_node_state(
    connection: Any, ns_cols: set[str], validation_state: str, now: str, linked_node_id: str
) -> None:
    if not ({"node_id", "validation_state", "approval_state", "retrieval_eligibility", "legacy_status_text", "updated_at"} <= ns_cols):
        return
    if {"rag_state", "currentness_state"} <= ns_cols:
        connection.execute(
            """UPDATE node_state
               SET validation_state=?, approval_state='approved',
                   rag_state='green', currentness_state='current',
                   retrieval_eligibility='eligible', legacy_status_text='canonical',
                   updated_at=?
               WHERE node_id=? AND (approval_state='candidate' OR validation_state='needs_review' OR retrieval_eligibility!='eligible' OR rag_state!='green')""",
            (validation_state, now, linked_node_id),
        )
    else:
        connection.execute(
            """UPDATE node_state
               SET validation_state=?, approval_state='approved',
                   retrieval_eligibility='eligible', legacy_status_text='canonical',
                   updated_at=?
               WHERE node_id=? AND (approval_state='candidate' OR validation_state='needs_review' OR retrieval_eligibility!='eligible')""",
            (validation_state, now, linked_node_id),
        )


def promote_candidate_endpoint_nodes(
    connection: Any,
    before: Any,
    validation_state: str,
    now: str,
    table_exists_fn: Any,
) -> None:
    """Promote subject and object nodes to canonical status and index them in search_document."""
    has_node = table_exists_fn(connection, "node")
    has_node_status = _node_has_status_columns(connection, has_node)
    has_state = table_exists_fn(connection, "node_state")
    ns_cols = _node_state_columns(connection, has_state)
    has_sd = has_node and table_exists_fn(connection, "search_document")
    keys = before.keys() if hasattr(before, "keys") else ()
    endpoints = (
        before["subject_node_id"] if "subject_node_id" in keys else None,
        before["object_node_id"] if "object_node_id" in keys else None,
    )
    for linked_node_id in endpoints:
        if not linked_node_id or _has_unverified_speaker_provenance(connection, linked_node_id, before, has_node):
            continue
        if has_node_status:
            connection.execute(
                "UPDATE node SET status='canonical', updated_at=? WHERE node_id=? AND status!='canonical'",
                (now, linked_node_id),
            )
        if has_state:
            _update_single_node_state(connection, ns_cols, validation_state, now, linked_node_id)
        if has_sd:
            _index_promoted_node(connection, linked_node_id, now)


def _is_unverified_node_row(nid: str, canonical_name: Any, description: Any) -> bool:
    if not nid.startswith("person:"):
        return False
    low_id = nid.lower()
    if any(tok in low_id for tok in _UNVERIFIED_ID_TOKENS):
        return True
    cname = str(canonical_name or "").lower()
    desc = str(description or "").upper()
    return cname.startswith(("speaker ", "speaker_", "voice ")) or "UNVERIFIED_SPEAKER" in desc or "UNDIARIZED_QUARANTINED" in desc


def _add_endpoint_rows(target: set[str], rows: list[Any]) -> None:
    for r in rows:
        target.update(str(x) for x in (r[0], r[1]) if x)


def _collect_assertion_endpoints(connection: Any, promoted_assertion_ids: list[str], source_id: str | None) -> tuple[set[str], set[str]]:
    canonical_eps: set[str] = set()
    _add_endpoint_rows(
        canonical_eps,
        connection.execute("SELECT subject_node_id, object_node_id FROM assertion WHERE canonical_status = 'canonical'").fetchall(),
    )
    for i in range(0, len(promoted_assertion_ids), 400):
        chunk = promoted_assertion_ids[i : i + 400]
        ph = ",".join("?" for _ in chunk)
        ep_sql = "SELECT subject_node_id, object_node_id FROM assertion WHERE assertion_id IN (" + ph + ")"
        _add_endpoint_rows(canonical_eps, connection.execute(ep_sql, chunk).fetchall())
    if not source_id:
        return canonical_eps, set()
    source_eps: set[str] = set()
    _add_endpoint_rows(
        source_eps,
        connection.execute("SELECT subject_node_id, object_node_id FROM assertion WHERE primary_source_id = ?", (source_id,)).fetchall(),
    )
    return canonical_eps, source_eps


def plan_promotable_nodes(
    connection: Any, promoted_assertion_ids: list[str], *, source_id: str | None = None
) -> list[str]:
    """Identify active candidate nodes or candidate node_state rows eligible for canonical promotion."""
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "node" not in tables or "assertion" not in tables:
        return []
    canonical_eps, source_eps = _collect_assertion_endpoints(connection, promoted_assertion_ids, source_id)
    has_state = "node_state" in tables
    sql = (
        "SELECT n.node_id, n.status, n.canonical_name, n.description FROM node n "
        "LEFT JOIN node_state ns ON n.node_id = ns.node_id "
        "WHERE n.active = 1 AND (n.status = 'candidate' OR ns.approval_state = 'candidate' OR ns.retrieval_eligibility = 'candidate_only')"
        if has_state
        else "SELECT n.node_id, n.status, n.canonical_name, n.description FROM node n WHERE n.active = 1 AND n.status = 'candidate'"
    )
    promotable: list[str] = []
    for r in connection.execute(sql).fetchall():
        nid, nstatus = str(r[0]), str(r[1] or "")
        if source_id and nid not in source_eps:
            continue
        if _is_unverified_node_row(nid, r[2], r[3]):
            continue
        if nstatus != "canonical" and nid not in canonical_eps:
            continue
        promotable.append(nid)
    return promotable


def _batch_update_node_state(connection: Any, ns_cols: set[str], chunk: list[str], ph: str, now: str) -> None:
    if not ({"node_id", "validation_state", "approval_state", "retrieval_eligibility", "legacy_status_text", "updated_at"} <= ns_cols):
        return
    if {"rag_state", "currentness_state"} <= ns_cols:
        upd_ns_sql = (
            "UPDATE node_state SET validation_state='accepted_legacy', approval_state='approved', "
            "rag_state='green', currentness_state='current', retrieval_eligibility='eligible', "
            "legacy_status_text='canonical', updated_at=? "
            "WHERE node_id IN (" + ph + ") AND (approval_state='candidate' OR validation_state='needs_review' OR retrieval_eligibility!='eligible' OR rag_state!='green')"
        )
    else:
        upd_ns_sql = (
            "UPDATE node_state SET validation_state='accepted_legacy', approval_state='approved', "
            "retrieval_eligibility='eligible', legacy_status_text='canonical', updated_at=? "
            "WHERE node_id IN (" + ph + ") AND (approval_state='candidate' OR validation_state='needs_review' OR retrieval_eligibility!='eligible')"
        )
    connection.execute(upd_ns_sql, [now, *chunk])


def apply_node_promotions(connection: Any, node_ids: list[str], now: str) -> None:
    """Batch-promote eligible nodes and node_state rows to canonical and synchronize search_document."""
    if not node_ids:
        return
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_state, has_sd = "node_state" in tables, "search_document" in tables
    ns_cols = _node_state_columns(connection, has_state)
    for i in range(0, len(node_ids), 400):
        chunk = node_ids[i : i + 400]
        ph = ",".join("?" for _ in chunk)
        upd_node_sql = "UPDATE node SET status='canonical', updated_at=? WHERE node_id IN (" + ph + ") AND status != 'canonical'"
        connection.execute(upd_node_sql, [now, *chunk])
        if has_state:
            _batch_update_node_state(connection, ns_cols, chunk, ph, now)
        if has_sd:
            upd_sd_sql = (
                "UPDATE search_document SET canonical_status='canonical', currentness_state='current', "
                "retrieval_eligible=1, updated_at=? "
                "WHERE object_type='node' AND object_id IN (" + ph + ") "
                "AND (canonical_status!='canonical' OR currentness_state!='current' OR retrieval_eligible!=1)"
            )
            connection.execute(upd_sd_sql, [now, *chunk])
            sel_sd_sql = "SELECT object_id FROM search_document WHERE object_type='node' AND object_id IN (" + ph + ")"
            indexed = {str(r[0]) for r in connection.execute(sel_sd_sql, chunk).fetchall()}
            for nid in chunk:
                if nid not in indexed:
                    _index_promoted_node(connection, nid, now)

