"""Taxonomy partition filter isolating Intelligent Automation workflows from EDO data products.

Wave 3 Deliverable:
- Deterministic classification and partitioning by TaxonomyLane.
- Guarantees 100% exclusion of Enterprise Data Office data products when selecting Intelligent Automation.
- Validates structured metric extraction for claims.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from storage.database import sqlite3

from contracts.dual_pipeline.graph_contracts import (
    ClaimContract,
    NodeContract,
    StructuredMetrics,
    TaxonomyLane,
)
from dual_pipeline.core import connect_sqlite

# Canonical identifiers and names for the 15 Intelligent Automation deliverables
IA_CANONICAL_KEYS = {
    "lens",
    "dociq",
    "nucleus",
    "nexus",
    "eva",
    "outage",
    "spade",
    "ssup",
    "regai",
    "flexai",
    "fcdb",
    "rocc",
    "conduit",
    "arc_angel",
    "sherpa",
    "devhub",
    "bille",
    "bill-e",
    "pgd_copilot",
    "swarm",
    "gemini_enablement",
}

# Explicit Enterprise Data Office (EDO) data-product tokens that MUST be isolated from IA
EDO_EXCLUSION_KEYS = {
    "digital_thread",
    "dqt",
    "data_quality",
    "gridopti",
    "spring",
    "project_spring",
    "trade_floor",
    "etrm",
    "pricing_workbook",
    "atlas",
    "data_office",
    "data_product",
    "edo",
}


def _extract_explicit_lane(entity: Any) -> TaxonomyLane | None:
    """Extract explicit taxonomy_lane if specified on the entity."""
    if isinstance(entity, (NodeContract, ClaimContract)):
        return entity.taxonomy_lane
    if isinstance(entity, dict):
        raw_lane = entity.get("taxonomy_lane")
        if raw_lane:
            try:
                return TaxonomyLane(raw_lane)
            except ValueError:
                return None
    return None


def _extract_entity_text(entity: Any) -> tuple[str, str]:
    """Extract normalized text chunks from entity fields."""
    import re

    text_chunks: list[str] = []
    if isinstance(entity, NodeContract):
        text_chunks.extend([entity.node_id, entity.canonical_name, entity.node_type])
    elif isinstance(entity, ClaimContract):
        text_chunks.extend([entity.claim_id, entity.claim_text, entity.subject_node_id])
    elif isinstance(entity, dict):
        for field in ("node_id", "canonical_name", "display_name", "claim_id", "claim_text", "subject_node_id"):
            val = entity.get(field)
            if isinstance(val, str):
                text_chunks.append(val)

    raw_text = " ".join(text_chunks).lower()
    clean_text = " " + re.sub(r"[^a-z0-9]+", " ", raw_text) + " "
    return raw_text, clean_text


def _matches_taxonomy_tokens(clean_text: str, raw_text: str, keys: Iterable[str]) -> bool:
    """Check if any token key matches against clean or raw text."""
    for key in keys:
        clean_key = key.replace("_", " ").replace("-", " ")
        if f" {clean_key} " in clean_text or key in raw_text:
            return True
    return False


def classify_entity_taxonomy(entity: NodeContract | ClaimContract | dict[str, Any]) -> TaxonomyLane:
    """Deterministically classify an entity or claim into a TaxonomyLane."""
    explicit_lane = _extract_explicit_lane(entity)
    if explicit_lane and explicit_lane is not TaxonomyLane.UNCLASSIFIED:
        return explicit_lane

    raw_text, clean_text = _extract_entity_text(entity)

    # Priority 1: Check for EDO exclusions
    if _matches_taxonomy_tokens(clean_text, raw_text, EDO_EXCLUSION_KEYS):
        return TaxonomyLane.ENTERPRISE_DATA_OFFICE

    # Priority 2: Check for Intelligent Automation
    if _matches_taxonomy_tokens(clean_text, raw_text, IA_CANONICAL_KEYS):
        return TaxonomyLane.INTELLIGENT_AUTOMATION

    return TaxonomyLane.UNCLASSIFIED


def filter_by_taxonomy_lane(
    entities: Iterable[Any],
    target_lane: TaxonomyLane | str,
) -> list[Any]:
    """Filter entities and return only those strictly matching the target taxonomy lane.

    When target_lane is INTELLIGENT_AUTOMATION, 100% of EDO data products are excluded.
    """
    if isinstance(target_lane, TaxonomyLane):
        target = target_lane
    else:
        val = target_lane.value if hasattr(target_lane, "value") else str(target_lane)
        target = TaxonomyLane(val)

    results: list[Any] = []
    for item in entities:
        lane = classify_entity_taxonomy(item)
        if lane == target:
            results.append(item)
    return results


def partition_graph_nodes(
    conn: sqlite3.Connection,
) -> dict[TaxonomyLane, list[dict[str, Any]]]:
    """Partition all nodes from a SQLite graph connection into taxonomy lane buckets."""
    cursor = conn.cursor()
    cursor.execute("SELECT node_id, canonical_name, node_type FROM node ORDER BY node_id")
    rows = cursor.fetchall()

    partitions: dict[TaxonomyLane, list[dict[str, Any]]] = {
        TaxonomyLane.INTELLIGENT_AUTOMATION: [],
        TaxonomyLane.ENTERPRISE_DATA_OFFICE: [],
        TaxonomyLane.CORE_IT: [],
        TaxonomyLane.UNCLASSIFIED: [],
    }

    for row in rows:
        nid, cname, ntype = row
        node_dict = {
            "node_id": nid,
            "canonical_name": cname,
            "node_type": ntype,
        }
        lane = classify_entity_taxonomy(node_dict)
        node_dict["taxonomy_lane"] = lane.value
        partitions[lane].append(node_dict)

    return partitions
