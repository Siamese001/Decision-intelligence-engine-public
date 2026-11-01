#!/usr/bin/env python3
"""
SCAN 3: Overlapping Assertion Detection

Scans the NEE Knowledge Graph for overlapping and duplicate assertions:
- Assertions with same subject and predicate having matching object literals (similarity >= 0.85)
  with differing confidence scores
- Assertions with same subject and predicate having identical object node IDs
  with differing confidence scores
- Multi-source same-fact assertions (same subject, predicate, object from different sources)
- Evidence link counts and assertion metadata (assertion_type, confidence_label)

All SQLite operations are strictly read-only with PRAGMA query_only = ON.
"""

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import DEFAULT_DB_PATH
from tools.tier2_common import get_tier2_db_connection as get_db_connection


def calculate_literal_similarity(lit_a: str, lit_b: str) -> float:
    norm_a = lit_a.strip().lower()
    norm_b = lit_b.strip().lower()
    return difflib.SequenceMatcher(None, norm_a, norm_b).ratio()


def scan_assertion_overlaps(conn: Any) -> Dict[str, Any]:
    cur = conn.cursor()

    # Pre-fetch evidence counts per assertion
    evidence_counts: Dict[str, int] = {}
    cur.execute("SELECT assertion_id, COUNT(*) AS cnt FROM assertion_evidence GROUP BY assertion_id")
    for r in cur.fetchall():
        evidence_counts[r["assertion_id"]] = r["cnt"]

    # Candidate set query
    candidate_sql = """
        SELECT a.assertion_id, a.subject_node_id, a.predicate, a.object_node_id, 
               a.object_literal, a.assertion_type, a.confidence_label, a.confidence_score, 
               a.primary_source_id, a.extraction_run_id, a.recorded_at, 
               n.canonical_name AS subject_name
        FROM assertion a
        JOIN node n ON a.subject_node_id = n.node_id
        WHERE a.canonical_status = 'canonical'
          AND a.currentness_state = 'current'
          AND a.superseded_by_assertion_id IS NULL
        ORDER BY a.subject_node_id, a.predicate
    """
    rows = cur.execute(candidate_sql).fetchall()
    total_canonical_current = len(rows)

    literal_overlaps: List[Dict[str, Any]] = []
    object_node_duplicates: List[Dict[str, Any]] = []
    multi_source_same_fact: List[Dict[str, Any]] = []

    # Pairwise comparison
    for i in range(total_canonical_current):
        a = rows[i]
        for j in range(i + 1, total_canonical_current):
            b = rows[j]

            # Fast subject filter
            if a["subject_node_id"] != b["subject_node_id"]:
                break  # Ordered by subject_node_id, can break inner loop

            # Predicate comparison
            if a["predicate"] != b["predicate"]:
                continue

            # Case 1: Object Literal Overlap
            if a["object_literal"] is not None and b["object_literal"] is not None:
                sim = calculate_literal_similarity(a["object_literal"], b["object_literal"])
                if sim >= 0.85:
                    # Item 3: Different confidence_score values defines overlap
                    if a["confidence_score"] != b["confidence_score"]:
                        literal_overlaps.append({
                            "assertion_id_a": a["assertion_id"],
                            "assertion_id_b": b["assertion_id"],
                            "subject_name": a["subject_name"],
                            "predicate": a["predicate"],
                            "literal_a": a["object_literal"],
                            "literal_b": b["object_literal"],
                            "similarity": round(sim, 4),
                            "confidence_a": a["confidence_score"],
                            "confidence_b": b["confidence_score"],
                            "confidence_label_a": a["confidence_label"],
                            "confidence_label_b": b["confidence_label"],
                            "assertion_type_a": a["assertion_type"],
                            "assertion_type_b": b["assertion_type"],
                            "source_a": a["primary_source_id"],
                            "source_b": b["primary_source_id"],
                            "evidence_count_a": evidence_counts.get(a["assertion_id"], 0),
                            "evidence_count_b": evidence_counts.get(b["assertion_id"], 0),
                        })

                    # Item 4: Same subject+predicate+object from different sources
                    if a["primary_source_id"] != b["primary_source_id"]:
                        multi_source_same_fact.append({
                            "assertion_id_a": a["assertion_id"],
                            "assertion_id_b": b["assertion_id"],
                            "subject_name": a["subject_name"],
                            "predicate": a["predicate"],
                            "literal_a": a["object_literal"],
                            "literal_b": b["object_literal"],
                            "similarity": round(sim, 4),
                            "confidence_a": a["confidence_score"],
                            "confidence_b": b["confidence_score"],
                            "confidence_label_a": a["confidence_label"],
                            "confidence_label_b": b["confidence_label"],
                            "assertion_type_a": a["assertion_type"],
                            "assertion_type_b": b["assertion_type"],
                            "source_a": a["primary_source_id"],
                            "source_b": b["primary_source_id"],
                            "evidence_count_a": evidence_counts.get(a["assertion_id"], 0),
                            "evidence_count_b": evidence_counts.get(b["assertion_id"], 0),
                        })

            # Case 2: Object Node Overlap / Duplicates
            elif a["object_node_id"] is not None and b["object_node_id"] is not None:
                if a["object_node_id"] == b["object_node_id"]:
                    # Item 3: Different confidence_score values defines overlap
                    if a["confidence_score"] != b["confidence_score"]:
                        object_node_duplicates.append({
                            "assertion_id_a": a["assertion_id"],
                            "assertion_id_b": b["assertion_id"],
                            "subject_name": a["subject_name"],
                            "predicate": a["predicate"],
                            "object_node_id": a["object_node_id"],
                            "confidence_a": a["confidence_score"],
                            "confidence_b": b["confidence_score"],
                            "confidence_label_a": a["confidence_label"],
                            "confidence_label_b": b["confidence_label"],
                            "assertion_type_a": a["assertion_type"],
                            "assertion_type_b": b["assertion_type"],
                            "source_a": a["primary_source_id"],
                            "source_b": b["primary_source_id"],
                            "evidence_count_a": evidence_counts.get(a["assertion_id"], 0),
                            "evidence_count_b": evidence_counts.get(b["assertion_id"], 0),
                        })

                    # Item 4: Same subject+predicate+object from different sources
                    if a["primary_source_id"] != b["primary_source_id"]:
                        multi_source_same_fact.append({
                            "assertion_id_a": a["assertion_id"],
                            "assertion_id_b": b["assertion_id"],
                            "subject_name": a["subject_name"],
                            "predicate": a["predicate"],
                            "object_node_id": a["object_node_id"],
                            "confidence_a": a["confidence_score"],
                            "confidence_b": b["confidence_score"],
                            "confidence_label_a": a["confidence_label"],
                            "confidence_label_b": b["confidence_label"],
                            "assertion_type_a": a["assertion_type"],
                            "assertion_type_b": b["assertion_type"],
                            "source_a": a["primary_source_id"],
                            "source_b": b["primary_source_id"],
                            "evidence_count_a": evidence_counts.get(a["assertion_id"], 0),
                            "evidence_count_b": evidence_counts.get(b["assertion_id"], 0),
                        })

    total_overlaps_found = len(literal_overlaps) + len(object_node_duplicates)

    return {
        "literal_overlaps": literal_overlaps,
        "object_node_duplicates": object_node_duplicates,
        "multi_source_same_fact": multi_source_same_fact,
        "total_canonical_current": total_canonical_current,
        "total_overlaps_found": total_overlaps_found,
    }


def main():
    parser = argparse.ArgumentParser(description="Scan for overlapping and duplicate assertions.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to nee_graph.sqlite database",
    )
    args = parser.parse_args()

    conn = get_db_connection(args.db)
    try:
        results = scan_assertion_overlaps(conn)
        print(json.dumps(results, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
