"""Automated Mutex Contradiction Invalidation and Supersession Engine."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from dual_pipeline.core import utc_now

from .constants import MUTEX_CARDINALITY_PREDICATES


def is_functional_mutex_target(node_id: Optional[str]) -> bool:
    """Returns True if the target node represents a functional delivery pod, workstream, or entity."""
    if not node_id:
        return False
    lower = node_id.casefold()
    return (
        lower.startswith("workstream")
        or lower.startswith("entity-")
        or lower.startswith("pod")
        or ":workstream:" in lower
        or ":pod:" in lower
    )


def _classify_org_text(text: str) -> str:
    low = text.lower()
    if "nee" in low or "apex_global" in low or "apex" in low:
        return "org_nee"
    if "partner_advisory" in low:
        return "org_partner_advisory"
    return "unknown_org"


def _fetch_affiliation_rows(connection: Any, subject_node_id: str) -> list[Any]:
    try:
        return list(
            connection.execute(
                """SELECT object_node_id, scope_text FROM assertion
                   WHERE subject_node_id = ? AND predicate = 'AFFILIATED_WITH'
                     AND currentness_state = 'current'""",
                (subject_node_id,),
            ).fetchall()
        )
    except Exception:
        return []


def _resolve_node_org_fallback(connection: Any, subject_node_id: str) -> str:
    try:
        n_row = connection.execute("SELECT node_type, description FROM node WHERE node_id = ?", (subject_node_id,)).fetchone()
        if n_row and str(n_row[0] or "") == "org":
            return subject_node_id
        return _classify_org_text(str(n_row[1])) if (n_row and n_row[1]) else "unknown_org"
    except Exception:
        n_row = connection.execute("SELECT description FROM node WHERE node_id = ?", (subject_node_id,)).fetchone()
        return _classify_org_text(str(n_row[0])) if (n_row and n_row[0]) else "unknown_org"


def _resolve_subject_org(connection: Any, subject_node_id: str) -> str:
    if subject_node_id.startswith(("org_", "org:")):
        return subject_node_id.replace(":", "_")
    for obj_id, s_txt in _fetch_affiliation_rows(connection, subject_node_id):
        org = _classify_org_text(f"{obj_id or ''} {s_txt or ''}")
        if org != "unknown_org":
            return org
    return _resolve_node_org_fallback(connection, subject_node_id)


def _resolve_assertion_track(connection: Any, assertion_id: Optional[str]) -> str:
    if not assertion_id:
        return "general_track"
    try:
        q_row = connection.execute(
            "SELECT qualifiers_json, scope_text FROM assertion WHERE assertion_id = ?",
            (assertion_id,),
        ).fetchone()
    except Exception:
        return "general_track"
    if not q_row:
        return "general_track"
    combined = f"{q_row[0] or ''} {q_row[1] or ''}".lower()
    if "client_tech_lead" in combined or "tech lead" in combined:
        return "tech_lead"
    if "product" in combined or "business owner" in combined:
        return "product_owner"
    if "solution architect" in combined or "project lead" in combined:
        return "solution_architect"
    return "general_track"


def _resolve_subject_org_and_track(
    connection: Any,
    subject_node_id: Optional[str],
    assertion_id: Optional[str] = None,
) -> tuple[str, str]:
    """Return (org_affiliation, leadership_track) for a subject node and assertion."""
    if not subject_node_id:
        return "unknown_org", "general_track"
    try:
        return _resolve_subject_org(connection, subject_node_id), _resolve_assertion_track(connection, assertion_id)
    except Exception:
        return "unknown_org", "general_track"


def _find_mutex_cardinality_conflicts(
    connection: Any,
    object_node_id: str,
    predicate: str,
    incoming_assertion_id: Optional[str],
    subject_node_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if predicate not in MUTEX_CARDINALITY_PREDICATES or not is_functional_mutex_target(object_node_id):
        return []

    inc_org, inc_track = _resolve_subject_org_and_track(connection, subject_node_id, incoming_assertion_id)

    query = """
        SELECT assertion_id, subject_node_id, predicate, object_node_id,
               recorded_at, created_at, canonical_status, currentness_state
          FROM assertion
         WHERE object_node_id = ?
           AND predicate = ?
           AND currentness_state = 'current'
           AND canonical_status = 'canonical'
    """
    params: List[Any] = [object_node_id, predicate]
    if incoming_assertion_id:
        query += " AND assertion_id != ?"
        params.append(incoming_assertion_id)

    rows = connection.execute(query, tuple(params)).fetchall()
    conflicts: List[Dict[str, Any]] = []
    for r in rows:
        cand_aid, cand_sub = r[0], r[1]
        cand_org, cand_track = _resolve_subject_org_and_track(connection, cand_sub, cand_aid)
        if inc_org != "unknown_org" and cand_org != "unknown_org" and inc_org != cand_org:
            continue
        if inc_track != "general_track" and cand_track != "general_track" and inc_track != cand_track:
            continue
        conflicts.append(
            {
                "assertion_id": r[0],
                "subject_node_id": r[1],
                "predicate": r[2],
                "object_node_id": r[3],
                "recorded_at": r[4],
                "created_at": r[5],
                "canonical_status": r[6],
                "currentness_state": r[7],
                "conflict_type": "MUTEX_CARDINALITY_COLLISION",
            }
        )
    return conflicts


def _find_exact_relationship_conflicts(
    connection: Any,
    subject_node_id: Optional[str],
    predicate: str,
    object_node_id: str,
    incoming_assertion_id: Optional[str],
) -> List[Dict[str, Any]]:
    if not subject_node_id:
        return []

    query_exact = """
        SELECT assertion_id, subject_node_id, predicate, object_node_id,
               recorded_at, created_at, canonical_status, currentness_state
          FROM assertion
         WHERE subject_node_id = ?
           AND predicate = ?
           AND object_node_id = ?
           AND currentness_state = 'current'
    """
    params_exact: List[Any] = [subject_node_id, predicate, object_node_id]
    if incoming_assertion_id:
        query_exact += " AND assertion_id != ?"
        params_exact.append(incoming_assertion_id)

    rows_exact = connection.execute(query_exact, tuple(params_exact)).fetchall()
    return [
        {
            "assertion_id": r[0],
            "subject_node_id": r[1],
            "predicate": r[2],
            "object_node_id": r[3],
            "recorded_at": r[4],
            "created_at": r[5],
            "canonical_status": r[6],
            "currentness_state": r[7],
            "conflict_type": "EXACT_RELATIONSHIP_SUPERSEDED",
        }
        for r in rows_exact
    ]


def find_conflicting_assertions(
    connection: Any,
    *,
    object_node_id: Optional[str],
    predicate: str,
    incoming_assertion_id: Optional[str] = None,
    subject_node_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Finds existing active canonical assertions that conflict with the given assertion."""
    if not object_node_id:
        return []

    # Negative decision predicates: REJECTED, DECIDED_AGAINST target an option entity as object_node_id
    # All assertions/edges where subject_node_id = object_node_id OR object_node_id = object_node_id must be superseded
    if predicate in ("DECIDED_AGAINST", "REJECTED"):
        query = """
            SELECT assertion_id, subject_node_id, predicate, object_node_id,
                   recorded_at, created_at, canonical_status, currentness_state
              FROM assertion
             WHERE (subject_node_id = ? OR object_node_id = ?)
               AND currentness_state = 'current'
        """
        params: List[Any] = [object_node_id, object_node_id]
        if incoming_assertion_id:
            query += " AND assertion_id != ?"
            params.append(incoming_assertion_id)
        rows = connection.execute(query, tuple(params)).fetchall()
        return [
            {
                "assertion_id": r[0],
                "subject_node_id": r[1],
                "predicate": r[2],
                "object_node_id": r[3],
                "recorded_at": r[4],
                "created_at": r[5],
                "canonical_status": r[6],
                "currentness_state": r[7],
                "conflict_type": "DECISION_REJECTION_CASCADE",
            }
            for r in rows
        ]

    conflicts = _find_mutex_cardinality_conflicts(
        connection, object_node_id, predicate, incoming_assertion_id, subject_node_id=subject_node_id
    )
    seen_ids = {c["assertion_id"] for c in conflicts}

    exact_conflicts = _find_exact_relationship_conflicts(
        connection, subject_node_id, predicate, object_node_id, incoming_assertion_id
    )
    for ec in exact_conflicts:
        if ec["assertion_id"] not in seen_ids:
            conflicts.append(ec)
            seen_ids.add(ec["assertion_id"])

    return conflicts


def _resolve_edge_superseded_by(connection: Any, incoming_assertion_id: str) -> Optional[str]:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'edge'").fetchone():
        return None
    fk_row = connection.execute("PRAGMA foreign_keys").fetchone()
    if not (fk_row and fk_row[0]):
        return incoming_assertion_id
    if not connection.execute("SELECT 1 FROM pragma_foreign_key_list('edge') WHERE \"table\" = 'edge' AND \"from\" = 'superseded_by'").fetchone():
        return incoming_assertion_id
    exists = connection.execute("SELECT 1 FROM edge WHERE edge_id = ?", (incoming_assertion_id,)).fetchone()
    return incoming_assertion_id if exists else None


def supersede_conflicting_assertions(
    connection: Any,
    *,
    incoming_assertion_id: str,
    subject_node_id: str,
    predicate: str,
    object_node_id: Optional[str],
    now_iso: Optional[str] = None,
) -> List[str]:
    """Supersedes any conflicting assertions, updating assertion and search_document records."""
    if not object_node_id:
        return []
    conflicts = find_conflicting_assertions(
        connection, object_node_id=object_node_id, predicate=predicate,
        incoming_assertion_id=incoming_assertion_id, subject_node_id=subject_node_id,
    )
    if not conflicts:
        return []
    now = now_iso or utc_now()
    superseded_ids: List[str] = []
    has_conf = {"confidence_score", "confidence_label"}.issubset(
        {row[1] for row in connection.execute("PRAGMA table_info(assertion)").fetchall()}
    )
    edge_sup = _resolve_edge_superseded_by(connection, incoming_assertion_id)
    for conflict in conflicts:
        cid = conflict["assertion_id"]
        if has_conf:
            connection.execute(
                "UPDATE assertion SET currentness_state='historical', superseded_at=?, superseded_by_assertion_id=?, retrieval_eligible=0, confidence_score=0.0, confidence_label='superseded', updated_at=? WHERE assertion_id=?",
                (now, incoming_assertion_id, now, cid),
            )
        else:
            connection.execute(
                "UPDATE assertion SET currentness_state='historical', superseded_at=?, superseded_by_assertion_id=?, retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
                (now, incoming_assertion_id, now, cid),
            )
        connection.execute(
            "UPDATE search_document SET currentness_state='historical', retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
            (now, cid),
        )
        if edge_sup is not None:
            connection.execute(
                "UPDATE edge SET superseded_by=?, updated_at=? WHERE (from_node_id=? OR to_node_id=?) AND superseded_by IS NULL",
                (edge_sup, now, object_node_id, object_node_id),
            )
        superseded_ids.append(cid)
    return superseded_ids


def reconcile_all_graph_mutex(
    connection: Any,
    *,
    now_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Audits entire graph for existing mutex collisions and supersedes all but the newest."""
    now = now_iso or utc_now()
    placeholders = ",".join("?" for _ in MUTEX_CARDINALITY_PREDICATES)
    query = f"""
        SELECT object_node_id, predicate, count(*) as cnt
          FROM assertion
         WHERE currentness_state = 'current'
           AND canonical_status = 'canonical'
           AND predicate IN ({placeholders})
           AND object_node_id IS NOT NULL
           AND (object_node_id LIKE 'workstream%' OR object_node_id LIKE 'entity-%' OR object_node_id LIKE 'pod%')
         GROUP BY object_node_id, predicate
        HAVING cnt > 1
    """
    collision_groups = connection.execute(query, tuple(MUTEX_CARDINALITY_PREDICATES)).fetchall()
    reconciliation_logs: List[Dict[str, Any]] = []

    for obj_id, pred, _ in collision_groups:
        assertions = connection.execute(
            """SELECT assertion_id, subject_node_id, recorded_at, created_at
                 FROM assertion
                WHERE object_node_id = ?
                  AND predicate = ?
                  AND currentness_state = 'current'
                  AND canonical_status = 'canonical'
                ORDER BY recorded_at DESC, created_at DESC, assertion_id DESC""",
            (obj_id, pred),
        ).fetchall()

        if len(assertions) <= 1:
            continue

        winner = assertions[0]
        winner_id = winner[0]
        stale_assertions = assertions[1:]

        for stale in stale_assertions:
            stale_id = stale[0]
            connection.execute(
                """UPDATE assertion
                      SET currentness_state = 'historical',
                          superseded_at = ?,
                          superseded_by_assertion_id = ?,
                          retrieval_eligible = 0,
                          updated_at = ?
                    WHERE assertion_id = ?""",
                (now, winner_id, now, stale_id),
            )
            connection.execute(
                """UPDATE search_document
                      SET currentness_state = 'historical',
                          retrieval_eligible = 0,
                          updated_at = ?
                    WHERE assertion_id = ?""",
                (now, stale_id),
            )
            reconciliation_logs.append({
                "object_node_id": obj_id,
                "predicate": pred,
                "winner_assertion_id": winner_id,
                "superseded_assertion_id": stale_id,
                "superseded_subject": stale[1],
            })

    return reconciliation_logs
