"""Validation closeout plan compilation and integrity verification."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    ZERO_HASH,
    digest,
    sha256_file,
    stable_id,
)
from storage.connection import sqlite3

from .constants import (
    _CLOSEOUT_DISPOSITIONS,
    _DISPOSITION_CONFIRM_CANONICAL_NAME,
    _DISPOSITION_CONFIRM_MAPPING,
    _DISPOSITION_REMOVE_FROM_GRAPH,
    _DISPOSITION_RETAIN_EVIDENCE_ONLY,
    CLOSEOUT_POLICY_VERSION,
    _row_state,
)
from .qualifiers import (
    _assertion_state,
    _confirmation_assertion_state,
    _confirmation_edge_state,
    _queue_state,
)
from .targets import (
    _assert_removal_dependencies_clear,
    _canonical_name_confirmation_authority,
    _canonical_name_confirmation_targets,
    _mapping_confirmation_authority,
    _mapping_confirmation_targets,
    _removal_targets,
)


def _assert_plan_digest(plan: dict[str, Any]) -> None:
    expected = dict(plan)
    supplied = expected.pop("plan_digest", None)
    if supplied != digest(expected):
        raise RuntimeError("validation closeout plan digest mismatch")
    if plan.get("plan_type") != "validation_closeout":
        raise ValueError("unsupported closeout plan")
    if plan.get("disposition", _DISPOSITION_RETAIN_EVIDENCE_ONLY) not in _CLOSEOUT_DISPOSITIONS:
        raise ValueError("unsupported closeout disposition")


def build_validation_closeout_plan(
    graph_path: Path,
    *,
    active_pair: dict[str, Any],
    validation_id: str,
    resolution: str,
    disposition: str = _DISPOSITION_RETAIN_EVIDENCE_ONLY,
    confirmed_edge_ids: list[str] | None = None,
    candidate_claim_ids: list[str] | None = None,
    confirmed_name: str | None = None,
    directive_approval_id: str | None = None,
) -> dict[str, Any]:
    """Build a baseline-bound closeout plan for one open validation record."""

    if not validation_id.strip() or not resolution.strip():
        raise ValueError("validation closeout requires an item ID and resolution")
    if disposition not in _CLOSEOUT_DISPOSITIONS:
        raise ValueError("validation closeout disposition is unsupported")
    graph_path = Path(graph_path).resolve()
    graph_sha256 = sha256_file(graph_path)
    if graph_sha256 != active_pair["graph_sha256"]:
        raise RuntimeError("validation closeout plan must target the active graph")
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        queue = connection.execute(
            """SELECT validation_id,node_id,edge_id,claim_id,validation_question,owner,
                      priority,due_date,status,resolution,resolved_at,created_at
                 FROM validation_queue WHERE validation_id=?""",
            (validation_id,),
        ).fetchone()
        if queue is None:
            raise KeyError(f"validation item does not exist: {validation_id}")
        if queue["status"] != "open" and not (
            disposition == _DISPOSITION_REMOVE_FROM_GRAPH
            and queue["status"] in {"closed", "resolved"}
        ):
            raise RuntimeError("validation closeout requires an open item")
        assertions: list[sqlite3.Row] = []
        targets: dict[str, list[str]] | None = None
        mapping_targets: dict[str, Any] | None = None
        mapping_authority: dict[str, Any] | None = None
        name_targets: dict[str, Any] | None = None
        name_authority: dict[str, Any] | None = None
        if disposition == _DISPOSITION_RETAIN_EVIDENCE_ONLY and queue["claim_id"]:
            assertions = connection.execute(
                """SELECT a.assertion_id,a.canonical_status,a.currentness_state,
                          a.canonical_eligible,a.retrieval_eligible,a.validation_state,
                          a.primary_source_id,a.qualifiers_json
                     FROM assertion a
                     JOIN json_each(a.legacy_record_ids_json) legacy
                       ON legacy.value=?
                    WHERE a.legacy_record_type='claim'
                    ORDER BY a.assertion_id""",
                (queue["claim_id"],),
            ).fetchall()
        elif disposition == _DISPOSITION_REMOVE_FROM_GRAPH:
            targets = _removal_targets(connection, queue)
            _assert_removal_dependencies_clear(connection, targets)
        elif disposition == _DISPOSITION_CONFIRM_MAPPING:
            if not directive_approval_id:
                raise ValueError("mapping confirmation requires the bound HITL approval ID")
            mapping_targets = _mapping_confirmation_targets(
                connection,
                queue=queue,
                confirmed_edge_ids=confirmed_edge_ids or [],
                candidate_claim_ids=candidate_claim_ids or [],
            )
            mapping_authority = _mapping_confirmation_authority(
                graph_sha256=graph_sha256,
                validation_id=validation_id,
                resolution=resolution.strip(),
                directive_approval_id=directive_approval_id,
                targets=mapping_targets,
            )
            existing = connection.execute(
                "SELECT 1 FROM governance_approval WHERE approval_reference=?",
                (mapping_authority["approval_reference"],),
            ).fetchone()
            if existing is not None:
                raise RuntimeError("mapping confirmation authority already exists")
        elif disposition == _DISPOSITION_CONFIRM_CANONICAL_NAME:
            if not directive_approval_id:
                raise ValueError("canonical-name confirmation requires the bound HITL approval ID")
            name_targets = _canonical_name_confirmation_targets(
                connection,
                queue=queue,
                confirmed_name=confirmed_name or "",
            )
            name_authority = _canonical_name_confirmation_authority(
                graph_sha256=graph_sha256,
                validation_id=validation_id,
                resolution=resolution.strip(),
                directive_approval_id=directive_approval_id,
                targets=name_targets,
            )
            existing = connection.execute(
                "SELECT 1 FROM governance_approval WHERE approval_reference=?",
                (name_authority["approval_reference"],),
            ).fetchone()
            if existing is not None:
                raise RuntimeError("canonical-name confirmation authority already exists")
    finally:
        connection.close()
    if disposition == _DISPOSITION_REMOVE_FROM_GRAPH:
        assert targets is not None
        operations = [
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "remove", length=28
                ),
                "operation": "remove_irrelevant_graph_item",
                "target_id": validation_id,
                "before_state_hash": digest(_queue_state(queue)),
                "resolution": resolution.strip(),
                "targets": targets,
            }
        ]
    elif disposition == _DISPOSITION_RETAIN_EVIDENCE_ONLY:
        operations = [
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "queue", length=28
                ),
                "operation": "close_validation_item",
                "target_id": validation_id,
                "before_state_hash": digest(_queue_state(queue)),
                "resolution": resolution.strip(),
            }
        ]
        for assertion in assertions:
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        assertion["assertion_id"],
                        length=28,
                    ),
                    "operation": "retain_claim_as_evidence_only",
                    "target_id": assertion["assertion_id"],
                    "before_state_hash": digest(_assertion_state(assertion)),
                    "resolution": resolution.strip(),
                }
            )
    elif disposition == _DISPOSITION_CONFIRM_MAPPING:
        assert mapping_targets is not None
        assert mapping_authority is not None
        operations = [
            {
                "operation_id": stable_id(
                    "validation-closeout",
                    graph_sha256,
                    validation_id,
                    "mapping-authority",
                    length=28,
                ),
                "operation": "record_mapping_confirmation_authority",
                "target_id": mapping_authority["approval_reference"],
                "before_state_hash": ZERO_HASH,
                "approval": mapping_authority,
                "resolution": resolution.strip(),
                "source_id": mapping_targets["source_id"],
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "queue", length=28
                ),
                "operation": "close_validation_item",
                "target_id": validation_id,
                "before_state_hash": digest(_queue_state(queue)),
                "resolution": resolution.strip(),
            },
        ]
        for edge in mapping_targets["edges"]:
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        edge["edge_id"],
                        "confirm",
                        length=28,
                    ),
                    "operation": "confirm_legacy_edge",
                    "target_id": edge["edge_id"],
                    "before_state_hash": digest(_confirmation_edge_state(edge)),
                    "resolution": resolution.strip(),
                }
            )
        for assertion in mapping_targets["assertions"]:
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        assertion["assertion_id"],
                        "accept",
                        length=28,
                    ),
                    "operation": "accept_confirmed_mapping_assertion",
                    "target_id": assertion["assertion_id"],
                    "before_state_hash": digest(_confirmation_assertion_state(assertion)),
                    "approval_reference": mapping_authority["approval_reference"],
                    "resolution": resolution.strip(),
                }
            )
        for assertion in mapping_targets["candidate_assertions"]:
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        assertion["assertion_id"],
                        "context",
                        length=28,
                    ),
                    "operation": "resolve_candidate_context_as_evidence_only",
                    "target_id": assertion["assertion_id"],
                    "before_state_hash": digest(_assertion_state(assertion)),
                    "resolution": resolution.strip(),
                }
            )
    else:
        assert name_targets is not None
        assert name_authority is not None
        operations = [
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "name-authority", length=28
                ),
                "operation": "record_mapping_confirmation_authority",
                "target_id": name_authority["approval_reference"],
                "before_state_hash": ZERO_HASH,
                "approval": name_authority,
                "resolution": resolution.strip(),
                "source_id": name_targets["edge"]["source_id"],
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "queue", length=28
                ),
                "operation": "close_validation_item",
                "target_id": validation_id,
                "before_state_hash": digest(_queue_state(queue)),
                "resolution": resolution.strip(),
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "canonical-name", length=28
                ),
                "operation": "confirm_node_canonical_name",
                "target_id": name_targets["node"]["node_id"],
                "before_state_hash": digest(_row_state(name_targets["node"])),
                "confirmed_name": name_targets["confirmed_name"],
                "resolution": resolution.strip(),
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "open-question", length=28
                ),
                "operation": "retire_resolved_open_question_node",
                "target_id": name_targets["open_question"]["node_id"],
                "before_state_hash": digest(_row_state(name_targets["open_question"])),
                "confirmed_name": name_targets["confirmed_name"],
                "resolution": resolution.strip(),
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "edge", length=28
                ),
                "operation": "resolve_name_question_edge",
                "target_id": name_targets["edge"]["edge_id"],
                "before_state_hash": digest(_row_state(name_targets["edge"])),
                "confirmed_name": name_targets["confirmed_name"],
                "resolution": resolution.strip(),
            },
            {
                "operation_id": stable_id(
                    "validation-closeout", graph_sha256, validation_id, "claim", length=28
                ),
                "operation": "resolve_name_question_claim",
                "target_id": name_targets["claim"]["claim_id"],
                "before_state_hash": digest(_row_state(name_targets["claim"])),
                "confirmed_name": name_targets["confirmed_name"],
                "resolution": resolution.strip(),
            },
        ]
        for assertion_key, operation_name in [
            ("edge_assertion", "retire_name_question_assertion"),
            ("claim_assertion", "retire_name_question_assertion"),
        ]:
            assertion = name_targets[assertion_key]
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        assertion["assertion_id"],
                        "retire",
                        length=28,
                    ),
                    "operation": operation_name,
                    "target_id": assertion["assertion_id"],
                    "before_state_hash": digest(_row_state(assertion)),
                    "approval_reference": name_authority["approval_reference"],
                    "resolution": resolution.strip(),
                }
            )
        for alias in name_targets["aliases"]:
            if alias["preferred_flag"]:
                operations.append(
                    {
                        "operation_id": stable_id(
                            "validation-closeout",
                            graph_sha256,
                            validation_id,
                            alias["canonical_alias_id"],
                            "unprefer",
                            length=28,
                        ),
                        "operation": "unprefer_canonical_alias",
                        "target_id": alias["canonical_alias_id"],
                        "before_state_hash": digest(_row_state(alias)),
                        "resolution": resolution.strip(),
                    }
                )
        matching_alias = name_targets["matching_alias"]
        if matching_alias is None:
            alias_id = stable_id(
                "canonical-alias",
                name_targets["node"]["node_id"],
                name_targets["normalized_name"],
                length=28,
            )
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout", graph_sha256, validation_id, alias_id, "create", length=28
                    ),
                    "operation": "create_preferred_canonical_alias",
                    "target_id": alias_id,
                    "before_state_hash": ZERO_HASH,
                    "alias": {
                        "canonical_alias_id": alias_id,
                        "normalized_alias": name_targets["normalized_name"],
                        "display_alias": name_targets["confirmed_name"],
                        "entity_id": name_targets["node"]["node_id"],
                        "alias_type": "official_name",
                        "source_id": name_targets["edge"]["source_id"],
                        "confidence_label": "confirmed",
                        "confidence_score": 1.0,
                        "valid_from": "2026-07-27",
                        "valid_to": None,
                        "ambiguity_state": "unique",
                        "preferred_flag": 1,
                        "review_state": "reviewed",
                        "active": 1,
                    },
                    "resolution": resolution.strip(),
                }
            )
        else:
            operations.append(
                {
                    "operation_id": stable_id(
                        "validation-closeout",
                        graph_sha256,
                        validation_id,
                        matching_alias["canonical_alias_id"],
                        "prefer",
                        length=28,
                    ),
                    "operation": "confirm_existing_canonical_alias",
                    "target_id": matching_alias["canonical_alias_id"],
                    "before_state_hash": digest(_row_state(matching_alias)),
                    "confirmed_name": name_targets["confirmed_name"],
                    "resolution": resolution.strip(),
                }
            )
    plan = {
        "schema_version": "1.0.0",
        "plan_type": "validation_closeout",
        "policy_version": CLOSEOUT_POLICY_VERSION,
        "base_generation_id": active_pair["generation_id"],
        "base_graph_sha256": graph_sha256,
        "validation_id": validation_id,
        "validation_status_before": queue["status"],
        "disposition": disposition,
        "operations": operations,
        "summary": {
            "validation_items_closed": (
                1
                if disposition in {
                    _DISPOSITION_RETAIN_EVIDENCE_ONLY,
                    _DISPOSITION_CONFIRM_MAPPING,
                    _DISPOSITION_CONFIRM_CANONICAL_NAME,
                }
                else 0
            ),
            "validation_items_removed": 1 if disposition == _DISPOSITION_REMOVE_FROM_GRAPH else 0,
            "linked_assertions_reclassified": (
                len(assertions)
                if disposition == _DISPOSITION_RETAIN_EVIDENCE_ONLY
                else (
                    len(mapping_targets["candidate_assertions"])
                    if mapping_targets is not None
                    else (2 if name_targets is not None else 0)
                )
            ),
            "confirmed_mapping_assertions": (
                len(mapping_targets["assertions"]) if mapping_targets is not None else 0
            ),
            "confirmed_legacy_edges": (
                len(mapping_targets["edges"]) if mapping_targets is not None else 0
            ),
            "canonical_name_confirmed": (
                name_targets["confirmed_name"] if name_targets is not None else None
            ),
            "linked_assertions_removed": (
                len(targets["assertion_ids"])
                if disposition == _DISPOSITION_REMOVE_FROM_GRAPH and targets is not None
                else 0
            ),
            "graph_operation_count": len(operations),
        },
    }
    plan["plan_id"] = stable_id(
        "validation-closeout-plan",
        plan["base_generation_id"],
        validation_id,
        operations,
        length=28,
    )
    plan["plan_digest"] = digest(plan)
    return plan
