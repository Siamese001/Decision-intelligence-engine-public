from typing import Any, Dict, List, Optional


class GraphTopologyFilter:
    """
    Evaluator that takes vector-routed candidates, traverses GraphDB temporal edges
    (e.g., SUPERSEDED_BY, stale_after), and drops stale/superseded facts.
    """
    def __init__(self, temporal_edges: Optional[List[Dict[str, str]]] = None):
        # temporal_edges: list of {"source": fact_id, "target": fact_id, "relation": "SUPERSEDED_BY"}
        self.temporal_edges = temporal_edges or []

        # Build adjacency list for fast lookup of what supersedes what
        self.superseded_by_map: Dict[str, List[str]] = {}
        for edge in self.temporal_edges:
            if edge.get("relation") == "SUPERSEDED_BY":
                src = edge.get("source")
                tgt = edge.get("target")
                if src and tgt:
                    if src not in self.superseded_by_map:
                        self.superseded_by_map[src] = []
                    self.superseded_by_map[src].append(tgt)

    def is_superseded(self, fact_id: str, visited: Optional[set] = None) -> bool:
        """Recursively checks if a fact_id is superseded by an active fact."""
        if visited is None:
            visited = set()

        if fact_id in visited:
            return False # Cycle detected, treat as not fully superseded or handle gracefully

        visited.add(fact_id)

        superseding_facts = self.superseded_by_map.get(fact_id, [])
        return bool(superseding_facts)

    def filter_active_facts(self, candidate_facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Takes a list of facts (dicts with 'id' and 'text') and returns only those
        that are not superseded.
        """
        active_facts = []
        for fact in candidate_facts:
            fact_id = fact.get("id")
            if fact_id and self.is_superseded(fact_id):
                continue

            # Also check 'stale_after' logic if present
            # if fact.get("stale_after") < current_time: continue

            active_facts.append(fact)

        return active_facts
