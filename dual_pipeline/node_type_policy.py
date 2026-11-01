"""Registry-backed policy for generic internal candidate-entity creation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .core import connect_sqlite

NON_CREATABLE_NODE_TYPES = frozenset(
    {
        "card",
        "external_concept",
        "source",
        "source_lineage",
        "source_pack",
    }
)
NO_NAME_AUTO_MERGE_RULE = "never auto-merge on name similarity"


def _active_registry_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """SELECT node_type_id,identity_rule,registry_state
                 FROM node_type_registry
                WHERE registry_state='active'
                ORDER BY node_type_id"""
        )
    )


def active_creatable_node_types(graph_path: Path) -> list[str]:
    """Return active registered types eligible for generic candidate creation."""

    connection = connect_sqlite(graph_path, readonly=True)
    try:
        return active_creatable_node_types_in_connection(connection)
    finally:
        connection.close()


def active_creatable_node_types_in_connection(
    connection: sqlite3.Connection,
) -> list[str]:
    values: list[str] = []
    for row in _active_registry_rows(connection):
        node_type = row["node_type_id"]
        if node_type in NON_CREATABLE_NODE_TYPES or node_type.startswith("deprecated"):
            continue
        if NO_NAME_AUTO_MERGE_RULE not in row["identity_rule"].casefold():
            continue
        values.append(node_type)
    return values


def registry_identity_rule(graph_path: Path, node_type: str) -> str | None:
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        row = connection.execute(
            """SELECT identity_rule FROM node_type_registry
                WHERE node_type_id=? AND registry_state='active'""",
            (node_type,),
        ).fetchone()
        return row["identity_rule"] if row is not None else None
    finally:
        connection.close()


def require_creatable_node_type(
    connection: sqlite3.Connection, node_type: object
) -> str:
    """Fail closed unless ``node_type`` is active and generically creatable."""

    if not isinstance(node_type, str) or not node_type.strip():
        raise ValueError("internal entity proposal has no declared node type")
    normalized = node_type.strip()
    row = connection.execute(
        """SELECT registry_state,identity_rule
             FROM node_type_registry WHERE node_type_id=?""",
        (normalized,),
    ).fetchone()
    if row is None or row["registry_state"] != "active":
        raise ValueError("internal entity proposal node type is not active in the registry")
    if normalized in NON_CREATABLE_NODE_TYPES or normalized.startswith("deprecated"):
        raise ValueError("internal entity proposal node type is system-owned or non-creatable")
    if NO_NAME_AUTO_MERGE_RULE not in row["identity_rule"].casefold():
        raise ValueError("internal entity proposal node type lacks the no-name-auto-merge rule")
    return normalized


def type_forbids_name_auto_merge(graph_path: Path, node_type: str) -> bool:
    rule = registry_identity_rule(graph_path, node_type)
    return bool(rule and NO_NAME_AUTO_MERGE_RULE in rule.casefold())


__all__ = [
    "NON_CREATABLE_NODE_TYPES",
    "NO_NAME_AUTO_MERGE_RULE",
    "active_creatable_node_types",
    "active_creatable_node_types_in_connection",
    "registry_identity_rule",
    "require_creatable_node_type",
    "type_forbids_name_auto_merge",
]
