#!/usr/bin/env python3
"""
SCAN 0: Baseline Graph Statistics
Collects comprehensive baseline counts and metrics for the NEE knowledge graph.
All SQLite access is strictly read-only.
"""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import get_tier2_db_connection as get_db_connection


def collect_baseline_stats(conn: Any) -> dict:
    cur = conn.cursor()

    table_counts = {}

    # 1. node (count by node_type, count active vs inactive)
    cur.execute("SELECT COUNT(*) AS total FROM node")
    node_total = cur.fetchone()["total"]
    cur.execute("SELECT node_type, COUNT(*) AS count FROM node GROUP BY node_type ORDER BY count DESC")
    by_node_type = {row["node_type"]: row["count"] for row in cur.fetchall()}
    cur.execute("SELECT active, COUNT(*) AS count FROM node GROUP BY active")
    active_map = {row["active"]: row["count"] for row in cur.fetchall()}
    table_counts["node"] = {
        "row_count": node_total,
        "by_node_type": by_node_type,
        "active_vs_inactive": {
            "active": active_map.get(1, 0),
            "inactive": active_map.get(0, 0),
        },
    }

    # 2. source
    cur.execute("SELECT COUNT(*) AS total FROM source")
    table_counts["source"] = {"row_count": cur.fetchone()["total"]}

    # 3. source_version
    cur.execute("SELECT COUNT(*) AS total FROM source_version")
    table_counts["source_version"] = {"row_count": cur.fetchone()["total"]}

    # 4. extraction_run
    cur.execute("SELECT COUNT(*) AS total FROM extraction_run")
    table_counts["extraction_run"] = {"row_count": cur.fetchone()["total"]}

    # 5. evidence_item
    cur.execute("SELECT COUNT(*) AS total FROM evidence_item")
    table_counts["evidence_item"] = {"row_count": cur.fetchone()["total"]}

    # 6. assertion (by canonical_status, by currentness_state, by assertion_type)
    cur.execute("SELECT COUNT(*) AS total FROM assertion")
    assertion_total = cur.fetchone()["total"]
    cur.execute("SELECT canonical_status, COUNT(*) AS count FROM assertion GROUP BY canonical_status ORDER BY count DESC")
    by_canonical_status = {row["canonical_status"]: row["count"] for row in cur.fetchall()}
    cur.execute("SELECT currentness_state, COUNT(*) AS count FROM assertion GROUP BY currentness_state ORDER BY count DESC")
    by_currentness_state = {row["currentness_state"]: row["count"] for row in cur.fetchall()}
    cur.execute("SELECT assertion_type, COUNT(*) AS count FROM assertion GROUP BY assertion_type ORDER BY count DESC")
    by_assertion_type = {row["assertion_type"]: row["count"] for row in cur.fetchall()}
    table_counts["assertion"] = {
        "row_count": assertion_total,
        "by_canonical_status": by_canonical_status,
        "by_currentness_state": by_currentness_state,
        "by_assertion_type": by_assertion_type,
    }

    # 7. assertion_evidence
    cur.execute("SELECT COUNT(*) AS total FROM assertion_evidence")
    table_counts["assertion_evidence"] = {"row_count": cur.fetchone()["total"]}

    # 8. assertion_conflict
    cur.execute("SELECT COUNT(*) AS total FROM assertion_conflict")
    table_counts["assertion_conflict"] = {"row_count": cur.fetchone()["total"]}

    # 9. assertion_predicate_registry (active count)
    cur.execute("SELECT COUNT(*) AS total FROM assertion_predicate_registry")
    pred_total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS active_count FROM assertion_predicate_registry WHERE active = 1")
    pred_active = cur.fetchone()["active_count"]
    table_counts["assertion_predicate_registry"] = {
        "row_count": pred_total,
        "active_count": pred_active,
    }

    # 10. edge (by edge_type, count active [superseded_by IS NULL])
    cur.execute("SELECT COUNT(*) AS total FROM edge")
    edge_total = cur.fetchone()["total"]
    cur.execute("SELECT edge_type, COUNT(*) AS count FROM edge GROUP BY edge_type ORDER BY count DESC")
    by_edge_type = {row["edge_type"]: row["count"] for row in cur.fetchall()}
    cur.execute("SELECT COUNT(*) AS count FROM edge WHERE superseded_by IS NULL")
    edge_active = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) AS count FROM edge WHERE superseded_by IS NOT NULL")
    edge_superseded = cur.fetchone()["count"]
    table_counts["edge"] = {
        "row_count": edge_total,
        "active_count": edge_active,
        "superseded_count": edge_superseded,
        "by_edge_type": by_edge_type,
    }

    # 11. edge_type_registry (active count)
    cur.execute("SELECT COUNT(*) AS total FROM edge_type_registry")
    etr_total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS active_count FROM edge_type_registry WHERE registry_state = 'active'")
    etr_active = cur.fetchone()["active_count"]
    table_counts["edge_type_registry"] = {
        "row_count": etr_total,
        "active_count": etr_active,
    }

    # 12. edge_type_domain_range
    cur.execute("SELECT COUNT(*) AS total FROM edge_type_domain_range")
    table_counts["edge_type_domain_range"] = {"row_count": cur.fetchone()["total"]}

    # 13. search_document (by object_type, by retrieval_eligible)
    cur.execute("SELECT COUNT(*) AS total FROM search_document")
    sdoc_total = cur.fetchone()["total"]
    cur.execute("SELECT object_type, COUNT(*) AS count FROM search_document GROUP BY object_type ORDER BY count DESC")
    sdoc_by_obj = {row["object_type"]: row["count"] for row in cur.fetchall()}
    cur.execute("SELECT retrieval_eligible, COUNT(*) AS count FROM search_document GROUP BY retrieval_eligible")
    sdoc_by_ret = {str(row["retrieval_eligible"]): row["count"] for row in cur.fetchall()}
    table_counts["search_document"] = {
        "row_count": sdoc_total,
        "by_object_type": sdoc_by_obj,
        "by_retrieval_eligible": sdoc_by_ret,
    }

    # 14. canonical_alias (by ambiguity_state)
    cur.execute("SELECT COUNT(*) AS total FROM canonical_alias")
    alias_total = cur.fetchone()["total"]
    cur.execute("SELECT ambiguity_state, COUNT(*) AS count FROM canonical_alias GROUP BY ambiguity_state ORDER BY count DESC")
    alias_by_amb = {row["ambiguity_state"]: row["count"] for row in cur.fetchall()}
    table_counts["canonical_alias"] = {
        "row_count": alias_total,
        "by_ambiguity_state": alias_by_amb,
    }

    # 15. entity_merge_event
    cur.execute("SELECT COUNT(*) AS total FROM entity_merge_event")
    table_counts["entity_merge_event"] = {"row_count": cur.fetchone()["total"]}

    # 16. entity_redirect
    cur.execute("SELECT COUNT(*) AS total FROM entity_redirect")
    table_counts["entity_redirect"] = {"row_count": cur.fetchone()["total"]}

    # 17. entity_resolution_candidate (by status)
    cur.execute("SELECT COUNT(*) AS total FROM entity_resolution_candidate")
    erc_total = cur.fetchone()["total"]
    cur.execute("SELECT status, COUNT(*) AS count FROM entity_resolution_candidate GROUP BY status ORDER BY count DESC")
    erc_by_status = {row["status"]: row["count"] for row in cur.fetchall()}
    table_counts["entity_resolution_candidate"] = {
        "row_count": erc_total,
        "by_status": erc_by_status,
    }

    # 18. node_state
    cur.execute("SELECT COUNT(*) AS total FROM node_state")
    table_counts["node_state"] = {"row_count": cur.fetchone()["total"]}

    # 19. claim (count active [superseded_by IS NULL])
    cur.execute("SELECT COUNT(*) AS total FROM claim")
    claim_total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS count FROM claim WHERE superseded_by IS NULL")
    claim_active = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) AS count FROM claim WHERE superseded_by IS NOT NULL")
    claim_superseded = cur.fetchone()["count"]
    table_counts["claim"] = {
        "row_count": claim_total,
        "active_count": claim_active,
        "superseded_count": claim_superseded,
    }

    # 20. card
    cur.execute("SELECT COUNT(*) AS total FROM card")
    table_counts["card"] = {"row_count": cur.fetchone()["total"]}

    # 21. node_card_map
    cur.execute("SELECT COUNT(*) AS total FROM node_card_map")
    table_counts["node_card_map"] = {"row_count": cur.fetchone()["total"]}

    # 22. ingestion_run
    cur.execute("SELECT COUNT(*) AS total FROM ingestion_run")
    table_counts["ingestion_run"] = {"row_count": cur.fetchone()["total"]}

    # 23. promotion_event
    cur.execute("SELECT COUNT(*) AS total FROM promotion_event")
    table_counts["promotion_event"] = {"row_count": cur.fetchone()["total"]}

    # 24. audit_event
    cur.execute("SELECT COUNT(*) AS total FROM audit_event")
    table_counts["audit_event"] = {"row_count": cur.fetchone()["total"]}

    # 25. governance_approval
    cur.execute("SELECT COUNT(*) AS total FROM governance_approval")
    table_counts["governance_approval"] = {"row_count": cur.fetchone()["total"]}

    # 26. validation_queue (count by status)
    cur.execute("SELECT COUNT(*) AS total FROM validation_queue")
    vq_total = cur.fetchone()["total"]
    cur.execute("SELECT status, COUNT(*) AS count FROM validation_queue GROUP BY status ORDER BY count DESC")
    vq_by_status = {row["status"]: row["count"] for row in cur.fetchall()}
    table_counts["validation_queue"] = {
        "row_count": vq_total,
        "by_status": vq_by_status,
    }

    # 27. quarantine_item (count by status)
    cur.execute("SELECT COUNT(*) AS total FROM quarantine_item")
    qi_total = cur.fetchone()["total"]
    cur.execute("SELECT status, COUNT(*) AS count FROM quarantine_item GROUP BY status ORDER BY count DESC")
    qi_by_status = {row["status"]: row["count"] for row in cur.fetchall()}
    table_counts["quarantine_item"] = {
        "row_count": qi_total,
        "by_status": qi_by_status,
    }

    # 28. candidate_entity
    cur.execute("SELECT COUNT(*) AS total FROM candidate_entity")
    table_counts["candidate_entity"] = {"row_count": cur.fetchone()["total"]}

    # 29. candidate_assertion
    cur.execute("SELECT COUNT(*) AS total FROM candidate_assertion")
    table_counts["candidate_assertion"] = {"row_count": cur.fetchone()["total"]}

    # COMPUTED METRICS
    # 1. Total unique predicates in assertion table (for active canonical assertions)
    cur.execute("""
        SELECT DISTINCT predicate 
        FROM assertion 
        WHERE canonical_status = 'canonical' 
          AND superseded_by_assertion_id IS NULL
        ORDER BY predicate
    """)
    predicates = [row["predicate"] for row in cur.fetchall()]
    unique_predicates_count = len(predicates)

    # 2. Total unique edge_types in edge table (for active edges)
    cur.execute("SELECT COUNT(DISTINCT edge_type) AS count FROM edge WHERE superseded_by IS NULL")
    unique_active_edge_types = cur.fetchone()["count"]

    # 3. Average confidence_score for canonical current assertions
    cur.execute("""
        SELECT AVG(confidence_score) AS avg_conf
        FROM assertion
        WHERE canonical_status = 'canonical'
          AND currentness_state = 'current'
    """)
    avg_confidence_canonical_current = cur.fetchone()["avg_conf"]

    # 4. Min/max/avg evidence count per assertion (via assertion_evidence)
    cur.execute("""
        SELECT 
            MIN(cnt) AS min_count,
            MAX(cnt) AS max_count,
            AVG(cnt) AS avg_count
        FROM (
            SELECT COUNT(*) AS cnt 
            FROM assertion_evidence 
            GROUP BY assertion_id
        )
    """)
    ev_row = cur.fetchone()

    cur.execute("""
        SELECT 
            MIN(cnt) AS min_count,
            MAX(cnt) AS max_count,
            AVG(cnt) AS avg_count
        FROM (
            SELECT a.assertion_id, COUNT(ae.evidence_id) AS cnt 
            FROM assertion a 
            LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
            GROUP BY a.assertion_id
        )
    """)
    ev_all_row = cur.fetchone()

    computed_metrics = {
        "unique_predicates_in_active_canonical_assertions": {
            "count": unique_predicates_count,
            "predicates": predicates,
        },
        "unique_edge_types_in_active_edges": unique_active_edge_types,
        "avg_confidence_score_canonical_current_assertions": avg_confidence_canonical_current,
        "evidence_count_per_assertion_via_assertion_evidence": {
            "min": ev_row["min_count"],
            "max": ev_row["max_count"],
            "avg": ev_row["avg_count"],
        },
        "evidence_count_per_assertion_including_zero_evidence": {
            "min": ev_all_row["min_count"],
            "max": ev_all_row["max_count"],
            "avg": ev_all_row["avg_count"],
        },
    }

    return {
        "table_counts": table_counts,
        "computed_metrics": computed_metrics,
    }


def main():
    conn = get_db_connection()
    try:
        results = collect_baseline_stats(conn)
        print(json.dumps(results, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
