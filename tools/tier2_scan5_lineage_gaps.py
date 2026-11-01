#!/usr/bin/env python3
"""
SCAN 5: Edge Lineage Gap Audit

Audits the NEE Knowledge Graph for edge and assertion lineage gaps:
- Unmigrated legacy edges
- Canonical assertions lacking extraction_run lineage
- Assertions referencing orphaned extraction_runs
- Assertion vs evidence source ID mismatches
- Active edges with unregistered edge types
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import DEFAULT_DB_PATH as DB_PATH
from tools.tier2_common import get_tier2_db_connection as get_db_connection


def scan_lineage_gaps(db_path: Path = DB_PATH) -> dict:
    conn = get_db_connection(db_path)

    try:
        # Total active edges
        total_active_edges = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE superseded_by IS NULL"
        ).fetchone()[0]

        # 3.a Legacy edges without assertion migration
        q_unmigrated = """
            SELECT e.edge_id, e.from_node_id, e.edge_type, e.to_node_id, e.confidence, e.source_id
            FROM edge e
            WHERE e.superseded_by IS NULL
              AND e.edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)
        """
        unmigrated_edges = [
            {
                "edge_id": row["edge_id"],
                "from_node_id": row["from_node_id"],
                "edge_type": row["edge_type"],
                "to_node_id": row["to_node_id"],
                "confidence": row["confidence"],
                "source_id": row["source_id"],
            }
            for row in conn.execute(q_unmigrated).fetchall()
        ]

        # 3.b Assertions without extraction_run lineage
        q_no_extraction_run = """
            SELECT assertion_id, subject_node_id, predicate, extraction_run_id, extraction_method
            FROM assertion
            WHERE extraction_run_id IS NULL
              AND canonical_status = 'canonical'
        """
        no_extraction_run = [
            {
                "assertion_id": row["assertion_id"],
                "subject_node_id": row["subject_node_id"],
                "predicate": row["predicate"],
                "extraction_method": row["extraction_method"],
            }
            for row in conn.execute(q_no_extraction_run).fetchall()
        ]

        # 3.c Assertions referencing non-existent extraction_runs
        q_orphaned_extraction_refs = """
            SELECT a.assertion_id, a.extraction_run_id
            FROM assertion a
            LEFT JOIN extraction_run er ON a.extraction_run_id = er.extraction_run_id
            WHERE a.extraction_run_id IS NOT NULL AND er.extraction_run_id IS NULL
        """
        orphaned_extraction_refs = [
            {
                "assertion_id": row["assertion_id"],
                "extraction_run_id": row["extraction_run_id"],
            }
            for row in conn.execute(q_orphaned_extraction_refs).fetchall()
        ]

        # 3.d Assertion-evidence source mismatches
        q_source_mismatches = """
            SELECT a.assertion_id, a.primary_source_id AS assertion_source,
                   ei.source_id AS evidence_source, ei.evidence_id
            FROM assertion a
            JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
            JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id
            WHERE a.primary_source_id != ei.source_id
              AND a.canonical_status = 'canonical'
        """
        source_mismatches = [
            {
                "assertion_id": row["assertion_id"],
                "assertion_source": row["assertion_source"],
                "evidence_source": row["evidence_source"],
                "evidence_id": row["evidence_id"],
            }
            for row in conn.execute(q_source_mismatches).fetchall()
        ]

        # 3.e Edge type registry gaps
        q_unregistered_edge_types = """
            SELECT DISTINCT e.edge_type
            FROM edge e
            WHERE e.edge_type NOT IN (SELECT edge_type_id FROM edge_type_registry WHERE registry_state = 'active')
              AND e.superseded_by IS NULL
        """
        unregistered_edge_types = [
            row["edge_type"] for row in conn.execute(q_unregistered_edge_types).fetchall()
        ]

        total_lineage_gaps = (
            len(unmigrated_edges)
            + len(no_extraction_run)
            + len(orphaned_extraction_refs)
            + len(source_mismatches)
            + len(unregistered_edge_types)
        )

        return {
            "unmigrated_edges": unmigrated_edges,
            "no_extraction_run": no_extraction_run,
            "orphaned_extraction_refs": orphaned_extraction_refs,
            "source_mismatches": source_mismatches,
            "unregistered_edge_types": unregistered_edge_types,
            "total_active_edges": total_active_edges,
            "total_lineage_gaps": total_lineage_gaps,
        }
    finally:
        conn.close()


def main():
    result = scan_lineage_gaps()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
