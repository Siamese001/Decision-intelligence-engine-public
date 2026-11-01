"""
dual_pipeline/graph_consolidation_core/detect/evidence.py

Detects evidence item fragmentation and duplicate locator occurrences in the active graph.
Strictly read-only execution.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Set, Tuple

from ..constants import (
    DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD,
)


def normalize_quote(text: Optional[str]) -> str:
    """Normalize quote string for similarity comparison."""
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def compute_quote_similarity(quote_a: Optional[str], quote_b: Optional[str]) -> float:
    """Compute normalized sequence matcher ratio between two evidence quotes."""
    qa = normalize_quote(quote_a)
    qb = normalize_quote(quote_b)
    if not qa and not qb:
        return 1.0
    if not qa or not qb:
        return 0.0
    if qa == qb:
        return 1.0
    return round(difflib.SequenceMatcher(None, qa, qb).ratio(), 4)


def _pick_evidence_winner(
    ev1: Dict[str, Any],
    ev2: Dict[str, Any],
    links1: int,
    links2: int,
) -> Tuple[str, str, str]:
    rank1 = ev1.get("source_authority_rank") or 1
    rank2 = ev2.get("source_authority_rank") or 1
    conf1 = float(ev1.get("extraction_confidence") or 0.8)
    conf2 = float(ev2.get("extraction_confidence") or 0.8)

    if rank1 != rank2:
        winner_id, loser_id = (ev1["evidence_id"], ev2["evidence_id"]) if rank1 > rank2 else (ev2["evidence_id"], ev1["evidence_id"])
        return winner_id, loser_id, f"higher authority rank ({max(rank1, rank2)} > {min(rank1, rank2)})"

    if abs(conf1 - conf2) >= 0.05:
        winner_id, loser_id = (ev1["evidence_id"], ev2["evidence_id"]) if conf1 > conf2 else (ev2["evidence_id"], ev1["evidence_id"])
        return winner_id, loser_id, f"higher extraction confidence ({max(conf1, conf2):.2f} > {min(conf1, conf2):.2f})"

    if links1 != links2:
        winner_id, loser_id = (ev1["evidence_id"], ev2["evidence_id"]) if links1 > links2 else (ev2["evidence_id"], ev1["evidence_id"])
        return winner_id, loser_id, f"higher assertion connectivity ({max(links1, links2)} > {min(links1, links2)})"

    winner_id = min(ev1["evidence_id"], ev2["evidence_id"])
    loser_id = max(ev1["evidence_id"], ev2["evidence_id"])
    return winner_id, loser_id, "lexical tie-break"


def _compare_evidence_pair(
    ev1: Dict[str, Any],
    ev2: Dict[str, Any],
    asrt_links: Dict[str, int],
    similarity_threshold: float,
    seen_evidence_pairs: Set[frozenset[str]],
) -> Optional[Dict[str, Any]]:
    pair_key = frozenset({ev1["evidence_id"], ev2["evidence_id"]})
    if pair_key in seen_evidence_pairs:
        return None
    seen_evidence_pairs.add(pair_key)

    sim = compute_quote_similarity(ev1.get("direct_quote"), ev2.get("direct_quote"))
    links1 = asrt_links.get(ev1["evidence_id"], 0)
    links2 = asrt_links.get(ev2["evidence_id"], 0)

    winner_id, loser_id, reason = _pick_evidence_winner(ev1, ev2, links1, links2)
    return {
        "evidence_id_1": ev1["evidence_id"],
        "evidence_id_2": ev2["evidence_id"],
        "quote_similarity": sim,
        "recommended_survivor_id": winner_id,
        "recommended_retired_id": loser_id,
        "selection_reason": reason,
        "hitl_required": sim < similarity_threshold,
    }


def _process_locator_items(
    items: List[Dict[str, Any]],
    asrt_links: Dict[str, int],
    similarity_threshold: float,
    seen_evidence_pairs: Set[frozenset[str]],
) -> List[Dict[str, Any]]:
    pairs_in_group: List[Dict[str, Any]] = []
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            pair_dict = _compare_evidence_pair(
                items[i], items[j], asrt_links, similarity_threshold, seen_evidence_pairs
            )
            if pair_dict is not None:
                pairs_in_group.append(pair_dict)
    return pairs_in_group


def _fetch_locator_items(cur: Any, h: Dict[str, Any]) -> List[Dict[str, Any]]:
    cur.execute("""
        SELECT evidence_id, evidence_fingerprint, source_id, source_version_id,
               locator_kind, locator_value, direct_quote, source_authority_rank,
               extraction_confidence, corroboration_group
        FROM evidence_item
        WHERE source_id = ?
          AND (source_version_id = ? OR (source_version_id IS NULL AND ? IS NULL))
          AND (locator_kind = ? OR (locator_kind IS NULL AND ? IS NULL))
          AND locator_value = ?
        ORDER BY source_authority_rank DESC, extraction_confidence DESC, evidence_id
    """, (h["source_id"], h["source_version_id"], h["source_version_id"],
          h["locator_kind"], h["locator_kind"], h["locator_value"]))
    return [dict(r) for r in cur.fetchall()]


def detect_evidence_fragmentation(
    conn: Any,
    similarity_threshold: float = DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD,
) -> Dict[str, Any]:
    """Scans evidence_item for duplicate locators and quote fragmentation."""
    cur = conn.cursor()
    cur.execute("PRAGMA query_only = ON;")

    cur.execute("SELECT evidence_id, count(*) as cnt FROM assertion_evidence GROUP BY evidence_id")
    asrt_links: Dict[str, int] = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("""
        SELECT source_id, source_version_id, locator_kind, locator_value, count(*) as cnt
        FROM evidence_item
        WHERE locator_value IS NOT NULL AND trim(locator_value) != ''
        GROUP BY source_id, source_version_id, locator_kind, locator_value
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    dup_locator_headers = [dict(r) for r in cur.fetchall()]

    locator_clusters: List[Dict[str, Any]] = []
    seen_evidence_pairs: Set[frozenset[str]] = set()

    for h in dup_locator_headers:
        items = _fetch_locator_items(cur, h)
        if len(items) < 2:
            continue

        pairs_in_group = _process_locator_items(
            items, asrt_links, similarity_threshold, seen_evidence_pairs
        )
        locator_clusters.append({
            "source_id": h["source_id"],
            "source_version_id": h["source_version_id"],
            "locator_kind": h["locator_kind"],
            "locator_value": h["locator_value"],
            "items_count": len(items),
            "evidence_ids": [it["evidence_id"] for it in items],
            "pairs": pairs_in_group,
        })

    return {
        "duplicate_locator_groups_count": len(locator_clusters),
        "total_fragmented_pairs_count": len(seen_evidence_pairs),
        "locator_clusters": locator_clusters,
    }
