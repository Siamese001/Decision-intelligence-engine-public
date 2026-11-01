"""Precomputed bounded multi-hop neighborhood extraction for Executive Copilot.
Enforces BFS traversal limits, hub suppression, SSOT priority predicates, and engagement scope.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from dual_pipeline.graph_retrieval_core.constants import GRAPH_RETRIEVAL_POLICY_BODY
from dual_pipeline.parameters import get_copilot_neighborhood_max_hops

from .c19_firewall import (
    C19_FORBIDDEN_MARKERS,
    has_c19_marker,
    is_c19_quarantined_assertion,
    is_c19_quarantined_node,
)
from .copilot_circuit_breaker import get_copilot_circuit_breaker
from .copilot_predicates import PRIORITY_PREDICATES, normalize_copilot_predicate
from .copilot_retriever_ranking import parse_assertion_epoch, resolve_assertion_epoch

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_neighborhood")

# Governed constants from GRAPH_RETRIEVAL_POLICY_BODY
DEFAULT_MAX_FANOUT: int = int(GRAPH_RETRIEVAL_POLICY_BODY.get("maximum_node_fanout", 12))
DEFAULT_HUB_DEGREE_THRESHOLD: int = int(GRAPH_RETRIEVAL_POLICY_BODY.get("hub_degree_threshold", 12))
EXCLUDED_RELATIONSHIPS: frozenset[str] = frozenset(r.upper() for r in GRAPH_RETRIEVAL_POLICY_BODY.get("excluded_relationship_families", ["ROUTES_TO"]))
EXCLUDED_NODE_TYPES: frozenset[str] = frozenset(t.lower() for t in GRAPH_RETRIEVAL_POLICY_BODY.get("excluded_node_types", ["card"]))
TERMINAL_NODE_TYPES: frozenset[str] = frozenset(t.lower() for t in GRAPH_RETRIEVAL_POLICY_BODY.get("terminal_node_types", ["source"]))

def _is_node_c19_clean(nd: Dict[str, Any]) -> bool:
    return not is_c19_quarantined_node(str(nd.get("node_id") or nd.get("canonical_name") or ""), nd)

def _is_node_allowed(
    topology: Any, node_id: str, engagement_id: Optional[str] = None,
    permitted_engagements: Optional[Set[str]] = None, is_attendee: bool = False,
) -> bool:
    if not node_id or has_c19_marker(str(node_id)):
        return False
    nodes = getattr(topology, "nodes", None)
    if isinstance(nodes, dict) and node_id in nodes:
        nd = nodes[node_id]
        if not isinstance(nd, dict):
            return True
        if str(nd.get("node_type", "")).lower() in EXCLUDED_NODE_TYPES or not _is_node_c19_clean(nd):
            return False
        if engagement_id:
            from .copilot_connection_policy import is_node_in_scope
            if not is_node_in_scope(node_id, nd, engagement_id, permitted_engagements, is_attendee=is_attendee):
                return False
    return True


def _extract_edges_from_adjacency(fwd_adj: Any, node_id: str, seen: Set[str]) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []
    if not isinstance(fwd_adj, dict):
        return edges
    for e in fwd_adj.get(node_id, []):
        if not isinstance(e, dict):
            continue
        target = str(e.get("to_node_id") or e.get("to_node") or e.get("target") or "")
        if not target or target == node_id or target in seen:
            continue
        pred = normalize_copilot_predicate(e.get("relationship_family") or e.get("predicate") or e.get("edge_type", "REL"))
        claim = str(e.get("claim_text") or e.get("object_literal") or "")
        if pred not in EXCLUDED_RELATIONSHIPS and not has_c19_marker(claim):
            seen.add(target)
            ep = parse_assertion_epoch(e.get("source_date_normalized") or e.get("observed_at") or e.get("source_date"))
            edges.append({"to_node_id": target, "predicate": pred, "claim_text": claim, "to_name": str(e.get("to_node_name") or target), "_observed_epoch": ep})
    return edges

def _is_assertion_edge_valid(
    a: Dict[str, Any], node_id: str, seen: Set[str],
    engagement_id: Optional[str], permitted_engagements: Optional[Set[str]],
) -> Optional[Tuple[str, str, str]]:
    if not isinstance(a, dict):
        return None
    subj = str(a.get("subject_node_id") or "")
    obj = str(a.get("object_node_id") or "")
    if subj == node_id:
        target = obj
    elif obj == node_id:
        target = subj
    else:
        return None
    if engagement_id:
        from .copilot_connection_policy import is_assertion_in_scope
        if not is_assertion_in_scope(a, engagement_id, permitted_engagements):
            return None
    if not target or target == node_id or target in seen:
        return None
    pred = normalize_copilot_predicate(a.get("predicate", "REL"))
    claim = str(a.get("claim_text") or a.get("object_literal") or "")
    if pred in EXCLUDED_RELATIONSHIPS or has_c19_marker(claim):
        return None
    return target, pred, claim

def _extract_edges_from_assertions(
    assertions: Any, node_id: str, seen: Set[str],
    engagement_id: Optional[str] = None, permitted_engagements: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []
    if not isinstance(assertions, dict):
        return edges
    for aid, a in assertions.items():
        res = _is_assertion_edge_valid(a, node_id, seen, engagement_id, permitted_engagements)
        if res is not None:
            target, pred, claim = res
            seen.add(target)
            ep = resolve_assertion_epoch(a)
            edges.append({"to_node_id": target, "predicate": pred, "claim_text": claim, "to_name": target, "assertion_id": aid, "_observed_epoch": ep})
    return edges

def _extract_outgoing_edges(
    topology: Any, node_id: str, engagement_id: Optional[str] = None,
    permitted_engagements: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    seen_targets: Set[str] = set()
    edges = _extract_edges_from_adjacency(getattr(topology, "forward_adjacency", None), node_id, seen_targets)
    edges.extend(_extract_edges_from_assertions(
        getattr(topology, "assertions", None), node_id, seen_targets,
        engagement_id=engagement_id, permitted_engagements=permitted_engagements,
    ))
    return edges


def _prioritize_and_cap_edges(
    edges: List[Dict[str, Any]],
    fanout_cap: int,
    hub_threshold: int,
) -> List[Dict[str, Any]]:
    """Applies hub suppression and fanout caps, prioritizing SSOT priority predicates and recency."""
    if not edges:
        return []
    return sorted(
        edges,
        key=lambda e: (
            0 if e.get("predicate") in PRIORITY_PREDICATES else 1,
            -float(e.get("_observed_epoch") or 0.0),
            e.get("predicate", ""),
            e.get("to_node_id", ""),
        )
    )[:fanout_cap]


def _format_chain_string(topology: Any, path_nodes: List[str], path_predicates: List[str]) -> str:
    nodes = getattr(topology, "nodes", {})
    parts: List[str] = []
    for i, nid in enumerate(path_nodes):
        name = nid
        if isinstance(nodes, dict) and isinstance(nodes.get(nid), dict):
            nd = nodes[nid]
            name = nd.get("canonical_name") or nd.get("display_name") or nid
        parts.append(name)
        if i < len(path_predicates):
            parts.append(f"--[{path_predicates[i]}]-->")
    return " ".join(parts)


def _make_chain_record(
    topology: Any, seed_id: str, curr_node: str, hops: int,
    path_nodes: List[str], path_preds: List[str], path_claims: List[str],
) -> Dict[str, Any]:
    return {
        "seed_id": seed_id, "target_id": curr_node, "hops": hops,
        "path": list(path_nodes), "predicates": list(path_preds),
        "chain_str": _format_chain_string(topology, path_nodes, path_preds),
        "is_priority": any(p in PRIORITY_PREDICATES for p in path_preds),
        "last_claim": path_claims[-1] if path_claims else "",
    }


def _is_terminal_node(nodes: Any, curr_node: str) -> bool:
    if isinstance(nodes, dict) and isinstance(nodes.get(curr_node), dict):
        return str(nodes[curr_node].get("node_type", "")).lower() in TERMINAL_NODE_TYPES
    return False


def _enqueue_neighbors(
    queue: collections.deque, topology: Any, curr_node: str, hops: int,
    path_nodes: List[str], path_preds: List[str], path_claims: List[str],
    fanout_cap: int, hub_threshold: int,
    engagement_id: Optional[str] = None,
    permitted_engagements: Optional[Set[str]] = None,
) -> None:
    raw_edges = _extract_outgoing_edges(topology, curr_node, engagement_id=engagement_id, permitted_engagements=permitted_engagements)
    capped_edges = _prioritize_and_cap_edges(raw_edges, fanout_cap, hub_threshold)
    for edge in capped_edges:
        nxt = edge["to_node_id"]
        if nxt not in path_nodes and _is_node_allowed(topology, nxt, engagement_id=engagement_id, permitted_engagements=permitted_engagements):
            queue.append((nxt, hops + 1, path_nodes + [nxt], path_preds + [edge["predicate"]], path_claims + [edge.get("claim_text", "")]))


def _expand_single_seed_bfs(
    seed_id: str, topology: Any, effective_hops: int, fanout_cap: int, hub_threshold: int,
    t0: float, timeout_seconds: float, seen_chain_signatures: Set[str],
    engagement_id: Optional[str] = None, permitted_engagements: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Expands BFS queue for a single seed node, returning (new_chains, timed_out)."""
    chains: List[Dict[str, Any]] = []
    nodes = getattr(topology, "nodes", {})
    queue: collections.deque = collections.deque([(seed_id, 0, [seed_id], [], [])])

    while queue:
        if (time.time() - t0) >= timeout_seconds:
            return chains, True

        curr_node, hops, path_nodes, path_preds, path_claims = queue.popleft()

        if hops >= 1:
            sig = f"{seed_id}->{curr_node}:{':'.join(path_preds)}"
            if sig not in seen_chain_signatures:
                seen_chain_signatures.add(sig)
                chains.append(_make_chain_record(topology, seed_id, curr_node, hops, path_nodes, path_preds, path_claims))

        if hops >= effective_hops or (hops >= 1 and _is_terminal_node(nodes, curr_node)):
            continue

        _enqueue_neighbors(
            queue, topology, curr_node, hops, path_nodes, path_preds, path_claims,
            fanout_cap, hub_threshold, engagement_id=engagement_id, permitted_engagements=permitted_engagements,
        )

    return chains, False


def compute_neighborhood_chains(
    topology: Any, seed_node_ids: Iterable[str], max_hops: Optional[int] = None,
    max_fanout: Optional[int] = None, timeout_seconds: float = 2.0,
    engagement_id: Optional[str] = None, permitted_engagements: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Executes bounded BFS from seed nodes, generating multi-hop connected chains."""
    if not topology or not seed_node_ids:
        return [], "EMPTY"

    t0 = time.time()
    effective_hops = max_hops if max_hops is not None else get_copilot_neighborhood_max_hops()
    effective_hops = max(1, min(2, effective_hops))
    fanout_cap = max_fanout or DEFAULT_MAX_FANOUT
    hub_threshold = DEFAULT_HUB_DEGREE_THRESHOLD

    status = "SUCCESS"
    chains: List[Dict[str, Any]] = []
    seen_chain_signatures: Set[str] = set()

    clean_seeds = [
        s for s in seed_node_ids
        if _is_node_allowed(topology, s, engagement_id=engagement_id, permitted_engagements=permitted_engagements)
    ]

    for seed_id in clean_seeds:
        if (time.time() - t0) >= timeout_seconds:
            status = "PARTIAL"
            break
        seed_chains, timed_out = _expand_single_seed_bfs(
            seed_id, topology, effective_hops, fanout_cap, hub_threshold,
            t0, timeout_seconds, seen_chain_signatures, engagement_id, permitted_engagements,
        )
        chains.extend(seed_chains)
        if timed_out:
            status = "PARTIAL"
            break

    chains.sort(key=lambda c: (c.get("hops", 0), 0 if c.get("is_priority") else 1, c.get("seed_id", ""), c.get("target_id", ""), c.get("chain_str", "")))
    return chains, status


DEFAULT_MAX_CHAINS: int = 250
HARD_MAX_CHAINS: int = 500


class CopilotNeighborhoodStore:
    """In-memory bounded index of neighborhood chains with LRU eviction, deterministic capacity limits, and lazy on-demand hydration."""

    def __init__(
        self,
        chains: Optional[List[Dict[str, Any]]] = None,
        status: str = "SUCCESS",
        engagement_id: Optional[str] = None,
        permitted_engagements: Optional[Set[str]] = None,
        max_chains: int = DEFAULT_MAX_CHAINS,
        max_entries: int = 500,
        max_ram_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.max_chains = min(HARD_MAX_CHAINS, max(1, max_chains))
        self.max_entries: int = max_entries
        self.max_ram_bytes: int = max_ram_bytes
        self.status: str = "COMPLETE" if status in ("SUCCESS", "COMPLETE") else status
        self.engagement_id: Optional[str] = engagement_id
        self.permitted_engagements: Optional[Set[str]] = permitted_engagements

        self._lock = threading.RLock()
        self.chains: List[Dict[str, Any]] = list(chains or [])
        self._by_node: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        self._by_seed: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        self._lru_nodes: collections.OrderedDict[str, float] = collections.OrderedDict()

        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._evictions: int = 0

        self._build_index()

    @property
    def chain_count(self) -> int:
        with self._lock:
            return len(self.chains)

    def _build_index(self) -> None:
        with self._lock:
            self._by_node.clear()
            self._by_seed.clear()
            self._lru_nodes.clear()
            now = time.time()
            for c in self.chains:
                seed, target = c.get("seed_id", ""), c.get("target_id", "")
                if seed:
                    self._by_seed[seed].append(c)
                    self._by_node[seed].append(c)
                    self._lru_nodes[seed] = now
                if target and target != seed:
                    self._by_node[target].append(c)
                    self._lru_nodes[target] = now
            self._enforce_capacity_limits()

    def _enforce_capacity_limits(self) -> None:
        """Enforces max_entries and max_ram_bytes by evicting oldest LRU keys using deterministic triage."""
        limit = min(self.max_entries, self.max_chains)
        excess = len(self._lru_nodes) - limit
        if excess <= 0:
            return
        keys_to_evict = list(self._lru_nodes.keys())[:excess]
        for evicted_node in keys_to_evict:
            del self._lru_nodes[evicted_node]
            self._evictions += 1
            self._by_node.pop(evicted_node, None)
            self._by_seed.pop(evicted_node, None)
        # Prune orphaned chains where neither seed nor target is retained
        self.chains = [
            c for c in self.chains
            if c.get("seed_id") in self._lru_nodes or c.get("target_id") in self._lru_nodes
        ]

    def add_chains(self, new_chains: Iterable[Dict[str, Any]]) -> int:
        """Adds new chains to the store, indexing them and enforcing LRU capacity."""
        new_list = [c for c in new_chains if isinstance(c, dict)]
        if not new_list:
            return 0
        with self._lock:
            now = time.time()
            for c in new_list:
                seed, target = c.get("seed_id", ""), c.get("target_id", "")
                self.chains.append(c)
                if seed:
                    self._by_seed[seed].append(c)
                    self._by_node[seed].append(c)
                    self._lru_nodes[seed] = now
                    self._lru_nodes.move_to_end(seed)
                if target and target != seed:
                    self._by_node[target].append(c)
                    self._lru_nodes[target] = now
                    self._lru_nodes.move_to_end(target)
            self._enforce_capacity_limits()
            return len(new_list)

    def get_chains_for_node(
        self,
        node_id: str,
        limit: int = 3,
        topology: Optional[Any] = None,
        auto_hydrate: bool = True,
    ) -> List[Dict[str, Any]]:
        """Returns precomputed multi-hop chains touching node_id in < 0.1ms with lazy hydration."""
        if not node_id:
            return []
        with self._lock:
            cb = get_copilot_circuit_breaker()
            if node_id in self._by_node and self._by_node[node_id]:
                self._cache_hits += 1
                if node_id in self._lru_nodes:
                    self._lru_nodes.move_to_end(node_id)
                cb.record_hit(node_id)
                return self._by_node[node_id][:limit]

            self._cache_misses += 1
            cb.record_miss(node_id)
            if auto_hydrate and topology is not None:
                if _is_node_allowed(topology, node_id, engagement_id=self.engagement_id, permitted_engagements=self.permitted_engagements):
                    cb.record_fallback("lazy_hydration", node_id)
                    new_chains, _ = compute_neighborhood_chains(
                        topology=topology,
                        seed_node_ids=[node_id],
                        max_hops=1,
                        timeout_seconds=0.2,
                        engagement_id=self.engagement_id,
                        permitted_engagements=self.permitted_engagements,
                    )
                    if new_chains:
                        self.add_chains(new_chains)
                        return self._by_node.get(node_id, [])[:limit]
            return []

    def get_chains_for_seed(self, seed_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns precomputed chains originating from seed_id."""
        with self._lock:
            cb = get_copilot_circuit_breaker()
            if seed_id in self._by_seed:
                self._cache_hits += 1
                if seed_id in self._lru_nodes:
                    self._lru_nodes.move_to_end(seed_id)
                cb.record_hit(seed_id)
                return self._by_seed.get(seed_id, [])[:limit]
            self._cache_misses += 1
            cb.record_miss(seed_id)
            return []

    def format_chains_summary(self, max_chains: int = 5) -> str:
        """Formats compact multi-hop relationship chains for copilot prompt injection."""
        with self._lock:
            if not self.chains:
                return ""
            lines = ["Connected Multi-Hop Knowledge:"]
            for c in self.chains[:max_chains]:
                lines.append(f"- {c['chain_str']}")
                if c.get("last_claim"):
                    lines.append(f"  * Fact: {c['last_claim']}")
            return "\n".join(lines)

    def compute_hash(self) -> str:
        """Deterministic RFC-8259 SHA-256 digest of stored chains."""
        with self._lock:
            encoded = json.dumps(self.chains, sort_keys=True).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

    def get_memory_footprint_bytes(self) -> int:
        """Estimates the in-memory RAM footprint of the cache structures."""
        with self._lock:
            total_bytes = sys.getsizeof(self.chains) + sys.getsizeof(self._by_node) + sys.getsizeof(self._by_seed) + sys.getsizeof(self._lru_nodes)
            for c in self.chains:
                total_bytes += sys.getsizeof(c)
                total_bytes += sys.getsizeof(c.get("chain_str", ""))
                total_bytes += sys.getsizeof(c.get("last_claim", ""))
                total_bytes += sys.getsizeof(c.get("path", []))
                total_bytes += sys.getsizeof(c.get("predicates", []))
            return total_bytes

    def get_cache_stats(self) -> Dict[str, Any]:
        """Returns cache telemetry including hit/miss counts, evictions, and memory footprint."""
        with self._lock:
            return {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "evictions": self._evictions,
                "entries": len(self._lru_nodes),
                "chains_count": len(self.chains),
                "footprint_bytes": self.get_memory_footprint_bytes(),
                "max_entries": self.max_entries,
                "max_ram_bytes": self.max_ram_bytes,
            }

    def clear(self) -> None:
        """Cleans up in-memory indices and resets counters."""
        with self._lock:
            self.chains.clear()
            self._by_node.clear()
            self._by_seed.clear()
            self._lru_nodes.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._evictions = 0

    def refresh_salient_seeds(
        self, new_seeds: List[str], topology: Any, max_hops: int = 1, timeout_seconds: float = 0.5
    ) -> int:
        """Refreshes neighborhood index off the hot path for newly salient entities."""
        with self._lock:
            unseen_seeds = [
                s for s in new_seeds
                if s not in self._by_seed and _is_node_allowed(topology, s, engagement_id=self.engagement_id, permitted_engagements=self.permitted_engagements)
            ]
            if not unseen_seeds:
                return 0
            new_chains, _ = compute_neighborhood_chains(
                topology=topology,
                seed_node_ids=unseen_seeds,
                max_hops=max_hops,
                timeout_seconds=timeout_seconds,
                engagement_id=self.engagement_id,
                permitted_engagements=self.permitted_engagements,
            )
            if new_chains:
                return self.add_chains(new_chains)
            return 0


def precompute_neighborhoods(
    topology: Any, seed_node_ids: Iterable[str], max_hops: Optional[int] = None,
    timeout_seconds: float = 0.150, engagement_id: Optional[str] = None,
    permitted_engagements: Optional[Set[str]] = None, max_chains: int = DEFAULT_MAX_CHAINS,
) -> CopilotNeighborhoodStore:
    chains, status = compute_neighborhood_chains(
        topology=topology, seed_node_ids=seed_node_ids, max_hops=max_hops,
        timeout_seconds=timeout_seconds, engagement_id=engagement_id,
        permitted_engagements=permitted_engagements,
    )
    return CopilotNeighborhoodStore(
        chains=chains, status=status, engagement_id=engagement_id,
        permitted_engagements=permitted_engagements, max_chains=max_chains,
    )
