"""Deterministic label migration candidate planning, validation, and execution."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..core import (
    artifact_digest,
    connect_sqlite,
    digest,
    seal_artifact,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
)
from .audit import _table_exists
from .constants import (
    _APPROVAL_COLUMNS,
    _CANDIDATE_BASE_TABLES,
    _CANDIDATE_HARDENING_TABLES,
    _CANDIDATE_REQUIRED_TABLES,
    _CANDIDATE_REQUIRED_VALUES,
    LABEL_MIGRATION_CANDIDATE_CONTRACT_ID,
    LABEL_MIGRATION_CANDIDATE_STATUS,
    LABEL_MIGRATION_SCHEMA_ID,
    LABEL_MIGRATION_SCHEMA_STATES,
    LABEL_POLICY_ID,
    LABEL_POLICY_VERSION,
    ZERO_HASH,
    LabelQualityError,
)
from .legacy_plan import build_legacy_label_migration_plan


def _row_hash(row: sqlite3.Row | dict[str, Any] | None) -> str:
    return ZERO_HASH if row is None else digest(dict(row))


def _assert_candidate_hardening_preconditions(connection: sqlite3.Connection) -> None:
    missing_tables = sorted(
        table for table in _CANDIDATE_REQUIRED_TABLES if not _table_exists(connection, table)
    )
    if missing_tables:
        raise LabelQualityError(
            "graph-label deterministic candidate requires the additive hardening "
            f"schema; missing tables: {missing_tables}"
        )
    registered = {
        (str(row["vocabulary"]), str(row["value"]))
        for row in connection.execute(
            """SELECT vocabulary,value FROM controlled_value_registry
               WHERE active=1"""
        )
    }
    missing_values = sorted(_CANDIDATE_REQUIRED_VALUES - registered)
    if missing_values:
        raise LabelQualityError(
            "graph-label deterministic candidate requires registered controlled values: "
            f"{missing_values}"
        )


def _candidate_schema_state(connection: sqlite3.Connection) -> str:
    missing_base = sorted(
        table for table in _CANDIDATE_BASE_TABLES if not _table_exists(connection, table)
    )
    if missing_base:
        raise LabelQualityError(
            "graph-label deterministic candidate lacks required base tables: "
            f"{missing_base}"
        )
    present_hardening = {
        table for table in _CANDIDATE_HARDENING_TABLES if _table_exists(connection, table)
    }
    if not present_hardening:
        return "APPLY_TO_CANDIDATE"
    if present_hardening != _CANDIDATE_HARDENING_TABLES:
        missing = sorted(_CANDIDATE_HARDENING_TABLES - present_hardening)
        raise LabelQualityError(
            "graph-label hardening schema is partially installed; candidate planning "
            f"must not guess the repair path: {missing}"
        )
    _assert_candidate_hardening_preconditions(connection)
    return "ALREADY_INSTALLED"


def _label_hardening_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / LABEL_MIGRATION_SCHEMA_ID
        / "schema.sql"
    )


def _assert_legacy_plan_digest(plan: dict[str, Any]) -> None:
    supplied = plan.get("plan_digest")
    expected = dict(plan)
    expected.pop("plan_digest", None)
    if not isinstance(supplied, str) or supplied != digest(expected):
        raise LabelQualityError("legacy graph-label migration plan digest mismatch")
    if plan.get("plan_type") != "graph_label_migration":
        raise LabelQualityError("unsupported legacy graph-label migration plan")
    if plan.get("status") != "PROPOSED_REVIEW_ONLY":
        raise LabelQualityError("legacy graph-label migration plan must remain review-only")
    if plan.get("mutation_authorized") is not False:
        raise LabelQualityError("legacy graph-label migration plan must not authorize mutation")


def _mapping_rule_id(*, field: str, legacy_value: str, canonical_value: str) -> str:
    return stable_id(
        "graph-label-deterministic-map",
        LABEL_POLICY_ID,
        LABEL_POLICY_VERSION,
        field,
        legacy_value,
        canonical_value,
        length=28,
    )


def _candidate_operation(
    *,
    graph_sha256: str,
    review_plan_digest: str,
    item: dict[str, Any],
    row: sqlite3.Row,
) -> dict[str, Any]:
    record_type = str(item["record_type"])
    record_id = str(item["record_id"])
    field = str(item["field"])
    legacy_value = str(item["legacy_value"])
    reason = str(item["reason"])

    if record_type == "source":
        legacy_state = {
            "source_id": str(row["source_id"]),
            "source_posture": str(row["source_posture"]),
        }
        normalized_label: dict[str, Any] = {
            "source_kind": "legacy_source",
            "source_format": "legacy_unknown",
            "source_origin": "legacy_unknown",
            "source_posture_code": str(item["proposed_value"]),
            "source_posture_note": reason,
            "mapping_state": "deterministic_mapped",
        }
        canonical_value = normalized_label["source_posture_code"]
        operation = "insert_source_label_state"
        source_id: str | None = record_id
    elif record_type == "evidence":
        legacy_state = {
            "evidence_id": str(row["evidence_id"]),
            "source_id": str(row["source_id"]),
            "evidence_directness": str(row["evidence_directness"]),
        }
        normalized_label = {
            "directness_code": str(item["proposed_value"]),
            "basis_note": reason,
            "mapping_state": "deterministic_mapped",
        }
        canonical_value = normalized_label["directness_code"]
        operation = "insert_evidence_label_state"
        source_id = str(row["source_id"])
    elif record_type == "assertion":
        score = float(row["confidence_score"])
        legacy_state = {
            "assertion_id": str(row["assertion_id"]),
            "primary_source_id": row["primary_source_id"],
            "confidence_label": str(row["confidence_label"]),
            "confidence_score": score,
            "extraction_method": str(row["extraction_method"]),
        }
        normalized_label = {
            "extraction_confidence": score,
            "assertion_confidence_label": None,
            "assertion_confidence_score": None,
            "validation_state": "accepted_legacy",
            "lifecycle_state": "unknown",
            "mapping_state": "deterministic_mapped",
        }
        canonical_value = "separate_extraction_confidence"
        operation = "insert_assertion_label_state"
        source_id = (
            str(row["primary_source_id"])
            if row["primary_source_id"] is not None
            else None
        )
    else:
        raise LabelQualityError(f"unsupported deterministic label record type: {record_type}")

    mapping_rule_id = _mapping_rule_id(
        field=field,
        legacy_value=legacy_value,
        canonical_value=canonical_value,
    )
    return {
        "operation_id": stable_id(
            "graph-label-deterministic-candidate-operation",
            graph_sha256,
            review_plan_digest,
            record_type,
            record_id,
            mapping_rule_id,
            length=28,
        ),
        "operation": operation,
        "record_type": record_type,
        "record_id": record_id,
        "source_id": source_id,
        "field": field,
        "legacy_value": legacy_value,
        "normalized_label": normalized_label,
        "mapping_rule_id": mapping_rule_id,
        "expected_legacy_state_sha256": digest(legacy_state),
        "expected_normalized_state_sha256": ZERO_HASH,
        "reason": reason,
    }


def build_deterministic_label_migration_candidate_plan(graph_path: Path) -> dict[str, Any]:
    """Build the W5 candidate-only subset of the review plan without writing a graph.

    The artifact contains only values already admitted by the closed-world
    contract.  It deliberately excludes every HITL item and remains incapable
    of authorizing a graph write on its own.
    """

    graph_path = Path(graph_path).resolve()
    review_plan = build_legacy_label_migration_plan(graph_path)
    _assert_legacy_plan_digest(review_plan)
    graph_sha256 = str(review_plan["base_graph_sha256"])
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        schema_state = _candidate_schema_state(connection)
        operations: list[dict[str, Any]] = []
        for item in review_plan["items"]:
            if item["decision"] != "deterministic_map":
                continue
            record_type = str(item["record_type"])
            record_id = str(item["record_id"])
            if item.get("proposed_value") is None:
                raise LabelQualityError(
                    f"deterministic label item has no proposed value: {record_type}:{record_id}"
                )
            if record_type == "source":
                row = connection.execute(
                    """SELECT source_id,source_posture FROM source
                       WHERE source_id=?""",
                    (record_id,),
                ).fetchone()
                normalized = (
                    connection.execute(
                        """SELECT source_id,source_kind,source_format,source_origin,
                                  source_posture_code,source_posture_note,mapping_state,
                                  mapping_rule_id,updated_at
                             FROM source_label_state WHERE source_id=?""",
                        (record_id,),
                    ).fetchone()
                    if schema_state == "ALREADY_INSTALLED"
                    else None
                )
            elif record_type == "evidence":
                row = connection.execute(
                    """SELECT evidence_id,source_id,evidence_directness FROM evidence_item
                       WHERE evidence_id=?""",
                    (record_id,),
                ).fetchone()
                normalized = (
                    connection.execute(
                        """SELECT evidence_id,directness_code,basis_note,mapping_state,
                                  mapping_rule_id,updated_at
                             FROM evidence_label_state WHERE evidence_id=?""",
                        (record_id,),
                    ).fetchone()
                    if schema_state == "ALREADY_INSTALLED"
                    else None
                )
            elif record_type == "assertion":
                row = connection.execute(
                    """SELECT assertion_id,primary_source_id,confidence_label,confidence_score,
                              extraction_method FROM assertion WHERE assertion_id=?""",
                    (record_id,),
                ).fetchone()
                normalized = (
                    connection.execute(
                        """SELECT assertion_id,extraction_confidence,
                                  assertion_confidence_label,assertion_confidence_score,
                                  validation_state,lifecycle_state,mapping_state,mapping_rule_id,
                                  updated_at
                             FROM assertion_label_state WHERE assertion_id=?""",
                        (record_id,),
                    ).fetchone()
                    if schema_state == "ALREADY_INSTALLED"
                    else None
                )
            else:
                raise LabelQualityError(f"unknown migration record type: {record_type}")
            if row is None:
                raise LabelQualityError(
                    f"review-plan record disappeared before candidate planning: "
                    f"{record_type}:{record_id}"
                )
            if normalized is not None:
                raise LabelQualityError(
                    f"normalized label state already exists; candidate plan cannot overwrite: "
                    f"{record_type}:{record_id}"
                )
            operations.append(
                _candidate_operation(
                    graph_sha256=graph_sha256,
                    review_plan_digest=str(review_plan["plan_digest"]),
                    item=item,
                    row=row,
                )
            )
    finally:
        connection.close()

    source_count = sum(item["record_type"] == "source" for item in operations)
    evidence_count = sum(item["record_type"] == "evidence" for item in operations)
    assertion_count = sum(item["record_type"] == "assertion" for item in operations)
    artifact = {
        "contract_id": LABEL_MIGRATION_CANDIDATE_CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": stable_id(
            "graph-label-deterministic-candidate-plan",
            graph_sha256,
            review_plan["plan_digest"],
            operations,
            length=28,
        ),
        "plan_type": "graph_label_deterministic_candidate",
        "policy": {
            "policy_id": LABEL_POLICY_ID,
            "policy_version": LABEL_POLICY_VERSION,
            "enforcement_state": "shadow",
        },
        "schema_migration": {
            "migration_id": LABEL_MIGRATION_SCHEMA_ID,
            "state": schema_state,
        },
        "base_graph_sha256": graph_sha256,
        "source_review_plan_digest": review_plan["plan_digest"],
        "quality_report_digest": review_plan["quality_report_digest"],
        "status": LABEL_MIGRATION_CANDIDATE_STATUS,
        "candidate_only": True,
        "mutation_authorized": False,
        "provider_call_count": 0,
        "graph_mutation_count": 0,
        "operations": operations,
        "summary": {
            "deterministic_operation_count": len(operations),
            "source_label_state_insert_count": source_count,
            "evidence_label_state_insert_count": evidence_count,
            "assertion_label_state_insert_count": assertion_count,
            "excluded_hitl_required_count": int(
                review_plan["summary"]["hitl_required_count"]
            ),
        },
        "extensions": {},
    }
    return seal_artifact(artifact)


def validate_deterministic_label_migration_candidate_plan(
    graph_path: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless a plan is the exact deterministic subset for this graph."""

    if plan.get("contract_id") != LABEL_MIGRATION_CANDIDATE_CONTRACT_ID:
        raise LabelQualityError("unsupported graph-label deterministic candidate contract")
    if plan.get("schema_version") != "1.0.0":
        raise LabelQualityError("unsupported graph-label deterministic candidate schema")
    if plan.get("status") != LABEL_MIGRATION_CANDIDATE_STATUS:
        raise LabelQualityError("candidate plan has an unsafe status")
    if plan.get("candidate_only") is not True or plan.get("mutation_authorized") is not False:
        raise LabelQualityError("candidate plan must remain non-authorizing and candidate-only")
    if plan.get("provider_call_count") != 0 or plan.get("graph_mutation_count") != 0:
        raise LabelQualityError("candidate plan must report zero provider calls and mutations")
    declared = plan.get("canonical_digest")
    if not isinstance(declared, dict) or declared.get("value") != artifact_digest(plan):
        raise LabelQualityError("candidate plan canonical digest mismatch")
    expected = build_deterministic_label_migration_candidate_plan(graph_path)
    if plan != expected:
        raise LabelQualityError(
            "candidate plan does not exactly match the graph-bound deterministic subset"
        )
    return expected


def _validate_label_candidate_approval_record(
    *,
    approval_record: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    if set(approval_record) != set(_APPROVAL_COLUMNS):
        raise LabelQualityError("graph-label candidate approval record has an invalid shape")
    approval_reference = approval_record["approval_reference"]
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        raise LabelQualityError("an exact approval reference is required")
    if (
        approval_record["approval_type"] != "release_implementation"
        or approval_record["status"] != "approved"
        or approval_record["active"] != 1
    ):
        raise LabelQualityError("graph-label candidate approval is not active and approved")
    if approval_record["baseline_graph_sha256"] != plan["base_graph_sha256"]:
        raise LabelQualityError("graph-label candidate approval is bound to a different graph")
    for field in ("proposal_sha256", "approval_statement_sha256"):
        value = approval_record[field]
        if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
            raise LabelQualityError(f"graph-label candidate approval has invalid {field}")
    for field in (
        "baseline_zip_sha256",
        "baseline_graph_sha256",
        "decision_record_sha256",
        "card_proposal_sha256",
        "gate_o1_sha256",
    ):
        value = approval_record[field]
        if value is not None and (
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
        ):
            raise LabelQualityError(f"graph-label candidate approval has invalid {field}")
    for field in (
        "approval_id",
        "approved_at",
        "approved_by",
        "release_version",
        "created_at",
    ):
        if not isinstance(approval_record[field], str) or not approval_record[field].strip():
            raise LabelQualityError(f"graph-label candidate approval has invalid {field}")
    if approval_record["expires_at"] is not None and (
        not isinstance(approval_record["expires_at"], str)
        or approval_record["expires_at"] < utc_now()
    ):
        raise LabelQualityError("graph-label candidate approval has expired or malformed expiry")
    if (
        not isinstance(approval_record["scope_json"], str)
        or not approval_record["scope_json"].strip()
    ):
        raise LabelQualityError("graph-label candidate approval scope is malformed")
    try:
        scope = json.loads(approval_record["scope_json"])
    except json.JSONDecodeError as exc:
        raise LabelQualityError("graph-label candidate approval scope is malformed") from exc
    if not isinstance(scope, dict):
        raise LabelQualityError("graph-label candidate approval scope must be an object")
    if scope.get("base_graph_sha256") != plan["base_graph_sha256"]:
        raise LabelQualityError("approval scope does not bind the base graph hash")
    if (
        scope.get("graph_label_candidate_plan_digest")
        != plan["canonical_digest"]["value"]
    ):
        raise LabelQualityError("approval scope does not bind the candidate plan digest")
    return approval_reference


def _approval_for_label_candidate(
    connection: sqlite3.Connection,
    *,
    approval_record: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    approval_reference = _validate_label_candidate_approval_record(
        approval_record=approval_record,
        plan=plan,
    )
    row = connection.execute(
        """SELECT approval_id,approval_reference,approval_type,scope_json,
                  baseline_zip_sha256,baseline_graph_sha256,proposal_sha256,
                  decision_record_sha256,card_proposal_sha256,gate_o1_sha256,
                  approval_statement_sha256,approved_at,expires_at,status,
                  approved_by,release_version,active,created_at
             FROM governance_approval WHERE approval_reference=?""",
        (approval_reference,),
    ).fetchone()
    if row is not None:
        if dict(row) != approval_record:
            raise LabelQualityError("existing graph-label approval differs from the supplied record")
        return approval_reference
    connection.execute(
        """INSERT INTO governance_approval
           (approval_id,approval_reference,approval_type,scope_json,baseline_zip_sha256,
            baseline_graph_sha256,proposal_sha256,decision_record_sha256,
            card_proposal_sha256,gate_o1_sha256,approval_statement_sha256,approved_at,
            expires_at,status,approved_by,release_version,active,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(approval_record[column] for column in _APPROVAL_COLUMNS),
    )
    return approval_reference


def _legacy_state_for_operation(
    connection: sqlite3.Connection,
    operation: dict[str, Any],
) -> dict[str, Any]:
    record_type = operation["record_type"]
    record_id = operation["record_id"]
    if record_type == "source":
        row = connection.execute(
            "SELECT source_id,source_posture FROM source WHERE source_id=?", (record_id,)
        ).fetchone()
        if row is None:
            raise LabelQualityError(f"source disappeared: {record_id}")
        return {"source_id": str(row["source_id"]), "source_posture": str(row["source_posture"])}
    if record_type == "evidence":
        row = connection.execute(
            """SELECT evidence_id,source_id,evidence_directness FROM evidence_item
               WHERE evidence_id=?""",
            (record_id,),
        ).fetchone()
        if row is None:
            raise LabelQualityError(f"evidence disappeared: {record_id}")
        return {
            "evidence_id": str(row["evidence_id"]),
            "source_id": str(row["source_id"]),
            "evidence_directness": str(row["evidence_directness"]),
        }
    if record_type == "assertion":
        row = connection.execute(
            """SELECT assertion_id,primary_source_id,confidence_label,confidence_score,
                      extraction_method FROM assertion WHERE assertion_id=?""",
            (record_id,),
        ).fetchone()
        if row is None:
            raise LabelQualityError(f"assertion disappeared: {record_id}")
        return {
            "assertion_id": str(row["assertion_id"]),
            "primary_source_id": row["primary_source_id"],
            "confidence_label": str(row["confidence_label"]),
            "confidence_score": float(row["confidence_score"]),
            "extraction_method": str(row["extraction_method"]),
        }
    raise LabelQualityError(f"unsupported candidate operation record type: {record_type}")


def _normalized_row_for_operation(
    connection: sqlite3.Connection,
    operation: dict[str, Any],
) -> sqlite3.Row | None:
    record_type = operation["record_type"]
    record_id = operation["record_id"]
    if record_type == "source":
        return connection.execute(
            """SELECT source_id,source_kind,source_format,source_origin,
                      source_posture_code,source_posture_note,mapping_state,
                      mapping_rule_id,updated_at
                 FROM source_label_state WHERE source_id=?""",
            (record_id,),
        ).fetchone()
    if record_type == "evidence":
        return connection.execute(
            """SELECT evidence_id,directness_code,basis_note,mapping_state,
                      mapping_rule_id,updated_at
                 FROM evidence_label_state WHERE evidence_id=?""",
            (record_id,),
        ).fetchone()
    if record_type == "assertion":
        return connection.execute(
            """SELECT assertion_id,extraction_confidence,assertion_confidence_label,
                      assertion_confidence_score,validation_state,lifecycle_state,
                      mapping_state,mapping_rule_id,updated_at
                 FROM assertion_label_state WHERE assertion_id=?""",
            (record_id,),
        ).fetchone()
    raise LabelQualityError(f"unsupported candidate operation record type: {record_type}")


def _assert_registered_value(
    connection: sqlite3.Connection,
    *,
    vocabulary: str,
    value: str,
) -> None:
    row = connection.execute(
        """SELECT 1 FROM controlled_value_registry
           WHERE vocabulary=? AND value=? AND active=1""",
        (vocabulary, value),
    ).fetchone()
    if row is None:
        raise LabelQualityError(f"unregistered normalized value: {vocabulary}:{value}")


def _mapping_canonical_value(operation: dict[str, Any]) -> str:
    label = operation["normalized_label"]
    if operation["record_type"] == "source":
        return str(label["source_posture_code"])
    if operation["record_type"] == "evidence":
        return str(label["directness_code"])
    if operation["record_type"] == "assertion":
        return "separate_extraction_confidence"
    raise LabelQualityError(f"unsupported candidate operation record type: {operation['record_type']}")


def _ensure_mapping_rule(
    connection: sqlite3.Connection,
    *,
    operation: dict[str, Any],
    approval_reference: str,
    applied_at: str,
) -> None:
    expected = {
        "mapping_rule_id": operation["mapping_rule_id"],
        "field_name": operation["field"],
        "legacy_value": operation["legacy_value"],
        "canonical_value": _mapping_canonical_value(operation),
        "decision_state": "approved",
        "mapping_basis": operation["reason"],
        "approval_reference": approval_reference,
        "active": 1,
    }
    row = connection.execute(
        """SELECT mapping_rule_id,field_name,legacy_value,canonical_value,
                  decision_state,mapping_basis,approval_reference,active
             FROM legacy_label_mapping WHERE mapping_rule_id=?""",
        (operation["mapping_rule_id"],),
    ).fetchone()
    if row is not None:
        if dict(row) != expected:
            raise LabelQualityError(
                f"existing mapping rule does not match exact approved candidate: "
                f"{operation['mapping_rule_id']}"
            )
        return
    conflict = connection.execute(
        """SELECT mapping_rule_id FROM legacy_label_mapping
           WHERE field_name=? AND legacy_value=?""",
        (operation["field"], operation["legacy_value"]),
    ).fetchone()
    if conflict is not None:
        raise LabelQualityError(
            "legacy value is already bound to another mapping rule: "
            f"{operation['field']}:{operation['legacy_value']}"
        )
    connection.execute(
        """INSERT INTO legacy_label_mapping
           (mapping_rule_id,field_name,legacy_value,canonical_value,decision_state,
            mapping_basis,approval_reference,active,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            expected["mapping_rule_id"],
            expected["field_name"],
            expected["legacy_value"],
            expected["canonical_value"],
            expected["decision_state"],
            expected["mapping_basis"],
            expected["approval_reference"],
            expected["active"],
            applied_at,
        ),
    )


def _insert_normalized_label_state(
    connection: sqlite3.Connection,
    *,
    operation: dict[str, Any],
    applied_at: str,
) -> sqlite3.Row:
    record_type = operation["record_type"]
    record_id = operation["record_id"]
    label = operation["normalized_label"]
    if record_type == "source":
        _assert_registered_value(
            connection, vocabulary="source_posture_code", value=str(label["source_posture_code"])
        )
        connection.execute(
            """INSERT INTO source_label_state
               (source_id,source_kind,source_format,source_origin,source_posture_code,
                source_posture_note,mapping_state,mapping_rule_id,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                record_id,
                label["source_kind"],
                label["source_format"],
                label["source_origin"],
                label["source_posture_code"],
                label["source_posture_note"],
                label["mapping_state"],
                operation["mapping_rule_id"],
                applied_at,
            ),
        )
    elif record_type == "evidence":
        _assert_registered_value(
            connection,
            vocabulary="evidence_directness_code",
            value=str(label["directness_code"]),
        )
        connection.execute(
            """INSERT INTO evidence_label_state
               (evidence_id,directness_code,basis_note,mapping_state,mapping_rule_id,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (
                record_id,
                label["directness_code"],
                label["basis_note"],
                label["mapping_state"],
                operation["mapping_rule_id"],
                applied_at,
            ),
        )
    elif record_type == "assertion":
        _assert_registered_value(
            connection,
            vocabulary="validation_state",
            value=str(label["validation_state"]),
        )
        _assert_registered_value(
            connection,
            vocabulary="lifecycle_state",
            value=str(label["lifecycle_state"]),
        )
        connection.execute(
            """INSERT INTO assertion_label_state
               (assertion_id,extraction_confidence,assertion_confidence_label,
                assertion_confidence_score,validation_state,lifecycle_state,mapping_state,
                mapping_rule_id,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                record_id,
                label["extraction_confidence"],
                label["assertion_confidence_label"],
                label["assertion_confidence_score"],
                label["validation_state"],
                label["lifecycle_state"],
                label["mapping_state"],
                operation["mapping_rule_id"],
                applied_at,
            ),
        )
    else:
        raise LabelQualityError(f"unsupported candidate operation record type: {record_type}")
    after = _normalized_row_for_operation(connection, operation)
    if after is None:
        raise LabelQualityError(f"normalized label insert did not persist: {record_type}:{record_id}")
    return after


def apply_approved_deterministic_label_migration_candidate(
    source_graph_path: Path,
    candidate_graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_record: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Apply W5's exact subset to a distinct candidate graph after exact approval.

    This intentionally has no CLI command.  W7 must call it from the normal
    approval-bound release lifecycle with a newly created candidate graph.
    The source graph is opened read-only and can never be the mutation target.
    """

    source_graph_path = Path(source_graph_path).resolve()
    candidate_graph_path = Path(candidate_graph_path).resolve()
    if source_graph_path == candidate_graph_path:
        raise LabelQualityError("label migration must target a distinct candidate graph")
    if not isinstance(run_id, str) or not run_id.strip():
        raise LabelQualityError("label migration requires a nonempty run identifier")
    exact_plan = validate_deterministic_label_migration_candidate_plan(
        source_graph_path, plan
    )
    schema_migration = exact_plan.get("schema_migration")
    if not isinstance(schema_migration, dict):
        raise LabelQualityError("candidate plan lacks its schema-migration binding")
    schema_state = schema_migration.get("state")
    if (
        schema_migration.get("migration_id") != LABEL_MIGRATION_SCHEMA_ID
        or schema_state not in LABEL_MIGRATION_SCHEMA_STATES
    ):
        raise LabelQualityError("candidate plan has an unsupported schema-migration binding")
    _validate_label_candidate_approval_record(
        approval_record=approval_record,
        plan=exact_plan,
    )
    before_hash = sha256_file(candidate_graph_path)
    if before_hash != exact_plan["base_graph_sha256"]:
        raise LabelQualityError("candidate graph does not match the approved base hash")

    connection = connect_sqlite(candidate_graph_path)
    applied_at = utc_now()
    receipts: list[dict[str, Any]] = []
    schema_applied = False
    try:
        observed_schema_state = _candidate_schema_state(connection)
        if observed_schema_state != schema_state:
            raise LabelQualityError(
                "candidate schema state does not match the approved candidate plan: "
                f"expected {schema_state}, got {observed_schema_state}"
            )
        if schema_state == "APPLY_TO_CANDIDATE":
            schema_path = _label_hardening_schema_path()
            if not schema_path.is_file():
                raise LabelQualityError(f"label hardening schema is unavailable: {schema_path}")
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            schema_applied = True
        _assert_candidate_hardening_preconditions(connection)
        schema_hash = sha256_file(candidate_graph_path)
        connection.execute("BEGIN IMMEDIATE")
        approval_reference = _approval_for_label_candidate(
            connection,
            approval_record=approval_record,
            plan=exact_plan,
        )
        for operation in exact_plan["operations"]:
            legacy_state = _legacy_state_for_operation(connection, operation)
            if digest(legacy_state) != operation["expected_legacy_state_sha256"]:
                raise LabelQualityError(
                    "legacy row drifted after candidate planning: "
                    f"{operation['record_type']}:{operation['record_id']}"
                )
            existing = _normalized_row_for_operation(connection, operation)
            if _row_hash(existing) != operation["expected_normalized_state_sha256"]:
                raise LabelQualityError(
                    "normalized label state already exists or drifted: "
                    f"{operation['record_type']}:{operation['record_id']}"
                )
            _ensure_mapping_rule(
                connection,
                operation=operation,
                approval_reference=approval_reference,
                applied_at=applied_at,
            )
            after = _insert_normalized_label_state(
                connection,
                operation=operation,
                applied_at=applied_at,
            )
            after_hash = _row_hash(after)
            mapping_event_id = stable_id(
                "graph-label-mapping-event",
                exact_plan["canonical_digest"]["value"],
                operation["operation_id"],
                length=28,
            )
            connection.execute(
                """INSERT INTO label_mapping_event
                   (mapping_event_id,mapping_rule_id,record_type,record_id,
                    before_value_sha256,after_value_sha256,approval_reference,applied_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    mapping_event_id,
                    operation["mapping_rule_id"],
                    operation["record_type"],
                    operation["record_id"],
                    operation["expected_legacy_state_sha256"],
                    after_hash,
                    approval_reference,
                    applied_at,
                ),
            )
            audit_event_id = stable_id(
                "graph-label-deterministic-candidate-audit",
                exact_plan["artifact_id"],
                operation["operation_id"],
                length=28,
            )
            connection.execute(
                """INSERT INTO audit_event
                   (audit_event_id,actor_or_process,action,record_type,record_id,
                    previous_value_json,new_value_json,source_id,event_timestamp,reason,
                    validation_reference,approval_reference,migration_or_ingestion_run_id,
                    release_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    audit_event_id,
                    "nee-graph-label-deterministic-candidate",
                    operation["operation"],
                    operation["record_type"],
                    operation["record_id"],
                    json.dumps(
                        {"legacy": legacy_state, "normalized": None},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        dict(after),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    operation["source_id"],
                    applied_at,
                    operation["reason"],
                    exact_plan["artifact_id"],
                    approval_reference,
                    run_id,
                    LABEL_POLICY_VERSION,
                ),
            )
            receipts.append(
                {
                    "operation_id": operation["operation_id"],
                    "record_type": operation["record_type"],
                    "record_id": operation["record_id"],
                    "mapping_rule_id": operation["mapping_rule_id"],
                    "before_legacy_state_sha256": operation[
                        "expected_legacy_state_sha256"
                    ],
                    "after_normalized_state_sha256": after_hash,
                }
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise LabelQualityError(
                f"candidate label migration foreign-key validation failed: {len(violations)}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    integrity = sqlite_integrity(candidate_graph_path)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        raise LabelQualityError("candidate label migration integrity validation failed")
    after_hash = sha256_file(candidate_graph_path)
    if receipts and after_hash == before_hash:
        raise LabelQualityError("candidate label migration did not change the candidate graph")
    return {
        "status": "COMMITTED",
        "candidate_only": True,
        "source_graph_sha256": exact_plan["base_graph_sha256"],
        "candidate_graph_sha256_before": before_hash,
        "candidate_graph_sha256_after_schema": schema_hash,
        "candidate_graph_sha256_after": after_hash,
        "candidate_plan_digest": exact_plan["canonical_digest"]["value"],
        "schema_migration": {
            "migration_id": LABEL_MIGRATION_SCHEMA_ID,
            "state": schema_state,
            "applied": schema_applied,
        },
        "approval_reference": approval_reference,
        "run_id": run_id,
        "operation_count": len(receipts),
        "operations": receipts,
        "provider_call_count": 0,
        "integrity": integrity,
        "committed_at": applied_at,
    }
