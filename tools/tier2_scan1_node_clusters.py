#!/usr/bin/env python3
"""
SCAN 1: Node-Level Concept Clustering

Discovers semantic deduplication candidates across active nodes in the NEE knowledge graph:
1. Same-type near-duplicate clustering via fuzzy name matching (SequenceMatcher + Jaccard)
2. Cross-type concept clustering via cross-type name similarity and description overlap (> 0.80)
3. Display name vs canonical name drift within each node
4. Ambiguous aliases in canonical_alias table

All SQLite access is strictly read-only with PRAGMA query_only = ON.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import DEFAULT_DB_PATH as DB_PATH
from tools.tier2_common import get_tier2_db_connection as get_db_connection

STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "by", "with",
    "project", "initiative", "program", "team", "workstream", "phase", "stage",
})


def normalize_text(text: str | None) -> str:
    """
    Normalize text for fuzzy similarity comparison:
    - lowercase
    - strip punctuation (replace non-alphanumeric with space)
    - remove stopwords
    """
    if not text:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    tokens = [t for t in cleaned.split() if t and t not in STOPWORDS]
    return " ".join(tokens) if tokens else cleaned.strip()


def compute_string_similarity(
    norm_a: str,
    norm_b: str,
    tokens_a: Set[str] | None = None,
    tokens_b: Set[str] | None = None,
) -> Tuple[float, float, float]:
    """
    Compute SequenceMatcher ratio, token Jaccard similarity, and combined score.
    Combined score = 0.55 * seq_ratio + 0.45 * jaccard.
    Returns (combined_score, seq_ratio, jaccard).
    """
    if not norm_a and not norm_b:
        return 1.0, 1.0, 1.0
    if not norm_a or not norm_b:
        return 0.0, 0.0, 0.0
    if norm_a == norm_b:
        return 1.0, 1.0, 1.0

    seq_ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()

    if tokens_a is None:
        tokens_a = set(norm_a.split())
    if tokens_b is None:
        tokens_b = set(norm_b.split())

    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    jaccard = intersection / union if union > 0 else 0.0

    combined = 0.55 * seq_ratio + 0.45 * jaccard
    return round(combined, 4), round(seq_ratio, 4), round(jaccard, 4)


def scan_node_clusters(
    conn: Any,
    similarity_threshold: float = 0.65,
    desc_overlap_threshold: float = 0.80,
) -> Dict[str, Any]:
    """Execute complete Scan 1 node-level concept clustering."""
    cursor = conn.cursor()

    # Query all active nodes
    cursor.execute(
        """SELECT node_id, node_type, canonical_name, display_name, description
           FROM node
           WHERE active = 1
           ORDER BY node_type, canonical_name"""
    )
    raw_nodes = [dict(row) for row in cursor.fetchall()]
    total_active_nodes = len(raw_nodes)

    # Pre-process nodes for fast similarity computation
    processed_nodes: List[Dict[str, Any]] = []
    by_type: Dict[str, List[Dict[str, Any]]] = {}

    for n in raw_nodes:
        norm_name = normalize_text(n["canonical_name"])
        name_tokens = set(norm_name.split())
        name_chars = Counter(norm_name)
        name_len = len(norm_name)

        desc = (n["description"] or "").strip()
        norm_desc = normalize_text(desc)
        desc_tokens = set(norm_desc.split())
        desc_chars = Counter(norm_desc)
        desc_len = len(norm_desc)

        node_data = {
            "node_id": n["node_id"],
            "node_type": n["node_type"],
            "canonical_name": n["canonical_name"],
            "display_name": n["display_name"],
            "description": n["description"],
            "norm_name": norm_name,
            "name_tokens": name_tokens,
            "name_chars": name_chars,
            "name_len": name_len,
            "norm_desc": norm_desc,
            "desc_tokens": desc_tokens,
            "desc_chars": desc_chars,
            "desc_len": desc_len,
        }
        processed_nodes.append(node_data)
        by_type.setdefault(n["node_type"], []).append(node_data)

    # 1. SAME-TYPE CLUSTERS (same node_type, normalized name similarity >= threshold)
    same_type_clusters: List[Dict[str, Any]] = []

    for node_type, type_nodes in by_type.items():
        count = len(type_nodes)
        for i in range(count):
            na = type_nodes[i]
            la = na["name_len"]
            if la == 0:
                continue
            for j in range(i + 1, count):
                nb = type_nodes[j]
                lb = nb["name_len"]
                if lb == 0:
                    continue

                # Mathematical upper bound on SequenceMatcher ratio:
                # 2 * common_chars / (la + lb)
                cb = nb["name_chars"]
                common = sum(min(cnt, cb.get(c, 0)) for c, cnt in na["name_chars"].items())
                upper_bound = (2.0 * common) / (la + lb)
                if upper_bound < similarity_threshold:
                    continue

                sim, seq, jacc = compute_string_similarity(
                    na["norm_name"], nb["norm_name"], na["name_tokens"], nb["name_tokens"]
                )

                if sim >= similarity_threshold:
                    same_type_clusters.append({
                        "node_id_a": na["node_id"],
                        "name_a": na["canonical_name"],
                        "type_a": na["node_type"],
                        "node_id_b": nb["node_id"],
                        "name_b": nb["canonical_name"],
                        "type_b": nb["node_type"],
                        "similarity": sim,
                        "seq_ratio": seq,
                        "jaccard": jacc,
                    })

    same_type_clusters.sort(key=lambda x: x["similarity"], reverse=True)

    # 2. CROSS-TYPE CLUSTERS (different node_type)
    # Checks cross-type pairs for:
    # a) Name similarity >= similarity_threshold (semantic concept across types, e.g. anchor vs stakeholder)
    # b) Description overlap > desc_overlap_threshold (e.g. source vs source_pack)
    cross_type_matches: Dict[Tuple[str, str], Dict[str, Any]] = {}
    total_nodes = len(processed_nodes)

    for i in range(total_nodes):
        na = processed_nodes[i]
        for j in range(i + 1, total_nodes):
            nb = processed_nodes[j]
            if na["node_type"] == nb["node_type"]:
                continue

            pair_key = (na["node_id"], nb["node_id"])

            # Check name similarity
            name_sim, name_seq, name_jacc = 0.0, 0.0, 0.0
            la, lb = na["name_len"], nb["name_len"]
            if la > 0 and lb > 0:
                cb = nb["name_chars"]
                common = sum(min(cnt, cb.get(c, 0)) for c, cnt in na["name_chars"].items())
                if (2.0 * common / (la + lb)) >= similarity_threshold:
                    name_sim, name_seq, name_jacc = compute_string_similarity(
                        na["norm_name"], nb["norm_name"], na["name_tokens"], nb["name_tokens"]
                    )

            # Check description overlap
            desc_sim, desc_seq, desc_jacc = 0.0, 0.0, 0.0
            dla, dlb = na["desc_len"], nb["desc_len"]
            if dla > 0 and dlb > 0:
                dcb = nb["desc_chars"]
                common_d = sum(min(cnt, dcb.get(c, 0)) for c, cnt in na["desc_chars"].items())
                bound_d = 2.0 * common_d / (dla + dlb)
                inter_d = len(na["desc_tokens"] & nb["desc_tokens"])
                union_d = len(na["desc_tokens"] | nb["desc_tokens"])
                token_jacc = inter_d / union_d if union_d else 0.0

                if bound_d >= desc_overlap_threshold or token_jacc > desc_overlap_threshold:
                    desc_sim, desc_seq, desc_jacc = compute_string_similarity(
                        na["norm_desc"], nb["norm_desc"], na["desc_tokens"], nb["desc_tokens"]
                    )

            desc_qualifies = (
                desc_jacc > desc_overlap_threshold
                or desc_seq > desc_overlap_threshold
                or desc_sim > desc_overlap_threshold
            )
            name_qualifies = name_sim >= similarity_threshold

            if name_qualifies or desc_qualifies:
                # Use higher scoring dimension
                if name_sim >= desc_sim:
                    sim, seq, jacc = name_sim, name_seq, name_jacc
                else:
                    sim, seq, jacc = desc_sim, desc_seq, desc_jacc

                cross_type_matches[pair_key] = {
                    "node_id_a": na["node_id"],
                    "name_a": na["canonical_name"],
                    "type_a": na["node_type"],
                    "node_id_b": nb["node_id"],
                    "name_b": nb["canonical_name"],
                    "type_b": nb["node_type"],
                    "similarity": sim,
                    "seq_ratio": seq,
                    "jaccard": jacc,
                }

    cross_type_clusters = sorted(
        cross_type_matches.values(), key=lambda x: x["similarity"], reverse=True
    )

    # 3. DISPLAY VS CANONICAL DRIFT
    # Compute divergence between canonical_name and display_name for each node
    display_canonical_drift: List[Dict[str, Any]] = []

    for n in processed_nodes:
        canonical = n["canonical_name"] or ""
        display = n["display_name"] or ""

        norm_c = n["norm_name"]
        norm_d = normalize_text(display)

        if not norm_c and not norm_d:
            sim = 1.0
        elif not norm_c or not norm_d:
            sim = 0.0
        elif norm_c == norm_d:
            sim = 1.0
        else:
            sim, _, _ = compute_string_similarity(norm_c, norm_d)

        drift_score = round(1.0 - sim, 4)
        if drift_score > 0:
            display_canonical_drift.append({
                "node_id": n["node_id"],
                "canonical_name": canonical,
                "display_name": display,
                "drift_score": drift_score,
            })

    display_canonical_drift.sort(key=lambda x: x["drift_score"], reverse=True)

    # 4. AMBIGUOUS ALIASES
    cursor.execute(
        """SELECT canonical_alias_id, normalized_alias, entity_id, ambiguity_state
           FROM canonical_alias
           WHERE ambiguity_state = 'ambiguous'
           ORDER BY normalized_alias, canonical_alias_id"""
    )
    ambiguous_aliases = [
        {
            "canonical_alias_id": row["canonical_alias_id"],
            "normalized_alias": row["normalized_alias"],
            "entity_id": row["entity_id"],
            "ambiguity_state": row["ambiguity_state"],
        }
        for row in cursor.fetchall()
    ]

    return {
        "same_type_clusters": same_type_clusters,
        "cross_type_clusters": cross_type_clusters,
        "display_canonical_drift": display_canonical_drift,
        "ambiguous_aliases": ambiguous_aliases,
        "total_active_nodes": total_active_nodes,
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="SCAN 1: Node-Level Concept Clustering")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Path to NEE knowledge graph SQLite database (read-only)",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.65,
        help="Similarity threshold for same-type and cross-type name clusters (default: 0.65)",
    )
    parser.add_argument(
        "--desc-overlap-threshold",
        type=float,
        default=0.80,
        help="Overlap threshold for cross-type descriptions (default: 0.80)",
    )
    args = parser.parse_args()

    conn = get_db_connection(args.db_path)
    try:
        results = scan_node_clusters(
            conn,
            similarity_threshold=args.similarity_threshold,
            desc_overlap_threshold=args.desc_overlap_threshold,
        )
        print(json.dumps(results, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
