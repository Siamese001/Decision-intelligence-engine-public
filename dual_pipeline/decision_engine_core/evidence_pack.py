"""
dual_pipeline/decision_engine_core/evidence_pack.py

Promotion of query and retrieval artifacts into typed decision evidence packs.
"""

from __future__ import annotations

from typing import Any, Mapping

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import digest, stable_id

from .constants import (
    CONSTRAINT_TERMS,
    RISK_TERMS,
    extract_tokens,
    text_field,
)
from .quantitative_math import (
    build_dialectical_constraint_tensions,
    enrich_constraints_with_quantitative_math,
)


def record_row(item: Mapping[str, Any], *, lane: str) -> dict[str, Any]:
    record = item.get("record")
    if not isinstance(record, Mapping):
        raise ValueError("evidence packet record is missing")
    record_id = text_field(record.get("record_id"), field="record_id")
    title = text_field(record.get("title"), field="record title")
    evidence_ids = [
        text_field(value, field="evidence_id") for value in record.get("evidence_ids", [])
    ]
    text = text_field(record.get("retrieval_text"), field="retrieval_text")
    metadata = item.get("retrieval_metadata")
    answer_use = (
        metadata.get("answer_use") if isinstance(metadata, Mapping) else None
    ) or record.get("answer_use")
    return {
        "record_id": record_id,
        "lane": lane,
        "title": title,
        "text": text,
        "record_type": str(record.get("record_type", "unknown")),
        "answer_use": str(answer_use or "unknown"),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "currentness_state": str(record.get("currentness_state", "unknown")),
    }


def _is_constraint_record(tokens: set[str], record: Mapping[str, Any]) -> bool:
    return bool(tokens & CONSTRAINT_TERMS) or record["answer_use"] == "decision_constraint"


def _partition_pack_records(
    records: list[dict[str, Any]],
    question: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    fact_uses = {"factual_answer", "strategic_context", "decision_constraint", "unknown"}
    for record in records:
        tokens = extract_tokens(f"{record['title']} {record['text']}")
        if tokens & RISK_TERMS:
            risks.append(record)
        if _is_constraint_record(tokens, record):
            constraints.append(record)
        if record["lane"] == "approved" and record["answer_use"] in fact_uses:
            facts.append(record)
    enriched_constraints = enrich_constraints_with_quantitative_math(constraints, records, question=question)
    return facts, risks, enriched_constraints


def build_decision_evidence_pack(
    *,
    query_result: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    retrieval_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote existing query artifacts into a typed decision evidence pack."""
    records: list[dict[str, Any]] = []
    for lane in ("approved", "candidate", "external_public"):
        values = evidence_packet.get(lane, [])
        if not isinstance(values, list):
            raise ValueError(f"evidence packet {lane} lane must be a list")
        records.extend(record_row(item, lane=lane) for item in values)

    facts, risks, constraints = _partition_pack_records(records, str(query_result.get("question") or ""))
    relationships = evidence_packet.get("graph_paths", [])
    conflicts = evidence_packet.get("counterevidence", [])
    unknowns = evidence_packet.get("gaps", [])
    if not isinstance(relationships, list):
        raise ValueError("graph_paths must be a list")
    if not isinstance(conflicts, list):
        raise ValueError("counterevidence must be a list")
    if not isinstance(unknowns, list):
        raise ValueError("gaps must be a list")
    synthesized_conflicts = build_dialectical_constraint_tensions(records, constraints, conflicts)
    report = retrieval_report or {}
    pack_fields: dict[str, Any] = {
        "generation": {
            "generation_id": query_result.get("generation_id"),
            "graph_snapshot": query_result.get("graph_snapshot"),
            "graph_sha256": query_result.get("graph_sha256_after"),
            "embedding_backend": query_result.get("embedding_backend"),
        },
        "retrieval_execution": {
            "exact": True,
            "bm25": bool(query_result.get("bm25_executed")),
            "dense": bool(query_result.get("dense_executed")),
            "sparse": bool(query_result.get("sparse_executed")),
            "colbert": bool(query_result.get("colbert_executed")),
            "graph_traversal": bool(query_result.get("graph_traversal_executed")),
        },
        "facts": facts,
        "relationships": [dict(item) for item in relationships if isinstance(item, Mapping)],
        "risks": risks,
        "constraints": constraints,
        "conflicts": synthesized_conflicts,
        "unknowns": [str(item) for item in unknowns],
        "records": records,
        "coverage": {
            "terminal_disposition": report.get("terminal_coverage_disposition"),
            "counts": dict(report.get("coverage_counts", {})),
        },
        "source_evidence_ids": sorted({value for record in records for value in record["evidence_ids"]}),
        "pack_digest": "",
    }
    pack_fields["pack_digest"] = digest({key: value for key, value in pack_fields.items() if key != "pack_digest"})
    return create_artifact(
        "decision-evidence-pack",
        stable_id("decision-evidence-pack", query_result.get("run_id"), pack_fields["pack_digest"], length=26),
        **pack_fields,
    )
