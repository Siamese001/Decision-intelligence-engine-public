"""
dual_pipeline/ptc/hybrid_query.py

Programmatic Hybrid Graph Query Execution (PHGQE) for Pipeline C.
Executes single-turn policy-gated hybrid retrieval, RRF ranking, and 1-hop
subgraph expansion, emitting a compact DecisionEvidencePack (<2KB).
Layer: L4 (Orchestration & Query Execution).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Sequence

from pipeline_c_retrieval.contracts import (
    RetrievedFact,
)


@dataclass(frozen=True)
class PTCHybridQueryPlan:
    """Programmatic retrieval parameters for single-turn hybrid query."""
    query: str
    stealth_mode: bool = True
    min_strategic_value: float = 0.0
    include_quarantined: bool = False
    limit: int = 5
    expand_subgraph: bool = True
    max_subgraph_depth: int = 1
    allowed_roles: Sequence[str] = field(default_factory=list)


@dataclass
class PTCDecisionEvidencePack:
    """Compact, context-efficient decision evidence envelope for LLM working memory."""
    query: str
    top_facts: List[Dict[str, Any]]
    subgraph_nodes: List[Dict[str, Any]]
    subgraph_edges: List[Dict[str, Any]]
    citation_index: Dict[str, Dict[str, str]]
    total_matches: int
    payload_bytes: int = 0

    def to_compact_json(self) -> str:
        """Serializes the evidence pack into compact, RFC-8259 JSON."""
        data = asdict(self)
        # Remove empty or redundant fields to minimize token consumption
        data.pop("payload_bytes", None)
        compact = json.dumps(data, separators=(",", ":"), default=str)
        self.payload_bytes = len(compact.encode("utf-8"))
        return compact

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["payload_bytes"] = self.payload_bytes or len(json.dumps(d, default=str).encode("utf-8"))
        return d


def _normalize_fact_item(fact: Any) -> Dict[str, Any]:
    if isinstance(fact, dict):
        props = fact.get("properties") if isinstance(fact.get("properties"), dict) else {}
        return {
            "node_id": str(fact.get("node_id") or fact.get("id") or ""),
            "evidence_id": str(fact.get("evidence_id") or ""),
            "name": str(fact.get("name") or ""),
            "type": str(fact.get("type") or "Entity"),
            "snippet": str(fact.get("snippet") or fact.get("content") or ""),
            "strategic_value": float(fact.get("strategic_value", 0.0)),
            "confidence": float(fact.get("confidence", 0.5)),
            "relevance_score": float(fact.get("relevance_score", 0.5)),
            "role": fact.get("role") or props.get("role"),
            "connected_edges": list(fact.get("connected_edges") or []),
        }
    props = getattr(fact, "properties", None) or {}
    return {
        "node_id": fact.node_id,
        "evidence_id": fact.evidence_id,
        "name": fact.name,
        "type": fact.type,
        "snippet": fact.snippet,
        "strategic_value": fact.strategic_value,
        "confidence": fact.confidence,
        "relevance_score": fact.relevance_score,
        "role": getattr(fact, "role", None) or (props.get("role") if isinstance(props, dict) else None),
        "connected_edges": list(getattr(fact, "connected_edges", None) or []),
    }


def _expand_fact_subgraph(
    norm: Dict[str, Any],
    max_depth: int,
    nodes_map: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> None:
    seed_id = norm["node_id"]
    nodes_map[seed_id] = {"node_id": seed_id, "name": norm["name"], "type": norm["type"]}
    frontier: set[str] = {seed_id} if seed_id else set()
    sorted_edges = sorted(
        norm["connected_edges"],
        key=lambda e: int(e.get("hop", e.get("depth", 1)) or 1),
    )
    for edge in sorted_edges:
        hop = int(edge.get("hop", edge.get("depth", 1)) or 1)
        if hop > max_depth:
            continue
        from_id, to_id = edge.get("from_node_id"), edge.get("to_node_id")
        if frontier and from_id and to_id and from_id not in frontier and to_id not in frontier:
            continue
        edges.append({"from_node_id": from_id, "to_node_id": to_id, "relationship": edge.get("relationship")})
        for endpoint_id in (from_id, to_id):
            if endpoint_id:
                frontier.add(str(endpoint_id))
                nodes_map.setdefault(endpoint_id, {"node_id": endpoint_id, "name": str(endpoint_id), "type": "Neighbor"})


class PTCHybridQueryExecutor:
    """Coordinates programmatic hybrid graph retrieval and compact pack assembly."""

    def __init__(self, retrieval_engine: Any) -> None:
        self.retrieval_engine = retrieval_engine

    def execute(self, plan: PTCHybridQueryPlan) -> PTCDecisionEvidencePack:
        """Executes single-turn retrieval enforcing Law 6 policy pre-filtering."""
        allowed_role_set = {str(r).strip().lower() for r in (plan.allowed_roles or []) if str(r).strip()}
        fetch_limit = max(plan.limit * 4, 20) if allowed_role_set else plan.limit
        raw_facts: List[Any] = self.retrieval_engine.search(
            query=plan.query, stealth_mode=plan.stealth_mode, min_strategic_value=plan.min_strategic_value,
            include_quarantined=plan.include_quarantined, limit=fetch_limit,
        )
        top_facts: List[Dict[str, Any]] = []
        citation_index: Dict[str, Dict[str, str]] = {}
        subgraph_nodes_map: Dict[str, Dict[str, Any]] = {}
        subgraph_edges: List[Dict[str, Any]] = []
        should_expand = bool(plan.expand_subgraph and plan.max_subgraph_depth > 0)
        matched_count = 0

        for fact in raw_facts:
            norm = _normalize_fact_item(fact)
            fact_role = str(norm.get("role") or "").strip().lower()
            if allowed_role_set and fact_role not in allowed_role_set:
                continue
            matched_count += 1
            if len(top_facts) >= plan.limit:
                continue
            cite_key = f"[{len(top_facts) + 1}]"
            citation_index[cite_key] = {"node_id": norm["node_id"], "evidence_id": norm["evidence_id"], "name": norm["name"]}
            top_facts.append({
                "citation": cite_key, "node_id": norm["node_id"], "name": norm["name"], "type": norm["type"],
                "snippet": norm["snippet"], "strategic_value": norm["strategic_value"],
                "confidence": norm["confidence"], "relevance_score": norm["relevance_score"],
            })
            if should_expand and norm["connected_edges"]:
                _expand_fact_subgraph(norm, plan.max_subgraph_depth, subgraph_nodes_map, subgraph_edges)

        evidence_pack = PTCDecisionEvidencePack(
            query=plan.query, top_facts=top_facts, subgraph_nodes=list(subgraph_nodes_map.values()),
            subgraph_edges=subgraph_edges[:20], citation_index=citation_index,
            total_matches=matched_count if allowed_role_set else len(raw_facts),
        )
        evidence_pack.to_compact_json()
        return evidence_pack
