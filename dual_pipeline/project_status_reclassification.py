"""Fail-closed semantic demotion for dated project-status sources.

This module is deliberately narrower than a generic assertion-update utility.
It converts every assertion directly sourced from an explicitly bound status
packet into dated, citation-only candidate evidence.  The control must cover
the complete source scope, bind its exact source-version hash, and retain each
assertion's direct source evidence.  It cannot be used to modify only the
convenient rows from a status source or to turn a mixed source into a status
source by accident.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ._validators import sql_placeholders, table_exists
from .core import ZERO_HASH, digest, sha256_file, sqlite_integrity, stable_id, utc_now
from .project_status import build_snapshot_profile, validate_snapshot_profile

PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION = "2026-08-04.1"
PROJECT_STATUS_RECLASSIFICATION_CONTROL_KEY = (
    "com.partner_advisory.nee.project-status-reclassification-control"
)

_ALLOWED_BEFORE_STATUSES = frozenset(
    {"canonical", "candidate", "historical_candidate", "validation_pending", "evidence_only"}
)
_REQUIRED_CONTROL_KEYS = {"schema_version", "targets", "reason"}
_REQUIRED_TARGET_KEYS = {
    "source_id",
    "source_version_id",
    "expected_content_sha256",
    "expected_source_type",
    "expected_source_name",
    "expected_source_posture",
    "expected_notes",
    "expected_scope_text",
    "expected_version_posture_text",
    "scope_id",
    "as_of_date",
    "assertion_ids",
}


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"project-status reclassification {label} must be nonblank text")
    return value.strip()


def _require_sha256(value: object, *, label: str) -> str:
    result = _require_text(value, label=label)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"project-status reclassification {label} must be lowercase SHA-256")
    return result


def _id_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"project-status reclassification {label} must be a nonempty list")
    result = [_require_text(item, label=label) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"project-status reclassification {label} must be unique")
    return sorted(result)


def _row_hash(row: sqlite3.Row | None) -> str:
    return ZERO_HASH if row is None else digest(dict(row))


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return table_exists(connection, table)


def _placeholders(values: list[str]) -> str:
    return sql_placeholders(values)


def _target_source_posture(*, scope_id: str, as_of_date: str) -> str:
    return (
        f"Dated {scope_id} project-status snapshot as of {as_of_date}; citation-only "
        "candidate evidence. Does not establish timeless current status, formal "
        "authority, or a promotable fact."
    )


def _target_source_notes(*, scope_id: str, as_of_date: str) -> str:
    return (
        f"Reclassified as an ephemeral {scope_id} project-status snapshot as of "
        f"{as_of_date}. Retained only for cited current-status or explicit-history "
        "retrieval."
    )


def validate_project_status_reclassification_control(control: object) -> dict[str, Any]:
    """Validate and canonically order a source-complete reclassification control."""

    if not isinstance(control, dict) or set(control) != _REQUIRED_CONTROL_KEYS:
        raise ValueError("project-status reclassification control has an invalid shape")
    if control["schema_version"] != "1.0.0":
        raise ValueError("project-status reclassification control has an unsupported version")
    raw_targets = control["targets"]
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("project-status reclassification control must have targets")
    targets: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    source_version_ids: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != _REQUIRED_TARGET_KEYS:
            raise ValueError("project-status reclassification target has an invalid shape")
        source_id = _require_text(item["source_id"], label="target.source_id")
        source_version_id = _require_text(
            item["source_version_id"], label="target.source_version_id"
        )
        if source_id in source_ids or source_version_id in source_version_ids:
            raise ValueError("project-status reclassification targets must be source-unique")
        source_ids.add(source_id)
        source_version_ids.add(source_version_id)
        expected_notes = item["expected_notes"]
        if expected_notes is not None and not isinstance(expected_notes, str):
            raise ValueError("project-status reclassification expected_notes must be text or null")
        profile = build_snapshot_profile(
            scope_id=_require_text(item["scope_id"], label="target.scope_id"),
            as_of_date=_require_text(item["as_of_date"], label="target.as_of_date"),
        )
        targets.append(
            {
                "source_id": source_id,
                "source_version_id": source_version_id,
                "expected_content_sha256": _require_sha256(
                    item["expected_content_sha256"], label="target.expected_content_sha256"
                ),
                "expected_source_type": _require_text(
                    item["expected_source_type"], label="target.expected_source_type"
                ),
                "expected_source_name": _require_text(
                    item["expected_source_name"], label="target.expected_source_name"
                ),
                "expected_source_posture": _require_text(
                    item["expected_source_posture"], label="target.expected_source_posture"
                ),
                "expected_notes": expected_notes,
                "expected_scope_text": _require_text(
                    item["expected_scope_text"], label="target.expected_scope_text"
                ),
                "expected_version_posture_text": _require_text(
                    item["expected_version_posture_text"],
                    label="target.expected_version_posture_text",
                ),
                "scope_id": profile["scope_id"],
                "as_of_date": profile["as_of_date"],
                "assertion_ids": _id_list(item["assertion_ids"], label="target.assertion_ids"),
                "snapshot_profile": profile,
            }
        )
    targets.sort(key=lambda target: target["source_id"])
    return {
        "schema_version": "1.0.0",
        "targets": targets,
        "reason": _require_text(control["reason"], label="reason"),
    }


def _assert_required_tables(connection: sqlite3.Connection) -> None:
    required = {
        "source",
        "source_version",
        "assertion",
        "assertion_evidence",
        "evidence_item",
        "search_document",
        "claim",
        "edge",
        "audit_event",
    }
    missing = sorted(table for table in required if not _table_exists(connection, table))
    if missing:
        raise RuntimeError(
            "project-status reclassification graph is missing required tables: "
            + ", ".join(missing)
        )


def _qualifiers_for_status_snapshot(
    qualifiers_json: str | None, *, snapshot_profile: dict[str, Any]
) -> dict[str, Any]:
    try:
        qualifiers = json.loads(qualifiers_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("project-status target has malformed qualifiers") from exc
    if not isinstance(qualifiers, dict):
        raise RuntimeError("project-status target qualifiers must be an object")
    existing = qualifiers.get("project_status_snapshot")
    if existing is not None:
        raise RuntimeError("project-status target is already a snapshot")
    result = dict(qualifiers)
    result.update(
        {
            "dual_pipeline_candidate_type": "source_chunk",
            "retrieval_kind": "evidence_span",
            "answer_use": "citation_only",
            "fact_promotion_eligible": False,
            "project_status_snapshot": validate_snapshot_profile(snapshot_profile),
        }
    )
    return result


def _source_rows(connection: sqlite3.Connection, target: dict[str, Any]) -> dict[str, Any]:
    source = connection.execute(
        "SELECT * FROM source WHERE source_id=?", (target["source_id"],)
    ).fetchone()
    if source is None:
        raise KeyError(f"project-status source is missing: {target['source_id']}")
    if (
        source["source_type"] != target["expected_source_type"]
        or source["source_name"] != target["expected_source_name"]
        or source["source_posture"] != target["expected_source_posture"]
        or source["notes"] != target["expected_notes"]
    ):
        raise RuntimeError(f"project-status source metadata drifted: {target['source_id']}")
    source_version = connection.execute(
        "SELECT * FROM source_version WHERE source_version_id=?", (target["source_version_id"],)
    ).fetchone()
    if source_version is None:
        raise KeyError(
            "project-status source version is missing: " + target["source_version_id"]
        )
    if (
        source_version["source_id"] != target["source_id"]
        or source_version["content_hash"] != target["expected_content_sha256"]
        or source_version["scope_text"] != target["expected_scope_text"]
        or source_version["posture_text"] != target["expected_version_posture_text"]
    ):
        raise RuntimeError(f"project-status source-version drifted: {target['source_version_id']}")
    return {"source": source, "source_version": source_version}


def _collect_target(connection: sqlite3.Connection, target: dict[str, Any]) -> dict[str, Any]:
    source_rows = _source_rows(connection, target)
    source_id = target["source_id"]
    assertion_rows = connection.execute(
        "SELECT * FROM assertion WHERE primary_source_id=? ORDER BY assertion_id", (source_id,)
    ).fetchall()
    actual_ids = [row["assertion_id"] for row in assertion_rows]
    if actual_ids != target["assertion_ids"]:
        raise RuntimeError(
            "project-status assertion selection does not exactly cover the target source: "
            + source_id
        )
    if not assertion_rows:
        raise RuntimeError("project-status target source has no assertions")
    status_counts: dict[str, int] = {}
    assertion_before_hashes: dict[str, str] = {}
    for row in assertion_rows:
        status = row["canonical_status"]
        if status not in _ALLOWED_BEFORE_STATUSES:
            raise RuntimeError(
                f"project-status assertion has unsupported pre-demotion status: {status}"
            )
        _qualifiers_for_status_snapshot(
            row["qualifiers_json"], snapshot_profile=target["snapshot_profile"]
        )
        evidence = connection.execute(
            """SELECT 1 FROM assertion_evidence ae
               JOIN evidence_item ei ON ei.evidence_id=ae.evidence_id
               WHERE ae.assertion_id=? AND ei.source_id=?""",
            (row["assertion_id"], source_id),
        ).fetchone()
        if evidence is None:
            raise RuntimeError(
                "project-status assertion lacks source-bound citation evidence: "
                + row["assertion_id"]
            )
        status_counts[status] = status_counts.get(status, 0) + 1
        assertion_before_hashes[row["assertion_id"]] = _row_hash(row)

    search_rows = connection.execute(
        "SELECT * FROM search_document WHERE assertion_id IN ("
        + _placeholders(actual_ids)
        + ") ORDER BY search_document_id",
        actual_ids,
    ).fetchall()
    search_assertions = {row["assertion_id"] for row in search_rows}
    if search_assertions != set(actual_ids):
        raise RuntimeError(
            "project-status target lacks a complete search-document projection: " + source_id
        )
    claims = connection.execute(
        "SELECT * FROM claim WHERE source_id=? ORDER BY claim_id", (source_id,)
    ).fetchall()
    edges = connection.execute(
        "SELECT * FROM edge WHERE source_id=? ORDER BY edge_id", (source_id,)
    ).fetchall()
    profile = validate_snapshot_profile(target["snapshot_profile"])
    source_posture = _target_source_posture(
        scope_id=profile["scope_id"], as_of_date=profile["as_of_date"]
    )
    source_notes = _target_source_notes(
        scope_id=profile["scope_id"], as_of_date=profile["as_of_date"]
    )
    return {
        "source_id": source_id,
        "source_version_id": target["source_version_id"],
        "snapshot_profile": profile,
        "source_before_hash": _row_hash(source_rows["source"]),
        "source_version_before_hash": _row_hash(source_rows["source_version"]),
        "assertion_ids": actual_ids,
        "assertion_before_hashes": assertion_before_hashes,
        "search_document_before_hashes": {
            row["search_document_id"]: _row_hash(row) for row in search_rows
        },
        "claim_before_hashes": {row["claim_id"]: _row_hash(row) for row in claims},
        "edge_before_hashes": {row["edge_id"]: _row_hash(row) for row in edges},
        "before_status_counts": status_counts,
        "target_source_posture": source_posture,
        "target_source_notes": source_notes,
    }


def _collect_targets(connection: sqlite3.Connection, control: dict[str, Any]) -> dict[str, Any]:
    _assert_required_tables(connection)
    sources = [_collect_target(connection, target) for target in control["targets"]]
    result = {"sources": sources}
    result["snapshot_digest"] = digest(result)
    return result


def build_project_status_reclassification_plan(
    graph_path: Path, *, active_pair: dict[str, Any], control: object
) -> dict[str, Any]:
    """Build an exact, active-pair-bound status demotion plan without mutation."""

    graph_path = Path(graph_path).resolve()
    graph_sha256 = sha256_file(graph_path)
    if graph_sha256 != active_pair["graph_sha256"]:
        raise RuntimeError("project-status reclassification plan must target the active graph hash")
    normalized_control = validate_project_status_reclassification_control(control)
    from .storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        targets = _collect_targets(connection, normalized_control)
    finally:
        connection.close()
    before_status_counts: dict[str, int] = {}
    for target in targets["sources"]:
        for status, count in target["before_status_counts"].items():
            before_status_counts[status] = before_status_counts.get(status, 0) + count
    plan = {
        "schema_version": "1.0.0",
        "plan_type": "project_status_reclassification",
        "policy_version": PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION,
        "base_generation_id": active_pair["generation_id"],
        "base_graph_sha256": graph_sha256,
        "control": normalized_control,
        "control_digest": digest(normalized_control),
        "targets": targets,
        "operations": [
            {
                "operation_id": stable_id(
                    "project-status-demotion",
                    active_pair["generation_id"],
                    graph_sha256,
                    target["source_id"],
                    target["snapshot_profile"]["snapshot_id"],
                    length=28,
                ),
                "operation": "demote_project_status_source",
                "target_id": target["source_id"],
                "before_state_hash": digest(
                    {
                        "source_before_hash": target["source_before_hash"],
                        "source_version_before_hash": target["source_version_before_hash"],
                        "assertion_before_hashes": target["assertion_before_hashes"],
                    }
                ),
            }
            for target in targets["sources"]
        ],
        "summary": {
            "source_count": len(targets["sources"]),
            "assertion_count": sum(len(target["assertion_ids"]) for target in targets["sources"]),
            "search_document_count": sum(
                len(target["search_document_before_hashes"]) for target in targets["sources"]
            ),
            "claim_count": sum(len(target["claim_before_hashes"]) for target in targets["sources"]),
            "edge_count": sum(len(target["edge_before_hashes"]) for target in targets["sources"]),
            "before_status_counts": dict(sorted(before_status_counts.items())),
            "graph_operation_count": len(targets["sources"]),
        },
    }
    plan["plan_id"] = stable_id(
        "project-status-reclassification-plan",
        plan["base_generation_id"],
        plan["base_graph_sha256"],
        plan["control_digest"],
        plan["targets"]["snapshot_digest"],
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan


def _assert_plan_digest(plan: dict[str, Any]) -> None:
    supplied = plan.get("plan_digest")
    expected = dict(plan)
    expected.pop("plan_digest", None)
    if supplied != digest(expected):
        raise RuntimeError("project-status reclassification plan digest mismatch")
    if plan.get("plan_type") != "project_status_reclassification":
        raise ValueError("unsupported project-status reclassification plan")
    if plan.get("policy_version") != PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION:
        raise ValueError("unsupported project-status reclassification policy version")
    if not isinstance(plan.get("control"), dict) or plan.get("control_digest") != digest(
        plan["control"]
    ):
        raise ValueError("project-status reclassification plan has invalid control binding")


def _apply_target(
    connection: sqlite3.Connection, *, target: dict[str, Any], now: str
) -> dict[str, int]:
    profile = validate_snapshot_profile(target["snapshot_profile"])
    assertion_ids = target["assertion_ids"]
    counts = {"assertion": 0, "search_document": 0, "claim": 0, "edge": 0, "source": 0, "source_version": 0}
    for assertion_id in assertion_ids:
        row = connection.execute(
            "SELECT qualifiers_json FROM assertion WHERE assertion_id=?", (assertion_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("project-status assertion disappeared before apply: " + assertion_id)
        qualifiers = json.dumps(
            _qualifiers_for_status_snapshot(
                row["qualifiers_json"], snapshot_profile=profile
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cursor = connection.execute(
            """UPDATE assertion
               SET canonical_status='candidate', canonical_eligible=0,
                   retrieval_eligible=1, currentness_state='current', qualifiers_json=?,
                   updated_at=?
               WHERE assertion_id=?""",
            (qualifiers, now, assertion_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("project-status assertion demotion did not apply: " + assertion_id)
        counts["assertion"] += cursor.rowcount
    # ``search_document`` is a derived projection.  Governed graphs maintain it
    # through assertion triggers and reject a hand-written projection that does
    # not exactly match their derivation rule.  Count the affected projections
    # for the receipt, but never bypass that synchronisation control here.
    counts["search_document"] += int(
        connection.execute(
            "SELECT count(*) FROM search_document WHERE assertion_id IN ("
            + _placeholders(assertion_ids)
            + ")",
            assertion_ids,
        ).fetchone()[0]
    )
    posture = (
        "citation-only project-status snapshot as of " + profile["as_of_date"]
    )
    cursor = connection.execute(
        "UPDATE claim SET evidence_posture=?,hitl_required=1,updated_at=? WHERE source_id=?",
        (posture, now, target["source_id"]),
    )
    counts["claim"] += cursor.rowcount
    cursor = connection.execute(
        "UPDATE edge SET evidence_posture=?,hitl_required=1,updated_at=? WHERE source_id=?",
        (posture, now, target["source_id"]),
    )
    counts["edge"] += cursor.rowcount
    cursor = connection.execute(
        "UPDATE source SET source_posture=?,notes=? WHERE source_id=?",
        (target["target_source_posture"], target["target_source_notes"], target["source_id"]),
    )
    counts["source"] += cursor.rowcount
    cursor = connection.execute(
        "UPDATE source_version SET scope_text=?,posture_text=? WHERE source_version_id=?",
        (target["target_source_notes"], target["target_source_posture"], target["source_version_id"]),
    )
    counts["source_version"] += cursor.rowcount
    return counts


def apply_project_status_reclassification_plan(
    graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
    event_timestamp: str | None = None,
) -> dict[str, Any]:
    """Apply one exact plan to a graph copy and record a source-bound audit event."""

    _assert_plan_digest(plan)
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("project-status reclassification plan is stale relative to its graph")
    from .storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path)
    connection.row_factory = sqlite3.Row
    now = event_timestamp or utc_now()
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        current = _collect_targets(connection, plan["control"])
        if current != plan["targets"]:
            raise RuntimeError("project-status reclassification target set changed before apply")
        counts = {"assertion": 0, "search_document": 0, "claim": 0, "edge": 0, "source": 0, "source_version": 0}
        for target, operation in zip(current["sources"], plan["operations"], strict=True):
            before_state_hash = digest(
                {
                    "source_before_hash": target["source_before_hash"],
                    "source_version_before_hash": target["source_version_before_hash"],
                    "assertion_before_hashes": target["assertion_before_hashes"],
                }
            )
            if operation["before_state_hash"] != before_state_hash:
                raise RuntimeError("project-status operation binding mismatch")
            changed = _apply_target(connection, target=target, now=now)
            for key, value in changed.items():
                counts[key] += value
            audit_id = stable_id(
                "project-status-demotion-audit", plan["plan_id"], operation["operation_id"], length=28
            )
            connection.execute(
                """INSERT INTO audit_event
                   (audit_event_id,actor_or_process,action,record_type,record_id,
                    previous_value_json,new_value_json,source_id,event_timestamp,reason,
                    validation_reference,approval_reference,migration_or_ingestion_run_id,
                    release_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    audit_id,
                    "nee-project-status-reclassification",
                    operation["operation"],
                    "project_status_source",
                    target["source_id"],
                    json.dumps(
                        {
                            "snapshot_profile": target["snapshot_profile"],
                            "assertion_before_hashes": target["assertion_before_hashes"],
                            "claim_before_hashes": target["claim_before_hashes"],
                            "edge_before_hashes": target["edge_before_hashes"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {"counts": changed, "citation_only": True},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    target["source_id"],
                    now,
                    plan["control"]["reason"],
                    plan["plan_id"],
                    approval_id,
                    run_id,
                    PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION,
                ),
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "project-status reclassification foreign-key validation failed: "
                + str(len(violations))
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    integrity = sqlite_integrity(graph_path)
    after_hash = sha256_file(graph_path)
    if (
        integrity["integrity_check"] != "ok"
        or integrity["foreign_key_violation_count"]
        or after_hash == before_hash
    ):
        raise RuntimeError("project-status reclassification integrity or graph-change check failed")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": len(plan["operations"]),
        "operations": [
            {
                "operation_id": operation["operation_id"],
                "change_set_id": plan["plan_id"],
                "run_id": run_id,
                "source_id": target["source_id"],
                "target_id": target["source_id"],
                "before_state_hash": operation["before_state_hash"],
            }
            for target, operation in zip(plan["targets"]["sources"], plan["operations"], strict=True)
        ],
        "updated": counts,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }
