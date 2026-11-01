#!/usr/bin/env python3
"""
SCAN 2: Predicate Synonym Detection

Discovers semantic deduplication candidates across edge and assertion predicates:
1. Connects read-only to nee_graph.sqlite with PRAGMA query_only = ON.
2. Queries assertion_predicate_registry for active predicates.
3. Queries edge_type_registry for active edge types.
4. Groups predicates by relationship_family and computes string similarity on canonical_predicate.
5. Identifies one-off predicates used by <= 2 assertions.
6. Cross-references edge_type_registry canonical_predicate with assertion_predicate_registry predicate.
7. Identifies predicates with identical cardinality + object_kind that may be semantic synonyms.
"""

from __future__ import annotations

import difflib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import get_tier2_db_connection as get_db_connection


def compute_string_similarity(a: str, b: str) -> float:
    """Compute string similarity ratio using difflib.SequenceMatcher, rounded to 4 decimals."""
    if a == b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def find_cardinality_object_kind_synonyms(
    assertion_predicates: List[Dict[str, Any]],
    threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Find active assertion predicate pairs with identical cardinality + object_kind
    whose string similarity meets or exceeds the threshold.
    """
    candidates = []
    n = len(assertion_predicates)
    for i in range(n):
        for j in range(i + 1, n):
            p1 = assertion_predicates[i]
            p2 = assertion_predicates[j]
            if (
                p1["cardinality"] == p2["cardinality"]
                and p1["object_kind"] == p2["object_kind"]
            ):
                sim = compute_string_similarity(p1["predicate"], p2["predicate"])
                if sim >= threshold:
                    candidates.append({
                        "predicate_1": p1["predicate"],
                        "predicate_2": p2["predicate"],
                        "cardinality": p1["cardinality"],
                        "object_kind": p1["object_kind"],
                        "similarity": sim,
                    })
    candidates.sort(key=lambda x: (-x["similarity"], x["predicate_1"]))
    return candidates


def run_predicate_synonym_scan(conn: Any) -> Dict[str, Any]:
    """Execute SCAN 2 predicate synonym discovery queries and assemble report."""
    cur = conn.cursor()

    # Step 3: Query assertion_predicate_registry for all active predicates
    cur.execute(
        """
        SELECT predicate, object_kind, cardinality, evidence_required,
               temporal_requirement, symmetric_mode, conflict_policy, definition
        FROM assertion_predicate_registry
        WHERE active = 1
        ORDER BY predicate
        """
    )
    assertion_predicates = [dict(r) for r in cur.fetchall()]
    total_active_predicates = len(assertion_predicates)
    assert_pred_map = {r["predicate"]: r for r in assertion_predicates}

    # Step 4: Query edge_type_registry for all active edge types
    cur.execute(
        """
        SELECT edge_type_id, canonical_predicate, definition, relationship_family,
               direction, inverse_predicate, cardinality, evidence_required
        FROM edge_type_registry
        WHERE registry_state = 'active'
        ORDER BY edge_type_id
        """
    )
    edge_types = [dict(r) for r in cur.fetchall()]
    total_active_edge_types = len(edge_types)

    # Step 7 (prep): Query assertion table for usage counts per legacy_predicate
    cur.execute(
        """
        SELECT legacy_predicate, COUNT(*) AS count
        FROM assertion
        WHERE legacy_predicate IS NOT NULL
        GROUP BY legacy_predicate
        """
    )
    usage_by_legacy = {r["legacy_predicate"]: r["count"] for r in cur.fetchall()}

    # Step 5 & 6: Group predicates by relationship_family and compute string similarity on canonical_predicate
    family_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for et in edge_types:
        family_map[et["relationship_family"]].append(et)

    synonym_groups = []
    for fam in sorted(family_map.keys()):
        items = family_map[fam]
        canonical = items[0]["canonical_predicate"]
        obj_kind = assert_pred_map.get(canonical, {}).get("object_kind", "entity")

        pred_list = []
        sims = []
        for it in items:
            p_name = it["edge_type_id"]
            u_count = usage_by_legacy.get(p_name, 0)
            card = it["cardinality"]

            pred_list.append({
                "predicate": p_name,
                "usage_count": u_count,
                "cardinality": card,
                "object_kind": obj_kind,
            })

            sim = compute_string_similarity(p_name, canonical)
            sims.append(sim)

        # Sort predicates within group: usage_count descending, then predicate ascending
        pred_list.sort(key=lambda x: (-x["usage_count"], x["predicate"]))
        avg_similarity = round(sum(sims) / len(sims), 4) if sims else 0.0

        synonym_groups.append({
            "family": fam,
            "predicates": pred_list,
            "similarity": avg_similarity,
        })

    # Step 7: Identify one-off predicates (predicates used by <= 2 assertions)
    one_off_predicates = []
    for et in edge_types:
        p_name = et["edge_type_id"]
        u_count = usage_by_legacy.get(p_name, 0)
        if u_count <= 2:
            canonical = et["canonical_predicate"]
            sim = compute_string_similarity(p_name, canonical)
            one_off_predicates.append({
                "predicate": p_name,
                "usage_count": u_count,
                "definition": et["definition"],
                "potential_canonical_match": canonical,
                "match_similarity": sim,
            })

    # Sort one-offs by usage_count ascending, then predicate name ascending
    one_off_predicates.sort(key=lambda x: (x["usage_count"], x["predicate"]))

    # Step 8: Cross-reference edge_type_registry canonical_predicate with assertion_predicate_registry predicate
    edge_assertion_overlaps = []
    for et in sorted(edge_types, key=lambda x: x["edge_type_id"]):
        canon = et["canonical_predicate"]
        if canon in assert_pred_map:
            sim = compute_string_similarity(canon, canon)
            edge_assertion_overlaps.append({
                "edge_type_id": et["edge_type_id"],
                "edge_predicate": canon,
                "assertion_predicate": canon,
                "similarity": sim,
            })

    return {
        "synonym_groups": synonym_groups,
        "one_off_predicates": one_off_predicates,
        "edge_assertion_overlaps": edge_assertion_overlaps,
        "total_active_predicates": total_active_predicates,
        "total_active_edge_types": total_active_edge_types,
    }


def main() -> None:
    conn = get_db_connection()
    try:
        results = run_predicate_synonym_scan(conn)
        print(json.dumps(results, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
