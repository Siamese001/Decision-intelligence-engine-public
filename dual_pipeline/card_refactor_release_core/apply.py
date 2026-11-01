"""Application of card refactor plan to target SQLite database."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..card_views import validate_card_selector
from ..core import digest, sha256_file, sqlite_integrity, stable_id, utc_now
from ..facets import (
    CARDS_REFACTOR_MIGRATION_ID,
    CARDS_REFACTOR_POLICY_VERSION,
    audit_cards_refactor_coverage,
    install_facet_schema,
    validate_facet_schema,
)
from .constants import _legacy_card_state, _operation_counts, _route_state, _row_hash
from .plan import assert_card_refactor_plan


def _validate_approval_row(
    row: sqlite3.Row,
    *,
    plan: dict[str, Any],
) -> None:
    if row["status"] != "approved" or int(row["active"]) != 1:
        raise PermissionError("cards refactor requires an active approved decision")
    if row["approval_type"] not in {"release_implementation", "route_change"}:
        raise PermissionError("cards refactor approval type is not authorized")
    if row["baseline_graph_sha256"] != plan["base_graph_sha256"]:
        raise PermissionError("cards refactor approval baseline hash mismatch")
    if row["proposal_sha256"] != plan["plan_digest"]:
        raise PermissionError("cards refactor approval proposal digest mismatch")
    now = utc_now()
    if row["expires_at"] is not None and str(row["expires_at"]) < now:
        raise PermissionError("cards refactor approval has expired")
    try:
        scope = json.loads(str(row["scope_json"]))
    except json.JSONDecodeError as exc:
        raise PermissionError("cards refactor approval scope is invalid JSON") from exc
    required_actions = sorted(
        {str(operation["operation"]) for operation in plan["operations"]}
    )
    if (
        not isinstance(scope, dict)
        or scope.get("plan_id") != plan["plan_id"]
        or sorted(scope.get("allowed_actions", [])) != required_actions
    ):
        raise PermissionError("cards refactor approval is not exact-scope bound")


def _resolve_or_stage_approval(
    connection: sqlite3.Connection,
    *,
    approval_id: str,
    plan: dict[str, Any],
    approval_record: dict[str, Any] | None,
    now: str,
) -> sqlite3.Row:
    row = connection.execute(
        """SELECT *
             FROM governance_approval
            WHERE approval_id=? OR approval_reference=?""",
        (approval_id, approval_id),
    ).fetchone()
    if row is None:
        if approval_record is None:
            raise PermissionError(
                "cards refactor approval is absent from the baseline and no exact "
                "external approval record was supplied"
            )
        required = {
            "approval_id",
            "approval_reference",
            "approval_type",
            "scope",
            "baseline_graph_sha256",
            "proposal_sha256",
            "approval_statement_sha256",
            "approved_at",
            "expires_at",
            "status",
            "approved_by",
            "release_version",
            "active",
        }
        if set(approval_record) != required:
            raise PermissionError(
                "external cards refactor approval record has an unexpected shape"
            )
        if (
            approval_record["approval_id"] != approval_id
            or approval_record["status"] != "approved"
            or approval_record["active"] != 1
        ):
            raise PermissionError("external cards refactor approval identity is invalid")
        connection.execute(
            """INSERT INTO governance_approval
               (approval_id,approval_reference,approval_type,scope_json,
                baseline_zip_sha256,baseline_graph_sha256,proposal_sha256,
                decision_record_sha256,card_proposal_sha256,gate_o1_sha256,
                approval_statement_sha256,approved_at,expires_at,status,
                approved_by,release_version,active,created_at)
               VALUES (?,?,?,?,NULL,?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?)""",
            (
                approval_record["approval_id"],
                approval_record["approval_reference"],
                approval_record["approval_type"],
                json.dumps(
                    approval_record["scope"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                approval_record["baseline_graph_sha256"],
                approval_record["proposal_sha256"],
                approval_record["approval_statement_sha256"],
                approval_record["approved_at"],
                approval_record["expires_at"],
                approval_record["status"],
                approval_record["approved_by"],
                approval_record["release_version"],
                approval_record["active"],
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM governance_approval WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("external cards refactor approval was not staged")
    elif approval_record is not None:
        raise PermissionError(
            "external approval was supplied but the baseline already contains its identity"
        )
    _validate_approval_row(row, plan=plan)
    return row


def _insert_audit_event(
    connection: sqlite3.Connection,
    *,
    plan: dict[str, Any],
    operation: dict[str, Any],
    approval_reference: str,
    run_id: str,
    now: str,
    record_type: str,
    record_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    connection.execute(
        """INSERT INTO audit_event
           (audit_event_id,actor_or_process,action,record_type,record_id,
            previous_value_json,new_value_json,source_id,event_timestamp,reason,
            validation_reference,approval_reference,migration_or_ingestion_run_id,
            release_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            stable_id(
                "cards-refactor-audit",
                plan["plan_id"],
                operation["operation_id"],
                length=28,
            ),
            "nee-cards-refactor",
            operation["operation"],
            record_type,
            record_id,
            None if before is None else json.dumps(before, sort_keys=True),
            json.dumps(after, sort_keys=True),
            None,
            now,
            operation.get(
                "retirement_reason",
                operation.get(
                    "reason",
                    "Apply the approved controlled-facet and card-view policy.",
                ),
            ),
            plan["plan_id"],
            approval_reference,
            run_id,
            CARDS_REFACTOR_POLICY_VERSION,
        ),
    )


def _apply_operation(
    connection: sqlite3.Connection,
    *,
    operation: dict[str, Any],
    approval_reference: str,
    now: str,
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any]]:
    action = operation["operation"]
    before: sqlite3.Row | None
    after: sqlite3.Row | None
    if action == "install_card_refactor_schema":
        before = connection.execute(
            "SELECT * FROM schema_migration WHERE migration_id=?",
            (CARDS_REFACTOR_MIGRATION_ID,),
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError("cards refactor schema baseline changed")
        install_facet_schema(
            connection,
            applied_at=now,
            applied_by="nee-cards-refactor",
        )
        validation = validate_facet_schema(connection)
        if validation["status"] != "PASS":
            raise RuntimeError(f"cards refactor schema validation failed: {validation}")
        after = connection.execute(
            "SELECT * FROM schema_migration WHERE migration_id=?",
            (CARDS_REFACTOR_MIGRATION_ID,),
        ).fetchone()
        return "schema_migration", CARDS_REFACTOR_MIGRATION_ID, None, dict(after)

    if action == "register_facet_derivation_rule":
        rule_id = operation["derivation_rule_id"]
        rule_version = int(operation["rule_version"])
        before = connection.execute(
            """SELECT * FROM facet_derivation_rule
                WHERE derivation_rule_id=? AND rule_version=?""",
            (rule_id, rule_version),
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"facet rule baseline changed: {rule_id}")
        connection.execute(
            """INSERT INTO facet_derivation_rule
               (derivation_rule_id,rule_version,policy_version,
                rule_definition_json,rule_digest,applicability_policy_digest,
                approval_reference,active,created_at,retired_at)
               VALUES (?,?,?,?,?,?,?,1,?,NULL)""",
            (
                rule_id,
                rule_version,
                operation["policy_version"],
                json.dumps(
                    operation["rule_definition"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                operation["rule_digest"],
                operation["applicability_policy_digest"],
                approval_reference,
                now,
            ),
        )
        after = connection.execute(
            """SELECT * FROM facet_derivation_rule
                WHERE derivation_rule_id=? AND rule_version=?""",
            (rule_id, rule_version),
        ).fetchone()
        return "facet_derivation_rule", f"{rule_id}:{rule_version}", None, dict(after)

    if action == "register_facet_property":
        key = (operation["property_name"], operation["applies_to"])
        before = connection.execute(
            """SELECT * FROM property_registry
                WHERE property_name=? AND applies_to=?""",
            key,
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"facet property baseline changed: {key}")
        connection.execute(
            """INSERT INTO property_registry
               (property_name,applies_to,data_type,required_flag,
                controlled_vocabulary,validation_rule,temporal_meaning,
                searchable_flag,retrieval_use)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                operation["property_name"],
                operation["applies_to"],
                operation["data_type"],
                operation["required_flag"],
                operation["controlled_vocabulary"],
                operation["validation_rule"],
                operation["temporal_meaning"],
                operation["searchable_flag"],
                operation["retrieval_use"],
            ),
        )
        after = connection.execute(
            """SELECT * FROM property_registry
                WHERE property_name=? AND applies_to=?""",
            key,
        ).fetchone()
        return "property_registry", ":".join(key), None, dict(after)

    if action == "register_controlled_facet_value":
        key = (operation["vocabulary"], operation["value"])
        before = connection.execute(
            """SELECT * FROM controlled_value_registry
                WHERE vocabulary=? AND value=?""",
            key,
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"controlled facet value baseline changed: {key}")
        connection.execute(
            """INSERT INTO controlled_value_registry
               (vocabulary,value,definition,active,sort_order)
               VALUES (?,?,?,1,?)""",
            (
                operation["vocabulary"],
                operation["value"],
                operation["definition"],
                operation["sort_order"],
            ),
        )
        after = connection.execute(
            """SELECT * FROM controlled_value_registry
                WHERE vocabulary=? AND value=?""",
            key,
        ).fetchone()
        return "controlled_value_registry", ":".join(key), None, dict(after)

    if action == "assign_object_facet":
        assignment = operation["assignment"]
        assignment_id = assignment["assignment_id"]
        before = connection.execute(
            "SELECT * FROM object_facet_assignment WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"facet assignment baseline changed: {assignment_id}")
        connection.execute(
            """INSERT INTO object_facet_assignment
               (assignment_id,object_type,object_id,vocabulary,value,
                assignment_method,review_state,derivation_rule_id,
                derivation_rule_version,rationale,approval_reference,
                active,created_at,retired_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,NULL)""",
            (
                assignment_id,
                assignment["object_type"],
                assignment["object_id"],
                assignment["vocabulary"],
                assignment["value"],
                assignment["assignment_method"],
                assignment["review_state"],
                assignment.get("derivation_rule_id"),
                assignment.get("derivation_rule_version"),
                assignment["rationale"],
                approval_reference,
                now,
            ),
        )
        after = connection.execute(
            "SELECT * FROM object_facet_assignment WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        return "object_facet_assignment", assignment_id, None, dict(after)

    if action == "create_facet_coverage_exception":
        exception_id = stable_id(
            "facet-coverage-exception",
            operation["object_type"],
            operation["object_id"],
            operation["vocabulary"],
            length=28,
        )
        before = connection.execute(
            "SELECT * FROM facet_coverage_exception WHERE exception_id=?",
            (exception_id,),
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"facet exception baseline changed: {exception_id}")
        connection.execute(
            """INSERT INTO facet_coverage_exception
               (exception_id,object_type,object_id,vocabulary,reason,owner,
                review_due_date,approval_reference,active,created_at,resolved_at)
               VALUES (?,?,?,?,?,?,?,?,1,?,NULL)""",
            (
                exception_id,
                operation["object_type"],
                operation["object_id"],
                operation["vocabulary"],
                operation["reason"],
                operation["owner"],
                operation["review_due_date"],
                approval_reference,
                now,
            ),
        )
        after = connection.execute(
            "SELECT * FROM facet_coverage_exception WHERE exception_id=?",
            (exception_id,),
        ).fetchone()
        return "facet_coverage_exception", exception_id, None, dict(after)

    if action == "add_card_view_definition":
        view = operation["card_view"]
        validate_card_selector(view["selector"])
        if digest(view["selector"]) != view["selector_digest"]:
            raise RuntimeError("card view selector digest mismatch in plan")
        card_view_id = view["card_view_id"]
        before = connection.execute(
            "SELECT * FROM card_view_definition WHERE card_view_id=?",
            (card_view_id,),
        ).fetchone()
        if _row_hash(before) != operation["before_state_hash"]:
            raise RuntimeError(f"card view baseline changed: {card_view_id}")
        active = 1 if view["view_state"] == "active" else 0
        connection.execute(
            """INSERT INTO card_view_definition
               (card_view_id,card_id,view_version,view_kind,
                selector_schema_version,selector_json,selector_digest,
                authority_mode,classification_ceiling,eligible_audiences_json,
                editorial_owner,view_state,approval_reference,active,created_at,
                retired_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                card_view_id,
                view["card_id"],
                view["view_version"],
                view["view_kind"],
                view["selector_schema_version"],
                json.dumps(view["selector"], sort_keys=True, separators=(",", ":")),
                view["selector_digest"],
                view["authority_mode"],
                view["classification_ceiling"],
                json.dumps(
                    view["eligible_audiences"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                view["editorial_owner"],
                view["view_state"],
                approval_reference,
                active,
                now,
            ),
        )
        after = connection.execute(
            "SELECT * FROM card_view_definition WHERE card_view_id=?",
            (card_view_id,),
        ).fetchone()
        return "card_view_definition", card_view_id, None, dict(after)

    if action == "retire_assertion_intentional_unrouted":
        route_id = operation["route_id"]
        before = connection.execute(
            """SELECT route_id,object_type,object_id,card_id,route_role,is_primary,
                      route_status,source_mechanism,intentional_unrouted_reason,
                      active,created_at
                 FROM canonical_route
                WHERE route_id=?""",
            (route_id,),
        ).fetchone()
        if before is None or digest(_route_state(before)) != operation["before_state_hash"]:
            raise RuntimeError(f"assertion route baseline changed: {route_id}")
        if (
            before["object_type"] != "assertion"
            or before["route_status"] != "intentional_unrouted"
            or int(before["active"]) != 1
            or before["object_id"] != operation["assertion_id"]
        ):
            raise RuntimeError(f"assertion route no longer matches retirement policy: {route_id}")
        connection.execute(
            "UPDATE canonical_route SET active=0 WHERE route_id=?",
            (route_id,),
        )
        after = connection.execute(
            """SELECT route_id,object_type,object_id,card_id,route_role,is_primary,
                      route_status,source_mechanism,intentional_unrouted_reason,
                      active,created_at
                 FROM canonical_route
                WHERE route_id=?""",
            (route_id,),
        ).fetchone()
        return "canonical_route", route_id, _route_state(before), _route_state(after)

    raise ValueError(f"unsupported cards refactor operation: {action}")


def apply_card_refactor_plan(
    graph_path: Path,
    *,
    plan: dict[str, Any],
    approval_id: str,
    run_id: str,
    approval_record: dict[str, Any] | None = None,
    fail_after_operations: int | None = None,
) -> dict[str, Any]:
    """Apply an exact approved plan in one rollback-safe SQLite transaction."""

    assert_card_refactor_plan(plan)
    graph_path = Path(graph_path).resolve()
    before_hash = sha256_file(graph_path)
    if before_hash != plan["base_graph_sha256"]:
        raise RuntimeError("cards refactor plan is stale relative to its graph")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path)
    connection.row_factory = sqlite3.Row
    now = utc_now()
    receipts: list[dict[str, Any]] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("BEGIN IMMEDIATE")
        approval = _resolve_or_stage_approval(
            connection,
            approval_id=approval_id,
            plan=plan,
            approval_record=approval_record,
            now=now,
        )
        approval_reference = str(approval["approval_reference"])
        approval_staged = approval_record is not None
        if approval_staged:
            connection.execute(
                """INSERT INTO audit_event
                   (audit_event_id,actor_or_process,action,record_type,record_id,
                    previous_value_json,new_value_json,source_id,event_timestamp,
                    reason,validation_reference,approval_reference,
                    migration_or_ingestion_run_id,release_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stable_id(
                        "cards-refactor-approval-audit",
                        plan["plan_id"],
                        approval_id,
                        length=28,
                    ),
                    "nee-cards-refactor",
                    "register_cards_refactor_exact_scope_approval",
                    "governance_approval",
                    approval_id,
                    None,
                    json.dumps(
                        {
                            "approval_id": approval["approval_id"],
                            "approval_reference": approval_reference,
                            "approval_type": approval["approval_type"],
                            "scope_digest": digest(
                                json.loads(str(approval["scope_json"]))
                            ),
                            "baseline_graph_sha256": approval[
                                "baseline_graph_sha256"
                            ],
                            "proposal_sha256": approval["proposal_sha256"],
                            "status": approval["status"],
                            "active": approval["active"],
                        },
                        sort_keys=True,
                    ),
                    None,
                    now,
                    "Register the externally authorized exact cards-refactor scope.",
                    plan["plan_id"],
                    approval_reference,
                    run_id,
                    CARDS_REFACTOR_POLICY_VERSION,
                ),
            )
        legacy_before = _legacy_card_state(connection)
        coverage_checked = False
        for index, operation in enumerate(plan["operations"], start=1):
            if operation["operation"] == "retire_assertion_intentional_unrouted":
                if not coverage_checked:
                    coverage = audit_cards_refactor_coverage(connection)
                    if coverage["status"] != "PASS":
                        raise RuntimeError(
                            f"replacement facet coverage failed: {coverage}"
                        )
                    coverage_checked = True
            record_type, record_id, previous, current = _apply_operation(
                connection,
                operation=operation,
                approval_reference=approval_reference,
                now=now,
            )
            _insert_audit_event(
                connection,
                plan=plan,
                operation=operation,
                approval_reference=approval_reference,
                run_id=run_id,
                now=now,
                record_type=record_type,
                record_id=record_id,
                before=previous,
                after=current,
            )
            receipts.append(
                {
                    "operation_id": operation["operation_id"],
                    "operation": operation["operation"],
                    "target_type": record_type,
                    "target_id": record_id,
                    "before_state_hash": operation["before_state_hash"],
                    "after_state_hash": digest(current),
                }
            )
            if fail_after_operations is not None and index >= fail_after_operations:
                raise RuntimeError("injected cards refactor failure")

        final_coverage = audit_cards_refactor_coverage(connection)
        if final_coverage["status"] != "PASS":
            raise RuntimeError(f"cards refactor final coverage failed: {final_coverage}")
        if final_coverage["active_assertion_intentional_unrouted"] != 0:
            raise RuntimeError("legacy assertion intentional-unrouted rows remain active")
        legacy_after = _legacy_card_state(connection)
        if legacy_after != legacy_before:
            raise RuntimeError("cards refactor changed legacy claim or node-card behavior")
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_violations:
            raise RuntimeError(
                "cards refactor foreign-key validation failed: "
                f"{len(foreign_key_violations)} violations"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    integrity = sqlite_integrity(graph_path)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        raise RuntimeError("cards refactor graph integrity validation failed")
    after_hash = sha256_file(graph_path)
    if receipts and after_hash == before_hash:
        raise RuntimeError("cards refactor operations did not change the graph hash")
    return {
        "status": "COMMITTED",
        "change_set_id": plan["plan_id"],
        "change_set_digest": plan["plan_digest"],
        "approval_id": approval_id,
        "approval_reference": approval_reference,
        "approval_staged": approval_staged,
        "transaction_required": True,
        "rollback_required": True,
        "operation_count": len(receipts),
        "operation_counts": _operation_counts(plan["operations"]),
        "operations": receipts,
        "coverage": final_coverage,
        "legacy_card_state": legacy_after,
        "graph_sha256_before": before_hash,
        "graph_sha256_after": after_hash,
        "integrity": integrity,
        "committed_at": now,
    }


