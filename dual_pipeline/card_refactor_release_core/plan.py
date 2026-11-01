"""Plan building and validation for card refactor release."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..card_views import build_default_card_view_operations
from ..contracts import create_artifact
from ..core import ZERO_HASH, digest, sha256_file, stable_id
from ..facets import (
    CARDS_REFACTOR_MIGRATION_ID,
    CARDS_REFACTOR_POLICY_VERSION,
    SEMANTIC_DOMAIN_VOCABULARY,
    TOPIC_VOCABULARY,
    build_semantic_domain_operations,
    build_topic_operations,
    facet_schema_checksum,
)
from .constants import (
    PLAN_TYPE,
    _legacy_card_state,
    _operation_counts,
    _route_state,
)


def build_card_refactor_plan(
    graph_path: Path,
    *,
    active_pair: dict[str, Any],
) -> dict[str, Any]:
    """Build an immutable proposal against a freshly verified active pair."""

    graph_path = Path(graph_path).resolve()
    graph_hash = sha256_file(graph_path)
    if graph_hash != active_pair.get("graph_sha256"):
        raise RuntimeError("cards refactor plan must target the current active graph hash")
    if not active_pair.get("generation_id") or not active_pair.get("graph_snapshot"):
        raise ValueError("active pair is missing its generation or graph snapshot")

    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        if any(
            connection.execute(
                """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name=?""",
                (table,),
            ).fetchone()
            for table in (
                "facet_derivation_rule",
                "object_facet_assignment",
                "card_view_definition",
            )
        ):
            raise RuntimeError(
                "cards refactor baseline already contains a partial or completed schema"
            )
        semantic_operations = build_semantic_domain_operations(connection)
        topic_operations, topic_membership = build_topic_operations(connection)
        view_operations = build_default_card_view_operations(
            connection,
            topic_membership=topic_membership,
        )
        route_operations: list[dict[str, Any]] = []
        for row in connection.execute(
            """SELECT route_id,object_type,object_id,card_id,route_role,is_primary,
                      route_status,source_mechanism,intentional_unrouted_reason,
                      active,created_at
                 FROM canonical_route
                WHERE object_type='assertion'
                  AND route_status='intentional_unrouted'
                  AND active=1
                ORDER BY route_id"""
        ):
            before = _route_state(row)
            route_operations.append(
                {
                    "operation": "retire_assertion_intentional_unrouted",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "retire-route",
                        row["route_id"],
                        length=28,
                    ),
                    "route_id": str(row["route_id"]),
                    "assertion_id": str(row["object_id"]),
                    "before_state_hash": digest(before),
                    "retirement_reason": (
                        "Controlled semantic-domain coverage replaces the legacy "
                        "requirement that every assertion declare a primary card."
                    ),
                }
            )
        baseline = {
            "current_canonical_assertions": int(
                connection.execute(
                    """SELECT count(*) FROM assertion
                        WHERE canonical_status='canonical'
                          AND currentness_state='current'"""
                ).fetchone()[0]
            ),
            "active_nodes": int(
                connection.execute(
                    "SELECT count(*) FROM node WHERE active=1"
                ).fetchone()[0]
            ),
            "cards": int(
                connection.execute("SELECT count(*) FROM card").fetchone()[0]
            ),
            "legacy_card_state": _legacy_card_state(connection),
        }
        current_assertion_ids = {
            str(row[0])
            for row in connection.execute(
                """SELECT assertion_id FROM assertion
                    WHERE canonical_status='canonical'
                      AND currentness_state='current'"""
            )
        }
    finally:
        connection.close()

    assignment_operations = [
        operation
        for operation in [*semantic_operations, *topic_operations]
        if operation["operation"] == "assign_object_facet"
    ]
    semantic_domain_counts = Counter(
        operation["assignment"]["value"]
        for operation in assignment_operations
        if operation["assignment"]["vocabulary"] == SEMANTIC_DOMAIN_VOCABULARY
        and operation["assignment"]["object_type"] == "assertion"
        and operation["assignment"]["object_id"] in current_assertion_ids
    )
    topic_approved_ids = {
        operation["assignment"]["object_id"]
        for operation in assignment_operations
        if operation["assignment"]["vocabulary"] == TOPIC_VOCABULARY
        and operation["assignment"]["object_type"] == "assertion"
        and operation["assignment"]["review_state"] == "approved"
        and operation["assignment"]["object_id"] in current_assertion_ids
    }
    topic_review_ids = {
        operation["assignment"]["object_id"]
        for operation in assignment_operations
        if operation["assignment"]["vocabulary"] == TOPIC_VOCABULARY
        and operation["assignment"]["object_type"] == "assertion"
        and operation["assignment"]["review_state"] == "needs_review"
        and operation["assignment"]["object_id"] in current_assertion_ids
    }
    topic_exception_ids = {
        operation["object_id"]
        for operation in topic_operations
        if operation["operation"] == "create_facet_coverage_exception"
        and operation["vocabulary"] == TOPIC_VOCABULARY
        and operation["object_id"] in current_assertion_ids
    }
    topic_satisfied = topic_approved_ids | topic_review_ids | topic_exception_ids
    operations = [
        {
            "operation": "install_card_refactor_schema",
            "operation_id": stable_id(
                "cards-refactor-operation",
                CARDS_REFACTOR_MIGRATION_ID,
                facet_schema_checksum(),
                length=28,
            ),
            "migration_id": CARDS_REFACTOR_MIGRATION_ID,
            "migration_checksum": facet_schema_checksum(),
            "before_state_hash": ZERO_HASH,
        },
        *semantic_operations,
        *topic_operations,
        *view_operations,
        *route_operations,
    ]
    plan: dict[str, Any] = {
        "schema_version": "1.0.0",
        "plan_type": PLAN_TYPE,
        "policy_version": CARDS_REFACTOR_POLICY_VERSION,
        "base_generation_id": str(active_pair["generation_id"]),
        "base_graph_snapshot": str(active_pair["graph_snapshot"]),
        "base_graph_sha256": graph_hash,
        "schema_migration": {
            "migration_id": CARDS_REFACTOR_MIGRATION_ID,
            "migration_checksum": facet_schema_checksum(),
        },
        "approval_requirements": {
            "approval_types": ["release_implementation", "route_change"],
            "scope_plan_id_required": True,
            "scope_actions_required": True,
        },
        "transaction_required": True,
        "rollback_required": True,
        "operations": operations,
        "summary": {
            **baseline,
            "operation_count": len(operations),
            "operation_counts": _operation_counts(operations),
            "topic_membership_count": sum(
                len(assertion_ids)
                for assertion_ids in topic_membership.values()
            ),
            "topic_count": len(topic_membership),
            "current_assertion_semantic_domain_counts": dict(
                sorted(semantic_domain_counts.items())
            ),
            "current_assertion_topic_posture": {
                "approved": len(topic_approved_ids),
                "needs_review_total": len(topic_review_ids),
                "needs_review_only": len(topic_review_ids - topic_approved_ids),
                "exceptions": len(topic_exception_ids),
                "missing": len(current_assertion_ids - topic_satisfied),
            },
            "card_view_states": dict(
                sorted(
                    Counter(
                        operation["card_view"]["view_state"]
                        for operation in view_operations
                    ).items()
                )
            ),
            "assertion_routes_to_retire": len(route_operations),
        },
    }
    plan["plan_id"] = stable_id(
        "cards-refactor-plan",
        plan["base_generation_id"],
        plan["base_graph_sha256"],
        operations,
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan


def assert_card_refactor_plan(plan: dict[str, Any]) -> None:
    supplied = plan.get("plan_digest")
    expected = dict(plan)
    expected.pop("plan_digest", None)
    if not isinstance(supplied, str) or supplied != digest(expected):
        raise RuntimeError("cards refactor plan digest mismatch")
    if plan.get("plan_type") != PLAN_TYPE:
        raise ValueError("unsupported cards refactor plan type")
    if plan.get("policy_version") != CARDS_REFACTOR_POLICY_VERSION:
        raise ValueError("unsupported cards refactor policy version")
    migration = plan.get("schema_migration")
    if migration != {
        "migration_id": CARDS_REFACTOR_MIGRATION_ID,
        "migration_checksum": facet_schema_checksum(),
    }:
        raise RuntimeError("cards refactor migration binding mismatch")
    if not plan.get("transaction_required") or not plan.get("rollback_required"):
        raise RuntimeError("cards refactor plan must require transaction and rollback")
    operation_ids = [
        str(operation.get("operation_id")) for operation in plan.get("operations", [])
    ]
    if not operation_ids or len(operation_ids) != len(set(operation_ids)):
        raise RuntimeError("cards refactor plan operation IDs are empty or duplicated")


def semantic_organization_change_set_artifact(
    plan: dict[str, Any],
    *,
    run_id: str | None = None,
    trace_id_value: str | None = None,
) -> dict[str, Any]:
    """Project the executable plan into the closed governance contract."""

    assert_card_refactor_plan(plan)
    run_id = run_id or stable_id(
        "run-cards-refactor-proposal",
        plan["plan_id"],
        length=28,
    )
    trace_id_value = trace_id_value or digest(
        ["trace-cards-refactor-proposal", plan["plan_id"], run_id]
    )[:32]
    projected: list[dict[str, Any]] = []
    policy_bindings: dict[str, str] = {}
    sequence = 0
    excluded_administrative_operations: list[str] = []
    for operation in plan["operations"]:
        action = operation["operation"]
        target_type: str
        target_id: str
        rationale: str
        payload: dict[str, Any]
        if action == "register_controlled_facet_value":
            target_type = "controlled_value"
            target_id = f"{operation['vocabulary']}:{operation['value']}"
            rationale = operation["definition"]
            payload = {
                "operation": action,
                "vocabulary": operation["vocabulary"],
                "value": operation["value"],
                "definition": operation["definition"],
                "sort_order": operation["sort_order"],
            }
        elif action == "register_facet_derivation_rule":
            target_type = "facet_derivation_rule"
            target_id = operation["derivation_rule_id"]
            rationale = "Register the exact versioned controlled-facet derivation rule."
            policy_id = stable_id(
                "facet-applicability-policy",
                operation["derivation_rule_id"],
                length=28,
            )
            policy_bindings[policy_id] = operation["applicability_policy_digest"]
            payload = {
                "operation": action,
                "rule_id": operation["derivation_rule_id"],
                "rule_version": operation["rule_version"],
                "rule_digest": operation["rule_digest"],
                "implementation_ref": (
                    "dual_pipeline.facets:"
                    f"{operation['derivation_rule_id']}"
                ),
                "applicability_policy_id": policy_id,
                "applicability_policy_digest": operation[
                    "applicability_policy_digest"
                ],
                "approval_reference": None,
            }
        elif action == "assign_object_facet":
            assignment = operation["assignment"]
            target_type = "facet_assignment"
            target_id = assignment["assignment_id"]
            rationale = assignment["rationale"]
            payload = {
                "operation": action,
                "assignment_id": assignment["assignment_id"],
                "object_type": assignment["object_type"],
                "object_id": assignment["object_id"],
                "vocabulary": assignment["vocabulary"],
                "value": assignment["value"],
                "assignment_method": assignment["assignment_method"],
                "review_state": assignment["review_state"],
                "derivation_rule_id": assignment.get("derivation_rule_id"),
                "derivation_rule_version": assignment.get(
                    "derivation_rule_version"
                ),
                "approval_reference": None,
            }
        elif action == "create_facet_coverage_exception":
            exception_id = stable_id(
                "facet-coverage-exception",
                operation["object_type"],
                operation["object_id"],
                operation["vocabulary"],
                length=28,
            )
            target_type = "facet_coverage_exception"
            target_id = exception_id
            rationale = operation["reason"]
            payload = {
                "operation": action,
                "exception_id": exception_id,
                "object_type": operation["object_type"],
                "object_id": operation["object_id"],
                "vocabulary": operation["vocabulary"],
                "reason": operation["reason"],
                "owner": operation["owner"],
                "review_due_date": operation["review_due_date"],
                "approval_reference": None,
            }
        elif action == "add_card_view_definition":
            view = operation["card_view"]
            target_type = "card_view_definition"
            target_id = view["card_view_id"]
            rationale = "Create an immutable access-sliced saved semantic view."
            payload = {
                "operation": "upsert_card_view_definition",
                "card_view_id": view["card_view_id"],
                "card_id": view["card_id"],
                "view_version": view["view_version"],
                "selector_id": stable_id(
                    "card-view-selector",
                    view["card_view_id"],
                    view["selector_digest"],
                    length=28,
                ),
                "selector_digest": view["selector_digest"],
                "authority_mode": view["authority_mode"],
                "classification": (
                    "public"
                    if view["classification_ceiling"] == "external_approved"
                    else view["classification_ceiling"]
                ),
                "audiences": view["eligible_audiences"],
                "editorial_owner": view["editorial_owner"],
                "view_state": view["view_state"],
                "approval_reference": None,
            }
        elif action == "retire_assertion_intentional_unrouted":
            target_type = "canonical_route"
            target_id = operation["route_id"]
            rationale = operation["retirement_reason"]
            payload = {
                "operation": "retire_assertion_card_route",
                "route_id": operation["route_id"],
                "object_type": "assertion",
                "assertion_id": operation["assertion_id"],
                "retirement_reason": operation["retirement_reason"],
            }
        else:
            excluded_administrative_operations.append(operation["operation_id"])
            continue
        sequence += 1
        projected.append(
            {
                "operation_id": operation["operation_id"],
                "sequence": sequence,
                "target_type": target_type,
                "target_id": target_id,
                "before_state_hash": operation["before_state_hash"],
                "after_state_hash": digest(payload),
                "rationale": rationale,
                "payload": payload,
            }
        )
    return create_artifact(
        "semantic-organization-change-set",
        plan["plan_id"],
        change_set_id=plan["plan_id"],
        run_id=run_id,
        trace_id=trace_id_value,
        status="proposed",
        base_graph_snapshot=plan["base_graph_snapshot"],
        base_graph_hash=plan["base_graph_sha256"],
        schema_migration=plan["schema_migration"],
        policy_bindings=[
            {"policy_id": policy_id, "policy_digest": policy_digest}
            for policy_id, policy_digest in sorted(policy_bindings.items())
        ],
        approval_id=None,
        operations=projected,
        transaction_required=True,
        rollback_required=True,
        extensions={
            "com.partner_advisory.nee.executable-plan-digest": plan["plan_digest"],
            "com.partner_advisory.nee.administrative-operation-digest": digest(
                excluded_administrative_operations
            ),
        },
    )


