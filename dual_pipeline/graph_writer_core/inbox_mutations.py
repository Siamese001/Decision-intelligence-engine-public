"""Governed atomic X3 micro-release graph mutations for deferred HITL inbox resolutions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.core import connect_sqlite, stable_id, utc_now


def apply_x3_inbox_mutation(
    graph_path: Path,
    item: Dict[str, Any],
    decision: str,
    now: str,
) -> None:
    """Execute atomic graph state transition under governed writer boundary."""
    if not graph_path.is_file():
        return
    cid = item["candidate_id"]
    existing_target = item.get("existing_target_id")
    with connect_sqlite(graph_path) as conn:
        if decision == "SUPERSEDE" and existing_target:
            conn.execute(
                "UPDATE assertion SET canonical_status='superseded', currentness_state='historical', "
                "superseded_at=?, superseded_by_assertion_id=? WHERE assertion_id=?",
                (now, cid, existing_target),
            )
            conn.execute(
                "UPDATE search_document SET canonical_status='superseded', currentness_state='historical', "
                "retrieval_eligible=0 WHERE assertion_id=?",
                (existing_target,),
            )
            conn.execute(
                "UPDATE assertion SET canonical_status='canonical', currentness_state='current' WHERE assertion_id=?",
                (cid,),
            )
            conn.execute(
                "UPDATE search_document SET canonical_status='canonical', currentness_state='current', "
                "retrieval_eligible=1 WHERE assertion_id=?",
                (cid,),
            )
        elif decision in {"PROMOTE_APPROVED", "CONFIRM", "MERGE_ALIAS"}:
            conn.execute(
                "UPDATE assertion SET canonical_status='canonical', currentness_state='current' WHERE assertion_id=?",
                (cid,),
            )
            conn.execute(
                "UPDATE search_document SET canonical_status='canonical', currentness_state='current', "
                "retrieval_eligible=1 WHERE assertion_id=?",
                (cid,),
            )
        elif decision == "REJECT":
            conn.execute(
                "UPDATE assertion SET canonical_status='rejected', currentness_state='historical' WHERE assertion_id=?",
                (cid,),
            )
            conn.execute("UPDATE search_document SET retrieval_eligible=0 WHERE assertion_id=?", (cid,))
        elif decision == "QUARANTINE":
            conn.execute(
                "UPDATE assertion SET canonical_status='quarantined', currentness_state='quarantined' WHERE assertion_id=?",
                (cid,),
            )
            conn.execute("UPDATE search_document SET retrieval_eligible=0 WHERE assertion_id=?", (cid,))
        conn.commit()


def sync_canonical_aliases_to_graph(graph_path: Path, decisions: List[Dict[str, Any]]) -> None:
    """Insert resolved canonical aliases into target graph database under governed writer boundary."""
    if not graph_path.is_file():
        return
    now = utc_now()
    with connect_sqlite(graph_path) as conn:
        ca_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canonical_alias'"
        ).fetchall()
        if not ca_tables:
            return
        for dec in decisions:
            ca_info = dec.get("extensions", {}).get("com.partner_advisory.nee.canonical-alias")
            if not (isinstance(ca_info, dict) and ca_info.get("entity_id") and ca_info.get("alias")):
                continue
            eid = ca_info["entity_id"]
            alias_name = ca_info["alias"]
            norm_alias = alias_name.lower().strip()
            alias_id = stable_id("ca", eid, norm_alias, length=24)
            conn.execute(
                """INSERT OR IGNORE INTO canonical_alias 
                   (canonical_alias_id, normalized_alias, display_alias, entity_id, alias_type,
                    source_id, confidence_label, confidence_score, ambiguity_state, preferred_flag,
                    review_state, active, created_at)
                   VALUES (?, ?, ?, ?, 'merged_canonical', 'hitl_shift_left', 'HIGH', 1.0, 'RESOLVED', 1, 'APPROVED', 1, ?)""",
                (alias_id, norm_alias, alias_name, eid, now),
            )
        conn.commit()
