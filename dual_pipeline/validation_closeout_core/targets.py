"""Target discovery, dependency clearing assertions, and governance authority formulation."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from dual_pipeline.core import digest, stable_id

from .constants import (
    CLOSEOUT_POLICY_VERSION,
    _ids,
    _normalized_alias,
    _placeholders,
)


def _removal_targets(connection: sqlite3.Connection, queue: sqlite3.Row) -> dict[str, list[str]]:
    """Find the bounded legacy and canonical representation of one irrelevant item."""

    node_ids = [queue["node_id"]] if queue["node_id"] else []
    edge_ids = [queue["edge_id"]] if queue["edge_id"] else []
    claim_ids = [queue["claim_id"]] if queue["claim_id"] else []
    if node_ids:
        edge_rows = connection.execute(
            """SELECT edge_id FROM edge
                 WHERE from_node_id=? OR to_node_id=?""",
            (node_ids[0], node_ids[0]),
        ).fetchall()
        claim_rows = connection.execute(
            "SELECT claim_id FROM claim WHERE subject_node_id=?",
            (node_ids[0],),
        ).fetchall()
        edge_ids = sorted(set(edge_ids) | set(_ids(edge_rows, "edge_id")))
        claim_ids = sorted(set(claim_ids) | set(_ids(claim_rows, "claim_id")))

    assertion_clauses: list[str] = []
    assertion_params: list[str] = []
    if node_ids:
        assertion_clauses.append("subject_node_id=? OR object_node_id=?")
        assertion_params.extend([node_ids[0], node_ids[0]])
    if claim_ids:
        assertion_clauses.append(
            """(legacy_record_type='claim' AND EXISTS
                 (SELECT 1 FROM json_each(assertion.legacy_record_ids_json)
                  WHERE value IN ("""
            + _placeholders(claim_ids)
            + ")))"
        )
        assertion_params.extend(claim_ids)
    if edge_ids:
        assertion_clauses.append(
            """(legacy_record_type='edge' AND EXISTS
                 (SELECT 1 FROM json_each(assertion.legacy_record_ids_json)
                  WHERE value IN ("""
            + _placeholders(edge_ids)
            + ")))"
        )
        assertion_params.extend(edge_ids)
    if not assertion_clauses:
        raise RuntimeError("irrelevant-item removal requires a node, edge, or claim target")
    assertion_rows = connection.execute(
        "SELECT assertion_id FROM assertion WHERE " + " OR ".join(assertion_clauses),
        tuple(assertion_params),
    ).fetchall()
    assertion_ids = _ids(assertion_rows, "assertion_id")

    validation_clauses = ["validation_id=?"]
    validation_params: list[str] = [queue["validation_id"]]
    if node_ids:
        validation_clauses.append("node_id=?")
        validation_params.extend(node_ids)
    if edge_ids:
        validation_clauses.append("edge_id IN (" + _placeholders(edge_ids) + ")")
        validation_params.extend(edge_ids)
    if claim_ids:
        validation_clauses.append("claim_id IN (" + _placeholders(claim_ids) + ")")
        validation_params.extend(claim_ids)
    validation_rows = connection.execute(
        "SELECT validation_id FROM validation_queue WHERE " + " OR ".join(validation_clauses),
        tuple(validation_params),
    ).fetchall()

    candidate_entity_ids: list[str] = []
    if node_ids:
        candidate_rows = connection.execute(
            "SELECT candidate_entity_id FROM candidate_entity WHERE matched_node_id=?",
            tuple(node_ids),
        ).fetchall()
        candidate_entity_ids = _ids(candidate_rows, "candidate_entity_id")
    candidate_assertion_clauses: list[str] = []
    candidate_assertion_params: list[str] = []
    if node_ids:
        candidate_assertion_clauses.append("subject_candidate_or_node_id=?")
        candidate_assertion_params.extend(node_ids)
    if candidate_entity_ids:
        candidate_assertion_clauses.append(
            "subject_candidate_or_node_id IN (" + _placeholders(candidate_entity_ids) + ")"
        )
        candidate_assertion_params.extend(candidate_entity_ids)
        candidate_assertion_clauses.append(
            "object_candidate_node_id IN (" + _placeholders(candidate_entity_ids) + ")"
        )
        candidate_assertion_params.extend(candidate_entity_ids)
    candidate_assertion_ids: list[str] = []
    if candidate_assertion_clauses:
        candidate_assertion_rows = connection.execute(
            "SELECT candidate_assertion_id FROM candidate_assertion WHERE "
            + " OR ".join(candidate_assertion_clauses),
            tuple(candidate_assertion_params),
        ).fetchall()
        candidate_assertion_ids = _ids(candidate_assertion_rows, "candidate_assertion_id")

    return {
        "assertion_ids": assertion_ids,
        "candidate_assertion_ids": candidate_assertion_ids,
        "candidate_entity_ids": candidate_entity_ids,
        "claim_ids": claim_ids,
        "edge_ids": edge_ids,
        "node_ids": node_ids,
        "validation_ids": _ids(validation_rows, "validation_id"),
    }


def _assert_removal_dependencies_clear(
    connection: sqlite3.Connection, targets: dict[str, list[str]]
) -> None:
    """Fail before mutation if an irrelevant item is part of an unrelated merge chain."""

    node_ids = targets["node_ids"]
    assertion_ids = targets["assertion_ids"]
    claim_ids = targets["claim_ids"]
    edge_ids = targets["edge_ids"]
    checks: list[tuple[str, str, tuple[str, ...]]] = []
    if node_ids:
        checks.extend(
            [
                (
                    "entity redirect",
                    "SELECT 1 FROM entity_redirect WHERE from_node_id=? OR to_node_id=? LIMIT 1",
                    (node_ids[0], node_ids[0]),
                ),
                (
                    "merge event",
                    "SELECT 1 FROM entity_merge_event WHERE canonical_node_id=? LIMIT 1",
                    tuple(node_ids),
                ),
                (
                    "node type registry",
                    "SELECT 1 FROM node_type_registry WHERE example_node_id=? LIMIT 1",
                    tuple(node_ids),
                ),
            ]
        )
    if assertion_ids:
        checks.append(
            (
                "retained assertion supersession",
                "SELECT 1 FROM assertion WHERE superseded_by_assertion_id IN ("
                + _placeholders(assertion_ids)
                + ") AND assertion_id NOT IN ("
                + _placeholders(assertion_ids)
                + ") LIMIT 1",
                tuple(assertion_ids + assertion_ids),
            )
        )
    if claim_ids:
        checks.append(
            (
                "retained claim supersession",
                "SELECT 1 FROM claim WHERE superseded_by IN ("
                + _placeholders(claim_ids)
                + ") AND claim_id NOT IN ("
                + _placeholders(claim_ids)
                + ") LIMIT 1",
                tuple(claim_ids + claim_ids),
            )
        )
    if edge_ids:
        checks.append(
            (
                "retained edge supersession",
                "SELECT 1 FROM edge WHERE superseded_by IN ("
                + _placeholders(edge_ids)
                + ") AND edge_id NOT IN ("
                + _placeholders(edge_ids)
                + ") LIMIT 1",
                tuple(edge_ids + edge_ids),
            )
        )
    for label, query, params in checks:
        if connection.execute(query, params).fetchone() is not None:
            raise RuntimeError(
                f"irrelevant-item removal is blocked by a retained {label} dependency"
            )


def _mapping_confirmation_targets(
    connection: sqlite3.Connection,
    *,
    queue: sqlite3.Row,
    confirmed_edge_ids: list[str],
    candidate_claim_ids: list[str],
) -> dict[str, Any]:
    """Return only the exact candidate mappings named in a HITL decision.

    A mapping confirmation is intentionally narrower than a generic source-row
    promotion.  Each target edge must come from the validation item's source
    and touch its node; every canonical assertion must explicitly map back to
    that edge.  This stops one answer from promoting adjacent roster facts.
    """

    edge_ids = sorted(set(item.strip() for item in confirmed_edge_ids if item.strip()))
    claim_ids = sorted(set(item.strip() for item in candidate_claim_ids if item.strip()))
    if not edge_ids:
        raise ValueError("mapping confirmation requires at least one exact edge ID")
    if len(edge_ids) != len(confirmed_edge_ids):
        raise ValueError("mapping confirmation edge IDs must be unique and nonblank")
    if len(claim_ids) != len(candidate_claim_ids):
        raise ValueError("mapping confirmation candidate claim IDs must be unique and nonblank")
    if not queue["node_id"] or not queue["claim_id"]:
        raise RuntimeError("mapping confirmation requires a node-bound, claim-bound validation item")
    source_row = connection.execute(
        "SELECT source_id FROM claim WHERE claim_id=?", (queue["claim_id"],)
    ).fetchone()
    if source_row is None:
        raise RuntimeError("mapping confirmation validation claim is missing")
    source_id = source_row["source_id"]
    edge_rows = connection.execute(
        """SELECT edge_id,description,evidence_posture,confidence,validation_priority,
                  validation_owner,hitl_required,source_id
             FROM edge WHERE edge_id IN ("""
        + _placeholders(edge_ids)
        + ") ORDER BY edge_id",
        tuple(edge_ids),
    ).fetchall()
    if _ids(edge_rows, "edge_id") != edge_ids:
        raise RuntimeError("mapping confirmation edge set does not exist exactly as supplied")
    for row in edge_rows:
        endpoint = connection.execute(
            "SELECT 1 FROM edge WHERE edge_id=? AND (from_node_id=? OR to_node_id=?)",
            (row["edge_id"], queue["node_id"], queue["node_id"]),
        ).fetchone()
        if endpoint is None or row["source_id"] != source_id:
            raise RuntimeError("mapping confirmation edge falls outside the validation item scope")
        if not row["hitl_required"]:
            raise RuntimeError("mapping confirmation edge is not awaiting human validation")

    assertion_rows: list[sqlite3.Row] = []
    for edge_id in edge_ids:
        rows = connection.execute(
            """SELECT a.assertion_id,a.canonical_status,a.currentness_state,
                      a.canonical_eligible,a.retrieval_eligible,a.validation_state,
                      a.confidence_label,a.confidence_score,a.hitl_required,
                      a.validation_priority,a.validation_owner,a.approval_reference,
                      a.primary_source_id,a.qualifiers_json
                 FROM assertion a
                 JOIN json_each(a.legacy_record_ids_json) legacy ON legacy.value=?
                WHERE a.legacy_record_type='edge'
                ORDER BY a.assertion_id""",
            (edge_id,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("mapping confirmation edge must resolve to exactly one assertion")
        row = rows[0]
        if (
            row["canonical_status"] != "canonical"
            or row["currentness_state"] != "current"
            or not row["canonical_eligible"]
            or not row["retrieval_eligible"]
            or row["validation_state"] == "accepted_legacy"
        ):
            raise RuntimeError("mapping confirmation assertion is not an eligible pending mapping")
        support = connection.execute(
            """SELECT 1 FROM assertion_evidence
                 WHERE assertion_id=? AND evidence_relationship='supports' LIMIT 1""",
            (row["assertion_id"],),
        ).fetchone()
        if support is None:
            raise RuntimeError("mapping confirmation assertion has no supporting evidence")
        assertion_rows.append(row)

    candidate_assertion_rows: list[sqlite3.Row] = []
    for claim_id in claim_ids:
        claim = connection.execute(
            """SELECT claim_id,subject_node_id,hitl_required
                 FROM claim WHERE claim_id=?""",
            (claim_id,),
        ).fetchone()
        if (
            claim is None
            or claim["subject_node_id"] != queue["node_id"]
            or not claim["hitl_required"]
        ):
            raise RuntimeError("candidate context claim falls outside the validation item scope")
        rows = connection.execute(
            """SELECT a.assertion_id,a.canonical_status,a.currentness_state,
                      a.canonical_eligible,a.retrieval_eligible,a.validation_state,
                      a.primary_source_id,a.qualifiers_json
                 FROM assertion a
                 JOIN json_each(a.legacy_record_ids_json) legacy ON legacy.value=?
                WHERE a.legacy_record_type='claim'
                ORDER BY a.assertion_id""",
            (claim_id,),
        ).fetchall()
        if len(rows) != 1 or rows[0]["canonical_status"] not in {
            "candidate",
            "validation_pending",
        }:
            raise RuntimeError("candidate context claim must resolve to one pending assertion")
        candidate_assertion_rows.append(rows[0])

    return {
        "source_id": source_id,
        "edges": [dict(row) for row in edge_rows],
        "assertions": [dict(row) for row in assertion_rows],
        "candidate_assertions": [dict(row) for row in candidate_assertion_rows],
    }


def _mapping_confirmation_authority(
    *,
    graph_sha256: str,
    validation_id: str,
    resolution: str,
    directive_approval_id: str,
    targets: dict[str, Any],
) -> dict[str, Any]:
    assertion_ids = [item["assertion_id"] for item in targets["assertions"]]
    edge_ids = [item["edge_id"] for item in targets["edges"]]
    scope = {
        "allowed_actions": [
            "canonical_assertion_promotion",
            "validation_updates",
            "legacy_edge_confirmation",
            "release_packaging",
        ],
        "directive_approval_id": directive_approval_id,
        "validation_id": validation_id,
        "assertion_ids": assertion_ids,
        "edge_ids": edge_ids,
        "limitations": (
            "Exact field alignment only. Does not establish formal reporting lines, "
            "decision rights, broader RACI, or an effective date beyond the supplied mapping."
        ),
    }
    decision = {
        "validation_id": validation_id,
        "resolution": resolution,
        "directive_approval_id": directive_approval_id,
        "assertion_ids": assertion_ids,
        "edge_ids": edge_ids,
        "base_graph_sha256": graph_sha256,
    }
    identity = stable_id("hitl-validation-confirmation", decision, length=28)
    return {
        "approval_id": stable_id("governance-approval", identity, length=28),
        "approval_reference": identity.upper(),
        "approval_type": "validation_transition",
        "scope_json": json.dumps(scope, sort_keys=True, separators=(",", ":")),
        "baseline_zip_sha256": None,
        "baseline_graph_sha256": graph_sha256,
        "proposal_sha256": digest({"scope": scope, "decision": decision}),
        "decision_record_sha256": digest(decision),
        "card_proposal_sha256": None,
        "gate_o1_sha256": None,
        "approval_statement_sha256": digest(
            {"directive_approval_id": directive_approval_id, "resolution": resolution}
        ),
        "expires_at": None,
        "status": "approved",
        "approved_by": "Amit Ayer",
        "release_version": CLOSEOUT_POLICY_VERSION,
        "active": 1,
    }


def _canonical_name_confirmation_targets(
    connection: sqlite3.Connection,
    *,
    queue: sqlite3.Row,
    confirmed_name: str,
) -> dict[str, Any]:
    """Bounded targets for resolving one open canonical-name question."""

    name = " ".join(confirmed_name.split())
    if not name:
        raise ValueError("canonical-name confirmation requires a nonblank name")
    if not queue["node_id"] or not queue["edge_id"] or not queue["claim_id"]:
        raise RuntimeError("canonical-name confirmation requires node, edge, and claim targets")
    node = connection.execute(
        """SELECT node_id,node_type,canonical_name,display_name,description,status,active,
                  created_at,updated_at
             FROM node WHERE node_id=?""",
        (queue["node_id"],),
    ).fetchone()
    edge = connection.execute(
        """SELECT edge_id,from_node_id,edge_type,to_node_id,description,evidence_posture,
                  confidence,materiality,validation_priority,validation_owner,hitl_required,
                  source_id,created_at,updated_at,superseded_by
             FROM edge WHERE edge_id=?""",
        (queue["edge_id"],),
    ).fetchone()
    claim = connection.execute(
        """SELECT claim_id,subject_node_id,claim_text,evidence_posture,confidence,materiality,
                  validation_priority,validation_owner,hitl_required,source_id,card_id,
                  created_at,updated_at,superseded_by
             FROM claim WHERE claim_id=?""",
        (queue["claim_id"],),
    ).fetchone()
    if node is None or edge is None or claim is None:
        raise RuntimeError("canonical-name confirmation target is missing")
    if claim["subject_node_id"] != queue["node_id"] or (
        edge["from_node_id"] != queue["node_id"] and edge["to_node_id"] != queue["node_id"]
    ):
        raise RuntimeError("canonical-name confirmation target falls outside the validation scope")
    if not edge["hitl_required"] or not claim["hitl_required"]:
        raise RuntimeError("canonical-name confirmation target is not awaiting human validation")
    open_question_node_id = (
        edge["to_node_id"]
        if edge["from_node_id"] == queue["node_id"]
        else edge["from_node_id"]
    )
    open_question = connection.execute(
        """SELECT node_id,node_type,canonical_name,display_name,description,status,active,
                  created_at,updated_at
             FROM node WHERE node_id=?""",
        (open_question_node_id,),
    ).fetchone()
    if open_question is None or open_question["node_type"] != "open_question":
        raise RuntimeError("canonical-name validation edge must point to an open-question node")

    def linked_assertion(record_type: str, record_id: str) -> sqlite3.Row:
        rows = connection.execute(
            """SELECT a.assertion_id,a.canonical_status,a.currentness_state,
                      a.canonical_eligible,a.retrieval_eligible,a.validation_state,
                      a.confidence_label,a.confidence_score,a.hitl_required,
                      a.validation_priority,a.validation_owner,a.approval_reference,
                      a.primary_source_id,a.qualifiers_json,a.legacy_record_type,
                      a.legacy_record_ids_json
                 FROM assertion a
                 JOIN json_each(a.legacy_record_ids_json) legacy ON legacy.value=?
                WHERE a.legacy_record_type=?
                ORDER BY a.assertion_id""",
            (record_id, record_type),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("canonical-name target must resolve to exactly one assertion")
        return rows[0]

    edge_assertion = linked_assertion("edge", queue["edge_id"])
    claim_assertion = linked_assertion("claim", queue["claim_id"])
    if edge_assertion["canonical_status"] != "canonical" or claim_assertion[
        "canonical_status"
    ] not in {"candidate", "validation_pending"}:
        raise RuntimeError("canonical-name target assertions are not in expected pending states")
    aliases = connection.execute(
        """SELECT canonical_alias_id,normalized_alias,display_alias,entity_id,alias_type,
                  source_id,confidence_label,confidence_score,valid_from,valid_to,
                  ambiguity_state,preferred_flag,review_state,active,created_at
             FROM canonical_alias WHERE entity_id=? ORDER BY canonical_alias_id""",
        (queue["node_id"],),
    ).fetchall()
    normalized = _normalized_alias(name)
    matching = [row for row in aliases if row["normalized_alias"] == normalized]
    if len(matching) > 1:
        raise RuntimeError("canonical-name confirmation has conflicting matching aliases")
    return {
        "confirmed_name": name,
        "normalized_name": normalized,
        "node": dict(node),
        "open_question": dict(open_question),
        "edge": dict(edge),
        "claim": dict(claim),
        "edge_assertion": dict(edge_assertion),
        "claim_assertion": dict(claim_assertion),
        "aliases": [dict(row) for row in aliases],
        "matching_alias": dict(matching[0]) if matching else None,
    }


def _canonical_name_confirmation_authority(
    *,
    graph_sha256: str,
    validation_id: str,
    resolution: str,
    directive_approval_id: str,
    targets: dict[str, Any],
) -> dict[str, Any]:
    scope = {
        "allowed_actions": [
            "canonical_name_confirmation",
            "canonical_alias_confirmation",
            "validation_updates",
            "release_packaging",
        ],
        "directive_approval_id": directive_approval_id,
        "validation_id": validation_id,
        "node_id": targets["node"]["node_id"],
        "confirmed_name": targets["confirmed_name"],
        "limitations": "Exact official-name confirmation only; no scope, commercial, or delivery-status inference.",
    }
    decision = {
        "validation_id": validation_id,
        "resolution": resolution,
        "directive_approval_id": directive_approval_id,
        "node_id": targets["node"]["node_id"],
        "confirmed_name": targets["confirmed_name"],
        "base_graph_sha256": graph_sha256,
    }
    identity = stable_id("hitl-canonical-name-confirmation", decision, length=28)
    return {
        "approval_id": stable_id("governance-approval", identity, length=28),
        "approval_reference": identity.upper(),
        "approval_type": "validation_transition",
        "scope_json": json.dumps(scope, sort_keys=True, separators=(",", ":")),
        "baseline_zip_sha256": None,
        "baseline_graph_sha256": graph_sha256,
        "proposal_sha256": digest({"scope": scope, "decision": decision}),
        "decision_record_sha256": digest(decision),
        "card_proposal_sha256": None,
        "gate_o1_sha256": None,
        "approval_statement_sha256": digest(
            {"directive_approval_id": directive_approval_id, "resolution": resolution}
        ),
        "expires_at": None,
        "status": "approved",
        "approved_by": "Amit Ayer",
        "release_version": CLOSEOUT_POLICY_VERSION,
        "active": 1,
    }
