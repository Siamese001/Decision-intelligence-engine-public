"""Entity facet retrieval helper for graph navigation."""

from __future__ import annotations

from typing import Any

from .records import retrieval_metadata_from_artifact


def retrieve_entity_facets_impl(
    retriever: Any,
    query: str,
    lane: str,
    *,
    classification: str,
    audiences: list[str],
    top_k: int = 20,
) -> dict[str, Any]:
    """Retrieve only lane-isolated entity facets for graph navigation."""
    modes = {
        "approved": "entity_navigation",
        "candidate": "candidate_entity_navigation",
    }
    if lane not in modes:
        raise ValueError("entity facet retrieval lane must be approved or candidate")
    result = retriever.retrieve_lane(
        query,
        lane,
        classification=classification,
        audiences=audiences,
        top_k=top_k,
        retrieval_mode=modes[lane],
    )
    for item in result["results"]:
        record = item["record"]
        metadata = retrieval_metadata_from_artifact(record)
        if (
            item["lane"] != lane
            or record.get("record_type") != "entity_facet"
            or metadata["retrieval_kind"] != "entity_facet"
            or metadata["answer_use"] != "navigation_only"
        ):
            raise RuntimeError("entity navigation retrieval admitted a non-facet record")
    return result | {
        "entity_navigation": True,
        "entity_navigation_request": {
            "lane": lane,
            "classification": classification,
            "audiences": sorted(set(audiences)),
        },
        "entity_navigation_graph_binding": {
            "generation_id": retriever.validation["metadata"]["generation_id"],
            "graph_snapshot": retriever.validation["metadata"]["graph_snapshot"],
            "graph_sha256": retriever.validation["metadata"]["graph_sha256"],
        },
    }
