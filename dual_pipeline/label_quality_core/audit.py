"""Graph label hygiene auditing, quality metrics, and regressions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._validators import table_exists
from ..core import connect_sqlite, digest, sha256_file
from .audiences import canonicalize_audiences
from .constants import (
    CANONICAL_EVIDENCE_DIRECTNESS,
    CANONICAL_SOURCE_POSTURES,
    LABEL_POLICY_ID,
    LABEL_POLICY_VERSION,
    NO_REGRESSION_METRICS,
    LabelQualityError,
)


def _table_exists(connection: Any, table: str) -> bool:
    return table_exists(connection, table)


def approved_validation_state(connection: Any) -> str:
    """Use the new semantic state when installed, otherwise preserve compatibility."""

    if not _table_exists(connection, "controlled_value_registry"):
        return "accepted_legacy"
    row = connection.execute(
        """SELECT 1 FROM controlled_value_registry
           WHERE vocabulary='validation_state' AND value='approved' AND active=1"""
    ).fetchone()
    return "approved" if row else "accepted_legacy"


def candidate_validation_state(connection: Any) -> str:
    """Use the new semantic state when installed, otherwise preserve compatibility."""

    if not _table_exists(connection, "controlled_value_registry"):
        return "needs_review"
    row = connection.execute(
        """SELECT 1 FROM controlled_value_registry
           WHERE vocabulary='validation_state' AND value='source_statement' AND active=1"""
    ).fetchone()
    return "source_statement" if row else "needs_review"


def _scalar(connection: Any, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    row = connection.execute(sql, parameters).fetchone()
    return int(row[0] or 0) if row else 0


def _policy_state(connection: Any) -> str:
    if not _table_exists(connection, "label_policy_version"):
        return "legacy_compatibility"
    row = connection.execute(
        """SELECT enforcement_state FROM label_policy_version
           WHERE policy_id=? ORDER BY installed_at DESC LIMIT 1""",
        (LABEL_POLICY_ID,),
    ).fetchone()
    return str(row[0]) if row else "legacy_compatibility"


def _audience_metrics(connection: Any) -> tuple[int, int, int]:
    known: set[str] | None = None
    if _table_exists(connection, "audience_registry"):
        known = {
            str(row[0])
            for row in connection.execute(
                "SELECT audience_id FROM audience_registry WHERE active=1"
            )
        }
    invalid = 0
    unknown = 0
    noncanonical_serializations: dict[tuple[str, ...], set[str]] = {}
    for row in connection.execute(
        "SELECT allowed_audiences_json FROM assertion ORDER BY assertion_id"
    ):
        raw = str(row[0])
        try:
            audiences = canonicalize_audiences(raw)
        except LabelQualityError:
            invalid += 1
            continue
        noncanonical_serializations.setdefault(audiences, set()).add(raw)
        if known is not None and any(value not in known for value in audiences):
            unknown += 1
    duplicate_serialization_rows = sum(
        _scalar(
            connection,
            """SELECT count(*) FROM assertion
               WHERE replace(replace(allowed_audiences_json,' ',''),char(9),'')=?""",
            (json.dumps(list(audiences), separators=(",", ":")),),
        )
        for audiences, variants in noncanonical_serializations.items()
        if len(variants) > 1
    )
    return invalid, unknown, duplicate_serialization_rows


def _normalized_coverage(
    connection: Any,
    table: str,
    base_table: str,
    key: str,
) -> int:
    if not _table_exists(connection, table):
        return 0
    return _scalar(
        connection,
        f"""SELECT count(*) FROM "{base_table}" b
            WHERE EXISTS (SELECT 1 FROM "{table}" s
                          WHERE s.{key}=b.{key})""",
    )


def audit_graph_labels(
    graph_path: Path,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Profile label semantics without mutating the graph."""

    graph_path = Path(graph_path).resolve()
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        required = {
            "assertion",
            "canonical_alias",
            "controlled_value_registry",
            "edge",
            "edge_type_registry",
            "evidence_item",
            "node",
            "source",
        }
        missing = sorted(table for table in required if not _table_exists(connection, table))
        if missing:
            raise LabelQualityError(f"graph lacks required label tables: {missing}")

        invalid_audiences, unknown_audiences, duplicate_audience_serializations = (
            _audience_metrics(connection)
        )
        metrics = {
            "node_count": _scalar(connection, "SELECT count(*) FROM node"),
            "assertion_count": _scalar(connection, "SELECT count(*) FROM assertion"),
            "source_count": _scalar(connection, "SELECT count(*) FROM source"),
            "evidence_count": _scalar(connection, "SELECT count(*) FROM evidence_item"),
            "active_unregistered_alias_confidence_count": _scalar(
                connection,
                """SELECT count(*) FROM canonical_alias a
                   WHERE a.active=1 AND NOT EXISTS (
                     SELECT 1 FROM controlled_value_registry c
                     WHERE c.vocabulary='confidence_label'
                       AND c.value=a.confidence_label AND c.active=1
                   )""",
            ),
            "hitl_assignment_incomplete_count": _scalar(
                connection,
                """SELECT count(*) FROM assertion
                   WHERE hitl_required=1
                     AND (trim(coalesce(validation_priority,''))=''
                          OR trim(coalesce(validation_owner,''))='')""",
            ),
            "invalid_audience_count": invalid_audiences,
            "unknown_audience_count": unknown_audiences,
            "duplicate_audience_serialization_row_count": duplicate_audience_serializations,
            "missing_nonlegacy_extraction_run_count": _scalar(
                connection,
                """SELECT count(*) FROM assertion
                   WHERE origin_type<>'legacy_migration' AND extraction_run_id IS NULL""",
            ),
            "noncanonical_source_posture_count": _scalar(
                connection,
                "SELECT count(*) FROM source WHERE source_posture NOT IN ({})".format(
                    ",".join("?" for _ in CANONICAL_SOURCE_POSTURES)
                ),
                tuple(sorted(CANONICAL_SOURCE_POSTURES)),
            ),
            "distinct_source_type_count": _scalar(
                connection, "SELECT count(DISTINCT source_type) FROM source"
            ),
            "distinct_source_posture_count": _scalar(
                connection, "SELECT count(DISTINCT source_posture) FROM source"
            ),
            "noncanonical_evidence_directness_count": _scalar(
                connection,
                "SELECT count(*) FROM evidence_item WHERE evidence_directness NOT IN (?,?,?)",
                tuple(sorted(CANONICAL_EVIDENCE_DIRECTNESS)),
            ),
            "distinct_evidence_directness_count": _scalar(
                connection,
                "SELECT count(DISTINCT evidence_directness) FROM evidence_item",
            ),
            "directional_high_score_count": _scalar(
                connection,
                """SELECT count(*) FROM assertion
                   WHERE confidence_label='directional' AND confidence_score>0.60""",
            ),
            "accepted_legacy_count": _scalar(
                connection,
                "SELECT count(*) FROM assertion WHERE validation_state='accepted_legacy'",
            ),
            "approved_validation_count": _scalar(
                connection,
                "SELECT count(*) FROM assertion WHERE validation_state='approved'",
            ),
            "unparsed_legacy_assertion_count": _scalar(
                connection,
                "SELECT count(*) FROM assertion WHERE atomicity_state='unparsed_legacy_text'",
            ),
            "legacy_edge_type_count": _scalar(
                connection, "SELECT count(DISTINCT edge_type) FROM edge"
            ),
            "singleton_legacy_edge_type_count": _scalar(
                connection,
                """SELECT count(*) FROM (
                     SELECT edge_type FROM edge GROUP BY edge_type HAVING count(*)=1
                   )""",
            ),
            "pending_identity_review_count": (
                _scalar(
                    connection,
                    """SELECT count(*) FROM entity_resolution_candidate
                       WHERE status='pending_human_review'""",
                )
                if _table_exists(connection, "entity_resolution_candidate")
                else 0
            ),
            "normalized_source_label_count": _normalized_coverage(
                connection, "source_label_state", "source", "source_id"
            ),
            "normalized_evidence_label_count": _normalized_coverage(
                connection, "evidence_label_state", "evidence_item", "evidence_id"
            ),
            "normalized_assertion_label_count": _normalized_coverage(
                connection, "assertion_label_state", "assertion", "assertion_id"
            ),
        }
        enforcement_state = _policy_state(connection)
        blocking_codes = {
            "UNREGISTERED_ACTIVE_ALIAS_CONFIDENCE": metrics[
                "active_unregistered_alias_confidence_count"
            ],
            "HITL_ASSIGNMENT_INCOMPLETE": metrics["hitl_assignment_incomplete_count"],
            "INVALID_AUDIENCE": metrics["invalid_audience_count"],
            "UNKNOWN_AUDIENCE": metrics["unknown_audience_count"],
            "NONLEGACY_EXTRACTION_LINEAGE_MISSING": metrics[
                "missing_nonlegacy_extraction_run_count"
            ],
        }
        blocking_violation_count = sum(blocking_codes.values())
        debt_count = sum(int(metrics[name]) for name in NO_REGRESSION_METRICS)
        enforced = strict or enforcement_state == "enforced"
        if enforced and blocking_violation_count:
            status = "FAIL"
        elif debt_count:
            status = "PASS_WITH_LEGACY_DEBT"
        else:
            status = "PASS"
        report = {
            "schema_version": "1.0.0",
            "report_type": "graph_label_quality",
            "policy": {
                "policy_id": LABEL_POLICY_ID,
                "policy_version": LABEL_POLICY_VERSION,
                "enforcement_state": enforcement_state,
            },
            "graph": {
                "path": str(graph_path),
                "sha256": sha256_file(graph_path),
            },
            "status": status,
            "strict": strict,
            "blocking_violation_count": blocking_violation_count,
            "blocking_codes": blocking_codes,
            "legacy_debt_count": debt_count,
            "metrics": metrics,
            "provider_call_count": 0,
            "graph_mutation_count": 0,
        }
        report["report_digest"] = digest(report)
        return report
    finally:
        connection.close()


def compare_label_quality(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Fail a release when governed label debt increases."""

    regressions: list[dict[str, Any]] = []
    for metric in NO_REGRESSION_METRICS:
        prior = int(before["metrics"].get(metric, 0))
        current = int(after["metrics"].get(metric, 0))
        if current > prior:
            regressions.append(
                {
                    "metric": metric,
                    "before": prior,
                    "after": current,
                    "delta": current - prior,
                }
            )
    if after["status"] == "FAIL":
        regressions.append(
            {
                "metric": "strict_label_policy",
                "before": before["status"],
                "after": after["status"],
                "delta": "blocking violations remain under enforced policy",
            }
        )
    result = {
        "schema_version": "1.0.0",
        "gate": "graph_label_quality_no_regression",
        "status": "PASS" if not regressions else "FAIL",
        "before_graph_sha256": before["graph"]["sha256"],
        "after_graph_sha256": after["graph"]["sha256"],
        "before_report_digest": before["report_digest"],
        "after_report_digest": after["report_digest"],
        "regressions": regressions,
    }
    result["gate_digest"] = digest(result)
    return result


def assert_label_quality_no_regression(
    before_graph_path: Path,
    after_graph_path: Path,
) -> dict[str, Any]:
    before = audit_graph_labels(before_graph_path)
    after = audit_graph_labels(after_graph_path)
    result = compare_label_quality(before, after)
    result["before_report"] = before
    result["after_report"] = after
    if result["status"] != "PASS":
        raise RuntimeError(f"graph label quality regressed: {result['regressions']}")
    return result

