"""Graph traversal anchor expansion for semantic unit retrieval."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dual_pipeline.core import connect_sqlite

from .anchor_traversal_support import (
    build_expanded_item,
    classification_rank,
    fetch_validated_assertion,
    score_assertion_relevance,
    seed_seen_assertions,
    validate_classification,
)


def _expand_passage_anchor(
    conn: Any,
    item: dict[str, Any],
    *,
    seen_ids: set[str],
    lane: str,
    req_rank: int,
    req_auds: set[str],
    max_facts: int,
    hop_budget: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any], str, float]]]:
    art = item.get("record", {})
    anchor_id = item.get("record_id", "")
    anchor_score = float(item.get("fused_score", 1.0))
    candidate_aids = art.get("extensions", {}).get("com.partner_advisory.nee.graph-assertion-ids", [])
    if not candidate_aids and art.get("evidence_ids"):
        eids = art.get("evidence_ids", [])
        placeholders = ",".join("?" for _ in eids)
        rows = conn.execute(
            f"""SELECT assertion_id FROM assertion_evidence
                WHERE evidence_id IN ({placeholders}) AND evidence_relationship='supports'""",
            eids,
        ).fetchall()
        candidate_aids = [r[0] for r in rows]

    expanded: list[dict[str, Any]] = []
    frontiers: list[tuple[str, dict[str, Any], str, float]] = []
    for aid in candidate_aids:
        if aid in seen_ids:
            continue
        asrt, ev_ids = fetch_validated_assertion(
            conn, aid, lane=lane, req_rank=req_rank, req_audiences=req_auds
        )
        if not asrt:
            continue
        seen_ids.add(aid)
        expanded.append(build_expanded_item(
            asrt, ev_ids, anchor_id=anchor_id, anchor_score=anchor_score,
            lane=lane, traversal_kind="passage_to_assertion", hops=1, base_item=item
        ))
        if hop_budget >= 2:
            for nid in [asrt.get("subject_node_id"), asrt.get("object_node_id")]:
                if nid:
                    frontiers.append((nid, item, anchor_id, anchor_score))
        if len(expanded) >= max_facts:
            break
    return expanded, frontiers


def _expand_profile_anchor(
    conn: Any,
    item: dict[str, Any],
    *,
    seen_ids: set[str],
    q_tokens: list[str],
    lane: str,
    req_rank: int,
    req_auds: set[str],
    max_facts: int,
    hop_budget: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any], str, float]]]:
    art = item.get("record", {})
    anchor_id = item.get("record_id", "")
    anchor_score = float(item.get("fused_score", 1.0))
    ext = art.get("extensions", {})
    entity_ids = ext.get("com.partner_advisory.nee.graph-entity-ids", []) or art.get("canonical_entities", [])

    expanded: list[dict[str, Any]] = []
    frontiers: list[tuple[str, dict[str, Any], str, float]] = []
    for target_entity_id in entity_ids:
        if not target_entity_id or len(expanded) >= max_facts:
            continue
        rows = conn.execute(
            """SELECT a.assertion_id FROM assertion a
               WHERE (a.subject_node_id = ? OR a.object_node_id = ?)
                 AND a.currentness_state IN ('current', 'unknown')
                 AND a.retrieval_eligible = 1
                 AND a.canonical_eligible = 1
                 AND a.superseded_by_assertion_id IS NULL
                 AND a.superseded_at IS NULL""",
            (target_entity_id, target_entity_id),
        ).fetchall()
        candidate_aids = [r[0] for r in rows if r[0] not in seen_ids]

        scored_candidates: list[tuple[int, dict[str, Any], list[str]]] = []
        for aid in candidate_aids:
            asrt, ev_ids = fetch_validated_assertion(
                conn, aid, lane=lane, req_rank=req_rank, req_audiences=req_auds
            )
            if asrt:
                rel_score = score_assertion_relevance(q_tokens, asrt)
                if q_tokens and rel_score <= 0:
                    continue
                scored_candidates.append((rel_score, asrt, ev_ids))
        scored_candidates.sort(key=lambda c: (-c[0], c[1]["assertion_id"]))

        for _sc, asrt, ev_ids in scored_candidates:
            aid = asrt["assertion_id"]
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            expanded.append(build_expanded_item(
                asrt, ev_ids, anchor_id=anchor_id, anchor_score=anchor_score,
                lane=lane, traversal_kind="profile_1hop", hops=1, base_item=item
            ))
            if hop_budget >= 2:
                for nid in [asrt.get("subject_node_id"), asrt.get("object_node_id")]:
                    if nid and nid != target_entity_id:
                        frontiers.append((nid, item, anchor_id, anchor_score))
            if len(expanded) >= max_facts:
                break
    return expanded, frontiers


def _traverse_hop2(
    conn: Any,
    frontiers: list[tuple[str, dict[str, Any], str, float]],
    *,
    seen_ids: set[str],
    q_tokens: list[str],
    lane: str,
    req_rank: int,
    req_auds: set[str],
    max_facts: int,
) -> list[dict[str, Any]]:
    hop2_by_node: dict[str, list[tuple[dict[str, Any], str, float]]] = {}
    for nid, base_it, a_id, a_sc in frontiers:
        hop2_by_node.setdefault(nid, []).append((base_it, a_id, a_sc))

    hop2_items: list[dict[str, Any]] = []
    for frontier_nid, anchor_tuples in hop2_by_node.items():
        base_item, anchor_id, anchor_score = anchor_tuples[0]
        rows = conn.execute(
            """SELECT a.assertion_id FROM assertion a
               WHERE (a.subject_node_id = ? OR a.object_node_id = ?)
                 AND a.currentness_state IN ('current', 'unknown')
                 AND a.retrieval_eligible = 1
                 AND a.canonical_eligible = 1
                 AND a.superseded_by_assertion_id IS NULL
                 AND a.superseded_at IS NULL""",
            (frontier_nid, frontier_nid),
        ).fetchall()
        candidate_aids = [r[0] for r in rows if r[0] not in seen_ids]

        scored: list[tuple[int, dict[str, Any], list[str]]] = []
        for aid in candidate_aids:
            asrt, ev_ids = fetch_validated_assertion(
                conn, aid, lane=lane, req_rank=req_rank, req_audiences=req_auds
            )
            if asrt:
                rel_score = score_assertion_relevance(q_tokens, asrt)
                if q_tokens and rel_score <= 0:
                    continue
                scored.append((rel_score, asrt, ev_ids))
        scored.sort(key=lambda c: (-c[0], c[1]["assertion_id"]))

        node_facts = 0
        for _sc, asrt, ev_ids in scored:
            aid = asrt["assertion_id"]
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            hop2_items.append(build_expanded_item(
                asrt, ev_ids, anchor_id=anchor_id, anchor_score=anchor_score,
                lane=lane, traversal_kind="multi_hop_2", hops=2, base_item=base_item
            ))
            node_facts += 1
            if node_facts >= max_facts:
                break
    return hop2_items


def expand_retrieval_anchors(
    results: list[dict[str, Any]],
    graph_path: Path,
    *,
    lane: str,
    classification: str,
    audiences: list[str],
    max_facts_per_anchor: int = 10,
    hop_budget: int = 1,
    query: str = "",
) -> list[dict[str, Any]]:
    """Expands retrieved passage and profile anchors to answer authority facts via graph traversal."""
    if not results:
        return []
    validate_classification(classification)
    req_rank = classification_rank(classification)
    req_audiences = set(audiences)
    if not req_audiences or any(not str(a).strip() for a in audiences):
        raise ValueError("at least one nonempty retrieval audience is required")

    q_tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    conn = connect_sqlite(graph_path, readonly=True)
    try:
        hop0_items: list[dict[str, Any]] = []
        hop1_items: list[dict[str, Any]] = []
        hop2_items: list[dict[str, Any]] = []
        seen_ids = seed_seen_assertions(results)
        hop2_frontiers: list[tuple[str, dict[str, Any], str, float]] = []

        for item in results:
            hop0_items.append(dict(item))
            art = item.get("record", {})
            rtype = art.get("record_type", "")
            rkind = art.get("retrieval_kind", "")

            if rtype == "source_chunk" or rkind == "evidence_span":
                exp, front = _expand_passage_anchor(
                    conn, item, seen_ids=seen_ids, lane=lane, req_rank=req_rank,
                    req_auds=req_audiences, max_facts=max_facts_per_anchor, hop_budget=hop_budget,
                )
                hop1_items.extend(exp)
                hop2_frontiers.extend(front)
            elif rtype in {"entity_facet", "edge"} or rkind in {"entity_facet", "edge"}:
                exp, front = _expand_profile_anchor(
                    conn, item, seen_ids=seen_ids, q_tokens=q_tokens, lane=lane, req_rank=req_rank,
                    req_auds=req_audiences, max_facts=max_facts_per_anchor, hop_budget=hop_budget,
                )
                hop1_items.extend(exp)
                hop2_frontiers.extend(front)

        if hop_budget >= 2 and hop2_frontiers:
            hop2_exp = _traverse_hop2(
                conn, hop2_frontiers, seen_ids=seen_ids, q_tokens=q_tokens, lane=lane,
                req_rank=req_rank, req_auds=req_audiences, max_facts=max_facts_per_anchor,
            )
            hop2_items.extend(hop2_exp)

        target_pred = None
        if query:
            from .predicate_typer import type_query_predicate
            pred_type = type_query_predicate(query)
            if pred_type and getattr(pred_type, "predicate", None):
                target_pred = pred_type.predicate

        def _sort_hop_by_pred(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not target_pred:
                return items
            matching = []
            other = []
            for it in items:
                rec = it.get("record", {})
                pred = rec.get("predicate") or rec.get("extensions", {}).get("com.partner_advisory.nee.predicate")
                if pred == target_pred:
                    matching.append(it)
                else:
                    other.append(it)
            return matching + other

        expanded_items = (
            _sort_hop_by_pred(hop0_items)
            + _sort_hop_by_pred(hop1_items)
            + _sort_hop_by_pred(hop2_items)
        )

        return expanded_items
    finally:
        conn.close()


class AnchorExpandingRetriever:
    """Retriever wrapper providing graph traversal anchor expansion."""

    def __init__(self, base_retriever: Any, graph_path: Path):
        self.base_retriever = base_retriever
        self.graph_path = Path(graph_path).resolve()
        self.backend = getattr(base_retriever, "backend", None)
        self.sidecar_path = getattr(base_retriever, "sidecar_path", None)
        self.validation = getattr(base_retriever, "validation", {})

    def retrieve_lane(
        self,
        query: str,
        lane: str,
        *,
        classification: str,
        audiences: list[str],
        top_k: int = 20,
        expand_anchors: bool = True,
        max_facts_per_anchor: int = 10,
        hop_budget: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute lane retrieval and optionally expand passage and profile anchors."""
        lane_result = self.base_retriever.retrieve_lane(
            query,
            lane,
            classification=classification,
            audiences=audiences,
            top_k=top_k,
            **kwargs,
        )
        if expand_anchors:
            expanded = expand_retrieval_anchors(
                lane_result["results"],
                self.graph_path,
                lane=lane,
                classification=classification,
                audiences=audiences,
                max_facts_per_anchor=max_facts_per_anchor,
                hop_budget=hop_budget,
                query=query,
            )
            lane_result["results"] = expanded[:top_k]
            lane_result["anchor_expansion_executed"] = True
        return lane_result

    def retrieve(
        self,
        query: str,
        *,
        lanes: list[str],
        classification: str,
        audiences: list[str],
        top_k: int = 20,
        expand_anchors: bool = True,
        max_facts_per_anchor: int = 10,
        hop_budget: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute multi-lane retrieval and optionally expand passage and profile anchors."""
        result = self.base_retriever.retrieve(
            query,
            lanes=lanes,
            classification=classification,
            audiences=audiences,
            top_k=top_k,
            **kwargs,
        )
        if expand_anchors and "lanes" in result:
            for l_name, l_res in result["lanes"].items():
                if "results" in l_res:
                    l_res["results"] = expand_retrieval_anchors(
                        l_res["results"],
                        self.graph_path,
                        lane=l_name,
                        classification=classification,
                        audiences=audiences,
                        max_facts_per_anchor=max_facts_per_anchor,
                        hop_budget=hop_budget,
                        query=query,
                    )[:top_k]
                    l_res["anchor_expansion_executed"] = True
        return result
