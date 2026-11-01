#!/usr/bin/env python3
"""
SCAN 4: Evidence Fragmentation Detection

Scans the NEE Knowledge Graph for duplicate and near-duplicate evidence items:
- Same source_id and source_version_id
- direct_quote text similarity >= 0.90 (after normalization) OR identical locator_value
- Different evidence_id
- Cross-references assertion_evidence to identify assertions linked to both evidence items
- Verifies zero identical evidence_fingerprint collisions
- Counts evidence items and duplicate pairs per source to identify fragmented sources

All SQLite operations are strictly read-only with PRAGMA query_only = ON.
"""

import argparse
import difflib
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import DEFAULT_DB_PATH
from tools.tier2_common import get_tier2_db_connection as get_db_connection

DEFAULT_SIMILARITY_THRESHOLD = 0.90


def normalize_text(text: Optional[str]) -> str:
    """Normalize text by converting NFKC unicode, lowercasing, and collapsing whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower().strip()
    return " ".join(text.split())


def check_fingerprint_integrity(conn: Any) -> List[Dict[str, Any]]:
    """Verify that no two evidence items share the exact same evidence_fingerprint."""
    sql = """
        SELECT evidence_fingerprint, COUNT(*) AS cnt
        FROM evidence_item
        GROUP BY evidence_fingerprint
        HAVING cnt > 1
    """
    rows = conn.execute(sql).fetchall()
    return [{"evidence_fingerprint": r["evidence_fingerprint"], "count": r["cnt"]} for r in rows]


def scan_evidence_fragmentation(
    conn: Any,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Dict[str, Any]:
    # 1. Fingerprint integrity check
    fp_dups = check_fingerprint_integrity(conn)
    if fp_dups:
        sys.stderr.write(f"[WARNING] Detected {len(fp_dups)} duplicate evidence_fingerprints!\n")

    # 2. Pre-fetch assertion_evidence mapping: evidence_id -> sorted list of assertion_ids
    cur = conn.cursor()
    cur.execute(
        "SELECT assertion_id, evidence_id, evidence_relationship, independent_corroboration "
        "FROM assertion_evidence"
    )
    evidence_to_assertions: Dict[str, List[str]] = {}
    for r in cur.fetchall():
        evidence_to_assertions.setdefault(r["evidence_id"], []).append(r["assertion_id"])

    # Ensure sorted unique lists
    for ev_id in evidence_to_assertions:
        evidence_to_assertions[ev_id] = sorted(set(evidence_to_assertions[ev_id]))

    # 3. Retrieve all evidence items ordered by source_id, source_version_id, locator_value
    evidence_sql = """
        SELECT evidence_id, evidence_fingerprint, source_id, source_version_id,
               locator_kind, locator_value, page_number, section_name,
               paragraph_number, transcript_timestamp, direct_quote,
               evidence_directness, source_authority_rank, extraction_confidence,
               corroboration_group
        FROM evidence_item
        ORDER BY source_id, source_version_id, locator_value
    """
    evidence_rows = conn.execute(evidence_sql).fetchall()
    total_evidence_items = len(evidence_rows)

    # 4. Count total evidence items per source_id
    source_counts: Dict[str, int] = {}
    for r in evidence_rows:
        source_counts[r["source_id"]] = source_counts.get(r["source_id"], 0) + 1

    # 5. Group evidence items by (source_id, source_version_id)
    grouped: Dict[Tuple[str, str], List[Any]] = {}
    for r in evidence_rows:
        grouped.setdefault((r["source_id"], r["source_version_id"]), []).append(r)

    quote_duplicates: List[Dict[str, Any]] = []
    locator_duplicates: List[Dict[str, Any]] = []
    source_dup_pairs: Dict[str, Set[Tuple[str, str]]] = {}
    all_unique_dup_pairs: Set[Tuple[str, str]] = set()

    # Pre-calculate minimum length ratio needed for similarity >= similarity_threshold
    # SequenceMatcher ratio = 2*M / (la + lb) <= 2*min / (min + max)
    # 2*min / (min + max) >= T  <=>  min / max >= T / (2 - T)
    min_len_ratio = similarity_threshold / (2.0 - similarity_threshold)

    for (source_id, _source_ver_id), items in grouped.items():
        n = len(items)
        if n < 2:
            continue

        norm_quotes = [normalize_text(it["direct_quote"]) for it in items]
        quote_lens = [len(q) for q in norm_quotes]

        for i in range(n):
            a = items[i]
            qa = norm_quotes[i]
            la = quote_lens[i]
            loc_a = a["locator_value"]
            ev_id_a = a["evidence_id"]

            for j in range(i + 1, n):
                b = items[j]
                qb = norm_quotes[j]
                lb = quote_lens[j]
                loc_b = b["locator_value"]
                ev_id_b = b["evidence_id"]

                # Ensure canonical pair ordering
                if ev_id_a < ev_id_b:
                    pair_key = (ev_id_a, ev_id_b)
                else:
                    pair_key = (ev_id_b, ev_id_a)

                is_loc_dup = False
                is_quote_dup = False
                sim = 0.0

                # Compute direct_quote similarity
                if la > 0 and lb > 0:
                    min_l, max_l = (la, lb) if la <= lb else (lb, la)
                    if (min_l / max_l) >= min(min_len_ratio, 0.85 / (2.0 - 0.85)):
                        sim = difflib.SequenceMatcher(None, qa, qb).ratio()

                # Check identical locator_value only when quote texts also overlap (sim >= 0.85) or both empty
                if loc_a and loc_b and loc_a == loc_b and (sim >= 0.85 or (la == 0 and lb == 0)):
                    is_loc_dup = True
                    locator_duplicates.append({
                        "evidence_id_a": pair_key[0],
                        "evidence_id_b": pair_key[1],
                        "source_id": source_id,
                        "locator_value": loc_a,
                        "quote_a": a["direct_quote"],
                        "quote_b": b["direct_quote"],
                    })

                # Check direct_quote similarity >= threshold
                if sim >= similarity_threshold:
                    is_quote_dup = True
                    quote_duplicates.append({
                        "evidence_id_a": pair_key[0],
                        "evidence_id_b": pair_key[1],
                        "source_id": source_id,
                        "quote_a": a["direct_quote"],
                        "quote_b": b["direct_quote"],
                        "similarity": round(sim, 4),
                        "locator_a": loc_a,
                        "locator_b": loc_b,
                        "linked_assertions_a": evidence_to_assertions.get(pair_key[0], []),
                        "linked_assertions_b": evidence_to_assertions.get(pair_key[1], []),
                    })

                if is_loc_dup or is_quote_dup:
                    all_unique_dup_pairs.add(pair_key)
                    source_dup_pairs.setdefault(source_id, set()).add(pair_key)

    # Sort duplicates for deterministic output
    quote_duplicates.sort(key=lambda d: (d["source_id"], -d["similarity"], d["evidence_id_a"], d["evidence_id_b"]))
    locator_duplicates.sort(key=lambda d: (d["source_id"], d["locator_value"], d["evidence_id_a"], d["evidence_id_b"]))

    # 6. Fragmented sources
    fragmented_sources: List[Dict[str, Any]] = []
    for src_id, total_cnt in source_counts.items():
        dup_count = len(source_dup_pairs.get(src_id, set()))
        if dup_count > 0:
            fragmented_sources.append({
                "source_id": src_id,
                "evidence_count": total_cnt,
                "potential_duplicates": dup_count,
            })

    fragmented_sources.sort(key=lambda s: (-s["potential_duplicates"], -s["evidence_count"], s["source_id"]))

    total_duplicate_pairs = len(all_unique_dup_pairs)

    return {
        "quote_duplicates": quote_duplicates,
        "locator_duplicates": locator_duplicates,
        "fragmented_sources": fragmented_sources,
        "total_evidence_items": total_evidence_items,
        "total_duplicate_pairs": total_duplicate_pairs,
    }


def main():
    parser = argparse.ArgumentParser(description="Scan for evidence fragmentation and duplicate evidence items.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to nee_graph.sqlite database",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help="Direct quote similarity threshold (default: 0.90)",
    )
    args = parser.parse_args()

    conn = get_db_connection(args.db)
    try:
        results = scan_evidence_fragmentation(conn, similarity_threshold=args.threshold)
        print(json.dumps(results, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
