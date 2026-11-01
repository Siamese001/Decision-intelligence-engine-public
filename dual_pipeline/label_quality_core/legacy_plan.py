"""Legacy graph label migration plan builder."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import (
    connect_sqlite,
    digest,
)
from .audit import audit_graph_labels
from .constants import (
    CANONICAL_EVIDENCE_DIRECTNESS,
    CANONICAL_SOURCE_POSTURES,
    LABEL_POLICY_ID,
    LABEL_POLICY_VERSION,
)


def _review_item(
    *,
    record_type: str,
    record_id: str,
    field: str,
    legacy_value: str,
    proposed_value: str | None,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "record_type": record_type,
        "record_id": record_id,
        "field": field,
        "legacy_value": legacy_value,
        "proposed_value": proposed_value,
        "decision": decision,
        "reason": reason,
    }


def build_legacy_label_migration_plan(graph_path: Path) -> dict[str, Any]:
    """Create a deterministic plan; never apply it or infer ambiguous mappings."""

    graph_path = Path(graph_path).resolve()
    report = audit_graph_labels(graph_path)
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        items: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT source_id,source_posture FROM source ORDER BY source_id"
        ):
            posture = str(row["source_posture"])
            items.append(
                _review_item(
                    record_type="source",
                    record_id=str(row["source_id"]),
                    field="source_posture",
                    legacy_value=posture,
                    proposed_value=posture if posture in CANONICAL_SOURCE_POSTURES else None,
                    decision=(
                        "deterministic_map"
                        if posture in CANONICAL_SOURCE_POSTURES
                        else "hitl_required"
                    ),
                    reason=(
                        "Value already matches the closed-world source-posture contract."
                        if posture in CANONICAL_SOURCE_POSTURES
                        else "Narrative legacy posture cannot be reduced without source review."
                    ),
                )
            )
        for row in connection.execute(
            """SELECT evidence_id,evidence_directness FROM evidence_item
               ORDER BY evidence_id"""
        ):
            directness = str(row["evidence_directness"])
            items.append(
                _review_item(
                    record_type="evidence",
                    record_id=str(row["evidence_id"]),
                    field="evidence_directness",
                    legacy_value=directness,
                    proposed_value=(
                        directness if directness in CANONICAL_EVIDENCE_DIRECTNESS else None
                    ),
                    decision=(
                        "deterministic_map"
                        if directness in CANONICAL_EVIDENCE_DIRECTNESS
                        else "hitl_required"
                    ),
                    reason=(
                        "Value already matches the closed-world evidence-directness contract."
                        if directness in CANONICAL_EVIDENCE_DIRECTNESS
                        else "Narrative evidence basis must be preserved and reviewed."
                    ),
                )
            )
        for row in connection.execute(
            """SELECT assertion_id,confidence_score,extraction_method
               FROM assertion
               WHERE confidence_label='directional' AND confidence_score>0.60
               ORDER BY assertion_id"""
        ):
            deterministic = row["extraction_method"] == "dual_pipeline_structured_adapter"
            items.append(
                _review_item(
                    record_type="assertion",
                    record_id=str(row["assertion_id"]),
                    field="confidence",
                    legacy_value=f"directional:{row['confidence_score']}",
                    proposed_value=(
                        "separate_extraction_confidence"
                        if deterministic
                        else None
                    ),
                    decision="deterministic_map" if deterministic else "hitl_required",
                    reason=(
                        "Structured-adapter score can be preserved as extraction confidence "
                        "while directional assertion confidence is reset."
                        if deterministic
                        else "Legacy confidence basis is not provable from the label alone."
                    ),
                )
            )
        for row in connection.execute(
            """SELECT canonical_alias_id,confidence_label FROM canonical_alias a
               WHERE a.active=1 AND NOT EXISTS (
                 SELECT 1 FROM controlled_value_registry c
                 WHERE c.vocabulary='confidence_label'
                   AND c.value=a.confidence_label AND c.active=1
               )
               ORDER BY canonical_alias_id"""
        ):
            items.append(
                _review_item(
                    record_type="canonical_alias",
                    record_id=str(row["canonical_alias_id"]),
                    field="confidence_label",
                    legacy_value=str(row["confidence_label"]),
                    proposed_value=None,
                    decision="hitl_required",
                    reason="Active alias uses an unregistered value; identity review is required.",
                )
            )
        summary = {
            "deterministic_map_count": sum(
                item["decision"] == "deterministic_map" for item in items
            ),
            "hitl_required_count": sum(
                item["decision"] == "hitl_required" for item in items
            ),
            "total_item_count": len(items),
        }
        plan = {
            "schema_version": "1.0.0",
            "plan_type": "graph_label_migration",
            "policy": {
                "policy_id": LABEL_POLICY_ID,
                "policy_version": LABEL_POLICY_VERSION,
            },
            "base_graph_sha256": report["graph"]["sha256"],
            "quality_report_digest": report["report_digest"],
            "status": "PROPOSED_REVIEW_ONLY",
            "summary": summary,
            "items": items,
            "mutation_authorized": False,
            "provider_call_count": 0,
        }
        plan["plan_digest"] = digest(plan)
        return plan
    finally:
        connection.close()
