"""Topic operations and facet extraction for objects."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from ..core import digest, stable_id
from .constants import (
    CARDS_REFACTOR_POLICY_VERSION,
    FACET_RULE_VERSION,
    TOPIC_ROUTE_RULE_ID,
    TOPIC_VOCABULARY,
)
from .schema import schema_is_installed


def build_topic_operations(
    connection: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Convert existing editorial routes into controlled topic proposals.

    Direct active assertion routes are preferred. Assertions explicitly marked
    intentional-unrouted inherit the active topic routes of their subject node.
    Ambiguous routes remain ``needs_review`` and are not silently promoted.
    """

    operations: list[dict[str, Any]] = []
    cards = {
        str(row["card_id"]): {
            "canonical_topic": str(row["canonical_topic"]),
            "card_name": str(row["card_name"]),
        }
        for row in connection.execute(
            "SELECT card_id,card_name,canonical_topic FROM card ORDER BY card_id"
        )
    }
    rule_definition = {
        "policy_version": CARDS_REFACTOR_POLICY_VERSION,
        "vocabulary": TOPIC_VOCABULARY,
        "direct_route": "assertion canonical_route",
        "inheritance": "intentional-unrouted assertion subject node canonical_route",
        "active_route_review_state": "approved",
        "ambiguous_route_review_state": "needs_review",
    }
    applicability_policy = {
        "object_types": ["node", "assertion"],
        "required_for": {
            "assertion": "canonical_status=canonical AND currentness_state=current",
        },
        "approved_view_posture": "approved assignments only",
        "exception_policy": "review-held assignment or active exception",
    }
    operations.append(
        {
            "operation": "register_facet_derivation_rule",
            "operation_id": stable_id(
                "cards-refactor-operation",
                TOPIC_ROUTE_RULE_ID,
                length=28,
            ),
            "derivation_rule_id": TOPIC_ROUTE_RULE_ID,
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
            "property_name": TOPIC_VOCABULARY,
            "applies_to": object_type,
            "data_type": "controlled_text_many",
            "required_flag": 0,
            "controlled_vocabulary": TOPIC_VOCABULARY,
            "validation_rule": "active card topic and exact route derivation rule",
            "temporal_meaning": "editorial topic membership",
            "searchable_flag": 1,
            "retrieval_use": "card scoping and navigation only; never evidence",
        }
        existing_property = connection.execute(
            """SELECT property_name,applies_to,data_type,required_flag,
                      controlled_vocabulary,validation_rule,temporal_meaning,
                      searchable_flag,retrieval_use
                 FROM property_registry
                WHERE property_name=? AND applies_to=?""",
            (TOPIC_VOCABULARY, object_type),
        ).fetchone()
        if existing_property is None:
            operations.append(
                {
                    "operation": "register_facet_property",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "facet-property",
                        object_type,
                        TOPIC_VOCABULARY,
                        length=28,
                    ),
                    **property_payload,
                    "before_state_hash": "0" * 64,
                }
            )
        elif dict(existing_property) != property_payload:
            raise RuntimeError(
                f"topic property registry conflicts with cards policy: {object_type}"
            )
    topics: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for card_id, card in sorted(cards.items()):
        topics[card["canonical_topic"]].append((card_id, card["card_name"]))
    for sort_order, (topic, topic_cards) in enumerate(
        sorted(topics.items()),
        start=200,
    ):
        card_description = ", ".join(
            f"{card_id}: {card_name}" for card_id, card_name in topic_cards
        )
        definition = f"Editorial topic represented by {card_description}."
        existing = connection.execute(
            """SELECT definition,active,sort_order
                 FROM controlled_value_registry
                WHERE vocabulary=? AND value=?""",
            (TOPIC_VOCABULARY, topic),
        ).fetchone()
        if existing is None:
            operations.append(
                {
                    "operation": "register_controlled_facet_value",
                    "operation_id": stable_id(
                        "cards-refactor-operation",
                        "controlled-topic",
                        topic,
                        length=28,
                    ),
                    "vocabulary": TOPIC_VOCABULARY,
                    "value": topic,
                    "definition": definition,
                    "sort_order": sort_order,
                    "before_state_hash": "0" * 64,
                }
            )
        elif tuple(existing) != (definition, 1, sort_order):
            raise RuntimeError(f"topic controlled value conflicts with policy: {topic}")

    proposals: dict[tuple[str, str, str], dict[str, Any]] = {}

    def propose(
        *,
        object_type: str,
        object_id: str,
        card_id: str,
        route_status: str,
        assignment_method: str,
        rationale: str,
    ) -> None:
        card = cards.get(card_id)
        if card is None:
            raise RuntimeError(f"route references an unknown card: {card_id}")
        review_state = "approved" if route_status == "active" else "needs_review"
        key = (object_type, object_id, card["canonical_topic"])
        candidate = {
            "object_type": object_type,
            "object_id": object_id,
            "vocabulary": TOPIC_VOCABULARY,
            "value": card["canonical_topic"],
            "assignment_method": assignment_method,
            "review_state": review_state,
            "derivation_rule_id": TOPIC_ROUTE_RULE_ID,
            "derivation_rule_version": FACET_RULE_VERSION,
            "rationale": rationale,
        }
        previous = proposals.get(key)
        if previous is None or (
            previous["review_state"] == "needs_review" and review_state == "approved"
        ):
            proposals[key] = candidate

    for row in connection.execute(
        """SELECT object_type,object_id,card_id,route_status
             FROM canonical_route
            WHERE active=1
              AND object_type IN ('node','assertion')
              AND route_status IN ('active','ambiguous')
              AND card_id IS NOT NULL
            ORDER BY object_type,object_id,card_id,route_id"""
    ):
        propose(
            object_type=str(row["object_type"]),
            object_id=str(row["object_id"]),
            card_id=str(row["card_id"]),
            route_status=str(row["route_status"]),
            assignment_method="rule_derived",
            rationale=(
                f"Derived from the object's {row['route_status']} editorial route "
                f"to {row['card_id']}."
            ),
        )

    for row in connection.execute(
        """SELECT DISTINCT ar.object_id AS assertion_id,
                          nr.card_id,nr.route_status
             FROM canonical_route ar
             JOIN assertion a
               ON ar.object_type='assertion'
              AND ar.object_id=a.assertion_id
             JOIN canonical_route nr
               ON nr.object_type='node'
              AND nr.object_id=a.subject_node_id
              AND nr.active=1
              AND nr.route_status IN ('active','ambiguous')
              AND nr.card_id IS NOT NULL
            WHERE ar.active=1
              AND ar.route_status='intentional_unrouted'
            ORDER BY ar.object_id,nr.card_id"""
    ):
        propose(
            object_type="assertion",
            object_id=str(row["assertion_id"]),
            card_id=str(row["card_id"]),
            route_status=str(row["route_status"]),
            assignment_method="inherited",
            rationale=(
                f"Inherited from the subject node's {row['route_status']} "
                f"editorial route to {row['card_id']}."
            ),
        )

    proposed_assertion_ids = {
        object_id
        for object_type, object_id, _value in proposals
        if object_type == "assertion"
    }
    for row in connection.execute(
        """SELECT assertion_id
             FROM assertion
            WHERE canonical_status='canonical'
              AND currentness_state='current'
            ORDER BY assertion_id"""
    ):
        assertion_id = str(row[0])
        if assertion_id in proposed_assertion_ids:
            continue
        operations.append(
            {
                "operation": "create_facet_coverage_exception",
                "operation_id": stable_id(
                    "cards-refactor-operation",
                    "topic-exception",
                    assertion_id,
                    length=28,
                ),
                "object_type": "assertion",
                "object_id": assertion_id,
                "vocabulary": TOPIC_VOCABULARY,
                "reason": (
                    "No direct assertion route or subject-node editorial route "
                    "exists; semantic-domain classification remains authoritative."
                ),
                "owner": "knowledge-graph-governance",
                "review_due_date": "2026-08-31",
                "before_state_hash": "0" * 64,
            }
        )

    approved_topic_membership: defaultdict[str, list[str]] = defaultdict(list)
    for key in sorted(proposals):
        proposal = proposals[key]
        assignment_id = stable_id(
            "object-facet-assignment",
            proposal["object_type"],
            proposal["object_id"],
            proposal["vocabulary"],
            proposal["value"],
            length=28,
        )
        operations.append(
            {
                "operation": "assign_object_facet",
                "operation_id": stable_id(
                    "cards-refactor-operation",
                    assignment_id,
                    length=28,
                ),
                "assignment": {
                    "assignment_id": assignment_id,
                    **proposal,
                },
                "before_state_hash": "0" * 64,
            }
        )
        if (
            proposal["object_type"] == "assertion"
            and proposal["review_state"] == "approved"
        ):
            approved_topic_membership[proposal["value"]].append(
                proposal["object_id"]
            )
    return operations, {
        topic: sorted(set(assertion_ids))
        for topic, assertion_ids in sorted(approved_topic_membership.items())
    }


def facets_for_object(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_id: str,
    approved_only: bool = True,
) -> dict[str, list[str]]:
    if not schema_is_installed(connection):
        return {}
    sql = """
        SELECT vocabulary,value
          FROM object_facet_assignment
         WHERE object_type=? AND object_id=? AND active=1
    """
    parameters: list[Any] = [object_type, object_id]
    if approved_only:
        sql += " AND review_state='approved'"
    sql += " ORDER BY vocabulary,value"
    result: dict[str, list[str]] = {}
    for row in connection.execute(sql, parameters):
        result.setdefault(str(row[0]), []).append(str(row[1]))
    return result

