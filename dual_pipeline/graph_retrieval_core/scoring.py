"""Graph path scoring, increments, and comparison functions."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from ..core import stable_id
from .constants import (
    _MATERIALITY_BOOST,
    DEFAULT_CONFIDENCE_SCORE,
    GRAPH_RETRIEVAL_POLICY_BODY,
    MAX_CONFIDENCE_BOOST,
    MAX_TEMPORAL_BOOST,
    TEMPORAL_DECAY_LAMBDA,
)


def _parse_date_epoch(date_str: Any) -> float | None:
    if not date_str or not isinstance(date_str, str):
        return None
    cleaned = date_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def compute_seed_recency_boost(
    recorded_at: Any,
    reference_epoch: float | None = None,
) -> float:
    """Compute a recency boost for node_context and hybrid seed records so active sprint evidence outranks stale SOWs."""
    edge_epoch = _parse_date_epoch(recorded_at)
    if edge_epoch is None:
        return 0.0
    now_ep = reference_epoch if reference_epoch is not None else time.time()
    delta_days = max(0.0, (now_ep - edge_epoch) / 86400.0)
    freshness_factor = math.exp(-TEMPORAL_DECAY_LAMBDA * (delta_days / 30.0))
    effective_max_boost = max(0.25, MAX_TEMPORAL_BOOST)
    return round(effective_max_boost * (freshness_factor - 0.5), 6)


def _base_seed_score(result: dict[str, Any]) -> float:
    rank = result.get("fused_rank")
    base = 1.0 / (1.0 + rank) if isinstance(rank, int) and rank > 0 else 0.1
    recency = compute_seed_recency_boost(
        result.get("recorded_at") or (result.get("record") or {}).get("recorded_at")
    )
    return base + max(0.0, recency)


def _edge_increment(
    edge: dict[str, Any],
    *,
    requested_relationships: set[str],
    destination_degree: int,
    hop: int,
    reference_epoch: float | None = None,
) -> tuple[float, float]:
    is_cap_bridge = edge["relationship_family"] == "SHARES_CAPABILITY_DOMAIN"
    relationship_boost = (
        0.18 if edge["relationship_family"] in requested_relationships else (0.14 if is_cap_bridge else 0.0)
    )
    evidence_boost = min(0.06, 0.015 * len(edge["evidence_ids"]))
    materiality_boost = _MATERIALITY_BOOST.get(
        str(edge.get("materiality") or "").casefold(),
        0.0,
    )
    hub_overage = max(
        0,
        destination_degree - GRAPH_RETRIEVAL_POLICY_BODY["hub_degree_threshold"],
    )
    hub_penalty = 0.0 if is_cap_bridge else 0.035 * math.log1p(hub_overage)
    length_penalty = 0.20 * hop

    # GS-10 Confidence Calibration Boost
    raw_conf = edge.get("confidence_score")
    conf = float(raw_conf) if raw_conf is not None else DEFAULT_CONFIDENCE_SCORE
    conf_clamped = max(0.0, min(1.0, conf))
    confidence_boost = MAX_CONFIDENCE_BOOST * (conf_clamped - 0.5)

    # GS-10 Temporal Freshness Decay
    edge_epoch = _parse_date_epoch(edge.get("recorded_at")) or _parse_date_epoch(edge.get("created_at"))
    if edge_epoch is not None:
        now_ep = reference_epoch if reference_epoch is not None else time.time()
        delta_days = max(0.0, (now_ep - edge_epoch) / 86400.0)
        freshness_factor = math.exp(-TEMPORAL_DECAY_LAMBDA * (delta_days / 30.0))
        temporal_boost = max(0.25, MAX_TEMPORAL_BOOST) * (freshness_factor - 0.5)
    else:
        temporal_boost = 0.0

    path_increment = (
        0.16
        + relationship_boost
        + evidence_boost
        + materiality_boost
        + confidence_boost
        + temporal_boost
        - hub_penalty
        - length_penalty
    )
    topology_increment = (
        1.0 / (1.0 + hop)
        + relationship_boost
        + 0.5 * confidence_boost
        + 0.5 * temporal_boost
        - hub_penalty
        - 0.08 * hop
    )
    return path_increment, topology_increment


def _path(
    *,
    seed_record_id: str,
    target_record_id: str,
    target_assertion_id: str,
    lane: str,
    origin: str,
    attachment_kind: str,
    path_score: float,
    topology_score: float,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_steps = [
        {
            key: value
            for key, value in step.items()
            if key
            in {
                "hop",
                "assertion_id",
                "from_node_id",
                "from_node_name",
                "from_node_type",
                "relationship_family",
                "edge_type",
                "to_node_id",
                "to_node_name",
                "to_node_type",
                "traversal_direction",
                "evidence_ids",
                "source_id",
            }
        }
        for step in steps
    ]
    path_id = stable_id(
        "connected-path",
        seed_record_id,
        target_record_id,
        target_assertion_id,
        clean_steps,
        length=28,
    )
    return {
        "path_id": path_id,
        "seed_record_id": seed_record_id,
        "target_record_id": target_record_id,
        "target_assertion_id": target_assertion_id,
        "lane": lane,
        "origin": origin,
        "attachment_kind": attachment_kind,
        "hop_count": len(clean_steps),
        "path_score": round(path_score, 8),
        "topology_score": round(topology_score, 8),
        "steps": clean_steps,
    }


def _better_path(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    return (
        -candidate["path_score"],
        candidate["hop_count"],
        candidate["path_id"],
    ) < (
        -current["path_score"],
        current["hop_count"],
        current["path_id"],
    )


def _seed_path_score(result: dict[str, Any], record_id: str, seed_paths: dict[str, dict[str, Any]] | None) -> float:
    if seed_paths and record_id in seed_paths:
        return float(seed_paths[record_id]["path_score"])
    return _base_seed_score(result)


def unified_result_sort_key(
    result: dict[str, Any],
    seed_paths: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, float, int, str]:
    """Compute a unified ranking key that interleaves top seeds and high-scoring graph expansions."""
    is_expansion = result.get("retrieval_origin") == "graph_expansion"
    fused_rank = result.get("fused_rank")
    record_id = str(result.get("record_id") or "")
    seed_score = _seed_path_score(result, record_id, seed_paths)
    if not is_expansion and isinstance(fused_rank, int) and 1 <= fused_rank <= 8:
        return (0, -seed_score, fused_rank, record_id)
    score = float(result.get("graph_score") or 0.0) if is_expansion else seed_score
    raw_rank = result.get("graph_rank") if is_expansion else fused_rank
    tier_rank = int(raw_rank) if isinstance(raw_rank, int) else 10_000
    return (1, -score, tier_rank, record_id)


def _edge_candidate_sort_key(
    edge: dict[str, Any], requested_relationships: set[str], degrees: dict[str, int]
) -> tuple[int, int, str, str, str]:
    rel = edge["relationship_family"]
    prio = 0 if rel in requested_relationships else (1 if rel == "SHARES_CAPABILITY_DOMAIN" else 2)
    return (prio, degrees.get(edge["to_node_id"], 0), rel, edge["assertion_id"], edge["to_node_id"])


def _should_skip_unmatched_context_record(
    non_entity_q_tokens: set[str],
    record_tokens: set[str],
    assertion: dict[str, Any],
    requested_relationships: set[str],
    node_steps: list[dict[str, Any]],
) -> bool:
    if not non_entity_q_tokens or (non_entity_q_tokens & record_tokens):
        return False
    if any(s.get("relationship_family") == "SHARES_CAPABILITY_DOMAIN" for s in node_steps):
        return False
    return (
        assertion["predicate"] not in requested_relationships
        and assertion["legacy_predicate"] not in requested_relationships
    )
