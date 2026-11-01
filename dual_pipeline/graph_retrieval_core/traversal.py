"""Graph traversal, authorization checks, BM25 ranking, and connected retrieval expansion."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import connect_sqlite, digest, stable_id
from dual_pipeline.hash_cache import cached_sha256_file as sha256_file
from dual_pipeline.index import (  # noqa: lazy-decouple
    CLASSIFICATION_RANK,
    normalize_assertion_scope,
    resolve_retrieval_mode,
    retrieval_metadata_from_artifact,
    validate_sidecar,
)
from dual_pipeline.retrieval_clearance import retrieval_clearance  # noqa: lazy-decouple
from .constants import (
    _EXPANSION_LIMITS,
    GRAPH_RETRIEVAL_POLICY,
    GRAPH_RETRIEVAL_POLICY_BODY,
)
from .graph_cache import get_global_graph_cache
from .scoring import (
    _base_seed_score,
    _better_path,
    _edge_candidate_sort_key,
    _edge_increment,
    _path,
    _should_skip_unmatched_context_record,
    unified_result_sort_key,
)
from .tokenize import (
    _question_tokens,
    bm25_query_tokens,
)


def _artifact_assertion_id(record: dict[str, Any]) -> str | None:
    value = record.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id")
    return value if isinstance(value, str) and value else None


def _artifact_entity_ids(record: dict[str, Any]) -> list[str]:
    value = record.get("extensions", {}).get("com.partner_advisory.nee.graph-entity-ids")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _assertion_is_navigation_only(
    assertion: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> bool:
    excluded_relationships = {
        value.casefold() for value in GRAPH_RETRIEVAL_POLICY_BODY["excluded_relationship_families"]
    }
    relationships = {
        value.casefold()
        for value in (
            assertion.get("predicate"),
            assertion.get("legacy_predicate"),
        )
        if isinstance(value, str) and value
    }
    if relationships & excluded_relationships:
        return True
    excluded_node_types = {
        value.casefold() for value in GRAPH_RETRIEVAL_POLICY_BODY["excluded_node_types"]
    }
    return any(
        node_id in nodes and str(nodes[node_id]["node_type"]).casefold() in excluded_node_types
        for node_id in (
            assertion.get("subject_node_id"),
            assertion.get("object_node_id"),
        )
    )


def _authorized_records(
    *,
    sidecar_path: Path,
    graph_path: Path,
    allowed_lanes: list[str],
    retrieval_mode: str,
    classification: str,
    audiences: list[str],
    allowed_assertion_ids: Iterable[str] | None,
    allow_lane_subset: bool = False,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, dict[str, Any]]]]:
    from .clearance_cache import authorized_records_cached

    return authorized_records_cached(
        sidecar_path=sidecar_path,
        graph_path=graph_path,
        allowed_lanes=allowed_lanes,
        retrieval_mode=retrieval_mode,
        classification=classification,
        audiences=audiences,
        allowed_assertion_ids=allowed_assertion_ids,
        allow_lane_subset=allow_lane_subset,
        validate_fn=validate_sidecar,
    )


def governed_bm25_assertion_ranks(
    *,
    question: str,
    graph_path: Path,
    sidecar_path: Path,
    allowed_lanes: list[str],
    retrieval_mode: str,
    classification: str,
    audiences: list[str],
    allowed_assertion_ids: Iterable[str] | None = None,
) -> tuple[dict[str, dict[str, int]], bool]:
    """Rank authorized assertion IDs through the graph's synchronized FTS5 index."""
    from .clearance_cache import fts5_bm25_matches_cached

    _, records_by_assertion = _authorized_records(
        sidecar_path=sidecar_path,
        graph_path=graph_path,
        allowed_lanes=allowed_lanes,
        retrieval_mode=retrieval_mode,
        classification=classification,
        audiences=audiences,
        allowed_assertion_ids=allowed_assertion_ids,
        allow_lane_subset=True,
    )
    ranks_by_lane: dict[str, dict[str, int]] = {lane: {} for lane in allowed_lanes}
    fts_executed, matched_assertion_ids = fts5_bm25_matches_cached(
        graph_path,
        question,
    )
    if not fts_executed:
        return ranks_by_lane, False
    assertions_by_lane: dict[str, list[str]] = defaultdict(list)
    for assertion_id in matched_assertion_ids:
        if assertion_id not in records_by_assertion:
            continue
        record = records_by_assertion[assertion_id][1]
        assertions_by_lane[record["lane"]].append(assertion_id)
    for lane in allowed_lanes:
        ranks_by_lane[lane] = {
            assertion_id: rank
            for rank, assertion_id in enumerate(
                assertions_by_lane[lane],
                1,
            )
        }
    return ranks_by_lane, True


def _load_graph(
    graph_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[str]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    return get_global_graph_cache().get(graph_path)


def expand_connected_retrieval(
    *,
    retrieval: dict[str, Any],
    question: str,
    graph_path: Path,
    sidecar_path: Path,
    run_id: str,
    graph_snapshot: str,
    query_plan_digest: str,
    requested_hops: int,
    relationships_to_inspect: list[str],
    classification: str,
    audiences: list[str],
    retrieval_mode: str,
    allowed_assertion_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand hybrid seeds into a bounded, typed, connected evidence set."""

    if not 0 <= requested_hops <= GRAPH_RETRIEVAL_POLICY_BODY["maximum_hops"]:
        raise ValueError("requested graph hops exceed the governed maximum")
    allowed_lanes = list(retrieval["allowed_lanes"])
    if retrieval.get("retrieval_mode") != retrieval_mode:
        raise RuntimeError("graph expansion retrieval mode binding mismatch")
    records_by_id, records_by_assertion = _authorized_records(
        sidecar_path=sidecar_path,
        graph_path=graph_path,
        allowed_lanes=allowed_lanes,
        retrieval_mode=retrieval_mode,
        classification=classification,
        audiences=audiences,
        allowed_assertion_ids=allowed_assertion_ids,
    )
    (
        nodes,
        assertions,
        supporting_evidence,
        adjacency,
        contradicting_evidence,
        conflicts,
        excluded_by_node,
    ) = _load_graph(graph_path)
    degrees = {node_id: len(edges) for node_id, edges in adjacency.items()}
    requested_relationships = {
        value for value in relationships_to_inspect if isinstance(value, str) and value
    }

    seed_results: list[dict[str, Any]] = []
    suppressed_navigation_record_ids: set[str] = set()
    suppressed_unauthorized_seed_record_ids: set[str] = set()
    for lane in allowed_lanes:
        for result in retrieval["lanes"][lane]["results"]:
            if result["record_id"] not in records_by_id:
                suppressed_unauthorized_seed_record_ids.add(result["record_id"])
                continue
            assertion_id = _artifact_assertion_id(result["record"])
            if assertion_id is None or assertion_id not in assertions:
                suppressed_unauthorized_seed_record_ids.add(result["record_id"])
                continue
            if _assertion_is_navigation_only(assertions[assertion_id], nodes):
                suppressed_navigation_record_ids.add(result["record_id"])
                continue
            seed_results.append(result)
    seed_results.sort(
        key=lambda value: (
            value.get("fused_rank") or 10_000,
            value["lane"],
            value["record_id"],
        )
    )
    seed_results = seed_results[: GRAPH_RETRIEVAL_POLICY_BODY["maximum_seed_records"]]
    seed_record_ids = [value["record_id"] for value in seed_results]
    seed_record_set = set(seed_record_ids)

    seed_paths: dict[str, dict[str, Any]] = {}
    best_node_paths: dict[
        tuple[str, str], tuple[float, float, list[dict[str, Any]], frozenset[str]]
    ] = {}
    frontier: list[
        tuple[
            str,
            str,
            float,
            float,
            list[dict[str, Any]],
            frozenset[str],
            frozenset[str],
        ]
    ] = []
    isolated_seed_records: set[str] = set()
    for result in seed_results:
        record = result["record"]
        record_id = result["record_id"]
        assertion_id = _artifact_assertion_id(record)
        if assertion_id is None or assertion_id not in assertions:
            raise RuntimeError("answer-bearing hybrid seed is not assertion-bound")
        seed_paths[record_id] = _path(
            seed_record_id=record_id,
            target_record_id=record_id,
            target_assertion_id=assertion_id,
            lane=result["lane"],
            origin="hybrid_seed",
            attachment_kind="seed",
            path_score=_base_seed_score(result),
            topology_score=1.0,
            steps=[],
        )
        entity_ids = [node_id for node_id in _artifact_entity_ids(record) if node_id in nodes]
        if not entity_ids:
            entity_ids = [
                value
                for value in (
                    assertions[assertion_id]["subject_node_id"],
                    assertions[assertion_id]["object_node_id"],
                )
                if value in nodes
            ]
        if not any(adjacency.get(node_id) for node_id in entity_ids):
            isolated_seed_records.add(record_id)
        for node_id in sorted(set(entity_ids)):
            state = (
                _base_seed_score(result),
                1.0,
                [],
                frozenset({node_id}),
            )
            best_node_paths[(record_id, node_id)] = state
            frontier.append(
                (
                    record_id,
                    node_id,
                    state[0],
                    state[1],
                    state[2],
                    state[3],
                    frozenset({assertion_id}),
                )
            )

    candidate_paths: dict[str, dict[str, Any]] = {}
    suppressed_hub_edges: set[tuple[str, str, str]] = set()
    for hop in range(1, requested_hops + 1):
        next_frontier: list[
            tuple[
                str,
                str,
                float,
                float,
                list[dict[str, Any]],
                frozenset[str],
                frozenset[str],
            ]
        ] = []
        for (
            seed_record_id,
            current_node_id,
            path_score,
            topology_score,
            steps,
            visited_nodes,
            visited_assertions,
        ) in frontier:
            candidates = sorted(
                adjacency.get(current_node_id, []),
                key=lambda edge: _edge_candidate_sort_key(edge, requested_relationships, degrees),
            )
            maximum_fanout = GRAPH_RETRIEVAL_POLICY_BODY["maximum_node_fanout"]
            if len(candidates) > maximum_fanout:
                suppressed_hub_edges.update(
                    (seed_record_id, current_node_id, edge["assertion_id"])
                    for edge in candidates[maximum_fanout:]
                )
            for edge in candidates[:maximum_fanout]:
                if (
                    edge["assertion_id"] in visited_assertions
                    or edge["to_node_id"] in visited_nodes
                ):
                    continue
                increment, topology_increment = _edge_increment(
                    edge,
                    requested_relationships=requested_relationships,
                    destination_degree=degrees.get(edge["to_node_id"], 0),
                    hop=hop,
                )
                next_score = path_score + increment
                next_topology = topology_score + topology_increment
                step = dict(edge)
                step["hop"] = hop
                next_steps = [*steps, step]
                next_nodes = frozenset({*visited_nodes, edge["to_node_id"]})
                next_assertions = frozenset({*visited_assertions, edge["assertion_id"]})
                node_key = (seed_record_id, edge["to_node_id"])
                previous = best_node_paths.get(node_key)
                if previous is None or next_score > previous[0]:
                    best_node_paths[node_key] = (
                        next_score,
                        next_topology,
                        next_steps,
                        next_nodes,
                    )
                bound_record = records_by_assertion.get(edge["assertion_id"])
                if bound_record is not None:
                    target_record_id, target_record = bound_record
                    if target_record_id not in seed_record_set:
                        value = _path(
                            seed_record_id=seed_record_id,
                            target_record_id=target_record_id,
                            target_assertion_id=edge["assertion_id"],
                            lane=target_record["lane"],
                            origin="graph_edge",
                            attachment_kind="relationship",
                            path_score=next_score,
                            topology_score=next_topology,
                            steps=next_steps,
                        )
                        if _better_path(value, candidate_paths.get(target_record_id)):
                            candidate_paths[target_record_id] = value
                if (
                    nodes[edge["to_node_id"]]["node_type"]
                    not in GRAPH_RETRIEVAL_POLICY_BODY["terminal_node_types"]
                ):
                    next_frontier.append(
                        (
                            seed_record_id,
                            edge["to_node_id"],
                            next_score,
                            next_topology,
                            next_steps,
                            next_nodes,
                            next_assertions,
                        )
                    )
        frontier = next_frontier

    best_path_for_node: dict[str, tuple[str, float, float, list[dict[str, Any]]]] = {}
    for (seed_record_id, node_id), (
        score,
        topology_score,
        steps,
        _visited,
    ) in best_node_paths.items():
        current = best_path_for_node.get(node_id)
        candidate = (seed_record_id, score, topology_score, steps)
        if current is None or (
            -candidate[1],
            len(candidate[3]),
            candidate[0],
        ) < (
            -current[1],
            len(current[3]),
            current[0],
        ):
            best_path_for_node[node_id] = candidate

    query_tokens = _question_tokens(question)
    from .clearance_cache import record_tokens_cached

    precomputed_record_tokens = record_tokens_cached(sidecar_path, graph_path)
    for record_id, record in sorted(records_by_id.items()):
        if requested_hops == 0:
            continue
        if record_id in seed_record_set:
            continue
        assertion_id = _artifact_assertion_id(record)
        if assertion_id is None or assertion_id not in assertions:
            continue
        if _assertion_is_navigation_only(assertions[assertion_id], nodes):
            suppressed_navigation_record_ids.add(record_id)
            continue
        reachable = [
            (node_id, best_path_for_node[node_id])
            for node_id in _artifact_entity_ids(record)
            if node_id in best_path_for_node
        ]
        if not reachable:
            continue
        node_id, (seed_record_id, node_score, node_topology, node_steps) = min(
            reachable, key=lambda item: (-item[1][1], len(item[1][3]), item[0], item[1][0])
        )
        assertion = assertions[assertion_id]
        if (
            record["lane"] == "approved" and assertion.get("canonical_eligible") not in (None, 1)
        ) or (
            assertion.get("confidence_score") is not None
            and float(assertion["confidence_score"]) < 0.65
        ):
            continue
        record_tokens = precomputed_record_tokens.get(record_id)
        if record_tokens is None:
            record_tokens = _question_tokens(record.get("retrieval_text", ""))
        non_entity_q_tokens = query_tokens - _question_tokens(
            str(nodes.get(node_id, {}).get("canonical_name", ""))
        )
        if _should_skip_unmatched_context_record(
            non_entity_q_tokens, record_tokens, assertion, requested_relationships, node_steps
        ):
            continue
        overlap = len(query_tokens & record_tokens) / len(query_tokens) if query_tokens else 0.0
        relationship_boost = (
            0.18
            if (
                assertion["predicate"] in requested_relationships
                or assertion["legacy_predicate"] in requested_relationships
            )
            else 0.0
        )
        entity_types = {
            nodes[value]["node_type"] for value in _artifact_entity_ids(record) if value in nodes
        }
        opportunity_boost = 0.20 if "opportunity" in entity_types else 0.0
        candidate_penalty = 0.05 if record["lane"] == "candidate" else 0.0
        context_score = (
            node_score
            + 0.05
            + 0.20 * overlap
            + relationship_boost
            + opportunity_boost
            - candidate_penalty
        )
        value = _path(
            seed_record_id=seed_record_id,
            target_record_id=record_id,
            target_assertion_id=assertion_id,
            lane=record["lane"],
            origin="node_context",
            attachment_kind="entity_context",
            path_score=context_score,
            topology_score=node_topology,
            steps=node_steps,
        )
        if _better_path(value, candidate_paths.get(record_id)):
            candidate_paths[record_id] = value

    limits = _EXPANSION_LIMITS.get(
        retrieval_mode,
        {"approved": 24, "candidate": 0, "external_public": 0},
    )
    selected_paths: dict[str, dict[str, Any]] = dict(seed_paths)
    expanded_by_lane: dict[str, int] = defaultdict(int)
    selected_families: set[str] = set()
    selected_nodes: set[str] = set()
    selected_bridge_counts: dict[str, int] = defaultdict(int)
    branch_suppressed_records: set[str] = set()
    candidates = list(candidate_paths.items())
    maximum_expanded = GRAPH_RETRIEVAL_POLICY_BODY["maximum_expanded_records"]
    while candidates and sum(expanded_by_lane.values()) < maximum_expanded:
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for record_id, path in candidates:
            lane = path["lane"]
            if expanded_by_lane[lane] >= limits.get(lane, 0):
                continue
            bridge_node_id = path["steps"][-1]["from_node_id"] if path["hop_count"] >= 2 else None
            if (
                bridge_node_id is not None
                and selected_bridge_counts[bridge_node_id]
                >= GRAPH_RETRIEVAL_POLICY_BODY["maximum_records_per_bridge_node"]
            ):
                branch_suppressed_records.add(record_id)
                continue
            families = {step["relationship_family"] for step in path["steps"]}
            path_nodes = {
                node_id
                for step in path["steps"]
                for node_id in (step["from_node_id"], step["to_node_id"])
            }
            diversity = 0.035 * len(families - selected_families) + 0.01 * min(
                3, len(path_nodes - selected_nodes)
            )
            scored.append((path["path_score"] + diversity, record_id, path))
        if not scored:
            break
        _, record_id, path = min(
            scored,
            key=lambda value: (
                -value[0],
                value[2]["hop_count"],
                value[1],
            ),
        )
        selected_paths[record_id] = path
        expanded_by_lane[path["lane"]] += 1
        selected_families.update(step["relationship_family"] for step in path["steps"])
        selected_nodes.update(
            node_id
            for step in path["steps"]
            for node_id in (step["from_node_id"], step["to_node_id"])
        )
        if path["hop_count"] >= 2:
            selected_bridge_counts[path["steps"][-1]["from_node_id"]] += 1
        candidates = [
            (candidate_id, candidate_path)
            for candidate_id, candidate_path in candidates
            if candidate_id != record_id
        ]

    combined = copy.deepcopy(retrieval)
    for lane in allowed_lanes:
        combined["lanes"][lane]["results"] = [
            result
            for result in combined["lanes"][lane]["results"]
            if result["record_id"] in seed_record_set
        ]
    existing_record_ids = set(seed_record_set)
    graph_rank = 0
    for record_id, path in sorted(
        selected_paths.items(),
        key=lambda item: (
            item[1]["origin"] == "hybrid_seed",
            -item[1]["path_score"],
            item[1]["hop_count"],
            item[0],
        ),
    ):
        if record_id in existing_record_ids:
            continue
        graph_rank += 1
        record = records_by_id[record_id]
        lane = record["lane"]
        combined["lanes"][lane]["results"].append(
            {
                "record_id": record_id,
                "lane": lane,
                "fused_rank": None,
                "fused_score": None,
                "dense_rank": None,
                "dense_score": None,
                "sparse_rank": None,
                "sparse_score": None,
                "bm25_rank": None,
                "colbert_rank": None,
                "colbert_score": None,
                "graph_rank": graph_rank,
                "graph_score": path["path_score"],
                "graph_hop_count": path["hop_count"],
                "graph_seed_record_id": path["seed_record_id"],
                "retrieval_origin": "graph_expansion",
                "evidence_stratum": record.get("evidence_stratum", "SUPPORTING"),
                "record": record,
            }
        )
        existing_record_ids.add(record_id)
    for lane in allowed_lanes:
        combined["lanes"][lane]["results"].sort(
            key=lambda result: unified_result_sort_key(result, seed_paths)
        )
    combined["final_record_count"] = len(existing_record_ids)

    selected_assertion_to_record: dict[str, str] = {}
    for record_id in existing_record_ids:
        assertion_id = _artifact_assertion_id(records_by_id[record_id])
        if assertion_id is not None:
            selected_assertion_to_record[assertion_id] = record_id
    counterevidence: list[dict[str, Any]] = []
    for assertion_id, record_id in sorted(selected_assertion_to_record.items()):
        evidence_ids = sorted(set(contradicting_evidence.get(assertion_id, [])))
        if evidence_ids:
            counterevidence.append(
                {
                    "counterevidence_id": stable_id(
                        "counterevidence",
                        "evidence",
                        assertion_id,
                        evidence_ids,
                        length=28,
                    ),
                    "kind": "contradicting_evidence",
                    "record_id": record_id,
                    "assertion_id": assertion_id,
                    "related_assertion_id": None,
                    "evidence_ids": evidence_ids,
                    "resolution_state": "not_applicable",
                }
            )
        for related_assertion_id in sorted(conflicts.get(assertion_id, set())):
            conflict_evidence_ids = sorted(
                set(supporting_evidence.get(assertion_id, []))
                | set(supporting_evidence.get(related_assertion_id, []))
            )
            counterevidence.append(
                {
                    "counterevidence_id": stable_id(
                        "counterevidence",
                        "conflict",
                        assertion_id,
                        related_assertion_id,
                        length=28,
                    ),
                    "kind": "assertion_conflict",
                    "record_id": record_id,
                    "assertion_id": assertion_id,
                    "related_assertion_id": related_assertion_id,
                    "evidence_ids": conflict_evidence_ids,
                    "resolution_state": "unresolved",
                }
            )
    counterevidence = [
        value
        for _, value in sorted(
            {value["counterevidence_id"]: value for value in counterevidence}.items()
        )
    ]

    selected_paths = {
        record_id: path
        for record_id, path in selected_paths.items()
        if record_id in existing_record_ids
    }
    reached_nodes = {
        node_id
        for path in selected_paths.values()
        for step in path["steps"]
        for node_id in (step["from_node_id"], step["to_node_id"])
    }
    for record_id in seed_record_ids:
        reached_nodes.update(_artifact_entity_ids(records_by_id[record_id]))
    navigation_edge_suppression_count = len(
        {
            assertion_id
            for node_id in reached_nodes
            for assertion_id in excluded_by_node.get(node_id, set())
        }
    )
    selected_assertion_ids = set(selected_assertion_to_record)
    missing_evidence_count = sum(
        not supporting_evidence.get(assertion_id) for assertion_id in selected_assertion_ids
    )
    opportunity_context_count = 0
    for assertion_id in selected_assertion_ids:
        assertion = assertions[assertion_id]
        entity_ids = {
            assertion["subject_node_id"],
            assertion["object_node_id"],
        }
        if any(
            node_id in nodes and nodes[node_id]["node_type"] == "opportunity"
            for node_id in entity_ids
        ):
            opportunity_context_count += 1
    max_hop_reached = max(
        (path["hop_count"] for path in selected_paths.values() if path["origin"] != "hybrid_seed"),
        default=0,
    )
    relationship_families = {
        step["relationship_family"] for path in selected_paths.values() for step in path["steps"]
    }
    gaps: list[str] = []
    if requested_hops and max_hop_reached == 0:
        gaps.append(
            "No governed multi-hop relationship was found beyond the selected seed records."
        )
    if isolated_seed_records:
        gaps.append(
            f"{len(isolated_seed_records)} selected seed records have no traversable decision-safe relationship."
        )
    if missing_evidence_count:
        gaps.append(
            f"{missing_evidence_count} selected records are missing supporting evidence and must not be used."
        )
    if not counterevidence:
        gaps.append("No governed counterevidence was found within the selected connected scope.")

    selected_record_ids_by_lane = {
        lane: [result["record_id"] for result in combined["lanes"][lane]["results"]]
        if lane in combined["lanes"]
        else []
        for lane in ("approved", "candidate", "external_public")
    }
    metrics = {
        "seed_record_count": len(seed_record_ids),
        "expanded_record_count": len(existing_record_ids - seed_record_set),
        "selected_record_count": len(existing_record_ids),
        "reached_node_count": len(reached_nodes),
        "path_count": len(selected_paths),
        "relationship_family_count": len(relationship_families),
        "bm25_seed_count": sum(result.get("bm25_rank") is not None for result in seed_results),
        "isolated_seed_count": len(isolated_seed_records),
        "navigation_edge_suppression_count": navigation_edge_suppression_count,
        "navigation_record_suppression_count": len(suppressed_navigation_record_ids),
        "hub_edge_suppression_count": len(suppressed_hub_edges),
        "branch_suppression_count": len(branch_suppressed_records),
        "candidate_context_count": sum(
            path["lane"] == "candidate" and path["origin"] != "hybrid_seed"
            for path in selected_paths.values()
        ),
        "opportunity_context_count": opportunity_context_count,
        "counterevidence_count": len(counterevidence),
        "missing_evidence_count": missing_evidence_count,
    }
    paths = sorted(
        selected_paths.values(),
        key=lambda path: (
            path["lane"],
            path["origin"] != "hybrid_seed",
            path["hop_count"],
            -path["path_score"],
            path["target_record_id"],
        ),
    )
    map_id = stable_id(
        "connected-evidence-map",
        run_id,
        graph_snapshot,
        query_plan_digest,
        seed_record_ids,
        paths,
        length=28,
    )
    artifact = create_artifact(
        "connected-evidence-map",
        map_id,
        map_id=map_id,
        run_id=run_id,
        query_plan_digest=query_plan_digest,
        graph_snapshot=graph_snapshot,
        graph_sha256=sha256_file(graph_path),
        retrieval_policy=GRAPH_RETRIEVAL_POLICY,
        requested_hops=requested_hops,
        max_hop_reached=max_hop_reached,
        seed_record_ids=seed_record_ids,
        selected_record_ids_by_lane=selected_record_ids_by_lane,
        paths=paths,
        counterevidence=counterevidence,
        gaps=gaps,
        metrics=metrics,
        automatic_graph_mutation_count=0,
        extensions={
            "com.partner_advisory.nee.connected-retrieval": {
                "source_retrieval_digest": digest(retrieval),
                "question_digest": digest(question),
                "relationship_policy": sorted(requested_relationships),
                "suppressed_navigation_record_ids": sorted(suppressed_navigation_record_ids),
            }
        },
    )
    combined.update(
        {
            "graph_traversal_executed": requested_hops > 0,
            "graph_requested_hops": requested_hops,
            "graph_max_hop_reached": max_hop_reached,
            "graph_expanded_record_count": metrics["expanded_record_count"],
            "connected_evidence_map_id": map_id,
            "connected_evidence_map_digest": artifact["canonical_digest"]["value"],
        }
    )
    return combined, artifact
