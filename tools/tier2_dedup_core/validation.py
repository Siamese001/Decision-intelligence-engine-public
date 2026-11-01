"""
tools/tier2_dedup_core/validation.py

Signal quality validation and 14-point graph integrity verification.
"""
from __future__ import annotations

from typing import Any


def execute_signal_quality_validation(conn: Any) -> dict:
    """Run comprehensive signal quality checks."""
    results: dict = {}

    integrity = conn.execute("PRAGMA integrity_check;").fetchone()
    results["integrity"] = integrity[0] if integrity else "UNKNOWN"

    conn.execute("PRAGMA foreign_keys=ON;")
    fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
    results["fk_violations"] = len(fk)

    dup_edges = conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT from_node_id, edge_type, to_node_id, COUNT(*) AS cnt
               FROM edge WHERE superseded_by IS NULL
               GROUP BY from_node_id, edge_type, to_node_id HAVING cnt > 1
           )"""
    ).fetchone()[0]
    results["duplicate_active_edges"] = dup_edges

    redirects = conn.execute(
        "SELECT er.from_node_id, er.to_node_id, n.active FROM entity_redirect er JOIN node n ON er.from_node_id = n.node_id WHERE er.active = 1"
    ).fetchall()
    bad_redirects = [r for r in redirects if r["active"] == 1]
    results["active_redirected_nodes"] = len(bad_redirects)

    bad_assertions = conn.execute(
        """SELECT COUNT(*) FROM assertion a JOIN node n ON a.subject_node_id = n.node_id
           WHERE a.canonical_status = 'canonical' AND a.currentness_state = 'current' AND n.active = 0"""
    ).fetchone()[0]
    results["assertions_on_inactive_nodes"] = bad_assertions

    stale_retrieval = conn.execute(
        """SELECT COUNT(*) FROM search_document sd JOIN node n ON sd.object_id = n.node_id AND sd.object_type = 'node'
           WHERE n.active = 0 AND sd.retrieval_eligible = 1"""
    ).fetchone()[0]
    results["stale_retrieval_docs"] = stale_retrieval

    no_evidence = conn.execute(
        """SELECT COUNT(*) FROM assertion a
           WHERE a.canonical_status = 'canonical' AND a.currentness_state = 'current'
             AND a.superseded_by_assertion_id IS NULL
             AND NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id)"""
    ).fetchone()[0]
    total_canonical = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE canonical_status = 'canonical' AND currentness_state = 'current' AND superseded_by_assertion_id IS NULL"
    ).fetchone()[0]
    prov_rate = ((total_canonical - no_evidence) / total_canonical * 100 if total_canonical > 0 else 0)
    results["provenance_coverage"] = round(prov_rate, 2)
    results["assertions_without_evidence"] = no_evidence

    counts = {}
    for table in ["node", "assertion", "edge", "evidence_item", "assertion_evidence", "search_document", "audit_event", "entity_merge_event", "entity_redirect", "entity_resolution_candidate"]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]

    counts["node_active"] = conn.execute("SELECT COUNT(*) FROM node WHERE active = 1").fetchone()[0]
    counts["node_inactive"] = conn.execute("SELECT COUNT(*) FROM node WHERE active = 0").fetchone()[0]
    counts["canonical_current_assertions"] = total_canonical
    counts["active_edges"] = conn.execute("SELECT COUNT(*) FROM edge WHERE superseded_by IS NULL").fetchone()[0]
    counts["retrieval_eligible_docs"] = conn.execute("SELECT COUNT(*) FROM search_document WHERE retrieval_eligible = 1").fetchone()[0]

    avg_conf = conn.execute(
        "SELECT AVG(confidence_score) FROM assertion WHERE canonical_status = 'canonical' AND currentness_state = 'current' AND superseded_by_assertion_id IS NULL"
    ).fetchone()[0]
    counts["avg_confidence"] = round(avg_conf, 4) if avg_conf else 0
    results["final_counts"] = counts

    results["overall"] = (
        "PASS"
        if (
            results["integrity"] == "ok"
            and results["duplicate_active_edges"] == 0
            and results["assertions_on_inactive_nodes"] == 0
            and results["stale_retrieval_docs"] == 0
        )
        else "FAIL"
    )
    return results
