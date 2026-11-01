"""Read-only W6 human-adjudication packets for graph-label remediation.

This module prepares source-bound review work.  It never chooses a label,
resolves an identity, assigns extraction lineage, changes a graph, or activates
a graph/index pair.  Human decisions remain separate artifacts under the
existing HITL decision contract and must be applied only in an approved W7
release.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import (
    connect_sqlite,
    digest,
    read_json,
    seal_artifact,
    sha256_file,
    stable_id,
    trace_id,
)
from .exceptions import DualPipelineValueError
from .label_quality import (
    CANONICAL_EVIDENCE_DIRECTNESS,
    CANONICAL_SOURCE_POSTURES,
    LABEL_POLICY_ID,
    LABEL_POLICY_VERSION,
    build_deterministic_label_migration_candidate_plan,
    build_legacy_label_migration_plan,
)

GRAPH_LABEL_ADJUDICATION_CONTRACT_ID = (
    "urn:nee:dual-pipeline:graph-label-adjudication-packet:1.0.0"
)
HITL_DECISION_CONTRACT_ID = "urn:nee:dual-pipeline:hitl-decision:1.0.0"
PACKET_STATUS = "PENDING_HUMAN_REVIEW"
_PENDING_ENTITY_STATUS = "pending_human_review"
_RELEVANCE_DIMENSIONS = (
    "positive_relevance",
    "hard_negative",
    "lane_isolation",
    "acl_boundary",
    "stale_or_superseded",
)
_REQUIRED_DECISION_FIELDS = (
    "decision",
    "reviewer",
    "decided_at",
    "rationale",
    "evidence_reviewed",
    "alternatives",
    "before_state_digest",
    "proposed_after_state_digest",
    "affected_candidate_ids",
)


class LabelAdjudicationError(DualPipelineValueError):
    """Raised when a W6 packet cannot be bound safely."""


def _resolved_pair_path(runtime_root: Path, graph_path: str) -> Path:
    raw = Path(graph_path)
    return raw.resolve() if raw.is_absolute() else (runtime_root / raw).resolve()


def _pair_binding(*, graph_path: Path, runtime_root: Path) -> dict[str, Any]:
    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        raise LabelAdjudicationError(f"active pair is unavailable: {active_pair_path}")
    pair = read_json(active_pair_path)
    if not isinstance(pair, dict):
        raise LabelAdjudicationError("active pair must be a JSON object")
    required = (
        "generation_id",
        "graph_path",
        "graph_sha256",
        "graph_snapshot",
        "sidecar_sha256",
        "embedding_backend",
        "model_id",
        "model_revision",
        "model_manifest_digest",
        "production_qualified",
    )
    missing = sorted(field for field in required if field not in pair)
    if missing:
        raise LabelAdjudicationError(f"active pair lacks required bindings: {missing}")
    actual_graph_hash = sha256_file(graph_path)
    if pair["graph_sha256"] != actual_graph_hash:
        raise LabelAdjudicationError("active pair graph hash does not match the review graph")
    if _resolved_pair_path(runtime_root, str(pair["graph_path"])) != graph_path:
        raise LabelAdjudicationError("review graph is not the active pair graph for this runtime")
    return {
        "active_pair_sha256": sha256_file(active_pair_path),
        "generation_id": str(pair["generation_id"]),
        "graph_sha256": actual_graph_hash,
        "graph_snapshot": str(pair["graph_snapshot"]),
        "sidecar_sha256": str(pair["sidecar_sha256"]),
        "embedding_backend": str(pair["embedding_backend"]),
        "model_id": str(pair["model_id"]),
        "model_revision": str(pair["model_revision"]),
        "model_manifest_digest": str(pair["model_manifest_digest"]),
        "production_qualified": bool(pair["production_qualified"]),
    }


def _source_context(connection: Any, source_ids: list[str]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    placeholders = ",".join("?" for _ in source_ids)
    rows = connection.execute(
        f"""SELECT source_id,source_name,source_type,source_posture
             FROM source WHERE source_id IN ({placeholders}) ORDER BY source_id""",
        tuple(source_ids),
    ).fetchall()
    return [
        {
            "source_id": str(row["source_id"]),
            "source_name": (
                str(row["source_name"]) if row["source_name"] is not None else None
            ),
            "source_type": str(row["source_type"]),
            "source_posture": str(row["source_posture"]),
        }
        for row in rows
    ]


def _queue(
    *,
    graph_sha256: str,
    category: str,
    subtype: str,
    record_type: str,
    field: str,
    legacy_value: str,
    record_ids: list[str],
    source_ids: list[str],
    source_context: list[dict[str, Any]],
    candidate_values: list[str],
    decision_options: list[str],
    tier: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    normalized_record_ids = sorted(set(record_ids))
    normalized_source_ids = sorted(set(source_ids))
    baseline = {
        "category": category,
        "subtype": subtype,
        "record_type": record_type,
        "field": field,
        "legacy_value": legacy_value,
        "record_ids": normalized_record_ids,
        "source_ids": normalized_source_ids,
        "context": context,
    }
    queue_id = stable_id(
        "graph-label-adjudication-queue",
        graph_sha256,
        baseline,
        length=28,
    )
    return {
        "queue_id": queue_id,
        "category": category,
        "subtype": subtype,
        "record_type": record_type,
        "field": field,
        "legacy_value": legacy_value,
        "record_count": len(normalized_record_ids),
        "record_ids": normalized_record_ids,
        "source_ids": normalized_source_ids,
        "source_context": source_context,
        "candidate_values": candidate_values,
        "decision_options": decision_options,
        "review_tier": tier,
        "dual_review_required": tier == 2,
        "approval_owner_required": True,
        "required_decision_fields": list(_REQUIRED_DECISION_FIELDS),
        "baseline_state_digest": digest(baseline),
        "status": PACKET_STATUS,
        "context": context,
    }


def _label_mapping_queues(
    connection: Any,
    *,
    graph_sha256: str,
    review_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence_sources = {
        str(row["evidence_id"]): str(row["source_id"])
        for row in connection.execute("SELECT evidence_id,source_id FROM evidence_item")
    }
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in review_plan["items"]:
        if item["decision"] != "hitl_required":
            continue
        record_type = str(item["record_type"])
        if record_type == "canonical_alias":
            continue
        record_id = str(item["record_id"])
        source_id = (
            evidence_sources[record_id]
            if record_type == "evidence"
            else record_id
        )
        groups[
            (
                record_type,
                str(item["field"]),
                str(item["legacy_value"]),
                source_id,
            )
        ].append(item)

    queues: list[dict[str, Any]] = []
    for (record_type, field, legacy_value, source_id), items in sorted(groups.items()):
        if record_type == "source":
            candidate_values = sorted(CANONICAL_SOURCE_POSTURES)
            subtype = "source_posture_mapping"
        elif record_type == "evidence":
            candidate_values = sorted(CANONICAL_EVIDENCE_DIRECTNESS)
            subtype = "evidence_directness_mapping"
        else:
            raise LabelAdjudicationError(
                f"unsupported label mapping record type: {record_type}"
            )
        record_ids = [str(item["record_id"]) for item in items]
        queues.append(
            _queue(
                graph_sha256=graph_sha256,
                category="label_mapping",
                subtype=subtype,
                record_type=record_type,
                field=field,
                legacy_value=legacy_value,
                record_ids=record_ids,
                source_ids=[source_id],
                source_context=_source_context(connection, [source_id]),
                candidate_values=candidate_values,
                decision_options=["CONFIRM", "HOLD", "QUARANTINE"],
                tier=1,
                context={
                    "review_reason": str(items[0]["reason"]),
                    "grouping": "same source, field, and legacy value",
                },
            )
        )
    return queues


def _lineage_queues(connection: Any, *, graph_sha256: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT assertion_id,primary_source_id,origin_type,extraction_method
             FROM assertion
            WHERE origin_type<>'legacy_migration' AND extraction_run_id IS NULL
            ORDER BY primary_source_id,origin_type,extraction_method,assertion_id"""
    ).fetchall()
    groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for row in rows:
        groups[
            (
                str(row["primary_source_id"] or "NO_PRIMARY_SOURCE"),
                str(row["origin_type"]),
                str(row["extraction_method"]),
            )
        ].append(row)
    queues: list[dict[str, Any]] = []
    for (source_id, origin_type, extraction_method), group_rows in sorted(groups.items()):
        source_ids = [] if source_id == "NO_PRIMARY_SOURCE" else [source_id]
        queues.append(
            _queue(
                graph_sha256=graph_sha256,
                category="lineage",
                subtype="missing_extraction_run",
                record_type="assertion",
                field="extraction_run_id",
                legacy_value="MISSING",
                record_ids=[str(row["assertion_id"]) for row in group_rows],
                source_ids=source_ids,
                source_context=_source_context(connection, source_ids),
                candidate_values=[
                    "BIND_VERIFIED_EXTRACTION_RUN",
                    "RECLASSIFY_LEGACY_MIGRATION",
                    "HOLD",
                ],
                decision_options=["CONFIRM", "HOLD", "QUARANTINE"],
                tier=2 if origin_type == "model_inference" else 1,
                context={
                    "origin_type": origin_type,
                    "extraction_method": extraction_method,
                    "grouping": "same primary source, origin type, and extraction method",
                },
            )
        )
    return queues


def _identity_queues(connection: Any, *, graph_sha256: str) -> list[dict[str, Any]]:
    queues: list[dict[str, Any]] = []
    alias_rows = connection.execute(
        """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,source_id,
                  confidence_label,confidence_score,ambiguity_state,review_state
             FROM canonical_alias a
            WHERE a.active=1 AND NOT EXISTS (
                SELECT 1 FROM controlled_value_registry c
                 WHERE c.vocabulary='confidence_label'
                   AND c.value=a.confidence_label AND c.active=1
            )
            ORDER BY canonical_alias_id"""
    ).fetchall()
    for row in alias_rows:
        source_ids = [str(row["source_id"])] if row["source_id"] else []
        queues.append(
            _queue(
                graph_sha256=graph_sha256,
                category="identity_resolution",
                subtype="unregistered_active_alias_confidence",
                record_type="canonical_alias",
                field="confidence_label",
                legacy_value=str(row["confidence_label"]),
                record_ids=[str(row["canonical_alias_id"])],
                source_ids=source_ids,
                source_context=_source_context(connection, source_ids),
                candidate_values=[
                    "REGISTER_CONTROLLED_CONFIDENCE",
                    "RENAME_ALIAS",
                    "RETIRE_ALIAS",
                    "HOLD",
                ],
                decision_options=["CONFIRM", "REJECT", "ADD_ALIAS", "HOLD", "QUARANTINE"],
                tier=2,
                context={
                    "normalized_alias": str(row["normalized_alias"]),
                    "display_alias": str(row["display_alias"]),
                    "entity_id": str(row["entity_id"]),
                    "confidence_score": float(row["confidence_score"]),
                    "ambiguity_state": str(row["ambiguity_state"]),
                    "review_state": str(row["review_state"]),
                },
            )
        )
    candidate_rows = connection.execute(
        """SELECT candidate_id,candidate_type,normalized_key,node_type_scope,
                  auto_merge_allowed,rationale,created_at
             FROM entity_resolution_candidate
            WHERE status=? ORDER BY candidate_id""",
        (_PENDING_ENTITY_STATUS,),
    ).fetchall()
    for row in candidate_rows:
        queues.append(
            _queue(
                graph_sha256=graph_sha256,
                category="identity_resolution",
                subtype=str(row["candidate_type"]),
                record_type="entity_resolution_candidate",
                field="resolution",
                legacy_value=str(row["normalized_key"]),
                record_ids=[str(row["candidate_id"])],
                source_ids=[],
                source_context=[],
                candidate_values=[
                    "CONFIRM",
                    "REJECT",
                    "CREATE_NEW",
                    "CREATE_EXTERNAL_CONCEPT",
                    "ADD_ALIAS",
                    "HOLD",
                ],
                decision_options=[
                    "CONFIRM",
                    "REJECT",
                    "CREATE_NEW",
                    "CREATE_EXTERNAL_CONCEPT",
                    "ADD_ALIAS",
                    "HOLD",
                    "QUARANTINE",
                ],
                tier=2,
                context={
                    "node_type_scope": row["node_type_scope"],
                    "auto_merge_allowed": bool(row["auto_merge_allowed"]),
                    "rationale": str(row["rationale"]),
                    "created_at": str(row["created_at"]),
                },
            )
        )
    return queues


def build_graph_label_adjudication_packet(
    graph_path: Path,
    *,
    runtime_root: Path,
) -> dict[str, Any]:
    """Build one deterministic, pair-bound W6 packet without changing state."""

    graph_path = Path(graph_path).resolve()
    runtime_root = Path(runtime_root).resolve()
    pair_binding = _pair_binding(graph_path=graph_path, runtime_root=runtime_root)
    review_plan = build_legacy_label_migration_plan(graph_path)
    candidate_plan = build_deterministic_label_migration_candidate_plan(graph_path)
    if review_plan["base_graph_sha256"] != pair_binding["graph_sha256"]:
        raise LabelAdjudicationError("review plan is not bound to the active pair graph")
    if candidate_plan["base_graph_sha256"] != pair_binding["graph_sha256"]:
        raise LabelAdjudicationError("candidate plan is not bound to the active pair graph")

    connection = connect_sqlite(graph_path, readonly=True)
    try:
        label_queues = _label_mapping_queues(
            connection,
            graph_sha256=pair_binding["graph_sha256"],
            review_plan=review_plan,
        )
        lineage_queues = _lineage_queues(
            connection,
            graph_sha256=pair_binding["graph_sha256"],
        )
        identity_queues = _identity_queues(
            connection,
            graph_sha256=pair_binding["graph_sha256"],
        )
    finally:
        connection.close()

    queues = sorted(
        [*label_queues, *lineage_queues, *identity_queues],
        key=lambda item: (item["category"], -int(item["review_tier"]), item["queue_id"]),
    )
    review_run_id = stable_id(
        "run-graph-label-adjudication",
        pair_binding["generation_id"],
        review_plan["plan_digest"],
        candidate_plan["canonical_digest"]["value"],
        length=28,
    )
    packet = {
        "contract_id": GRAPH_LABEL_ADJUDICATION_CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": stable_id(
            "graph-label-adjudication-packet",
            pair_binding,
            review_plan["plan_digest"],
            candidate_plan["canonical_digest"]["value"],
            queues,
            length=28,
        ),
        "packet_type": "graph_label_human_adjudication",
        "status": PACKET_STATUS,
        "policy": {
            "policy_id": LABEL_POLICY_ID,
            "policy_version": LABEL_POLICY_VERSION,
        },
        "pair_binding": pair_binding,
        "review_plan": {
            "base_graph_sha256": review_plan["base_graph_sha256"],
            "legacy_review_plan_digest": review_plan["plan_digest"],
            "quality_report_digest": review_plan["quality_report_digest"],
            "candidate_plan_digest": candidate_plan["canonical_digest"]["value"],
            "candidate_schema_migration_state": candidate_plan["schema_migration"][
                "state"
            ],
        },
        "review_run_id": review_run_id,
        "trace_id": trace_id(
            "graph-label-adjudication",
            pair_binding["generation_id"],
            review_plan["plan_digest"],
        ),
        "human_decision_contract": HITL_DECISION_CONTRACT_ID,
        "review_protocol": {
            "automatic_labels_are_not_human_labels": True,
            "final_approval_owner_required": True,
            "tier_two_requires_independent_review": True,
            "required_decision_fields": list(_REQUIRED_DECISION_FIELDS),
            "decision_artifact_binding": "one exact HITL decision artifact per queue",
        },
        "queues": queues,
        "relevance_review": {
            "status": "DEFERRED_UNTIL_SUCCESSOR_PAIR",
            "exact_pair_binding_required": True,
            "minimum_independent_reviewer_count": 2,
            "required_dimensions": list(_RELEVANCE_DIMENSIONS),
            "completion_requirement": (
                "Build a fresh packet from the W7 successor pair, record every human "
                "judgment, seal the resulting suite, and rerun it in W8."
            ),
        },
        "summary": {
            "label_mapping_record_count": sum(
                int(item["record_count"]) for item in label_queues
            ),
            "label_mapping_queue_count": len(label_queues),
            "lineage_record_count": sum(
                int(item["record_count"]) for item in lineage_queues
            ),
            "lineage_queue_count": len(lineage_queues),
            "identity_record_count": sum(
                int(item["record_count"]) for item in identity_queues
            ),
            "identity_queue_count": len(identity_queues),
            "total_queue_count": len(queues),
            "provider_call_count": 0,
            "graph_mutation_count": 0,
        },
        "extensions": {},
    }
    return seal_artifact(packet)
