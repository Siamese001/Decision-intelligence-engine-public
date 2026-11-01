"""Constants and routing helpers for card refactor release."""
from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from .._validators import row_hash
from ..core import ZERO_HASH, digest

PLAN_TYPE = "semantic_organization_change_set"
_ROUTE_FIELDS = (
    "route_id",
    "object_type",
    "object_id",
    "card_id",
    "route_role",
    "is_primary",
    "route_status",
    "source_mechanism",
    "intentional_unrouted_reason",
    "active",
    "created_at",
)


def _row_hash(row: sqlite3.Row | dict[str, Any] | None) -> str:
    return row_hash(row, zero_hash=ZERO_HASH, digest_fn=digest)


def _route_state(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in _ROUTE_FIELDS}


def _legacy_card_state(connection: sqlite3.Connection) -> dict[str, str]:
    claims = [
        dict(row)
        for row in connection.execute(
            """SELECT claim_id,card_id,superseded_by
                 FROM claim
                ORDER BY claim_id"""
        )
    ]
    node_map = [
        dict(row)
        for row in connection.execute(
            "SELECT node_id,card_id,role FROM node_card_map ORDER BY node_id,card_id,role"
        )
    ]
    claim_routes = [
        dict(row)
        for row in connection.execute(
            """SELECT route_id,object_id,card_id,route_role,is_primary,
                      route_status,active
                 FROM canonical_route
                WHERE object_type='claim'
                ORDER BY route_id"""
        )
    ]
    return {
        "claim_card_digest": digest(claims),
        "node_card_map_digest": digest(node_map),
        "claim_route_digest": digest(claim_routes),
    }


def _operation_counts(operations: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        sorted(Counter(str(operation["operation"]) for operation in operations).items())
    )
