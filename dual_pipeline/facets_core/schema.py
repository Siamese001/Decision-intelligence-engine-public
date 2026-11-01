"""Facet schema installation, validation, and checksum calculation."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..core import sha256_bytes
from .constants import (
    CARDS_REFACTOR_MIGRATION_ID,
    CARDS_REFACTOR_POLICY_VERSION,
    FACET_SCHEMA_SQL,
)


def facet_schema_checksum() -> str:
    return sha256_bytes(FACET_SCHEMA_SQL.encode("utf-8"))


def schema_is_installed(connection: sqlite3.Connection) -> bool:
    required = {
        "facet_derivation_rule",
        "object_facet_assignment",
        "facet_coverage_exception",
        "card_view_definition",
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    return required <= present


_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "facet_derivation_rule": (
        "derivation_rule_id",
        "rule_version",
        "policy_version",
        "rule_definition_json",
        "rule_digest",
        "applicability_policy_digest",
        "approval_reference",
        "active",
        "created_at",
        "retired_at",
    ),
    "object_facet_assignment": (
        "assignment_id",
        "object_type",
        "object_id",
        "vocabulary",
        "value",
        "assignment_method",
        "review_state",
        "derivation_rule_id",
        "derivation_rule_version",
        "rationale",
        "approval_reference",
        "active",
        "created_at",
        "retired_at",
    ),
    "facet_coverage_exception": (
        "exception_id",
        "object_type",
        "object_id",
        "vocabulary",
        "reason",
        "owner",
        "review_due_date",
        "approval_reference",
        "active",
        "created_at",
        "resolved_at",
    ),
    "card_view_definition": (
        "card_view_id",
        "card_id",
        "view_version",
        "view_kind",
        "selector_schema_version",
        "selector_json",
        "selector_digest",
        "authority_mode",
        "classification_ceiling",
        "eligible_audiences_json",
        "editorial_owner",
        "view_state",
        "approval_reference",
        "active",
        "created_at",
        "retired_at",
    ),
}
_EXPECTED_INDEXES: dict[str, tuple[str, tuple[str, ...], bool, bool]] = {
    "ux_object_facet_assignment_active": (
        "object_facet_assignment",
        ("object_type", "object_id", "vocabulary", "value"),
        True,
        True,
    ),
    "idx_object_facet_assignment_lookup": (
        "object_facet_assignment",
        ("object_type", "object_id", "vocabulary", "active", "value"),
        False,
        False,
    ),
    "ux_facet_coverage_exception_active": (
        "facet_coverage_exception",
        ("object_type", "object_id", "vocabulary"),
        True,
        True,
    ),
    "ux_card_view_definition_active": (
        "card_view_definition",
        ("card_id",),
        True,
        True,
    ),
}


def install_facet_schema(
    connection: sqlite3.Connection,
    *,
    applied_at: str,
    applied_by: str = "nee-cards-refactor",
) -> None:
    """Install the additive schema inside the caller's transaction boundary."""

    statement = ""
    for line in FACET_SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                connection.execute(sql)
    if statement.strip():
        raise RuntimeError("cards-refactoring schema contains an incomplete statement")
    existing = connection.execute(
        "SELECT checksum_sha256 FROM schema_migration WHERE migration_id=?",
        (CARDS_REFACTOR_MIGRATION_ID,),
    ).fetchone()
    checksum = facet_schema_checksum()
    if existing is None:
        connection.execute(
            """INSERT INTO schema_migration
               (migration_id,checksum_sha256,description,applied_at,applied_by,
                rollback_supported,release_version)
               VALUES (?,?,?,?,?,?,?)""",
            (
                CARDS_REFACTOR_MIGRATION_ID,
                checksum,
                "Add governed object facets and versioned card view definitions.",
                applied_at,
                applied_by,
                1,
                CARDS_REFACTOR_POLICY_VERSION,
            ),
        )
    elif str(existing[0]) != checksum:
        raise RuntimeError("cards-refactoring schema migration checksum mismatch")


def validate_facet_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                  'facet_derivation_rule',
                  'object_facet_assignment',
                  'facet_coverage_exception',
                  'card_view_definition'
                )"""
        )
    }
    triggers = {
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
                WHERE type='trigger' AND name IN (
                  'object_facet_assignment_object_guard_bi',
                  'object_facet_assignment_policy_guard_bi',
                  'object_facet_assignment_immutable_bu',
                  'object_facet_assignment_immutable_bd',
                  'facet_coverage_exception_object_guard_bi',
                  'facet_derivation_rule_approval_guard_bi',
                  'facet_derivation_rule_immutable_bu',
                  'facet_derivation_rule_immutable_bd',
                  'card_view_definition_approval_guard_bi',
                  'card_view_definition_immutable_bu',
                  'card_view_definition_immutable_bd'
                )"""
        )
    }
    migration = connection.execute(
        """SELECT checksum_sha256 FROM schema_migration
            WHERE migration_id=?""",
        (CARDS_REFACTOR_MIGRATION_ID,),
    ).fetchone()
    expected_tables = {
        "facet_derivation_rule",
        "object_facet_assignment",
        "facet_coverage_exception",
        "card_view_definition",
    }
    expected_triggers = {
        "object_facet_assignment_object_guard_bi",
        "object_facet_assignment_policy_guard_bi",
        "object_facet_assignment_immutable_bu",
        "object_facet_assignment_immutable_bd",
        "facet_coverage_exception_object_guard_bi",
        "facet_derivation_rule_approval_guard_bi",
        "facet_derivation_rule_immutable_bu",
        "facet_derivation_rule_immutable_bd",
        "card_view_definition_approval_guard_bi",
        "card_view_definition_immutable_bu",
        "card_view_definition_immutable_bd",
    }
    failures: list[str] = []
    if tables != expected_tables:
        failures.append("tables")
    if triggers != expected_triggers:
        failures.append("triggers")
    column_signatures = {
        table: tuple(
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        )
        for table in sorted(expected_tables)
    }
    for table, expected in _EXPECTED_COLUMNS.items():
        if column_signatures.get(table) != expected:
            failures.append(f"columns:{table}")
    index_signatures: dict[str, dict[str, Any]] = {}
    for index_name, (table, columns, unique, partial) in _EXPECTED_INDEXES.items():
        index_row = next(
            (
                row
                for row in connection.execute(f'PRAGMA index_list("{table}")')
                if str(row[1]) == index_name
            ),
            None,
        )
        actual_columns = tuple(
            str(row[2])
            for row in connection.execute(f'PRAGMA index_info("{index_name}")')
        )
        actual_unique = bool(index_row[2]) if index_row is not None else False
        actual_partial = bool(index_row[4]) if index_row is not None else False
        index_signatures[index_name] = {
            "table": table,
            "columns": list(actual_columns),
            "unique": actual_unique,
            "partial": actual_partial,
        }
        if (
            index_row is None
            or actual_columns != columns
            or actual_unique != unique
            or actual_partial != partial
        ):
            failures.append(f"index:{index_name}")
    if migration is None or str(migration[0]) != facet_schema_checksum():
        failures.append("migration_ledger")
    return {
        "status": "PASS" if not failures else "FAIL",
        "tables": sorted(tables),
        "triggers": sorted(triggers),
        "column_signatures": column_signatures,
        "index_signatures": index_signatures,
        "migration_checksum": None if migration is None else str(migration[0]),
        "failures": failures,
    }

