"""Plan application, transactional graph mutation, and audit event recording."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    ZERO_HASH,
    digest,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
)
from storage.connection import sqlite3

from .constants import (
    CLOSEOUT_POLICY_VERSION,
    _ids,
    _placeholders,
    _row_hash,
    _row_state,
)
from .plan import _assert_plan_digest
from .qualifiers import (
    _assertion_state,
    _closeout_qualifiers,
    _confirmation_assertion_state,
    _confirmation_edge_state,
    _confirmation_qualifiers,
    _confirmed_edge_description,
    _queue_state,
)
from .targets import (
    _assert_removal_dependencies_clear,
    _removal_targets,
)


def _delete_by_ids(connection: sqlite3.Connection, table: str, field: str, ids: list[str]) -> int:
    if not ids:
        return 0
    cursor = connection.execute(
        f"DELETE FROM {table} WHERE {field} IN ({_placeholders(ids)})", tuple(ids)
    )
    return cursor.rowcount


def _delete_canonical_routes(
    connection: sqlite3.Connection, object_pairs: list[tuple[str, list[str]]]
) -> None:
    """Remove routes only after their upstream objects are gone.

    The legacy route trigger correctly protects live claims from losing their
    primary route, but it does not exempt a claim that has just been removed.
    Suspend that one trigger inside this transaction, delete the now-orphaned
    routes, and restore its exact definition before the transaction commits.
    """

    trigger = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='canonical_route_sync_ad'"
    ).fetchone()
    trigger_sql = trigger[0] if trigger is not None else None
    if trigger_sql:
        connection.execute("DROP TRIGGER canonical_route_sync_ad")
    try:
        for object_type, object_ids in object_pairs:
            if object_ids:
                connection.execute(
                    "DELETE FROM canonical_route WHERE object_type=? AND object_id IN ("
                    + _placeholders(object_ids)
                    + ")",
                    (object_type, *object_ids),
                )
    finally:
        if trigger_sql:
            connection.execute(trigger_sql)


def _apply_irrelevant_graph_removal(
    connection: sqlite3.Connection,
    *,
    targets: dict[str, list[str]],
) -> dict[str, int]:
    """Delete the bounded graph representation while preserving durable audit history."""

    _assert_removal_dependencies_clear(connection, targets)
    assertion_ids = targets["assertion_ids"]
    counts = {
        "assertion": 0,
        "candidate_assertion": 0,
        "candidate_entity": 0,
        "claim": 0,
        "edge": 0,
        "node": 0,
        "validation_queue": 0,
    }
    object_pairs: list[tuple[str, list[str]]] = [
        ("node", targets["node_ids"]),
        ("claim", targets["claim_ids"]),
        ("assertion", assertion_ids),
    ]
    for object_type, object_ids in object_pairs:
        if object_ids:
            connection.execute(
                "DELETE FROM graph_quality_exception WHERE object_type=? AND object_id IN ("
                + _placeholders(object_ids)
                + ")",
                (object_type, *object_ids),
            )
    counts["validation_queue"] = _delete_by_ids(
        connection, "validation_queue", "validation_id", targets["validation_ids"]
    )
    _delete_by_ids(connection, "legacy_claim_assertion_map", "claim_id", targets["claim_ids"])
    _delete_by_ids(connection, "legacy_edge_assertion_map", "edge_id", targets["edge_ids"])
    counts["assertion"] = _delete_by_ids(connection, "assertion", "assertion_id", assertion_ids)
    for object_type, object_ids in [
        ("claim", targets["claim_ids"]),
        ("assertion", assertion_ids),
    ]:
        if object_ids:
            connection.execute(
                "DELETE FROM search_document WHERE object_type=? AND object_id IN ("
                + _placeholders(object_ids)
                + ")",
                (object_type, *object_ids),
            )
    counts["candidate_assertion"] = _delete_by_ids(
        connection, "candidate_assertion", "candidate_assertion_id", targets["candidate_assertion_ids"]
    )
    counts["candidate_entity"] = _delete_by_ids(
        connection, "candidate_entity", "candidate_entity_id", targets["candidate_entity_ids"]
    )
    counts["edge"] = _delete_by_ids(connection, "edge", "edge_id", targets["edge_ids"])
    counts["claim"] = _delete_by_ids(connection, "claim", "claim_id", targets["claim_ids"])
    _delete_canonical_routes(connection, object_pairs)
    if targets["node_ids"]:
        node_ids = targets["node_ids"]
        canonical_alias_rows = connection.execute(
            "SELECT canonical_alias_id FROM canonical_alias WHERE entity_id IN ("
            + _placeholders(node_ids)
            + ")",
            tuple(node_ids),
        ).fetchall()
        canonical_alias_ids = _ids(canonical_alias_rows, "canonical_alias_id")
        alias_rows = connection.execute(
            "SELECT alias_id FROM alias WHERE node_id IN ("
            + _placeholders(node_ids)
            + ")",
            tuple(node_ids),
        ).fetchall()
        alias_ids = _ids(alias_rows, "alias_id")
        _delete_by_ids(connection, "legacy_alias_canonical_map", "alias_id", alias_ids)
        _delete_by_ids(connection, "canonical_alias", "canonical_alias_id", canonical_alias_ids)
        _delete_by_ids(connection, "alias", "alias_id", alias_ids)
        connection.execute(
            "DELETE FROM entity_resolution_candidate_member WHERE node_id IN ("
            + _placeholders(node_ids)
            + ")",
            tuple(node_ids),
        )
        connection.execute(
            "DELETE FROM node_card_map WHERE node_id IN ("
            + _placeholders(node_ids)
            + ")",
            tuple(node_ids),
        )
        _delete_by_ids(connection, "node_state", "node_id", node_ids)
        counts["node"] = _delete_by_ids(connection, "node", "node_id", node_ids)
        connection.execute(
            "DELETE FROM search_document WHERE object_type='node' AND object_id IN ("
            + _placeholders(node_ids)
            + ")",
            tuple(node_ids),
        )
    return counts


def apply_validation_closeout_plan(
    graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Close the validation and retain the linked claim as evidence only."""

    _assert_plan_digest(plan)
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("validation closeout plan is stale")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path)
    connection.row_factory = sqlite3.Row
    now = utc_now()
    receipts: list[dict[str, Any]] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for operation in plan["operations"]:
            if operation["operation"] == "record_mapping_confirmation_authority":
                approval = operation.get("approval")
                if not isinstance(approval, dict):
                    raise ValueError("mapping confirmation authority is missing")
                row = connection.execute(
                    "SELECT * FROM governance_approval WHERE approval_reference=?",
                    (operation["target_id"],),
                ).fetchone()
                if _row_hash(row) != operation["before_state_hash"]:
                    raise RuntimeError("mapping confirmation authority changed before release")
                connection.execute(
                    """INSERT INTO governance_approval (
                           approval_id,approval_reference,approval_type,scope_json,
                           baseline_zip_sha256,baseline_graph_sha256,proposal_sha256,
                           decision_record_sha256,card_proposal_sha256,gate_o1_sha256,
                           approval_statement_sha256,approved_at,expires_at,status,
                           approved_by,release_version,active,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        approval["approval_id"],
                        approval["approval_reference"],
                        approval["approval_type"],
                        approval["scope_json"],
                        approval["baseline_zip_sha256"],
                        approval["baseline_graph_sha256"],
                        approval["proposal_sha256"],
                        approval["decision_record_sha256"],
                        approval["card_proposal_sha256"],
                        approval["gate_o1_sha256"],
                        approval["approval_statement_sha256"],
                        now,
                        approval["expires_at"],
                        approval["status"],
                        approval["approved_by"],
                        approval["release_version"],
                        approval["active"],
                        now,
                    ),
                )
                after = connection.execute(
                    "SELECT * FROM governance_approval WHERE approval_reference=?",
                    (operation["target_id"],),
                ).fetchone()
                source_id = operation["source_id"]
                previous_value = None
                new_value = dict(after)
                record_type = "governance_approval"
            elif operation["operation"] == "close_validation_item":
                row = connection.execute(
                    """SELECT validation_id,node_id,edge_id,claim_id,validation_question,
                              owner,priority,due_date,status,resolution,resolved_at,created_at
                         FROM validation_queue WHERE validation_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_queue_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("validation item changed before closeout")
                connection.execute(
                    """UPDATE validation_queue
                          SET status='closed',resolution=?,resolved_at=?
                        WHERE validation_id=? AND status='open'""",
                    (operation["resolution"], now, operation["target_id"]),
                )
                after = connection.execute(
                    """SELECT validation_id,node_id,edge_id,claim_id,validation_question,
                              owner,priority,due_date,status,resolution,resolved_at,created_at
                         FROM validation_queue WHERE validation_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_row = connection.execute(
                    "SELECT source_id FROM claim WHERE claim_id=?", (row["claim_id"],)
                ).fetchone()
                source_id = source_row["source_id"] if source_row else None
                previous_value = _queue_state(row)
                new_value = _queue_state(after)
                record_type = "validation_queue"
            elif operation["operation"] in {
                "retain_claim_as_evidence_only",
                "resolve_candidate_context_as_evidence_only",
            }:
                row = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              primary_source_id,qualifiers_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_assertion_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("linked assertion changed before closeout")
                qualifiers = _closeout_qualifiers(
                    row["qualifiers_json"], resolution=operation["resolution"], closed_at=now
                )
                if operation["operation"] == "resolve_candidate_context_as_evidence_only":
                    qualifiers = _confirmation_qualifiers(
                        qualifiers,
                        resolution=operation["resolution"],
                        confirmed_at=now,
                        approval_reference=next(
                            item["approval"]["approval_reference"]
                            for item in plan["operations"]
                            if item["operation"] == "record_mapping_confirmation_authority"
                        ),
                    )
                    parsed = json.loads(qualifiers)
                    parsed["nee_validation_closeout"]["disposition"] = "confirmed_mapping_context"
                    qualifiers = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
                connection.execute(
                    """UPDATE assertion
                          SET canonical_status='evidence_only',validation_state='unreviewed',
                              canonical_eligible=0,retrieval_eligible=0,qualifiers_json=?,updated_at=?
                        WHERE assertion_id=?""",
                    (qualifiers, now, operation["target_id"]),
                )
                after = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              primary_source_id,qualifiers_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["primary_source_id"]
                previous_value = _assertion_state(row)
                new_value = _assertion_state(after)
                record_type = "assertion"
            elif operation["operation"] == "confirm_legacy_edge":
                row = connection.execute(
                    """SELECT edge_id,description,evidence_posture,confidence,validation_priority,
                              validation_owner,hitl_required,source_id
                         FROM edge WHERE edge_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_confirmation_edge_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("mapping edge changed before confirmation")
                connection.execute(
                    """UPDATE edge
                          SET description=?,
                              evidence_posture=?,
                              confidence='high',
                              validation_priority=NULL,
                              validation_owner=NULL,
                              hitl_required=0,
                              updated_at=?
                        WHERE edge_id=?""",
                    (
                        _confirmed_edge_description(row["description"]),
                        "source-attributed roster field alignment confirmed by approved human HITL; "
                        "formal RACI and decision rights are not inferred",
                        now,
                        operation["target_id"],
                    ),
                )
                after = connection.execute(
                    """SELECT edge_id,description,evidence_posture,confidence,validation_priority,
                              validation_owner,hitl_required,source_id
                         FROM edge WHERE edge_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["source_id"]
                previous_value = _confirmation_edge_state(row)
                new_value = _confirmation_edge_state(after)
                record_type = "edge"
            elif operation["operation"] == "accept_confirmed_mapping_assertion":
                row = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              confidence_label,confidence_score,hitl_required,
                              validation_priority,validation_owner,approval_reference,
                              primary_source_id,qualifiers_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_confirmation_assertion_state(row)) != operation[
                    "before_state_hash"
                ]:
                    raise RuntimeError("mapping assertion changed before confirmation")
                qualifiers = _confirmation_qualifiers(
                    row["qualifiers_json"],
                    resolution=operation["resolution"],
                    confirmed_at=now,
                    approval_reference=operation["approval_reference"],
                )
                connection.execute(
                    """UPDATE assertion
                          SET validation_state='accepted_legacy',
                              confidence_label='high',
                              confidence_score=0.9,
                              hitl_required=0,
                              validation_priority=NULL,
                              validation_owner=NULL,
                              approval_reference=?,
                              qualifiers_json=?,
                              updated_at=?
                        WHERE assertion_id=?""",
                    (
                        operation["approval_reference"],
                        qualifiers,
                        now,
                        operation["target_id"],
                    ),
                )
                after = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              confidence_label,confidence_score,hitl_required,
                              validation_priority,validation_owner,approval_reference,
                              primary_source_id,qualifiers_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["primary_source_id"]
                previous_value = _confirmation_assertion_state(row)
                new_value = _confirmation_assertion_state(after)
                record_type = "assertion"
            elif operation["operation"] in {
                "confirm_node_canonical_name",
                "retire_resolved_open_question_node",
            }:
                row = connection.execute(
                    """SELECT node_id,node_type,canonical_name,display_name,description,status,active,
                              created_at,updated_at
                         FROM node WHERE node_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name node changed before confirmation")
                if operation["operation"] == "confirm_node_canonical_name":
                    status = f"Official/current name confirmed as {operation['confirmed_name']}."
                    connection.execute(
                        """UPDATE node
                              SET canonical_name=?,display_name=?,status=?,updated_at=?
                            WHERE node_id=?""",
                        (
                            operation["confirmed_name"],
                            operation["confirmed_name"],
                            status,
                            now,
                            operation["target_id"],
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE node
                              SET status=?,active=0,updated_at=?
                            WHERE node_id=?""",
                        (
                            f"Resolved: official/current name confirmed as {operation['confirmed_name']}.",
                            now,
                            operation["target_id"],
                        ),
                    )
                after = connection.execute(
                    """SELECT node_id,node_type,canonical_name,display_name,description,status,active,
                              created_at,updated_at
                         FROM node WHERE node_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = None
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "node"
            elif operation["operation"] == "resolve_name_question_edge":
                row = connection.execute(
                    """SELECT edge_id,from_node_id,edge_type,to_node_id,description,evidence_posture,
                              confidence,materiality,validation_priority,validation_owner,hitl_required,
                              source_id,created_at,updated_at,superseded_by
                         FROM edge WHERE edge_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name edge changed before confirmation")
                connection.execute(
                    """UPDATE edge
                          SET description=?,evidence_posture=?,confidence='high',
                              validation_priority=NULL,validation_owner=NULL,hitl_required=0,
                              updated_at=?
                        WHERE edge_id=?""",
                    (
                        f"Historical open-name question resolved by approved HITL: official/current name is {operation['confirmed_name']}.",
                        "historical name-question evidence; resolved by approved human HITL",
                        now,
                        operation["target_id"],
                    ),
                )
                after = connection.execute(
                    """SELECT edge_id,from_node_id,edge_type,to_node_id,description,evidence_posture,
                              confidence,materiality,validation_priority,validation_owner,hitl_required,
                              source_id,created_at,updated_at,superseded_by
                         FROM edge WHERE edge_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["source_id"]
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "edge"
            elif operation["operation"] == "resolve_name_question_claim":
                row = connection.execute(
                    """SELECT claim_id,subject_node_id,claim_text,evidence_posture,confidence,materiality,
                              validation_priority,validation_owner,hitl_required,source_id,card_id,
                              created_at,updated_at,superseded_by
                         FROM claim WHERE claim_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name claim changed before confirmation")
                connection.execute(
                    """UPDATE claim
                          SET claim_text=?,evidence_posture=?,confidence='high',
                              validation_priority=NULL,validation_owner=NULL,hitl_required=0,
                              updated_at=?
                        WHERE claim_id=?""",
                    (
                        f"Historical pre-confirmation question: official/current name was TBD; approved HITL confirms {operation['confirmed_name']}.",
                        "historical name-question evidence; resolved by approved human HITL",
                        now,
                        operation["target_id"],
                    ),
                )
                after = connection.execute(
                    """SELECT claim_id,subject_node_id,claim_text,evidence_posture,confidence,materiality,
                              validation_priority,validation_owner,hitl_required,source_id,card_id,
                              created_at,updated_at,superseded_by
                         FROM claim WHERE claim_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["source_id"]
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "claim"
            elif operation["operation"] == "retire_name_question_assertion":
                row = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              confidence_label,confidence_score,hitl_required,
                              validation_priority,validation_owner,approval_reference,
                              primary_source_id,qualifiers_json,legacy_record_type,
                              legacy_record_ids_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name assertion changed before confirmation")
                qualifiers = _confirmation_qualifiers(
                    row["qualifiers_json"],
                    resolution=operation["resolution"],
                    confirmed_at=now,
                    approval_reference=operation["approval_reference"],
                    disposition="confirmed_canonical_name",
                )
                connection.execute(
                    """UPDATE assertion
                          SET canonical_status='evidence_only',canonical_eligible=0,
                              retrieval_eligible=0,validation_state='unreviewed',
                              hitl_required=0,validation_priority=NULL,validation_owner=NULL,
                              approval_reference=?,qualifiers_json=?,updated_at=?
                        WHERE assertion_id=?""",
                    (
                        operation["approval_reference"],
                        qualifiers,
                        now,
                        operation["target_id"],
                    ),
                )
                after = connection.execute(
                    """SELECT assertion_id,canonical_status,currentness_state,
                              canonical_eligible,retrieval_eligible,validation_state,
                              confidence_label,confidence_score,hitl_required,
                              validation_priority,validation_owner,approval_reference,
                              primary_source_id,qualifiers_json,legacy_record_type,
                              legacy_record_ids_json
                         FROM assertion WHERE assertion_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["primary_source_id"]
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "assertion"
            elif operation["operation"] == "unprefer_canonical_alias":
                row = connection.execute(
                    """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                              source_id,confidence_label,confidence_score,valid_from,valid_to,
                              ambiguity_state,preferred_flag,review_state,active,created_at
                         FROM canonical_alias WHERE canonical_alias_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("preferred alias changed before name confirmation")
                connection.execute(
                    "UPDATE canonical_alias SET preferred_flag=0 WHERE canonical_alias_id=?",
                    (operation["target_id"],),
                )
                after = connection.execute(
                    """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                              source_id,confidence_label,confidence_score,valid_from,valid_to,
                              ambiguity_state,preferred_flag,review_state,active,created_at
                         FROM canonical_alias WHERE canonical_alias_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["source_id"]
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "canonical_alias"
            elif operation["operation"] == "create_preferred_canonical_alias":
                alias = operation.get("alias")
                if not isinstance(alias, dict):
                    raise ValueError("canonical-name confirmation alias is missing")
                row = connection.execute(
                    "SELECT * FROM canonical_alias WHERE canonical_alias_id=?",
                    (operation["target_id"],),
                ).fetchone()
                if _row_hash(row) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name alias changed before creation")
                connection.execute(
                    """INSERT INTO canonical_alias (
                           canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                           source_id,confidence_label,confidence_score,valid_from,valid_to,
                           ambiguity_state,preferred_flag,review_state,active,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        alias["canonical_alias_id"], alias["normalized_alias"], alias["display_alias"],
                        alias["entity_id"], alias["alias_type"], alias["source_id"],
                        alias["confidence_label"], alias["confidence_score"], alias["valid_from"],
                        alias["valid_to"], alias["ambiguity_state"], alias["preferred_flag"],
                        alias["review_state"], alias["active"], now,
                    ),
                )
                after = connection.execute(
                    "SELECT * FROM canonical_alias WHERE canonical_alias_id=?",
                    (operation["target_id"],),
                ).fetchone()
                source_id = alias["source_id"]
                previous_value = None
                new_value = _row_state(after)
                record_type = "canonical_alias"
            elif operation["operation"] == "confirm_existing_canonical_alias":
                row = connection.execute(
                    """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                              source_id,confidence_label,confidence_score,valid_from,valid_to,
                              ambiguity_state,preferred_flag,review_state,active,created_at
                         FROM canonical_alias WHERE canonical_alias_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_row_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("canonical-name alias changed before confirmation")
                connection.execute(
                    """UPDATE canonical_alias
                          SET display_alias=?,confidence_label='confirmed',confidence_score=1.0,
                              valid_from='2026-07-27',valid_to=NULL,ambiguity_state='unique',
                              preferred_flag=1,review_state='reviewed',active=1
                        WHERE canonical_alias_id=?""",
                    (operation["confirmed_name"], operation["target_id"]),
                )
                after = connection.execute(
                    """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                              source_id,confidence_label,confidence_score,valid_from,valid_to,
                              ambiguity_state,preferred_flag,review_state,active,created_at
                         FROM canonical_alias WHERE canonical_alias_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                source_id = row["source_id"]
                previous_value = _row_state(row)
                new_value = _row_state(after)
                record_type = "canonical_alias"
            elif operation["operation"] == "remove_irrelevant_graph_item":
                row = connection.execute(
                    """SELECT validation_id,node_id,edge_id,claim_id,validation_question,
                              owner,priority,due_date,status,resolution,resolved_at,created_at
                         FROM validation_queue WHERE validation_id=?""",
                    (operation["target_id"],),
                ).fetchone()
                if row is None or digest(_queue_state(row)) != operation["before_state_hash"]:
                    raise RuntimeError("validation item changed before removal")
                targets = operation.get("targets")
                if not isinstance(targets, dict):
                    raise ValueError("irrelevant-item removal plan has no bounded targets")
                current_targets = _removal_targets(connection, row)
                if current_targets != targets:
                    raise RuntimeError("irrelevant-item removal target set changed before release")
                source_row = connection.execute(
                    "SELECT source_id FROM claim WHERE claim_id=?", (row["claim_id"],)
                ).fetchone()
                source_id = source_row["source_id"] if source_row else None
                previous_value = {
                    "queue": _queue_state(row),
                    "targets": targets,
                }
                removed_counts = _apply_irrelevant_graph_removal(connection, targets=targets)
                new_value = {"removed": removed_counts}
                record_type = "graph_removal"
            else:
                raise ValueError("unsupported validation closeout operation")
            audit_id = stable_id(
                "validation-closeout-audit", plan["plan_id"], operation["operation_id"], length=28
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
                    "nee-validation-closeout",
                    operation["operation"],
                    record_type,
                    operation["target_id"],
                    json.dumps(previous_value, sort_keys=True),
                    json.dumps(new_value, sort_keys=True),
                    source_id,
                    now,
                    operation["resolution"],
                    plan["validation_id"],
                    approval_id,
                    run_id,
                    CLOSEOUT_POLICY_VERSION,
                ),
            )
            receipts.append(
                {
                    "operation_id": operation["operation_id"],
                    "change_set_id": plan["plan_id"],
                    "run_id": run_id,
                    "source_id": source_id,
                    "target_id": operation["target_id"],
                    "before_state_hash": operation["before_state_hash"],
                    "after_row_hash": (
                        _row_hash(after)
                        if operation["operation"] != "remove_irrelevant_graph_item"
                        else ZERO_HASH
                    ),
                }
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("validation closeout foreign-key validation failed")
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
        raise RuntimeError("validation closeout integrity or graph-change check failed")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "approval_id": approval_id,
        "transaction_required": True,
        "operation_count": len(receipts),
        "operations": receipts,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }
