"""Mutation summary calculation and standardized reporting engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import connect_sqlite, sha256_file
from .constants import _SIDECAR_COMPONENTS, _SIDECAR_NET_KEYS
from .counts import _graph_counts, _sidecar_counts, _table_counts
from .table_formatter import format_markdown_mutation_table, format_mutation_summary_banner

__all__ = [
    "build_graph_mutation_summary",
    "format_markdown_mutation_table",
    "format_mutation_summary_banner",
]


def build_graph_mutation_summary(
    *,
    before_graph_path: Path | None = None,
    after_graph_path: Path | None = None,
    before_sidecar_path: Path | None = None,
    after_sidecar_path: Path | None = None,
    generation_before: str | None = None,
    generation_after: str | None = None,
    graph_sha256_before: str | None = None,
    graph_sha256_after: str | None = None,
    sidecar_sha256_before: str | None = None,
    sidecar_sha256_after: str | None = None,
    total_operations: int | None = None,
    actual_graph_delta: dict[str, Any] | None = None,
    sidecar_delta: dict[str, Any] | None = None,
    change_set: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, standardized summary of graph mutations or no-op runs."""
    is_noop = (
        status == "IDEMPOTENT_NOOP"
        or (
            total_operations == 0
            and generation_before is not None
            and generation_before == generation_after
        )
    )

    if graph_sha256_before is None and before_graph_path is not None and before_graph_path.is_file():
        graph_sha256_before = sha256_file(before_graph_path)
    if graph_sha256_after is None and after_graph_path is not None and after_graph_path.is_file():
        graph_sha256_after = sha256_file(after_graph_path)

    if sidecar_sha256_before is None and before_sidecar_path is not None and before_sidecar_path.is_file():
        sidecar_sha256_before = sha256_file(before_sidecar_path)
    if sidecar_sha256_after is None and after_sidecar_path is not None and after_sidecar_path.is_file():
        sidecar_sha256_after = sha256_file(after_sidecar_path)

    if is_noop:
        net_changes = {
            "sources": 0,
            "source_versions": 0,
            "extraction_runs": 0,
            "evidence_items": 0,
            "entities": 0,
            "canonical_assertions": 0,
            "candidate_assertions": 0,
            "superseded_assertions": 0,
            "assertion_evidence": 0,
            "search_documents": 0,
            "search_fts": 0,
            "audit_events": 0,
            "dense_vectors": 0,
            "sparse_weights": 0,
            "colbert_tokens": 0,
            "retrieval_records": 0,
        }
        sidecar_table = [
            {
                "component_key": comp_key,
                "table": tbl,
                "label": label,
                "baseline": 0,
                "actual_after": 0,
                "actual_delta": 0,
            }
            for comp_key, label, tbl in _SIDECAR_COMPONENTS
        ]
        summary = {
            "schema_version": "1.0.0",
            "summary_type": "graph_mutation_summary",
            "status": "IDEMPOTENT_NOOP",
            "generation_before": generation_before,
            "generation_after": generation_after or generation_before,
            "graph_sha256_before": graph_sha256_before,
            "graph_sha256_after": graph_sha256_after or graph_sha256_before,
            "sidecar_sha256_before": sidecar_sha256_before,
            "sidecar_sha256_after": sidecar_sha256_after or sidecar_sha256_before,
            "total_operations": 0,
            "net_changes": net_changes,
            "entities_created": [],
            "sources_added": [],
            "sidecar_table": sidecar_table,
            "summary_text": "No graph mutations: 0 operations applied (active graph and generation unchanged).",
            "hash_transition": {
                "state": "IDEMPOTENT_NOOP",
                "active_generation": generation_after or generation_before,
                "graph_sha256_before": graph_sha256_before,
                "graph_sha256_after": graph_sha256_after or graph_sha256_before,
                "sidecar_sha256_before": sidecar_sha256_before,
                "sidecar_sha256_after": sidecar_sha256_after or sidecar_sha256_before,
                "positive_operations_verified": False,
            },
        }
        summary["human_readable_banner"] = format_mutation_summary_banner(summary)
        summary["markdown_breakdown_table"] = format_markdown_mutation_table(summary)
        return summary

    # Mutating run:
    net_changes = {
        "sources": 0,
        "source_versions": 0,
        "extraction_runs": 0,
        "evidence_items": 0,
        "entities": 0,
        "canonical_assertions": 0,
        "candidate_assertions": 0,
        "superseded_assertions": 0,
        "assertion_evidence": 0,
        "search_documents": 0,
        "search_fts": 0,
        "audit_events": 0,
        "dense_vectors": 0,
        "sparse_weights": 0,
        "colbert_tokens": 0,
        "retrieval_records": 0,
    }
    sidecar_table: list[dict[str, Any]] = []

    if actual_graph_delta and isinstance(actual_graph_delta.get("table"), list):
        delta_by_key = {
            row.get("component_key"): int(row.get("actual_delta", 0))
            for row in actual_graph_delta["table"]
            if isinstance(row, dict)
        }
        before_raw = _table_counts(before_graph_path) if (before_graph_path and before_graph_path.is_file()) else {}
        after_raw = _table_counts(after_graph_path) if (after_graph_path and after_graph_path.is_file()) else {}
        has_raw_diff = bool(before_raw and after_raw)

        net_changes["sources"] = delta_by_key.get("source", 0)
        net_changes["source_versions"] = delta_by_key.get("source_version", 0)
        net_changes["extraction_runs"] = (
            after_raw.get("extraction_run", 0) - before_raw.get("extraction_run", 0)
            if has_raw_diff
            else delta_by_key.get("extraction_run", delta_by_key.get("source_version", 0))
        )
        net_changes["evidence_items"] = delta_by_key.get("evidence", 0)
        net_changes["entities"] = delta_by_key.get("internal_entity", 0) + delta_by_key.get(
            "external_concept", 0
        )
        net_changes["canonical_assertions"] = delta_by_key.get("canonical_assertion", 0)
        net_changes["candidate_assertions"] = delta_by_key.get("candidate_assertion", 0)
        net_changes["superseded_assertions"] = delta_by_key.get("superseded_assertion", 0)
        net_changes["assertion_evidence"] = (
            after_raw.get("assertion_evidence", 0) - before_raw.get("assertion_evidence", 0)
            if has_raw_diff
            else delta_by_key.get("assertion_evidence", delta_by_key.get("evidence", 0))
        )
        net_changes["search_documents"] = (
            after_raw.get("search_document", 0) - before_raw.get("search_document", 0)
            if has_raw_diff
            else delta_by_key.get("candidate_search_document", 0) + delta_by_key.get("canonical_assertion", 0)
        )
        net_changes["search_fts"] = (
            after_raw.get("search_fts", 0) - before_raw.get("search_fts", 0)
            if has_raw_diff
            else delta_by_key.get("search_fts", delta_by_key.get("candidate_search_document", 0))
        )
        net_changes["audit_events"] = delta_by_key.get("audit_event", 0)
    elif before_graph_path and after_graph_path and before_graph_path.is_file() and after_graph_path.is_file():
        before_counts = _graph_counts(before_graph_path)
        after_counts = _graph_counts(after_graph_path)
        before_raw = _table_counts(before_graph_path)
        after_raw = _table_counts(after_graph_path)

        net_changes["sources"] = after_counts.get("source", 0) - before_counts.get("source", 0)
        net_changes["source_versions"] = after_counts.get("source_version", 0) - before_counts.get(
            "source_version", 0
        )
        net_changes["extraction_runs"] = after_raw.get("extraction_run", 0) - before_raw.get("extraction_run", 0)
        net_changes["evidence_items"] = after_counts.get("evidence", 0) - before_counts.get(
            "evidence", 0
        )
        net_changes["entities"] = (
            after_counts.get("internal_entity", 0)
            + after_counts.get("external_concept", 0)
            - before_counts.get("internal_entity", 0)
            - before_counts.get("external_concept", 0)
        )
        net_changes["canonical_assertions"] = after_counts.get(
            "canonical_assertion", 0
        ) - before_counts.get("canonical_assertion", 0)
        net_changes["candidate_assertions"] = after_counts.get(
            "candidate_assertion", 0
        ) - before_counts.get("candidate_assertion", 0)
        net_changes["superseded_assertions"] = after_counts.get(
            "superseded_assertion", 0
        ) - before_counts.get("superseded_assertion", 0)
        net_changes["assertion_evidence"] = after_raw.get("assertion_evidence", 0) - before_raw.get("assertion_evidence", 0)
        net_changes["search_documents"] = after_counts.get(
            "candidate_search_document", 0
        ) - before_counts.get("candidate_search_document", 0)
        net_changes["search_fts"] = after_raw.get("search_fts", 0) - before_raw.get("search_fts", 0)
        net_changes["audit_events"] = after_raw.get("audit_event", 0) - before_raw.get("audit_event", 0)

    if before_sidecar_path and after_sidecar_path and before_sidecar_path.is_file() and after_sidecar_path.is_file():
        before_sc = _sidecar_counts(before_sidecar_path)
        after_sc = _sidecar_counts(after_sidecar_path)
        for comp_key, label, tbl in _SIDECAR_COMPONENTS:
            base = before_sc.get(comp_key, 0)
            aft = after_sc.get(comp_key, 0)
            delta = aft - base
            net_key = _SIDECAR_NET_KEYS.get(comp_key, comp_key)
            net_changes[net_key] = delta
            sidecar_table.append(
                {
                    "component_key": comp_key,
                    "table": tbl,
                    "label": label,
                    "baseline": base,
                    "actual_after": aft,
                    "actual_delta": delta,
                }
            )
    elif sidecar_delta and isinstance(sidecar_delta.get("table"), list):
        for row in sidecar_delta["table"]:
            comp_key = row.get("component_key")
            delta = int(row.get("actual_delta", 0))
            base = int(row.get("baseline", 0))
            aft = int(row.get("actual_after", base + delta))
            label = row.get("label") or dict((k, l) for k, l, _ in _SIDECAR_COMPONENTS).get(comp_key, comp_key)
            tbl = row.get("table") or dict((k, t) for k, _, t in _SIDECAR_COMPONENTS).get(comp_key, comp_key)
            net_key = _SIDECAR_NET_KEYS.get(comp_key, comp_key)
            net_changes[net_key] = delta
            sidecar_table.append(
                {
                    "component_key": comp_key,
                    "table": tbl,
                    "label": label,
                    "baseline": base,
                    "actual_after": aft,
                    "actual_delta": delta,
                }
            )

    entities_created: list[dict[str, str]] = []
    sources_added: list[dict[str, str]] = []

    # Extract specific entity and source names
    if change_set and isinstance(change_set.get("operations"), list):
        extensions = change_set.get("extensions", {})
        payloads = extensions.get("com.partner_advisory.nee.operation-payloads", {})
        source_binding = extensions.get("com.partner_advisory.nee.source", {})
        if source_binding.get("source_id"):
            sources_added.append(
                {
                    "source_id": str(source_binding["source_id"]),
                    "name": str(source_binding.get("name") or source_binding["source_id"]),
                }
            )
        for op in change_set["operations"]:
            if op.get("operation") == "create_entity":
                payload = payloads.get(op.get("operation_id"), {})
                candidate = payload.get("candidate", {})
                entities_created.append(
                    {
                        "id": str(op.get("target_id")),
                        "name": str(candidate.get("canonical_name") or op.get("target_id")),
                        "type": str(candidate.get("entity_type") or "entity"),
                        "kind": str(payload.get("entity_kind") or "internal"),
                    }
                )
    elif before_graph_path and after_graph_path and before_graph_path.is_file() and after_graph_path.is_file():
        conn_b = connect_sqlite(Path(before_graph_path), readonly=True)
        conn_a = connect_sqlite(Path(after_graph_path), readonly=True)
        try:
            b_nodes = {row[0] for row in conn_b.execute("SELECT node_id FROM node")}
            a_nodes = conn_a.execute("SELECT node_id, canonical_name, node_type FROM node").fetchall()
            for row in a_nodes:
                if row[0] not in b_nodes:
                    entities_created.append(
                        {
                            "id": str(row[0]),
                            "name": str(row[1] or row[0]),
                            "type": str(row[2] or "entity"),
                        }
                    )
            b_sources = {row[0] for row in conn_b.execute("SELECT source_id FROM source")}
            a_sources = conn_a.execute("SELECT source_id, source_name FROM source").fetchall()
            for row in a_sources:
                if row[0] not in b_sources:
                    sources_added.append(
                        {
                            "source_id": str(row[0]),
                            "name": str(row[1] or row[0]),
                        }
                    )
        finally:
            conn_b.close()
            conn_a.close()

    itemized_assertions: list[dict[str, Any]] = []
    if before_graph_path and after_graph_path and before_graph_path.is_file() and after_graph_path.is_file():
        from .itemized_assertion import build_itemized_assertion_delta
        assertion_delta = build_itemized_assertion_delta(before_graph_path, after_graph_path)
        itemized_assertions = [
            item for item in assertion_delta.get("items", [])
            if item.get("action") in ("CREATE", "MODIFY", "SUPERSEDE")
        ]

    if total_operations is None:
        total_operations = sum(max(0, v) for v in net_changes.values())

    summary_text = (
        f"Graph mutated: +{net_changes['sources']} sources, +{net_changes['entities']} entities, "
        f"+{net_changes['canonical_assertions']} canonical facts, +{net_changes['candidate_assertions']} candidate facts, "
        f"+{net_changes['evidence_items']} evidence items across {total_operations} operations."
    )

    summary = {
        "schema_version": "1.0.0",
        "summary_type": "graph_mutation_summary",
        "status": status or "GRAPH_MUTATED",
        "generation_before": generation_before,
        "generation_after": generation_after,
        "graph_sha256_before": graph_sha256_before,
        "graph_sha256_after": graph_sha256_after,
        "sidecar_sha256_before": sidecar_sha256_before,
        "sidecar_sha256_after": sidecar_sha256_after,
        "total_operations": total_operations,
        "net_changes": net_changes,
        "entities_created": entities_created,
        "sources_added": sources_added,
        "itemized_assertions": itemized_assertions,
        "sidecar_table": sidecar_table,
        "summary_text": summary_text,
        "actual_graph_delta": actual_graph_delta,
        "hash_transition": {
            "state": "ACTIVATED",
            "active_generation": generation_after or generation_before,
            "graph_sha256_before": graph_sha256_before,
            "graph_sha256_after": graph_sha256_after,
            "sidecar_sha256_before": sidecar_sha256_before,
            "sidecar_sha256_after": sidecar_sha256_after,
            "positive_operations_verified": True,
        },
    }
    summary["human_readable_banner"] = format_mutation_summary_banner(summary)
    summary["markdown_breakdown_table"] = format_markdown_mutation_table(summary, assertions_inserted=itemized_assertions or None)
    return summary
