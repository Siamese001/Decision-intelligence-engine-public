"""Constants, policy versions, disposition types, and shared record utilities for validation closeout."""
from __future__ import annotations

import sqlite3
from typing import Any

from dual_pipeline._validators import sql_placeholders
from dual_pipeline.core import ZERO_HASH, digest

CLOSEOUT_POLICY_VERSION = "2026-07-27.4"
_DISPOSITION_RETAIN_EVIDENCE_ONLY = "retain_evidence_only"
_DISPOSITION_REMOVE_FROM_GRAPH = "remove_from_graph"
_DISPOSITION_CONFIRM_MAPPING = "confirm_mapping"
_DISPOSITION_CONFIRM_CANONICAL_NAME = "confirm_canonical_name"
_CLOSEOUT_DISPOSITIONS = {
    _DISPOSITION_RETAIN_EVIDENCE_ONLY,
    _DISPOSITION_REMOVE_FROM_GRAPH,
    _DISPOSITION_CONFIRM_MAPPING,
    _DISPOSITION_CONFIRM_CANONICAL_NAME,
}
_QUEUE_FIELDS = (
    "validation_id",
    "node_id",
    "edge_id",
    "claim_id",
    "validation_question",
    "owner",
    "priority",
    "due_date",
    "status",
    "resolution",
    "resolved_at",
    "created_at",
)
_ASSERTION_FIELDS = (
    "assertion_id",
    "canonical_status",
    "currentness_state",
    "canonical_eligible",
    "retrieval_eligible",
    "validation_state",
    "primary_source_id",
    "qualifiers_json",
)


def _row_hash(row: sqlite3.Row | dict[str, Any] | None) -> str:
    return ZERO_HASH if row is None else digest(dict(row))


def _placeholders(values: list[str]) -> str:
    return sql_placeholders(values)


def _ids(rows: list[sqlite3.Row], field: str) -> list[str]:
    return sorted({str(row[field]) for row in rows if row[field]})


def _row_state(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _normalized_alias(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())
