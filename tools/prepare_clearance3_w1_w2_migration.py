# ruff: noqa: E402, I001
#!/usr/bin/env python
"""Prepare a digest-bound, draft-only W1/W2 migration proposal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, stable_id, write_json_exclusive


CLIENT_AUDIENCES = {"nee-engineering", "client_approved_external"}
RESTRICTED_AUDIENCES = {
    "internal_working_team",
    "partner_advisory_account_leadership",
    "private_personal",
}
DERIVED_TYPES = {"human_interpretation", "recommendation", "hypothesis", "pov"}


def _audiences(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("allowed_audiences_json is not a string array")
    return sorted(set(value))


def _state(row: sqlite3.Row) -> dict[str, Any]:
    fields = (
        "assertion_id",
        "assertion_type",
        "predicate",
        "canonical_status",
        "canonical_eligible",
        "retrieval_eligible",
        "validation_state",
        "currentness_state",
        "classification",
        "allowed_audiences_json",
        "primary_source_id",
        "origin_type",
        "extraction_run_id",
        "atomicity_state",
    )
    return {field: row[field] for field in fields}


def _operation(
    *,
    operation: str,
    record_id: str,
    reason: str,
    before: dict[str, Any],
    after: dict[str, Any],
    apply_ready: bool,
    human_decision_required: bool = False,
    alternatives: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation_id": stable_id("clearance3", operation, record_id, length=32),
        "operation": operation,
        "record_id": record_id,
        "reason": reason,
        "apply_ready": apply_ready,
        "human_decision_required": human_decision_required,
        "before_state": before,
        "before_state_digest": digest(before),
        "proposed_after_state": after,
        "proposed_after_state_digest": digest(after),
    }
    if alternatives:
        result["alternatives"] = alternatives
    return result


def build_plan(graph: Path, contract_path: Path) -> dict[str, Any]:
    graph = graph.resolve()
    contract = json.loads(contract_path.resolve().read_text(encoding="utf-8"))
    connection = create_sqlite_conn(graph, readonly=True)
    try:
        rows = connection.execute(
            """SELECT assertion_id,assertion_type,predicate,canonical_status,
                      canonical_eligible,retrieval_eligible,validation_state,
                      currentness_state,classification,allowed_audiences_json,
                      primary_source_id,origin_type,extraction_run_id,
                      atomicity_state
                 FROM assertion ORDER BY assertion_id"""
        ).fetchall()
    finally:
        connection.close()

    operations: list[dict[str, Any]] = []
    derived_rows = [
        row
        for row in rows
        if row["assertion_type"] in DERIVED_TYPES
        and row["canonical_status"] == "canonical"
        and row["currentness_state"] == "current"
        and row["retrieval_eligible"] == 1
    ]
    mixed_rows: list[sqlite3.Row] = []
    confidential_client_rows: list[sqlite3.Row] = []
    for row in rows:
        audiences = set(_audiences(row["allowed_audiences_json"]))
        if audiences & CLIENT_AUDIENCES and audiences & RESTRICTED_AUDIENCES:
            mixed_rows.append(row)
        if row["classification"] in {"confidential", "restricted"} and audiences & CLIENT_AUDIENCES:
            confidential_client_rows.append(row)

    for row in derived_rows:
        before = _state(row)
        after = dict(before)
        after.update(
            {
                "canonical_status": "evidence_only",
                "canonical_eligible": 0,
                "retrieval_eligible": 0,
                "validation_state": "needs_review",
                "derived_artifact_required": stable_id(
                    "derived-artifact", row["assertion_id"], length=28
                ),
            }
        )
        operations.append(
            _operation(
                operation="MOVE_DERIVED_ASSERTION_TO_EVIDENCE_ONLY_LANE",
                record_id=row["assertion_id"],
                reason=(
                    "Derived interpretation/recommendation cannot remain a "
                    "canonical current fact."
                ),
                before=before,
                after=after,
                apply_ready=False,
                human_decision_required=True,
            )
        )

    for row in mixed_rows + confidential_client_rows:
        before = _state(row)
        current = _audiences(row["allowed_audiences_json"])
        after = dict(before)
        safe_audiences = sorted(set(current) - CLIENT_AUDIENCES)
        after["allowed_audiences_json"] = json.dumps(
            safe_audiences, ensure_ascii=False, separators=(",", ":")
        )
        is_confidential = row["classification"] in {"confidential", "restricted"}
        operation = (
            "REMOVE_CLIENT_AUDIENCE_FROM_CONFIDENTIAL_RECORD"
            if is_confidential
            else "REMOVE_CLIENT_AUDIENCE_FROM_RESTRICTED_MIX"
        )
        operations.append(
            _operation(
                operation=operation,
                record_id=row["assertion_id"],
                reason=(
                    "Confidential/restricted record cannot expose to a client audience."
                    if is_confidential
                    else "Client and restricted audiences must not share one assertion scope."
                ),
                before=before,
                after=after,
                apply_ready=bool(safe_audiences),
            )
        )

    legacy_rows = [row for row in rows if row["atomicity_state"] == "unparsed_legacy_text"]
    for row in legacy_rows:
        before = _state(row)
        after = dict(before)
        after["retrieval_eligible"] = 0
        if before["retrieval_eligible"] == 1:
            after["validation_state"] = "needs_review"
        operations.append(
            _operation(
                operation="QUARANTINE_UNPARSED_LEGACY_ASSERTION",
                record_id=row["assertion_id"],
                reason=(
                    "Legacy assertion is not atomically parsed into a typed "
                    "semantic proposition."
                ),
                before=before,
                after=after,
                apply_ready=before["retrieval_eligible"] == 1,
            )
        )

    compound_rows = [row for row in rows if row["predicate"] == "OWNS_OR_LEADS"]
    for row in compound_rows:
        state = _state(row)
        operations.append(
            _operation(
                operation="HOLD_COMPOUND_OWNS_OR_LEADS_FOR_SEMANTIC_REVIEW",
                record_id=row["assertion_id"],
                reason="Compound edge must be resolved to owns or leads before normalization.",
                before=state,
                after={**state, "predicate": "<OWNS_OR_LEADS_REVIEW_REQUIRED>"},
                apply_ready=False,
                human_decision_required=True,
                alternatives=["OWNS", "LEADS", "PROVENANCE_ONLY"],
            )
        )

    generic_rows = [row for row in rows if row["predicate"] == "RELATED_TO"]
    for row in generic_rows:
        state = _state(row)
        operations.append(
            _operation(
                operation="HOLD_GENERIC_RELATED_TO_FOR_TYPED_RELATION_REVIEW",
                record_id=row["assertion_id"],
                reason="Generic relatedness is insufficient for a decision-safe semantic edge.",
                before=state,
                after={**state, "predicate": "<TYPED_RELATION_REVIEW_REQUIRED>"},
                apply_ready=False,
                human_decision_required=True,
                alternatives=["CONTEXT_FOR", "AFFILIATED_WITH", "SUPPORTED_BY", "PROVENANCE_ONLY"],
            )
        )

    provenance_count = sum(1 for row in rows if row["predicate"] == "HAS_CLAIM_TEXT")
    target_nodes = contract["node_domains"]["decision_intelligence_target"]["target_node_types"]
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": "clearance3_w1_w2_migration_proposal",
        "plan_id": stable_id("clearance3-w1-w2", sha256_file(graph), length=32),
        "graph_path": str(graph),
        "base_graph_sha256": sha256_file(graph),
        "semantic_spine_contract_path": str(contract_path.resolve()),
        "semantic_spine_contract_digest": digest(contract),
        "scope": {
            "w1_derived_current_retrieval": len(derived_rows),
            "w1_mixed_client_restricted": len(mixed_rows),
            "w1_confidential_client": len(confidential_client_rows),
            "w2_unparsed_legacy": len(legacy_rows),
            "w2_compound_owns_or_leads": len(compound_rows),
            "w2_generic_related_to": len(generic_rows),
            "w2_claim_text_provenance_only": provenance_count,
            "w2_future_target_node_types_deferred": len(target_nodes),
        },
        "draft_only": True,
        "graph_mutation_authorized": False,
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "operations": operations,
        "deferred_target_node_types": target_nodes,
        "summary": {
            "operation_count": len(operations),
            "apply_ready_operations": sum(1 for op in operations if op["apply_ready"]),
            "human_decision_required_operations": sum(
                1 for op in operations if op["human_decision_required"]
            ),
            "w1_derived_lane_operations": len(derived_rows),
            "w1_audience_operations": len(mixed_rows) + len(confidential_client_rows),
            "w2_legacy_quarantine_operations": len(legacy_rows),
            "w2_semantic_review_operations": len(compound_rows) + len(generic_rows),
        },
        "approval_prerequisites": [
            "Complete HITL review for derived assertions and compound/generic relations.",
            "Bind exact graph SHA, contract digest, and proposal digest to approval.",
            "Run W0/W1/W2 audits plus the full governed release lifecycle.",
        ],
    }
    payload["canonical_digest"] = {"algorithm": "sha256", "value": digest(payload)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "governance" / "decision_intelligence" / "W2_SEMANTIC_SPINE_CONTRACT.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    proposal = build_plan(args.graph, args.contract)
    output = args.output.expanduser().resolve()
    write_json_exclusive(output, proposal)
    print(
        json.dumps(
            {
                "status": "DRAFT_ONLY",
                "output": str(output),
                "summary": proposal["summary"],
                "canonical_digest": proposal["canonical_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
