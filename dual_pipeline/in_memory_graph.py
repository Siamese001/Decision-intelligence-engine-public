"""In-memory topological accelerator for the NEE Knowledge Graph runtime.

Hydrates a zero-daemon, index-free adjacency graph directly from an immutable
SQLite generation, supporting clearance-masked k-hop causal path finding,
Personalized PageRank (PPR) for localized centrality scoring, and induced
witness subgraph extraction.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Sequence

from .core import connect_sqlite, sha256_file
from .index import CLASSIFICATION_RANK
from .retrieval_clearance import retrieval_clearance


class GraphIntegrityError(Exception):
    """Raised when the loaded graph topology does not match its expected state or hash."""

    pass


DEFAULT_EXCLUDED_RELATIONSHIPS = frozenset({"ROUTES_TO"})
DEFAULT_EXCLUDED_NODE_TYPES = frozenset({"card"})

PREDICATE_WEIGHTS: dict[str, float] = {
    "HAS_RISK": 1.0,
    "DEPENDS_ON": 1.0,
    "CONSTRAINS": 1.0,
    "GOVERNS": 1.0,
    "OWNS_OR_LEADS": 1.0,
    "ROUTES_TO": 0.0,
    "SUPPORTED_BY": 0.0,
}

MATERIALITY_WEIGHTS: dict[str, float] = {
    "critical": 1.0,
    "high": 0.8,
    "medium-high": 0.6,
    "medium": 0.4,
    "low": 0.2,
}


def _load_evidence_metadata(connection: Any, evidence_tables: bool) -> dict[str, dict[str, Any]]:
    evidence_meta: dict[str, dict[str, Any]] = {}
    if not evidence_tables:
        return evidence_meta
    has_source = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='source'"
    ).fetchone()
    has_evidence_item = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_item'"
    ).fetchone()
    if not (has_source and has_evidence_item):
        return evidence_meta

    source_cols = {col["name"] for col in connection.execute("PRAGMA table_info(source)")}
    ev_cols = {col["name"] for col in connection.execute("PRAGMA table_info(evidence_item)")}

    title_expr = (
        "s.source_name"
        if "source_name" in source_cols
        else ("s.title" if "title" in source_cols else "NULL")
    )
    date_expr = (
        "s.source_date_normalized"
        if "source_date_normalized" in source_cols
        else ("s.source_date" if "source_date" in source_cols else "NULL")
    )
    locator_expr = (
        "e.transcript_timestamp"
        if "transcript_timestamp" in ev_cols
        else ("e.locator" if "locator" in ev_cols else "NULL")
    )
    quote_expr = (
        "e.direct_quote"
        if "direct_quote" in ev_cols
        else ("e.content" if "content" in ev_cols else "NULL")
    )

    q = f"""
        SELECT e.evidence_id, {title_expr} as title, {date_expr} as sdn,
               {locator_expr} as locator, {quote_expr} as content
        FROM evidence_item e
        LEFT JOIN source s ON e.source_id = s.source_id
    """
    import re

    try:
        for row in connection.execute(q):
            sdn = row["sdn"]
            if not sdn and row["title"]:
                m = re.search(r"(\d{2}/\d{2}/\d{4})", str(row["title"]))
                if m:
                    sdn = m.group(1)
            content_str = str(row["content"] or "")
            evidence_meta[row["evidence_id"]] = {
                "source_name": row["title"],
                "source_date_normalized": sdn,
                "transcript_timestamp": row["locator"],
                "direct_quote": content_str[:160],
            }
    except Exception:
        pass
    return evidence_meta


def _infer_assertion_scope(
    assertion: dict[str, Any],
    source_map: dict[str, str] | None = None,
) -> str:
    if assertion.get("engagement_id"):
        return str(assertion["engagement_id"])
    if assertion.get("project_scope"):
        return str(assertion["project_scope"])

    src_map = source_map or {}
    sid = str(assertion.get("primary_source_id") or "")
    s_name = src_map.get(sid, "").lower()
    scope_text = str(assertion.get("scope_text") or "").lower()
    subject = str(assertion.get("subject_node_id") or "").lower()
    obj_node = str(assertion.get("object_node_id") or "").lower()
    claim = str(assertion.get("object_literal") or "").lower()
    text = f"{s_name} {scope_text} {subject} {obj_node} {claim}"

    if "dociq" in text:
        return "project:dociq"
    if "jarvis" in text:
        return "project:project_jarvis"
    if "nexus" in text:
        return "project:project_nexus"
    if any(k in text for k in ("lens", "sow 1", "drone", "pipeline review", "pipeline jake", "sow 2", "sow1", "sow2")):
        return "project:project_lens"
    if any(k in text for k in ("pgd", "pgd followup")):
        return "project:pgd"
    if any(k in text for k in ("sherpa",)):
        return "project:project_beacon"
    if any(k in text for k in ("rewire",)):
        return "project:project_atlas"
    if any(k in text for k in ("apex_trans",)):
        return "project:apex_trans"
    if any(k in text for k in ("enterprise", "policy", "business case", "technical_anchor")) or subject.startswith("anchor_") or obj_node.startswith("anchor_") or assertion.get("is_enterprise_policy") is True:
        return "project:enterprise"
    return "quarantine:unknown_ownership"


def _hydrate_assertion_scopes(
    connection: Any,
    assertions: dict[str, dict[str, Any]],
) -> None:
    source_map: dict[str, str] = {}
    try:
        has_source = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='source'"
        ).fetchone()
        if has_source:
            s_cols = {row["name"] for row in connection.execute("PRAGMA table_info(source)")}
            if "source_name" in s_cols:
                for s_row in connection.execute("SELECT source_id, source_name FROM source"):
                    source_map[s_row["source_id"]] = str(s_row["source_name"] or "")
            elif "name" in s_cols:
                for s_row in connection.execute("SELECT source_id, name FROM source"):
                    source_map[s_row["source_id"]] = str(s_row["name"] or "")
            elif "title" in s_cols:
                for s_row in connection.execute("SELECT source_id, title FROM source"):
                    source_map[s_row["source_id"]] = str(s_row["title"] or "")
    except Exception:
        pass

    for ast_dict in assertions.values():
        inferred = _infer_assertion_scope(ast_dict, source_map)
        ast_dict["engagement_id"] = inferred
        if not ast_dict.get("project_scope"):
            ast_dict["project_scope"] = inferred


class InMemoryGraphTopology:
    """Zero-daemon, in-memory graph topology loaded from an immutable SQLite generation."""

    def __init__(
        self,
        *,
        graph_path: Path,
        graph_sha256: str,
        nodes: dict[str, dict[str, Any]],
        assertions: dict[str, dict[str, Any]],
        supporting_evidence: dict[str, list[str]],
        contradicting_evidence: dict[str, list[str]],
        evidence_meta: dict[str, dict[str, Any]] | None = None,
        forward_adjacency: dict[str, list[dict[str, Any]]],
        reverse_adjacency: dict[str, list[dict[str, Any]]],
        undirected_adjacency: dict[str, list[dict[str, Any]]],
        canonical_edges: list[dict[str, Any]],
    ) -> None:
        self.graph_path = Path(graph_path)
        self.graph_sha256 = graph_sha256
        self.nodes = nodes
        self.assertions = assertions
        self.supporting_evidence = supporting_evidence
        self.contradicting_evidence = contradicting_evidence
        self.evidence_meta = evidence_meta or {}
        self.forward_adjacency = forward_adjacency
        self.reverse_adjacency = reverse_adjacency
        self.undirected_adjacency = undirected_adjacency
        self.canonical_edges = canonical_edges

        # Precompute degrees
        self.node_out_degree: dict[str, int] = {
            node_id: len(forward_adjacency.get(node_id, [])) for node_id in nodes
        }
        self.node_in_degree: dict[str, int] = {
            node_id: len(reverse_adjacency.get(node_id, [])) for node_id in nodes
        }
        self.node_degree: dict[str, int] = {
            node_id: self.node_out_degree[node_id] + self.node_in_degree[node_id]
            for node_id in nodes
        }

    @classmethod
    def load_from_sqlite(
        cls,
        graph_path: Path | str,
        *,
        expected_sha256: str | None = None,
        load_scope: str = "all",
    ) -> InMemoryGraphTopology:
        """Hydrate graph topology into memory directly from SQLite."""
        path = Path(graph_path)
        if not path.is_file():
            raise FileNotFoundError(f"Graph database not found: {path}")

        computed_sha256 = sha256_file(path)
        if expected_sha256 and computed_sha256 != expected_sha256:
            raise GraphIntegrityError(
                f"Hash mismatch: expected {expected_sha256}, got {computed_sha256}"
            )
        connection = connect_sqlite(path, readonly=True)
        try:
            # 1. Load active nodes
            node_cols = {
                row["name"] for row in connection.execute("PRAGMA table_info(node)")
            }
            extra_node_cols = [c for c in ("engagement_id", "project_scope", "namespace") if c in node_cols]
            select_node_sql = f"""SELECT node_id, canonical_name, display_name, node_type,
                              description, status, active{', ' + ', '.join(extra_node_cols) if extra_node_cols else ''}
                         FROM node
                        WHERE active = 1
                        ORDER BY node_id"""
            nodes = {
                row["node_id"]: dict(row)
                for row in connection.execute(select_node_sql)
            }

            # 2. Load assertions
            table_name = "assertion"
            if load_scope == "current":
                has_view = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='view' AND name='v_current_assertion'"
                ).fetchone()
                if has_view:
                    table_name = "v_current_assertion"

            assertion_cols = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            select_cols = [
                "assertion_id",
                "subject_node_id",
                "predicate",
                "object_node_id",
                "object_literal",
                "canonical_status",
                "retrieval_eligible",
                "materiality",
                "classification",
                "allowed_audiences_json",
            ]
            optional_cols = [
                "engagement_id",
                "project_scope",
                "namespace",
                "legacy_predicate",
                "assertion_type",
                "origin_type",
                "extraction_run_id",
                "currentness",
                "superseded_by",
                "valid_from",
                "valid_to",
                "is_assertion_cleared",
                "validation_status",
                "observed_at",
                "source_date_normalized",
                "has_unresolved_conflict",
                "assertion_conflict",
                "hitl_required",
                "validation_priority",
                "validation_owner",
                "atomicity_state",
                "confidence_score",
                "validation_state",
                "currentness_state",
                "primary_source_id",
                "scope_text",
                "engagement_id",
                "project_scope",
            ]
            for col in optional_cols:
                if col in assertion_cols:
                    select_cols.append(col)

            assertions = {
                row["assertion_id"]: dict(row)
                for row in connection.execute(
                    f"SELECT {', '.join(select_cols)} FROM assertion ORDER BY assertion_id"
                )
            }
            _hydrate_assertion_scopes(connection, assertions)

            # 3. Load assertion evidence
            supporting_evidence: dict[str, list[str]] = defaultdict(list)
            contradicting_evidence: dict[str, list[str]] = defaultdict(list)
            evidence_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_evidence'"
                )
            }
            if evidence_tables:
                for row in connection.execute(
                    """SELECT assertion_id, evidence_id, evidence_relationship
                         FROM assertion_evidence
                        ORDER BY assertion_id, evidence_id"""
                ):
                    if row["evidence_relationship"] == "supports":
                        supporting_evidence[row["assertion_id"]].append(row["evidence_id"])
                    else:
                        contradicting_evidence[row["assertion_id"]].append(row["evidence_id"])

            # 3b. Load evidence_meta
            evidence_meta = _load_evidence_metadata(connection, evidence_tables)

            # 4. Load canonical edges and build index-free adjacency
            forward_adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
            reverse_adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
            undirected_adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
            canonical_edges: list[dict[str, Any]] = []

            seen_edge_keys: set[tuple[str, str, str]] = set()

            edge_rows = connection.execute(
                """SELECT assertion_id, from_node_id, relationship_family, edge_type,
                          to_node_id, materiality, source_id, hitl_required
                     FROM v_canonical_edge
                    ORDER BY assertion_id, from_node_id, to_node_id"""
            )
            for row in edge_rows:
                from_id = row["from_node_id"]
                to_id = row["to_node_id"]
                assertion_id = row["assertion_id"]
                rel_family = row["relationship_family"]
                edge_type = row["edge_type"]

                if from_id not in nodes or to_id not in nodes:
                    continue

                from_node = nodes[from_id]
                to_node = nodes[to_id]

                if rel_family == "AFFILIATED_WITH":
                    from_node["org"] = to_node["canonical_name"]

                if (
                    rel_family in DEFAULT_EXCLUDED_RELATIONSHIPS
                    or from_node["node_type"] in DEFAULT_EXCLUDED_NODE_TYPES
                    or to_node["node_type"] in DEFAULT_EXCLUDED_NODE_TYPES
                ):
                    continue

                edge_dict = {
                    "edge_id": f"{assertion_id}:{from_id}->{to_id}",
                    "assertion_id": assertion_id,
                    "from_node_id": from_id,
                    "from_node_name": from_node["canonical_name"],
                    "from_node_type": from_node["node_type"],
                    "relationship_family": rel_family,
                    "edge_type": edge_type,
                    "to_node_id": to_id,
                    "to_node_name": to_node["canonical_name"],
                    "to_node_type": to_node["node_type"],
                    "materiality": row["materiality"],
                    "source_id": row["source_id"],
                    "hitl_required": bool(row["hitl_required"]),
                    "evidence_ids": list(supporting_evidence.get(assertion_id, [])),
                }

                edge_key = (assertion_id, from_id, to_id)
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    canonical_edges.append(edge_dict)

                fwd_edge = {**edge_dict, "traversal_direction": "forward"}
                rev_edge = {
                    **edge_dict,
                    "from_node_id": to_id,
                    "from_node_name": to_node["canonical_name"],
                    "from_node_type": to_node["node_type"],
                    "to_node_id": from_id,
                    "to_node_name": from_node["canonical_name"],
                    "to_node_type": from_node["node_type"],
                    "traversal_direction": "reverse",
                }

                forward_adj[from_id].append(fwd_edge)
                reverse_adj[to_id].append(rev_edge)
                undirected_adj[from_id].append(fwd_edge)
                undirected_adj[to_id].append(rev_edge)

            # Sort adjacency lists deterministically
            for adj_dict in (forward_adj, reverse_adj, undirected_adj):
                for node_id in adj_dict:
                    adj_dict[node_id].sort(
                        key=lambda e: (
                            e["relationship_family"],
                            e["edge_type"] or "",
                            e["assertion_id"],
                            e["to_node_id"],
                        )
                    )

            aliases_file = Path(__file__).resolve().parent.parent / "config" / "runtime" / "learned_aliases.json"
            learned_aliases: dict[str, str] = {
                "system:technosilva": "system:technosylva",
                "system:tens_sharepoint": "system:tens-sharepoint",
                "org:technosilva": "org:technosylva",
            }
            if aliases_file.is_file():
                try:
                    with open(aliases_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        alias_dict = data.get("aliases", data) if "aliases" in data and isinstance(data.get("aliases"), dict) else data
                        for k, v in alias_dict.items():
                            if k not in ("schema_version", "updated_at", "description") and isinstance(v, str):
                                learned_aliases[str(k).lower()] = str(v).lower()
                except Exception:
                    pass
            for alias, canonical in learned_aliases.items():
                if canonical in nodes:
                    if "aliases" not in nodes[canonical]:
                        nodes[canonical]["aliases"] = []
                    nodes[canonical]["aliases"].append(alias)

            # also, we can populate missing 'aliases' lists
            for node in nodes.values():
                if "aliases" not in node:
                    node["aliases"] = []

            return cls(
                graph_path=path,
                graph_sha256=computed_sha256,
                nodes=nodes,
                assertions=assertions,
                supporting_evidence=dict(supporting_evidence),
                contradicting_evidence=dict(contradicting_evidence),
                evidence_meta=evidence_meta,
                forward_adjacency=dict(forward_adj),
                reverse_adjacency=dict(reverse_adj),
                undirected_adjacency=dict(undirected_adj),
                canonical_edges=canonical_edges,
            )
        finally:
            connection.close()

    def is_assertion_cleared(
        self,
        assertion_id: str,
        *,
        allowed_audiences: Sequence[str] | None = None,
        max_classification: str = "restricted",
    ) -> bool:
        """Check whether an assertion passes fail-closed retrieval clearance."""
        assertion = self.assertions.get(assertion_id)
        if assertion is None:
            return False

        classification = str(assertion.get("classification", "confidential"))
        if CLASSIFICATION_RANK.get(classification, 99) > CLASSIFICATION_RANK.get(
            max_classification, 99
        ):
            return False

        audiences_raw = assertion.get("allowed_audiences_json", "[]")
        try:
            audiences = (
                json.loads(audiences_raw) if isinstance(audiences_raw, str) else audiences_raw
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            audiences = []

        if allowed_audiences is not None:
            req_audiences = set(allowed_audiences)
            if not (req_audiences & set(audiences)):
                return False

        cleared, _reason = retrieval_clearance(
            assertion_type=assertion.get("assertion_type"),
            classification=classification,
            audiences=audiences,
            origin_type=assertion.get("origin_type"),
            extraction_run_id=assertion.get("extraction_run_id"),
            hitl_required=assertion.get("hitl_required"),
            validation_priority=assertion.get("validation_priority"),
            validation_owner=assertion.get("validation_owner"),
            atomicity_state=assertion.get("atomicity_state"),
        )
        return cleared

    def find_causal_paths(
        self,
        source_ids: Sequence[str],
        target_ids: Sequence[str],
        *,
        max_hops: int = 3,
        allowed_audiences: Sequence[str] | None = None,
        max_classification: str = "restricted",
        direction: str = "forward",
    ) -> list[dict[str, Any]]:
        """Find deterministic k-hop causal paths between source and target nodes."""
        valid_sources = [s for s in source_ids if s in self.nodes]
        target_set = {t for t in target_ids if t in self.nodes}
        if not valid_sources or not target_set or max_hops < 1:
            return []

        if direction == "forward":
            adj = self.forward_adjacency
        elif direction == "reverse":
            adj = self.reverse_adjacency
        else:
            adj = self.undirected_adjacency

        discovered_paths: list[dict[str, Any]] = []

        # BFS queue elements: (current_node_id, path_node_ids, path_edges)
        for src_id in valid_sources:
            queue: deque[tuple[str, list[str], list[dict[str, Any]]]] = deque()
            queue.append((src_id, [src_id], []))

            while queue:
                current_id, node_path, edge_path = queue.popleft()
                current_hop = len(edge_path)

                if current_id in target_set and current_hop > 0:
                    # Found a valid causal path
                    path_nodes = [self.nodes[nid] for nid in node_path]
                    assertion_ids = [e["assertion_id"] for e in edge_path]
                    total_materiality = sum(
                        MATERIALITY_WEIGHTS.get(e.get("materiality", "medium"), 0.4)
                        for e in edge_path
                    ) / max(len(edge_path), 1)

                    discovered_paths.append(
                        {
                            "source_id": src_id,
                            "target_id": current_id,
                            "hop_count": current_hop,
                            "node_ids": list(node_path),
                            "nodes": path_nodes,
                            "edges": list(edge_path),
                            "assertion_ids": assertion_ids,
                            "average_materiality_score": round(total_materiality, 3),
                        }
                    )
                    # If reached max_hops, do not expand further
                    if current_hop >= max_hops:
                        continue

                if current_hop >= max_hops:
                    continue

                for edge in adj.get(current_id, []):
                    next_id = edge["to_node_id"]
                    if next_id in node_path:
                        # Avoid cycles
                        continue

                    assertion_id = edge.get("assertion_id")
                    if assertion_id and not self.is_assertion_cleared(
                        assertion_id,
                        allowed_audiences=allowed_audiences,
                        max_classification=max_classification,
                    ):
                        continue

                    queue.append(
                        (
                            next_id,
                            node_path + [next_id],
                            edge_path + [edge],
                        )
                    )

        # Sort paths by hop count (shortest first), then materiality score (descending)
        discovered_paths.sort(
            key=lambda p: (
                p["hop_count"],
                -p["average_materiality_score"],
                p["source_id"],
                p["target_id"],
            )
        )
        return discovered_paths

    def personalized_pagerank(
        self,
        seed_node_ids: Sequence[str],
        *,
        alpha: float = 0.85,
        max_iter: int = 50,
        tol: float = 1e-6,
    ) -> dict[str, float]:
        """Compute localized Personalized PageRank (PPR) around seed nodes."""
        valid_seeds = [s for s in seed_node_ids if s in self.nodes]
        if not valid_seeds:
            num_nodes = len(self.nodes)
            if num_nodes == 0:
                return {}
            uniform = 1.0 / num_nodes
            return {node_id: uniform for node_id in self.nodes}

        all_nodes = list(self.nodes.keys())
        num_nodes = len(all_nodes)
        node_to_idx = {nid: i for i, nid in enumerate(all_nodes)}

        # Seed restart distribution p0
        p0 = [0.0] * num_nodes
        seed_weight = 1.0 / len(valid_seeds)
        for s in valid_seeds:
            p0[node_to_idx[s]] = seed_weight

        # Initialize rank vector with p0
        p = list(p0)

        # Precalculate incoming neighbors for power iteration
        incoming_neighbors: list[list[tuple[int, int]]] = [[] for _ in range(num_nodes)]
        out_degrees: list[int] = [0] * num_nodes

        for node_id, u in node_to_idx.items():
            edges = self.forward_adjacency.get(node_id, [])
            out_degrees[u] = len(edges)
            for edge in edges:
                target_id = edge["to_node_id"]
                if target_id in node_to_idx:
                    v = node_to_idx[target_id]
                    incoming_neighbors[v].append((u, len(edges)))

        # Power iteration
        for _ in range(max_iter):
            p_next = [(1.0 - alpha) * p0[i] for i in range(num_nodes)]

            # Account for dangling nodes (out_degree == 0)
            dangling_sum = sum(p[u] for u in range(num_nodes) if out_degrees[u] == 0)
            if dangling_sum > 0:
                dangling_contrib = alpha * dangling_sum / len(valid_seeds)
                for s in valid_seeds:
                    p_next[node_to_idx[s]] += dangling_contrib

            # Propagate through incoming edges
            for v in range(num_nodes):
                edge_sum = 0.0
                for u, deg_u in incoming_neighbors[v]:
                    if deg_u > 0:
                        edge_sum += p[u] / deg_u
                p_next[v] += alpha * edge_sum

            # Check convergence L1 norm
            diff = sum(abs(p_next[i] - p[i]) for i in range(num_nodes))
            p = p_next
            if diff < tol:
                break

        # Normalize and package results sorted by score descending
        total = sum(p)
        if total > 0:
            p = [val / total for val in p]

        scores = {all_nodes[i]: round(p[i], 6) for i in range(num_nodes)}
        return dict(sorted(scores.items(), key=lambda item: -item[1]))

    def extract_induced_subgraph(
        self,
        node_ids: Iterable[str],
        *,
        allowed_audiences: Sequence[str] | None = None,
        max_classification: str = "restricted",
    ) -> dict[str, Any]:
        """Extract the induced subgraph over a given set of nodes."""
        node_set = {nid for nid in node_ids if nid in self.nodes}
        subgraph_nodes = [self.nodes[nid] for nid in sorted(node_set)]

        subgraph_edges: list[dict[str, Any]] = []
        assertion_ids_set: set[str] = set()
        evidence_ids_set: set[str] = set()

        for edge in self.canonical_edges:
            from_id = edge["from_node_id"]
            to_id = edge["to_node_id"]
            if from_id in node_set and to_id in node_set:
                assertion_id = edge["assertion_id"]
                if assertion_id:
                    if not self.is_assertion_cleared(
                        assertion_id,
                        allowed_audiences=allowed_audiences,
                        max_classification=max_classification,
                    ):
                        continue
                    assertion_ids_set.add(assertion_id)
                    evidence_ids_set.update(self.supporting_evidence.get(assertion_id, []))
                subgraph_edges.append(edge)

        return {
            "node_count": len(subgraph_nodes),
            "edge_count": len(subgraph_edges),
            "nodes": subgraph_nodes,
            "edges": subgraph_edges,
            "assertion_ids": sorted(assertion_ids_set),
            "evidence_ids": sorted(evidence_ids_set),
        }

    def summary(self) -> dict[str, Any]:
        """Return high-level topology metrics."""
        return {
            "graph_path": str(self.graph_path),
            "graph_sha256": self.graph_sha256,
            "node_count": len(self.nodes),
            "assertion_count": len(self.assertions),
            "canonical_edge_count": len(self.canonical_edges),
            "forward_adjacency_nodes": len(self.forward_adjacency),
        }


__all__ = ["InMemoryGraphTopology"]
