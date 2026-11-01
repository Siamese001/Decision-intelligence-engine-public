"""Constants and baseline helpers for source retirement."""
from __future__ import annotations

import sqlite3

from .._validators import sql_placeholders, table_exists
from ..core import ZERO_HASH, digest

SOURCE_RETIREMENT_POLICY_VERSION = "2026-08-04.2"
SOURCE_RETIREMENT_CONTROL_KEY = "com.partner_advisory.nee.source-retirement-control"

_TARGET_KINDS = (
    "assertion_ids",
    "claim_ids",
    "edge_ids",
    "alias_ids",
    "canonical_alias_ids",
    "evidence_ids",
)
_TARGET_TABLES = {
    "assertion_ids": ("assertion", "assertion_id", "primary_source_id"),
    "claim_ids": ("claim", "claim_id", "source_id"),
    "edge_ids": ("edge", "edge_id", "source_id"),
    "alias_ids": ("alias", "alias_id", "source_id"),
    "canonical_alias_ids": ("canonical_alias", "canonical_alias_id", "source_id"),
    "evidence_ids": ("evidence_item", "evidence_id", "source_id"),
}
_RELATED_TEXT_COLUMNS = {
    "assertion_ids": ("object_literal", "scope_text", "qualifiers_json"),
    "claim_ids": ("claim_text",),
    "edge_ids": ("description",),
    "evidence_ids": ("direct_quote",),
}
_SUPERSESSION_DETACHMENT_TARGETS = {
    "assertion_ids": ("assertion", "assertion_id", "superseded_by_assertion_id"),
    "claim_ids": ("claim", "claim_id", "superseded_by"),
    "edge_ids": ("edge", "edge_id", "superseded_by"),
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return table_exists(connection, table)


def _placeholders(values: list[str]) -> str:
    return sql_placeholders(values)


def _row_hash(row: sqlite3.Row | None) -> str:
    return ZERO_HASH if row is None else digest(dict(row))


def _id_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"source-retirement {label} must be a list")
    result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(result) != len(value) or len(result) != len(set(result)):
        raise ValueError(f"source-retirement {label} must contain unique nonblank IDs")
    return sorted(result)


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source-retirement {label} must be nonblank text")
    return value.strip()
