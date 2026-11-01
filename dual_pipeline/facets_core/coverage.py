"""Coverage audits, metrics, and helper utilities."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import digest
from .constants import (
    CARDS_REFACTOR_POLICY_VERSION,
    SEMANTIC_DOMAIN_VOCABULARY,
    TOPIC_VOCABULARY,
)
from .schema import schema_is_installed


def audit_semantic_domain_coverage(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Audit current canonical assertions against the required facet policy."""

    total = int(
        connection.execute(
            """SELECT count(*) FROM assertion
                WHERE canonical_status='canonical'
                  AND currentness_state='current'"""
        ).fetchone()[0]
    )
    if not schema_is_installed(connection):
        return {
            "status": "FAIL" if total else "PASS",
            "policy_version": CARDS_REFACTOR_POLICY_VERSION,
            "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
            "current_canonical_assertions": total,
            "covered": 0,
            "exceptions": 0,
            "missing": total,
            "missing_assertion_ids": [
                str(row[0])
                for row in connection.execute(
                    """SELECT assertion_id FROM assertion
                        WHERE canonical_status='canonical'
                          AND currentness_state='current'
                        ORDER BY assertion_id"""
                )
            ],
        }
    covered_ids = {
        str(row[0])
        for row in connection.execute(
            """SELECT a.assertion_id
                 FROM assertion a
                 JOIN object_facet_assignment f
                   ON f.object_type='assertion'
                  AND f.object_id=a.assertion_id
                  AND f.vocabulary=?
                  AND f.review_state='approved'
                  AND f.active=1
                WHERE a.canonical_status='canonical'
                  AND a.currentness_state='current'""",
            (SEMANTIC_DOMAIN_VOCABULARY,),
        )
    }
    exception_ids = {
        str(row[0])
        for row in connection.execute(
            """SELECT a.assertion_id
                 FROM assertion a
                 JOIN facet_coverage_exception e
                   ON e.object_type='assertion'
                  AND e.object_id=a.assertion_id
                  AND e.vocabulary=?
                  AND e.active=1
                WHERE a.canonical_status='canonical'
                  AND a.currentness_state='current'""",
            (SEMANTIC_DOMAIN_VOCABULARY,),
        )
    }
    all_ids = {
        str(row[0])
        for row in connection.execute(
            """SELECT assertion_id FROM assertion
                WHERE canonical_status='canonical'
                  AND currentness_state='current'"""
        )
    }
    missing = sorted(all_ids - covered_ids - exception_ids)
    domain_counts = Counter(
        str(row[0])
        for row in connection.execute(
            """SELECT f.value
                 FROM assertion a
                 JOIN object_facet_assignment f
                   ON f.object_type='assertion'
                  AND f.object_id=a.assertion_id
                  AND f.vocabulary=?
                  AND f.review_state='approved'
                  AND f.active=1
                WHERE a.canonical_status='canonical'
                  AND a.currentness_state='current'""",
            (SEMANTIC_DOMAIN_VOCABULARY,),
        )
    )
    return {
        "status": "PASS" if not missing else "FAIL",
        "policy_version": CARDS_REFACTOR_POLICY_VERSION,
        "vocabulary": SEMANTIC_DOMAIN_VOCABULARY,
        "current_canonical_assertions": total,
        "covered": len(covered_ids),
        "exceptions": len(exception_ids),
        "missing": len(missing),
        "missing_assertion_ids": missing,
        "domain_counts": dict(sorted(domain_counts.items())),
    }


def _facet_coverage(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_filter_sql: str,
    vocabulary: str,
    review_held_satisfies: bool,
) -> dict[str, Any]:
    id_column = {
        "node": "node_id",
        "claim": "claim_id",
        "assertion": "assertion_id",
    }[object_type]
    table = object_type
    object_ids = {
        str(row[0])
        for row in connection.execute(
            f"SELECT o.{id_column} FROM {table} o WHERE {object_filter_sql}"
        )
    }
    rows = connection.execute(
        f"""SELECT f.object_id,f.review_state
              FROM object_facet_assignment f
              JOIN {table} o ON o.{id_column}=f.object_id
             WHERE f.object_type=?
               AND f.vocabulary=?
               AND f.active=1
               AND ({object_filter_sql})""",
        (object_type, vocabulary),
    ).fetchall()
    approved = {str(row[0]) for row in rows if str(row[1]) == "approved"}
    needs_review = {str(row[0]) for row in rows if str(row[1]) == "needs_review"}
    exceptions = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT e.object_id
                  FROM facet_coverage_exception e
                  JOIN {table} o ON o.{id_column}=e.object_id
                 WHERE e.object_type=?
                   AND e.vocabulary=?
                   AND e.active=1
                   AND ({object_filter_sql})""",
            (object_type, vocabulary),
        )
    }
    needs_review_only = needs_review - approved
    exception_only = exceptions - approved - needs_review
    satisfied = approved | exceptions
    if review_held_satisfies:
        satisfied |= needs_review
    missing = sorted(object_ids - satisfied)
    return {
        "status": "PASS" if not missing else "FAIL",
        "object_type": object_type,
        "vocabulary": vocabulary,
        "applicable": len(object_ids),
        "approved": len(approved),
        "needs_review": len(needs_review),
        "needs_review_only": len(needs_review_only),
        "exceptions": len(exceptions),
        "exceptions_only": len(exception_only),
        "missing": len(missing),
        "missing_object_ids": missing,
    }


def audit_cards_refactor_coverage(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Validate replacement coverage before legacy assertion routes are retired."""

    if not schema_is_installed(connection):
        return {
            "status": "FAIL",
            "policy_version": CARDS_REFACTOR_POLICY_VERSION,
            "failure": "facet schema is not installed",
        }
    checks = {
        "active_node_semantic_domain": _facet_coverage(
            connection,
            object_type="node",
            object_filter_sql="o.active=1",
            vocabulary=SEMANTIC_DOMAIN_VOCABULARY,
            review_held_satisfies=False,
        ),
        "current_assertion_semantic_domain": _facet_coverage(
            connection,
            object_type="assertion",
            object_filter_sql=(
                "o.canonical_status='canonical' AND o.currentness_state='current'"
            ),
            vocabulary=SEMANTIC_DOMAIN_VOCABULARY,
            review_held_satisfies=False,
        ),
        "current_assertion_topic": _facet_coverage(
            connection,
            object_type="assertion",
            object_filter_sql=(
                "o.canonical_status='canonical' AND o.currentness_state='current'"
            ),
            vocabulary=TOPIC_VOCABULARY,
            review_held_satisfies=True,
        ),
    }
    intentional_unrouted = int(
        connection.execute(
            """SELECT count(*)
                 FROM canonical_route
                WHERE object_type='assertion'
                  AND route_status='intentional_unrouted'
                  AND active=1"""
        ).fetchone()[0]
    )
    return {
        "status": (
            "PASS"
            if all(check["status"] == "PASS" for check in checks.values())
            else "FAIL"
        ),
        "policy_version": CARDS_REFACTOR_POLICY_VERSION,
        "checks": checks,
        "active_assertion_intentional_unrouted": intentional_unrouted,
    }


def selector_digest(selector: dict[str, Any]) -> str:
    return digest(selector)


def normalize_audiences(raw: str | Iterable[str]) -> list[str]:
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            values = []
    else:
        values = list(raw)
    if not isinstance(values, list):
        raise ValueError("audiences must be an array")
    normalized = [str(value).strip() for value in values if str(value).strip()]
    if not normalized:
        raise ValueError("at least one audience is required")
    return sorted(set(normalized))


from dual_pipeline.storage.governed_context import GovernedStorageContext


def open_graph(path: Path) -> sqlite3.Connection:
    connection = GovernedStorageContext.connect(path)
    connection.row_factory = sqlite3.Row
    return connection
