"""Helper functions for candidate assertion cleanup."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from .._validators import row_hash
from ..core import ZERO_HASH, digest
from .constants import (
    _ASSERTION_FIELDS,
    CLEANUP_POLICY_VERSION,
    PRESERVED_FUTURE_STATE_SOURCE_IDS,
)


def _correlation(source_id: str) -> dict[str, str | None]:
    return {
        "source_id": source_id,
        "candidate_id": None,
        "query_id": None,
        "graph_operation_id": None,
        "hitl_item_id": None,
    }


def _assertion_state(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in _ASSERTION_FIELDS}


def _row_hash(row: sqlite3.Row | dict[str, Any] | None) -> str:
    return row_hash(row, zero_hash=ZERO_HASH, digest_fn=digest)


def _clean_qualifiers(
    raw: str,
    *,
    disposition: str,
    reason: str,
    cleaned_at: str,
) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["nee_candidate_cleanup"] = {
        "policy_version": CLEANUP_POLICY_VERSION,
        "disposition": disposition,
        "reason": reason,
        "cleaned_at": cleaned_at,
    }
    return json.dumps(value, sort_keys=True)


def _disposition(row: sqlite3.Row) -> tuple[str, str, str]:
    source_id = row["primary_source_id"]
    if source_id in PRESERVED_FUTURE_STATE_SOURCE_IDS:
        return (
            "candidate",
            row["validation_state"],
            "Future-state Sherpa architecture work remains visible as planned/in progress.",
        )
    if row["validation_priority"]:
        return (
            "validation_pending",
            "needs_review",
            "A named validation task exists; preserve the assertion for that queue only.",
        )
    return (
        "evidence_only",
        "unreviewed",
        "No named validation task exists; retain source evidence without candidate retrieval.",
    )


def _assert_plan_digest(plan: dict[str, Any]) -> None:
    supplied = plan.get("plan_digest")
    expected = dict(plan)
    expected.pop("plan_digest", None)
    if not isinstance(supplied, str) or supplied != digest(expected):
        raise RuntimeError("candidate cleanup plan digest mismatch")
    if plan.get("plan_type") != "candidate_backlog_cleanup":
        raise ValueError("unsupported cleanup plan type")
    if plan.get("policy_version") != CLEANUP_POLICY_VERSION:
        raise ValueError("unsupported candidate cleanup policy")


def _candidate_retrieval_count(graph_path: Path) -> int:
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        return int(
            connection.execute(
                """SELECT count(*) FROM assertion
                    WHERE canonical_status='candidate'
                      AND currentness_state IN ('current','unknown')"""
            ).fetchone()[0]
        )
    finally:
        connection.close()
