"""Generational in-memory cache for SQLite graph structures.

Eliminates repetitive SQLite queries, view resolutions, and adjacency index
construction across queries within the same graph generation.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ..core import connect_sqlite
from .capability_bridge import (
    _collect_phonetic_person_twins,
    _pairs_from_group,
    _phonetic_surname_key,
    collect_technical_alias_pairs,
    inject_capability_bridge_edges,
)
from .constants import (
    GRAPH_RETRIEVAL_POLICY_BODY,
    LITERAL_MENTION_RELATIONSHIP,
    SPLIT_IDENTITY_RELATIONSHIP,
)

logger = logging.getLogger("dual_pipeline.graph_retrieval_core.graph_cache")

GraphData = tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[str]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]


def _normalize_entity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _table_exists(connection: Any, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_active_alias_keys(
    connection: Any, nodes: dict[str, dict[str, Any]], excluded_node_types: set[str], by_norm_name: dict[str, list[str]]
) -> None:
    if not _table_exists(connection, "canonical_alias"):
        return
    ca_cols = {r[1] for r in connection.execute("PRAGMA table_info(canonical_alias)").fetchall()}
    if "entity_id" in ca_cols and "display_alias" in ca_cols:
        sql = "SELECT entity_id AS nid, display_alias AS al FROM canonical_alias WHERE active=1"
    elif "node_id" in ca_cols and "alias" in ca_cols:
        sql = "SELECT node_id AS nid, alias AS al FROM canonical_alias WHERE active=1"
    else:
        return
    for row in connection.execute(sql):
        nid = row["nid"]
        if nid not in nodes or nodes[nid]["node_type"] in excluded_node_types:
            continue
        key = _normalize_entity_key(row["al"])
        if len(key) >= 3 and nid not in by_norm_name[key]:
            by_norm_name[key].append(nid)


_SHORT_ACRONYM_NODE_TYPES = frozenset({"workstream", "sow", "program", "technical_anchor", "system", "deliverable"})
_ACRONYM_PREFIX_WORDS = frozenset({"project", "workstream", "program", "initiative"})
_WORKSTREAM_NODE_TYPES = frozenset({"workstream", "program", "sow", "project", "org", "deliverable"})


def _index_short_acronym(node_id: str, node_type: str, key: str, by_norm_name: dict[str, list[str]]) -> None:
    if node_type not in _SHORT_ACRONYM_NODE_TYPES:
        return
    words = key.split()
    if len(words) != 2 or words[0] not in _ACRONYM_PREFIX_WORDS or len(words[1]) < 3:
        return
    short_key = words[1]
    if node_id not in by_norm_name[short_key]:
        pos = 0 if node_type == "workstream" else len(by_norm_name[short_key])
        by_norm_name[short_key].insert(pos, node_id)


def _collect_norm_names_and_aliases(
    connection: Any, nodes: dict[str, dict[str, Any]], excluded_node_types: set[str]
) -> dict[str, list[str]]:
    by_norm_name: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        node_type = node["node_type"]
        if node_type not in excluded_node_types:
            key = _normalize_entity_key(node.get("canonical_name", ""))
            if len(key) >= 3 and node_id not in by_norm_name[key]:
                by_norm_name[key].append(node_id)
            _index_short_acronym(node_id, node_type, key, by_norm_name)
    _load_active_alias_keys(connection, nodes, excluded_node_types, by_norm_name)
    return by_norm_name


def _load_redirect_pairs(connection: Any, nodes: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if not _table_exists(connection, "entity_redirect"):
        return pairs
    er_cols = {r[1] for r in connection.execute("PRAGMA table_info(entity_redirect)").fetchall()}
    if "old_node_id" in er_cols and "new_node_id" in er_cols:
        sql = "SELECT old_node_id AS old_id, new_node_id AS new_id FROM entity_redirect"
    elif "from_entity_id" in er_cols and "to_entity_id" in er_cols:
        sql = "SELECT from_entity_id AS old_id, to_entity_id AS new_id FROM entity_redirect"
    else:
        return pairs
    for row in connection.execute(sql):
        old_id, new_id = row["old_id"], row["new_id"]
        if old_id in nodes and new_id in nodes and old_id != new_id:
            pairs.add((min(old_id, new_id), max(old_id, new_id)))
    return pairs


def _collect_twin_pairs(
    connection: Any, nodes: dict[str, dict[str, Any]], by_norm_name: dict[str, list[str]]
) -> set[tuple[str, str]]:
    twin_pairs = (
        _load_redirect_pairs(connection, nodes)
        | _collect_phonetic_person_twins(nodes)
        | collect_technical_alias_pairs(nodes)
    )
    for group in by_norm_name.values():
        if 1 < len(group) <= 6:
            sorted_ids = sorted(set(group))
            for idx, first_id in enumerate(sorted_ids):
                for second_id in sorted_ids[idx + 1 :]:
                    twin_pairs.add((first_id, second_id))
    return twin_pairs


def _append_bidirectional_virtual_edge(
    first_id: str,
    second_id: str,
    aid: str,
    rel_family: str,
    edge_type: str,
    materiality: str,
    conf_score: Any,
    nodes: dict[str, dict[str, Any]],
    assn_ref: dict[str, Any],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
) -> None:
    for current_id, next_id, direction in ((first_id, second_id, "forward"), (second_id, first_id, "reverse")):
        proj_key = (aid, current_id, next_id)
        if proj_key in seen_projection:
            continue
        seen_projection.add(proj_key)
        adjacency[current_id].append({
            "assertion_id": aid, "from_node_id": current_id,
            "from_node_name": nodes[current_id]["canonical_name"], "from_node_type": nodes[current_id]["node_type"],
            "relationship_family": rel_family, "edge_type": edge_type,
            "to_node_id": next_id, "to_node_name": nodes[next_id]["canonical_name"], "to_node_type": nodes[next_id]["node_type"],
            "traversal_direction": direction, "evidence_ids": list(supporting_evidence.get(aid) or [aid]),
            "source_id": assn_ref.get("primary_source_id"), "materiality": materiality, "confidence_score": conf_score,
            "recorded_at": assn_ref.get("recorded_at"), "created_at": assn_ref.get("created_at"),
        })


def _match_multi_word_targets(
    lit_words: list[str],
    idx: int,
    word: str,
    subj_id: str,
    multi_word_by_first: dict[str, list[tuple[list[str], list[str]]]],
) -> set[str]:
    found: set[str] = set()
    for pattern_words, mw_targets in multi_word_by_first.get(word, ()):
        if lit_words[idx : idx + len(pattern_words)] == pattern_words:
            found.update(t for t in mw_targets if t != subj_id)
    return found


def _find_literal_target_nodes(
    lit_words: list[str],
    subj_id: str,
    single_word_targets: dict[str, list[str]],
    multi_word_by_first: dict[str, list[tuple[list[str], list[str]]]],
) -> list[str]:
    matched: set[str] = set()
    for idx, word in enumerate(lit_words):
        matched.update(t for t in single_word_targets.get(word, ()) if t and t != subj_id)
        matched.update(_match_multi_word_targets(lit_words, idx, word, subj_id, multi_word_by_first))
    return sorted(matched)


def _build_literal_lookup_tables(
    by_norm_name: dict[str, list[str]],
    nodes: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[tuple[list[str], list[str]]]]]:
    single_word_targets: dict[str, list[str]] = {}
    multi_word_by_first: dict[str, list[tuple[list[str], list[str]]]] = defaultdict(list)
    for key, nids in by_norm_name.items():
        if not nids:
            continue
        words = key.split()
        if len(words) == 1:
            allow_short = len(key) >= 5 or (
                len(key) >= 3
                and nodes is not None
                and any(nodes.get(nid, {}).get("node_type") in _SHORT_ACRONYM_NODE_TYPES for nid in nids)
            )
            if allow_short:
                single_word_targets[key] = list(dict.fromkeys(nids[:3]))
        elif 2 <= len(words) <= 5 and len(key) >= 4:
            multi_word_by_first[words[0]].append((words, list(dict.fromkeys(nids[:3]))))
    return single_word_targets, multi_word_by_first


def _is_eligible_literal_assertion(
    assn: dict[str, Any], nodes: dict[str, dict[str, Any]], excluded_node_types: set[str]
) -> bool:
    subj_id = assn.get("subject_node_id")
    if assn.get("object_node_id") is not None or subj_id not in nodes:
        return False
    if nodes[subj_id]["node_type"] in excluded_node_types:
        return False
    return bool(assn.get("currentness_state") == "current" and assn.get("retrieval_eligible"))


def _is_workstream_node(nid: Any, nodes: dict[str, dict[str, Any]]) -> bool:
    return bool(nid and nid in nodes and nodes[nid]["node_type"] in _WORKSTREAM_NODE_TYPES)


def _primary_workstreams_for_source(
    source_assns: list[tuple[str, dict[str, Any]]],
    nodes: dict[str, dict[str, Any]],
    single_word_targets: dict[str, list[str]],
    multi_word_by_first: dict[str, list[tuple[list[str], list[str]]]],
) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for _aid, assn in source_assns:
        for ep in (assn.get("subject_node_id"), assn.get("object_node_id")):
            if _is_workstream_node(ep, nodes):
                counts[str(ep)] += 2
        lit_words = _normalize_entity_key(str(assn.get("object_literal") or "")).split()
        targets = _find_literal_target_nodes(
            lit_words, str(assn.get("subject_node_id") or ""), single_word_targets, multi_word_by_first
        )
        for t_nid in (t for t in targets if _is_workstream_node(t, nodes)):
            counts[t_nid] += 1
    return sorted(
        counts.keys(),
        key=lambda nid: (0 if nodes[nid]["node_type"] in {"workstream", "program", "sow", "project"} else 1, -counts[nid], nid),
    )[:2]


def _group_active_assertions_by_source(
    assertions: dict[str, dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    by_source: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for aid, assn in assertions.items():
        sid = assn.get("primary_source_id")
        if sid and assn.get("validation_state") not in {"superseded", "rejected", "retired"}:
            by_source[str(sid)].append((aid, assn))
    return by_source


def _link_source_assertions_to_workstreams(
    source_assns: list[tuple[str, dict[str, Any]]],
    primary_ws_nodes: list[str],
    nodes: dict[str, dict[str, Any]],
    excluded_node_types: set[str],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
) -> None:
    for aid, assn in source_assns:
        subj_id = assn.get("subject_node_id")
        if not subj_id or subj_id not in nodes or nodes[subj_id]["node_type"] in excluded_node_types:
            continue
        for ws_nid in primary_ws_nodes:
            if subj_id != ws_nid and assn.get("object_node_id") != ws_nid:
                _append_bidirectional_virtual_edge(
                    subj_id, ws_nid, aid, LITERAL_MENTION_RELATIONSHIP, LITERAL_MENTION_RELATIONSHIP,
                    assn.get("materiality") or "medium", assn.get("confidence_score"),
                    nodes, assn, supporting_evidence, adjacency, seen_projection,
                )


def _inject_co_source_workstream_edges(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    by_norm_name: dict[str, list[str]],
    excluded_node_types: set[str],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
) -> None:
    """Connect co-source assertions to the primary workstream node referenced within the same source."""
    single_word_targets, multi_word_by_first = _build_literal_lookup_tables(by_norm_name, nodes)
    by_source = _group_active_assertions_by_source(assertions)
    for source_assns in by_source.values():
        primary_ws_nodes = _primary_workstreams_for_source(
            source_assns, nodes, single_word_targets, multi_word_by_first
        )
        if primary_ws_nodes:
            _link_source_assertions_to_workstreams(
                source_assns, primary_ws_nodes, nodes, excluded_node_types,
                supporting_evidence, adjacency, seen_projection,
            )


def _inject_literal_mention_edges(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    by_norm_name: dict[str, list[str]],
    excluded_node_types: set[str],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
) -> None:
    single_word_targets, multi_word_by_first = _build_literal_lookup_tables(by_norm_name, nodes)
    for aid, assn in assertions.items():
        if not _is_eligible_literal_assertion(assn, nodes, excluded_node_types):
            continue
        subj_id = assn["subject_node_id"]
        lit_words = _normalize_entity_key(str(assn.get("object_literal") or "")).split()
        for target_nid in _find_literal_target_nodes(lit_words, subj_id, single_word_targets, multi_word_by_first):
            if nodes[target_nid]["node_type"] not in excluded_node_types:
                _append_bidirectional_virtual_edge(
                    subj_id, target_nid, aid, assn.get("predicate") or LITERAL_MENTION_RELATIONSHIP,
                    LITERAL_MENTION_RELATIONSHIP, assn.get("materiality") or "medium",
                    assn.get("confidence_score"), nodes, assn, supporting_evidence, adjacency, seen_projection,
                )
    _inject_co_source_workstream_edges(
        nodes, assertions, by_norm_name, excluded_node_types, supporting_evidence, adjacency, seen_projection
    )


def _inject_split_identity_and_literal_edges(
    connection: Any,
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
) -> None:
    """Connect split-identity node twins and literal-only HAS_CLAIM_TEXT mentions in adjacency."""
    excluded_node_types = set(GRAPH_RETRIEVAL_POLICY_BODY["excluded_node_types"])
    by_norm_name = _collect_norm_names_and_aliases(connection, nodes, excluded_node_types)
    twin_pairs = _collect_twin_pairs(connection, nodes, by_norm_name)
    node_primary_assertion: dict[str, str] = {}
    for aid, assn in assertions.items():
        if assn.get("currentness_state") == "current" and assn.get("retrieval_eligible"):
            for endpoint in (assn.get("subject_node_id"), assn.get("object_node_id")):
                if endpoint and endpoint not in node_primary_assertion:
                    node_primary_assertion[endpoint] = aid
    for first_id, second_id in sorted(twin_pairs):
        bridge_aid = node_primary_assertion.get(second_id) or node_primary_assertion.get(first_id) or f"split-twin:{first_id}:{second_id}"
        _append_bidirectional_virtual_edge(
            first_id, second_id, bridge_aid, SPLIT_IDENTITY_RELATIONSHIP, SPLIT_IDENTITY_RELATIONSHIP,
            "high", 1.0, nodes, assertions.get(bridge_aid, {}), supporting_evidence, adjacency, seen_projection,
        )
    _inject_literal_mention_edges(
        nodes, assertions, by_norm_name, excluded_node_types, supporting_evidence, adjacency, seen_projection
    )
    inject_capability_bridge_edges(
        nodes, assertions, node_primary_assertion, supporting_evidence,
        adjacency, seen_projection, _append_bidirectional_virtual_edge,
    )


def _load_canonical_edge_adjacency(
    connection: Any,
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    supporting_evidence: dict[str, list[str]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]], set[tuple[str, str, str]]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_by_node: dict[str, set[str]] = defaultdict(set)
    seen_projection: set[tuple[str, str, str]] = set()
    excluded_rels = GRAPH_RETRIEVAL_POLICY_BODY["excluded_relationship_families"]
    excluded_types = GRAPH_RETRIEVAL_POLICY_BODY["excluded_node_types"]
    for row in connection.execute(
        """SELECT assertion_id,from_node_id,relationship_family,edge_type,
                  to_node_id,materiality,source_id
             FROM v_canonical_edge
            ORDER BY assertion_id,from_node_id,to_node_id"""
    ):
        assertion_id, from_id, to_id = row["assertion_id"], row["from_node_id"], row["to_node_id"]
        if from_id not in nodes or to_id not in nodes:
            continue
        if (
            row["relationship_family"] in excluded_rels
            or nodes[from_id]["node_type"] in excluded_types
            or nodes[to_id]["node_type"] in excluded_types
        ):
            excluded_by_node[from_id].add(assertion_id)
            excluded_by_node[to_id].add(assertion_id)
            continue
        assn_ref = assertions.get(assertion_id, {})
        for current_id, next_id, direction in ((from_id, to_id, "forward"), (to_id, from_id, "reverse")):
            projection_key = (assertion_id, current_id, next_id)
            if projection_key in seen_projection:
                continue
            seen_projection.add(projection_key)
            adjacency[current_id].append({
                "assertion_id": assertion_id, "from_node_id": current_id,
                "from_node_name": nodes[current_id]["canonical_name"], "from_node_type": nodes[current_id]["node_type"],
                "relationship_family": row["relationship_family"], "edge_type": row["edge_type"],
                "to_node_id": next_id, "to_node_name": nodes[next_id]["canonical_name"], "to_node_type": nodes[next_id]["node_type"],
                "traversal_direction": direction, "evidence_ids": list(supporting_evidence.get(assertion_id, [])),
                "source_id": row["source_id"], "materiality": row["materiality"],
                "confidence_score": assn_ref.get("confidence_score"),
                "recorded_at": assn_ref.get("recorded_at"), "created_at": assn_ref.get("created_at"),
            })
    return adjacency, excluded_by_node, seen_projection


def read_graph_from_disk(graph_path: Path) -> GraphData:
    """Reads graph tables and views from SQLite disk into memory."""
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        nodes = {
            row["node_id"]: dict(row)
            for row in connection.execute("SELECT node_id,canonical_name,node_type FROM node WHERE active=1 ORDER BY node_id")
        }
        assertions = {
            row["assertion_id"]: dict(row)
            for row in connection.execute(
                """SELECT assertion_id,subject_node_id,predicate,legacy_predicate,
                          object_node_id,object_literal,materiality,canonical_status,
                          validation_state,currentness_state,retrieval_eligible,
                          primary_source_id,recorded_at,created_at,confidence_score
                     FROM assertion ORDER BY assertion_id"""
            )
        }
        supporting_evidence: dict[str, list[str]] = defaultdict(list)
        contradicting_evidence: dict[str, list[str]] = defaultdict(list)
        for row in connection.execute(
            "SELECT assertion_id,evidence_id,evidence_relationship FROM assertion_evidence ORDER BY assertion_id,evidence_id"
        ):
            target = supporting_evidence if row["evidence_relationship"] == "supports" else contradicting_evidence
            target[row["assertion_id"]].append(row["evidence_id"])

        adjacency, excluded_by_node, seen_projection = _load_canonical_edge_adjacency(
            connection, nodes, assertions, supporting_evidence
        )
        _inject_split_identity_and_literal_edges(
            connection, nodes, assertions, supporting_evidence, adjacency, seen_projection
        )
        for node_id in adjacency:
            adjacency[node_id].sort(
                key=lambda edge: (edge["relationship_family"], edge["edge_type"] or "", edge["assertion_id"], edge["to_node_id"])
            )
        conflicts: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
            "SELECT assertion_id_1,assertion_id_2 FROM v_conflicting_assertions WHERE resolution_state='unresolved' ORDER BY assertion_id_1,assertion_id_2"
        ):
            conflicts[row["assertion_id_1"]].add(row["assertion_id_2"])
            conflicts[row["assertion_id_2"]].add(row["assertion_id_1"])
        return nodes, assertions, supporting_evidence, adjacency, contradicting_evidence, conflicts, excluded_by_node
    finally:
        connection.close()


class GenerationalGraphCache:
    """Thread-safe generational cache for in-memory graph structures."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[tuple[str, int, int], GraphData] = {}
        self._hits: int = 0
        self._misses: int = 0

    def _make_key(self, resolved_path: Path) -> tuple[str, int, int]:
        try:
            stat = os.stat(resolved_path)
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError:
            mtime_ns = 0
            size = 0
        return str(resolved_path), mtime_ns, size

    def get(
        self,
        graph_path: Path | str,
        loader: Callable[[Path], GraphData] | None = None,
    ) -> GraphData:
        """Retrieves or loads graph data bound to the graph file mtime/size."""
        path = Path(graph_path).resolve()
        key = self._make_key(path)

        with self._lock:
            if key in self._cache:
                self._hits += 1
                return self._cache[key]

            self._misses += 1
            load_fn = loader or read_graph_from_disk
            data = load_fn(path)

            # Evict stale entries for this graph path
            path_str = str(path)
            stale_keys = [k for k in self._cache if k[0] == path_str]
            for stale_key in stale_keys:
                del self._cache[stale_key]

            self._cache[key] = data
            return data

    def invalidate(self, graph_path: Path | str | None = None) -> None:
        """Invalidates cache entries for a specific path, or all if None."""
        with self._lock:
            if graph_path is None:
                self._cache.clear()
            else:
                path_str = str(Path(graph_path).resolve())
                stale_keys = [k for k in self._cache if k[0] == path_str]
                for stale_key in stale_keys:
                    del self._cache[stale_key]

    def clear(self) -> None:
        """Clears all cached graphs and resets performance metrics."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        """Returns hit, miss, and cached item counts."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "cached_graphs": len(self._cache),
            }


_GLOBAL_GRAPH_CACHE: GenerationalGraphCache | None = None
_GLOBAL_LOCK = threading.RLock()


def get_global_graph_cache() -> GenerationalGraphCache:
    """Returns the process-wide generational graph cache singleton."""
    global _GLOBAL_GRAPH_CACHE
    with _GLOBAL_LOCK:
        if _GLOBAL_GRAPH_CACHE is None:
            _GLOBAL_GRAPH_CACHE = GenerationalGraphCache()
        return _GLOBAL_GRAPH_CACHE


def cached_load_graph(graph_path: Path | str) -> GraphData:
    """Convenience helper to retrieve cached graph data from the global cache."""
    return get_global_graph_cache().get(graph_path)
