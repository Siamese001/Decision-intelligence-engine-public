"""Semantic domain operations and node type mappings."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..core import digest, stable_id
from .constants import (
    CARDS_REFACTOR_POLICY_VERSION,
    FACET_RULE_VERSION,
    SEMANTIC_DOMAIN_BY_NODE_TYPE,
    SEMANTIC_DOMAIN_RULE_ID,
    SEMANTIC_DOMAIN_VOCABULARY,
    SEMANTIC_DOMAINS,
)


def semantic_domain_for_node_type(node_type: str) -> str | None:
    return SEMANTIC_DOMAIN_BY_NODE_TYPE.get(node_type)


def _assignment_payload(
    *,
    object_type: str,
    object_id: str,
    value: str,
    assignment_method: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "assignment_id": stable_id(
            "object-facet-assignment",
            object_type,
            object_id,
            SEMANTIC_DOMAIN_VOCABULARY,
            value,
            length=28,
        ),
        "object_type": object_type,
        "object_id": object_id,
        "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
        "value": value,
        "assignment_method": assignment_method,
        "review_state": "approved",
        "derivation_rule_id": SEMANTIC_DOMAIN_RULE_ID,
        "derivation_rule_version": FACET_RULE_VERSION,
        "rationale": rationale,
    }


def build_semantic_domain_operations(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Build deterministic facet operations without writing graph state."""

    operations: list[dict[str, Any]] = []
    rule_definition = {
        "policy_version": CARDS_REFACTOR_POLICY_VERSION,
        "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
        "node_type_to_value": dict(sorted(SEMANTIC_DOMAIN_BY_NODE_TYPE.items())),
        "assertion_inheritance": "subject_node",
    }
    applicability_policy = {
        "object_types": ["node", "assertion"],
        "required_for": {
            "node": "active=1",
            "assertion": "canonical_status=canonical AND currentness_state=current",
        },
        "exception_policy": "active owner-bound exception with review date",
    }
    operations.append(
        {
            "operation": "register_facet_derivation_rule",
            "operation_id": stable_id(
                "cards-refactor-operation",
                SEMANTIC_DOMAIN_RULE_ID,
                length=28,
            ),
            "derivation_rule_id": SEMANTIC_DOMAIN_RULE_ID,
            "rule_version": FACET_RULE_VERSION,
            "policy_version": CARDS_REFACTOR_POLICY_VERSION,
            "rule_definition": rule_definition,
            "rule_digest": digest(rule_definition),
            "applicability_policy_digest": digest(applicability_policy),
            "before_state_hash": "0" * 64,
        }
    )
    for object_type in ("assertion", "node"):
        property_payload = {
            "property_name": SEMANTIC_DOMAIN_VOCABULARY,
            "applies_to": object_type,
            "data_type": "controlled_text_many",
            "required_flag": 0,
            "controlled_vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
            "validation_rule": "active controlled value and approved derivation rule",
            "temporal_meaning": "current structural classification",
            "searchable_flag": 1,
            "retrieval_use": "facet filtering only; never assertion evidence",
        }
        existing_property = connection.execute(
            """SELECT property_name,applies_to,data_type,required_flag,
                      controlled_vocabulary,validation_rule,temporal_meaning,
                      searchable_flag,retrieval_use
                 FROM property_registry
                WHERE property_name=? AND applies_to=?""",
            (SEMANTIC_DOMAIN_VOCABULARY, object_type),
        ).fetchone()
        if existing_property is None:
            operations.append(
                {
                    "operation": "register_facet_property",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "facet-property",
                        object_type,
                        SEMANTIC_DOMAIN_VOCABULARY,
                        length=28,
                    ),
                    **property_payload,
                    "before_state_hash": "0" * 64,
                }
            )
        elif dict(existing_property) != property_payload:
            raise RuntimeError(
                "semantic-domain property registry conflicts with cards policy: "
                f"{object_type}"
            )
    for sort_order, (value, definition) in enumerate(
        sorted(SEMANTIC_DOMAINS.items()), start=100
    ):
        existing = connection.execute(
            """SELECT definition,active,sort_order
                 FROM controlled_value_registry
                WHERE vocabulary=? AND value=?""",
            (SEMANTIC_DOMAIN_VOCABULARY, value),
        ).fetchone()
        if existing is None:
            operations.append(
                {
                    "operation": "register_controlled_facet_value",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "controlled-value",
                        value,
                        length=28,
                    ),
                    "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
                    "value": value,
                    "definition": definition,
                    "sort_order": sort_order,
                    "before_state_hash": "0" * 64,
                }
            )
        elif tuple(existing) != (definition, 1, sort_order):
            raise RuntimeError(
                f"semantic-domain controlled value conflicts with policy: {value}"
            )

    node_domains: dict[str, tuple[str, str]] = {}
    for row in connection.execute(
        """SELECT node_id,node_type FROM node WHERE active=1 ORDER BY node_id"""
    ):
        domain = semantic_domain_for_node_type(str(row[1]))
        if domain is None:
            operations.append(
                {
                    "operation": "create_facet_coverage_exception",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "node-exception",
                        str(row[0]),
                        length=28,
                    ),
                    "object_type": "node",
                    "object_id": str(row[0]),
                    "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
                    "reason": f"Node type {row[1]!r} has no approved semantic-domain rule.",
                    "owner": "knowledge-graph-governance",
                    "review_due_date": "2026-08-31",
                    "before_state_hash": "0" * 64,
                }
            )
            continue
        node_domains[str(row[0])] = (str(row[1]), domain)
        payload = _assignment_payload(
            object_type="node",
            object_id=str(row[0]),
            value=domain,
            assignment_method="rule_derived",
            rationale=f"Derived from active node type {row[1]!r}.",
        )
        operations.append(
            {
                "operation": "assign_object_facet",
                "operation_id": stable_id(
                    "cards-refactor-operation",
                    payload["assignment_id"],
                    length=28,
                ),
                "assignment": payload,
                "before_state_hash": "0" * 64,
            }
        )

    for row in connection.execute(
        """SELECT assertion_id,subject_node_id
             FROM assertion
            WHERE canonical_status='canonical'
              AND currentness_state='current'
            ORDER BY assertion_id"""
    ):
        subject_id = str(row[1])
        subject = node_domains.get(subject_id)
        if subject is None:
            operations.append(
                {
                    "operation": "create_facet_coverage_exception",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "assertion-exception",
                        str(row[0]),
                        length=28,
                    ),
                    "object_type": "assertion",
                    "object_id": str(row[0]),
                    "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
                    "reason": (
                        "The assertion subject has no approved semantic-domain "
                        "assignment rule."
                    ),
                    "owner": "knowledge-graph-governance",
                    "review_due_date": "2026-08-31",
                    "before_state_hash": "0" * 64,
                }
            )
            continue
        node_type, domain = subject
        payload = _assignment_payload(
            object_type="assertion",
            object_id=str(row[0]),
            value=domain,
            assignment_method="inherited",
            rationale=(
                f"Inherited from subject node {subject_id!r} with active node "
                f"type {node_type!r}."
            ),
        )
        operations.append(
            {
                "operation": "assign_object_facet",
                "operation_id": stable_id(
                    "cards-refactor-operation",
                    payload["assignment_id"],
                    length=28,
                ),
                "assignment": payload,
                "before_state_hash": "0" * 64,
            }
        )
    return operations

