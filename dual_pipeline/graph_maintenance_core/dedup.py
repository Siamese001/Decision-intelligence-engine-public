"""dual_pipeline/graph_maintenance_core/dedup.py — Semantic assertion deduplication engine."""
from __future__ import annotations

import datetime
import difflib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PARAPHRASE_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "by", "with",
    "is", "are", "was", "were", "be", "been", "being", "that", "this", "as", "from",
})

_SYNTHETIC_HEX_SUFFIX_RE = re.compile(r"\s*\[[0-9a-f]{6}\]\.?\s*$", re.IGNORECASE)


def _strip_synthetic_suffix(text: Optional[str]) -> str:
    """Strips trailing synthetic 6-char hex disambiguation tags (e.g. '[8b2bc7]') from claim text."""
    raw = (text or "").strip()
    cleaned = _SYNTHETIC_HEX_SUFFIX_RE.sub("", raw).strip() if raw else ""
    return f"{cleaned}." if (raw.endswith(".") and cleaned and not cleaned.endswith(".")) else cleaned


def _paraphrase_token_bag(text: str) -> List[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", text.lower())
    stemmed: List[str] = []
    for t in raw_tokens:
        if t in _PARAPHRASE_STOPWORDS:
            continue
        if len(t) > 5 and t.endswith("ing"):
            t = t[:-3]
        elif len(t) > 4 and t.endswith(("ed", "es")):
            t = t[:-2]
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        stemmed.append(t)
    return sorted(set(stemmed))


def _compute_claim_similarity(text1: Optional[str], text2: Optional[str]) -> float:
    """Calculates normalized sequence matcher and token-bag paraphrase similarity between two claims."""
    t1 = _strip_synthetic_suffix(text1).lower()
    t2 = _strip_synthetic_suffix(text2).lower()
    if not t1 or not t2:
        return 0.0
    if t1 == t2:
        return 1.0
    from ..graph_scout_core.matching import has_conflicting_qualifiers
    if has_conflicting_qualifiers(t1, t2):
        return 0.0
    seq_sim = difflib.SequenceMatcher(None, t1, t2).ratio()
    bag1 = _paraphrase_token_bag(t1)
    bag2 = _paraphrase_token_bag(t2)
    bag_sim = 0.0
    if bag1 and bag2:
        s1, s2 = set(bag1), set(bag2)
        jaccard = len(s1 & s2) / len(s1 | s2)
        sorted_seq = difflib.SequenceMatcher(None, " ".join(bag1), " ".join(bag2)).ratio()
        bag_sim = 0.5 * jaccard + 0.5 * sorted_seq if jaccard >= 0.75 else 0.0
    return round(max(seq_sim, bag_sim), 4)


def _build_claim_expr(cols: Set[str]) -> str:
    if "claim_text" in cols:
        return "coalesce(claim_text, '')"
    if "object_literal" in cols and "scope_text" in cols:
        return "coalesce(object_literal, scope_text, '')"
    if "object_literal" in cols:
        return "coalesce(object_literal, '')"
    if "scope_text" in cols:
        return "coalesce(scope_text, '')"
    return "''"


def _build_ts_expr(cols: Set[str]) -> str:
    has_recorded = "recorded_at" in cols
    has_created = "created_at" in cols
    if has_recorded and has_created:
        return "coalesce(recorded_at, created_at, 0)"
    if has_recorded:
        return "coalesce(recorded_at, 0)"
    if has_created:
        return "coalesce(created_at, 0)"
    return "0"


def _build_where_sql(cols: Set[str]) -> str:
    where_clauses: List[str] = []
    if "currentness_state" in cols:
        where_clauses.append("currentness_state = 'current'")
    if "status" in cols:
        where_clauses.append("status != 'superseded' AND status != 'retired'")
    if "superseded_by_assertion_id" in cols:
        where_clauses.append("superseded_by_assertion_id IS NULL")
    return ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""


def _load_assertion_evidence_map(conn: Any) -> Dict[str, List[str]]:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_evidence'")
    if cursor.fetchone() is None:
        return {}
    ev_map: Dict[str, List[str]] = {}
    for row in cursor.execute("SELECT assertion_id, evidence_id FROM assertion_evidence").fetchall():
        aid, eid = str(row[0] or ""), str(row[1] or "")
        if aid and eid:
            ev_map.setdefault(aid, []).append(eid)
    return ev_map


def _fetch_active_assertions(conn: Any) -> List[Any]:
    """Fetches candidate assertions eligible for deduplication."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'")
    if cursor.fetchone() is None:
        return []

    cursor.execute("PRAGMA table_info(assertion)")
    cols = {row[1] for row in cursor.fetchall()}

    if "assertion_id" not in cols or "subject_node_id" not in cols:
        return []

    obj_expr = "coalesce(object_node_id, '')" if "object_node_id" in cols else "''"
    lit_expr = "object_literal" if "object_literal" in cols else "NULL"
    conf_expr = "coalesce(confidence_score, 0.8)" if "confidence_score" in cols else "0.8"
    auth_expr = "coalesce(source_authority_rank, 1)" if "source_authority_rank" in cols else "1"
    src_expr = "primary_source_id" if "primary_source_id" in cols else "NULL"
    claim_expr = _build_claim_expr(cols)
    ts_expr = _build_ts_expr(cols)
    where_sql = _build_where_sql(cols)

    query = f"""
        SELECT assertion_id, subject_node_id, predicate,
               {obj_expr} as object_node_id,
               {lit_expr} as object_literal,
               {conf_expr} as confidence_score,
               {auth_expr} as source_authority_rank,
               {src_expr} as primary_source_id,
               {claim_expr} as claim_text,
               {ts_expr} as ts
          FROM assertion
         {where_sql}
         ORDER BY subject_node_id, predicate, object_node_id, ts DESC
    """
    has_conf_col = "confidence_score" in cols
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
    for r in rows:
        r["_has_confidence_col"] = has_conf_col
    return rows


def _partition_into_groups(rows: List[Any]) -> Dict[Tuple[str, str, str], List[Any]]:
    """Groups assertions by (subject, predicate, object)."""
    groups: Dict[Tuple[str, str, str], List[Any]] = {}
    for r in rows:
        key = (str(r["subject_node_id"]), str(r["predicate"]), str(r["object_node_id"]))
        groups.setdefault(key, []).append(r)
    return groups


def _evaluate_group_pair_duplicate(
    r1: Dict[str, Any],
    r2: Dict[str, Any],
    key: Tuple[str, str, str],
    similarity_threshold: float,
    ev_links_map: Optional[Dict[str, List[str]]] = None,
) -> Optional[Dict[str, Any]]:
    sim = _compute_claim_similarity(r1["claim_text"], r2["claim_text"])
    if sim < similarity_threshold:
        return None
    from ..graph_consolidation_core.resolver import resolve_pairwise_supersession
    a1_eval, a2_eval = r1, r2
    if not str(r1.get("object_node_id") or "") and not str(r2.get("object_node_id") or ""):
        shared_lit = _strip_synthetic_suffix(r1.get("object_literal") or r1.get("claim_text"))
        a1_eval = {**r1, "object_literal": shared_lit}
        a2_eval = {**r2, "object_literal": shared_lit}
    decision = resolve_pairwise_supersession(a1_eval, a2_eval, ev_links_map=ev_links_map)
    if decision is None:
        return None
    subj, pred, obj = key
    is_r1_winner = (r1["assertion_id"] == decision.winner_id)
    has_conf = bool(r1.get("_has_confidence_col", True) and r2.get("_has_confidence_col", True))
    hitl_req = decision.hitl_required if has_conf else False
    return {
        "winner_id": decision.winner_id,
        "loser_id": decision.loser_id,
        "similarity": sim,
        "subject_node_id": subj,
        "predicate": pred,
        "object_node_id": obj,
        "winner_claim": r1["claim_text"] if is_r1_winner else r2["claim_text"],
        "loser_claim": r2["claim_text"] if is_r1_winner else r1["claim_text"],
        "confidence_delta": decision.confidence_delta,
        "hitl_required": hitl_req,
        "selection_reason": decision.selection_reason,
        "evidence_ids_to_transfer": decision.evidence_ids_to_transfer,
    }


def _find_group_duplicates(
    key: Tuple[str, str, str],
    group_rows: List[Any],
    similarity_threshold: float,
    ev_links_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Finds pairwise duplicates within a single (subject, predicate, object) group without transitive chains."""
    group_proposals: List[Dict[str, Any]] = []
    losers_seen: Set[str] = set()
    n = len(group_rows)
    for i in range(n):
        aid_i = str(group_rows[i]["assertion_id"])
        if aid_i in losers_seen:
            continue
        for j in range(i + 1, n):
            aid_j = str(group_rows[j]["assertion_id"])
            if aid_j in losers_seen:
                continue
            prop = _evaluate_group_pair_duplicate(
                group_rows[i], group_rows[j], key, similarity_threshold, ev_links_map
            )
            if prop is not None:
                losers_seen.add(str(prop["loser_id"]))
                group_proposals.append(prop)
                if str(prop["loser_id"]) == aid_i:
                    break
    return group_proposals


def find_semantic_duplicates(
    conn: Any,
    similarity_threshold: float = 0.9,
) -> List[Dict[str, Any]]:
    """Scans active assertions in the graph and finds clusters of semantically duplicate assertions."""
    rows = _fetch_active_assertions(conn)
    if not rows:
        return []
    ev_links_map = _load_assertion_evidence_map(conn)
    groups = _partition_into_groups(rows)
    proposals: List[Dict[str, Any]] = []
    for key, group_rows in groups.items():
        if len(group_rows) >= 2:
            proposals.extend(_find_group_duplicates(key, group_rows, similarity_threshold, ev_links_map))
    return proposals


def _build_assertion_update_sql(ast_cols: Set[str]) -> Tuple[Optional[str], bool, bool]:
    set_clauses: List[str] = []
    if "currentness_state" in ast_cols:
        set_clauses.append("currentness_state = 'historical'")
    if "validation_state" in ast_cols:
        set_clauses.append("validation_state = 'superseded'")
    if "retrieval_eligible" in ast_cols:
        set_clauses.append("retrieval_eligible = 0")
    has_superseded_at = "superseded_at" in ast_cols
    if has_superseded_at:
        set_clauses.append("superseded_at = ?")
    has_superseded_by = "superseded_by_assertion_id" in ast_cols
    if has_superseded_by:
        set_clauses.append("superseded_by_assertion_id = ?")

    if not set_clauses:
        return None, False, False
    sql = f"UPDATE assertion SET {', '.join(set_clauses)} WHERE assertion_id = ?"
    return sql, has_superseded_at, has_superseded_by


def _build_search_doc_update_sql(cursor: Any, has_search_doc: bool) -> Optional[str]:
    if not has_search_doc:
        return None
    cursor.execute("PRAGMA table_info(search_document)")
    sd_cols = {row[1] for row in cursor.fetchall()}
    sd_set: List[str] = []
    if "currentness_state" in sd_cols:
        sd_set.append("currentness_state = 'historical'")
    if "retrieval_eligible" in sd_cols:
        sd_set.append("retrieval_eligible = 0")
    if sd_set:
        return f"UPDATE search_document SET {', '.join(sd_set)} WHERE assertion_id = ?"
    return None


def _transfer_loser_evidence(cursor: Any, has_ae: bool, ae_cols: Set[str], winner_id: str, loser_id: str, now: str) -> None:
    if not has_ae:
        return
    existing = {
        r[0] for r in cursor.execute(
            "SELECT evidence_id FROM assertion_evidence WHERE assertion_id = ?", (winner_id,)
        ).fetchall()
    }
    rows = cursor.execute(
        "SELECT evidence_id FROM assertion_evidence WHERE assertion_id = ?", (loser_id,)
    ).fetchall()
    for (eid,) in rows:
        if eid in existing:
            continue
        existing.add(eid)
        if "evidence_relationship" in ae_cols and "created_at" in ae_cols:
            cursor.execute(
                "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES (?, ?, 'supports', ?)",
                (winner_id, eid, now),
            )
        else:
            cursor.execute(
                "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id) VALUES (?, ?)",
                (winner_id, eid),
            )


def _sync_fts_and_audit_supersessions(cursor: Any, applied_pairs: List[Dict[str, Any]], now: str) -> None:
    if not applied_pairs:
        return
    if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'").fetchone():
        try:
            cursor.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
            cursor.execute(
                "INSERT INTO search_fts(rowid, title, body, object_type, object_id) "
                "SELECT search_rowid, title, body, object_type, object_id FROM search_document WHERE retrieval_eligible = 1"
            )
        except Exception:
            pass
    if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_event'").fetchone():
        audit_cols = {r[1] for r in cursor.execute("PRAGMA table_info(audit_event)").fetchall()}
        if {"audit_event_id", "actor_or_process", "action", "record_type", "record_id", "event_timestamp"}.issubset(audit_cols):
            audit_id = f"audit-dedup-{uuid.uuid4().hex[:20]}"
            cursor.execute(
                """INSERT OR IGNORE INTO audit_event (
                    audit_event_id, actor_or_process, action, record_type, record_id,
                    new_value_json, event_timestamp, reason
                ) VALUES (?, 'graph_maintenance_dedup', 'supersede_duplicates', 'assertion', ?, ?, ?, 'Semantic duplicate supersession')""",
                (audit_id, applied_pairs[0]["loser_id"], json.dumps({"superseded_count": len(applied_pairs)}), now),
            )


def _commit_and_checkpoint(conn: Any, auto_commit: bool) -> None:
    if not auto_commit:
        return
    conn.commit()
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass


def apply_supersessions(
    conn: Any,
    proposals: List[Dict[str, Any]],
    now_iso: Optional[str] = None,
    *,
    include_hitl_approved: bool = False,
    auto_commit: bool = True,
) -> int:
    """Executes proposed supersessions, marking loser assertions as historical and transferring evidence."""
    if not proposals:
        return 0

    now = now_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor = conn.cursor()
    has_search_doc = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='search_document'"
    ).fetchone() is not None
    has_ae = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_evidence'"
    ).fetchone() is not None
    ae_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion_evidence)").fetchall()} if has_ae else set()

    cursor.execute("PRAGMA table_info(assertion)")
    ast_cols = {row[1] for row in cursor.fetchall()}
    update_ast_sql, has_at, has_by = _build_assertion_update_sql(ast_cols)
    if not update_ast_sql:
        return 0

    update_sd_sql = _build_search_doc_update_sql(cursor, has_search_doc)

    applied_pairs: List[Dict[str, Any]] = []
    for p in proposals:
        if not include_hitl_approved and p.get("hitl_required") is True:
            continue
        params = ([now] if has_at else []) + ([p["winner_id"]] if has_by else []) + [p["loser_id"]]
        cursor.execute(update_ast_sql, tuple(params))
        if update_sd_sql:
            cursor.execute(update_sd_sql, (p["loser_id"],))
        _transfer_loser_evidence(cursor, has_ae, ae_cols, str(p["winner_id"]), str(p["loser_id"]), now)
        applied_pairs.append(p)

    _sync_fts_and_audit_supersessions(cursor, applied_pairs, now)
    _commit_and_checkpoint(conn, auto_commit)
    return len(applied_pairs)
