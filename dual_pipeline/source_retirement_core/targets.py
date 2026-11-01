"""Target discovery and dependency audits for source retirement."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..core import digest
from .constants import (
    _RELATED_TEXT_COLUMNS,
    _SUPERSESSION_DETACHMENT_TARGETS,
    _TARGET_KINDS,
    _TARGET_TABLES,
    _placeholders,
    _row_hash,
    _table_exists,
)
from .control import validate_source_retirement_control


def _row_by_id(
    connection: sqlite3.Connection, table: str, field: str, value: str
) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT * FROM {table} WHERE {field}=?", (value,)
    ).fetchone()


def _source_scoped_ids(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_field: str,
    source_field: str,
    source_id: str,
) -> list[str]:
    return sorted(
        str(row[id_field])
        for row in connection.execute(
            f"SELECT {id_field} FROM {table} WHERE {source_field}=? ORDER BY {id_field}",
            (source_id,),
        ).fetchall()
    )


def _query_ids(
    connection: sqlite3.Connection, query: str, params: tuple[Any, ...], field: str
) -> list[str]:
    return sorted(str(row[field]) for row in connection.execute(query, params).fetchall())


def _in_clause(field: str, values: list[str]) -> tuple[str, tuple[str, ...]]:
    if not values:
        return "0", ()
    return f"{field} IN ({_placeholders(values)})", tuple(values)


def _derived_targets(
    connection: sqlite3.Connection, remove: dict[str, list[str]]
) -> dict[str, list[str]]:
    assertion_ids = remove["assertion_ids"]
    claim_ids = remove["claim_ids"]
    edge_ids = remove["edge_ids"]
    result: dict[str, list[str]] = {
        "validation_ids": [],
        "canonical_route_ids": [],
        "search_document_ids": [],
        "graph_quality_exception_ids": [],
        "claim_confidence_claim_ids": [],
        "legacy_alias_canonical_map_alias_ids": [],
    }
    if _table_exists(connection, "validation_queue"):
        clauses: list[str] = []
        params: list[str] = []
        for field, values in (("claim_id", claim_ids), ("edge_id", edge_ids)):
            clause, clause_params = _in_clause(field, values)
            if clause != "0":
                clauses.append(clause)
                params.extend(clause_params)
        if clauses:
            result["validation_ids"] = _query_ids(
                connection,
                "SELECT validation_id FROM validation_queue WHERE " + " OR ".join(clauses),
                tuple(params),
                "validation_id",
            )
    if _table_exists(connection, "canonical_route"):
        clauses = []
        params = []
        for object_type, values in (
            ("assertion", assertion_ids),
            ("claim", claim_ids),
            ("edge", edge_ids),
        ):
            if values:
                clauses.append(
                    "(object_type=? AND object_id IN (" + _placeholders(values) + "))"
                )
                params.extend([object_type, *values])
        if clauses:
            result["canonical_route_ids"] = _query_ids(
                connection,
                "SELECT route_id FROM canonical_route WHERE " + " OR ".join(clauses),
                tuple(params),
                "route_id",
            )
    if _table_exists(connection, "search_document"):
        clauses = []
        params = []
        if assertion_ids:
            clauses.append("assertion_id IN (" + _placeholders(assertion_ids) + ")")
            params.extend(assertion_ids)
        for object_type, values in (("claim", claim_ids), ("edge", edge_ids)):
            if values:
                clauses.append(
                    "(object_type=? AND object_id IN (" + _placeholders(values) + "))"
                )
                params.extend([object_type, *values])
        if clauses:
            result["search_document_ids"] = _query_ids(
                connection,
                "SELECT search_document_id FROM search_document WHERE " + " OR ".join(clauses),
                tuple(params),
                "search_document_id",
            )
    if _table_exists(connection, "graph_quality_exception"):
        clauses = []
        params = []
        for object_type, values in (
            ("assertion", assertion_ids),
            ("claim", claim_ids),
            ("edge", edge_ids),
        ):
            if values:
                clauses.append(
                    "(object_type=? AND object_id IN (" + _placeholders(values) + "))"
                )
                params.extend([object_type, *values])
        if clauses:
            result["graph_quality_exception_ids"] = _query_ids(
                connection,
                "SELECT exception_id FROM graph_quality_exception WHERE " + " OR ".join(clauses),
                tuple(params),
                "exception_id",
            )
    if _table_exists(connection, "claim_confidence_hitl_matrix") and claim_ids:
        result["claim_confidence_claim_ids"] = _query_ids(
            connection,
            "SELECT DISTINCT claim_id FROM claim_confidence_hitl_matrix WHERE claim_id IN ("
            + _placeholders(claim_ids)
            + ")",
            tuple(claim_ids),
            "claim_id",
        )
    alias_ids = remove["alias_ids"]
    canonical_alias_ids = remove["canonical_alias_ids"]
    if _table_exists(connection, "legacy_alias_canonical_map"):
        clauses = []
        params = []
        for field, values in (
            ("alias_id", alias_ids),
            ("canonical_alias_id", canonical_alias_ids),
        ):
            if values:
                clauses.append(field + " IN (" + _placeholders(values) + ")")
                params.extend(values)
        if clauses:
            result["legacy_alias_canonical_map_alias_ids"] = _query_ids(
                connection,
                "SELECT alias_id FROM legacy_alias_canonical_map WHERE " + " OR ".join(clauses),
                tuple(params),
                "alias_id",
            )
    return result


def _assert_related_text(
    connection: sqlite3.Connection, *, kind: str, object_id: str, fragment: str
) -> None:
    table, id_field, _source_field = _TARGET_TABLES[kind]
    text_fields = _RELATED_TEXT_COLUMNS[kind]
    row = connection.execute(
        "SELECT " + ",".join(text_fields) + f" FROM {table} WHERE {id_field}=?",
        (object_id,),
    ).fetchone()
    text = "\n".join(
        str(row[field]) for field in text_fields if row is not None and row[field] is not None
    )
    if row is None or fragment.lower() not in text.lower():
        raise RuntimeError(
            f"related source-retirement target no longer contains its bound text: {object_id}"
        )


def _assert_dependencies_clear(
    connection: sqlite3.Connection, targets: dict[str, Any]
) -> None:
    remove = targets["remove"]
    assertions = remove["assertion_ids"]
    claims = remove["claim_ids"]
    edges = remove["edge_ids"]
    evidence = remove["evidence_ids"]
    detached_by_kind = {
        kind: [
            item["object_id"]
            for item in targets["supersession_detachments"]
            if item["object_type"] == kind
        ]
        for kind in _SUPERSESSION_DETACHMENT_TARGETS
    }
    checks: list[tuple[str, str, tuple[str, ...]]] = []
    if _table_exists(connection, "assertion_evidence") and evidence:
        checks.append(
            (
                "retained assertion evidence",
                "SELECT 1 FROM assertion_evidence WHERE evidence_id IN ("
                + _placeholders(evidence)
                + ") AND assertion_id NOT IN ("
                + _placeholders(assertions)
                + ") LIMIT 1",
                tuple(evidence + assertions),
            )
        )
    if _table_exists(connection, "legacy_claim_assertion_map") and claims:
        checks.append(
            (
                "retained claim assertion mapping",
                "SELECT 1 FROM legacy_claim_assertion_map WHERE claim_id IN ("
                + _placeholders(claims)
                + ") AND assertion_id NOT IN ("
                + _placeholders(assertions)
                + ") LIMIT 1",
                tuple(claims + assertions),
            )
        )
    if _table_exists(connection, "legacy_edge_assertion_map") and edges:
        checks.append(
            (
                "retained edge assertion mapping",
                "SELECT 1 FROM legacy_edge_assertion_map WHERE edge_id IN ("
                + _placeholders(edges)
                + ") AND assertion_id NOT IN ("
                + _placeholders(assertions)
                + ") LIMIT 1",
                tuple(edges + assertions),
            )
        )
    if assertions:
        retained_assertion_filter = ""
        retained_assertion_params: tuple[str, ...] = tuple(assertions + assertions)
        if detached_by_kind["assertion_ids"]:
            retained_assertion_filter = " AND assertion_id NOT IN (" + _placeholders(
                detached_by_kind["assertion_ids"]
            ) + ")"
            retained_assertion_params = tuple(
                assertions + assertions + detached_by_kind["assertion_ids"]
            )
        checks.append(
            (
                "retained assertion supersession",
                "SELECT 1 FROM assertion WHERE superseded_by_assertion_id IN ("
                + _placeholders(assertions)
                + ") AND assertion_id NOT IN ("
                + _placeholders(assertions)
                + ")"
                + retained_assertion_filter
                + " LIMIT 1",
                retained_assertion_params,
            )
        )
    if claims:
        retained_claim_filter = ""
        retained_claim_params: tuple[str, ...] = tuple(claims + claims)
        if detached_by_kind["claim_ids"]:
            retained_claim_filter = " AND claim_id NOT IN (" + _placeholders(
                detached_by_kind["claim_ids"]
            ) + ")"
            retained_claim_params = tuple(
                claims + claims + detached_by_kind["claim_ids"]
            )
        checks.append(
            (
                "retained claim supersession",
                "SELECT 1 FROM claim WHERE superseded_by IN ("
                + _placeholders(claims)
                + ") AND claim_id NOT IN ("
                + _placeholders(claims)
                + ")"
                + retained_claim_filter
                + " LIMIT 1",
                retained_claim_params,
            )
        )
    if edges:
        retained_edge_filter = ""
        retained_edge_params: tuple[str, ...] = tuple(edges + edges)
        if detached_by_kind["edge_ids"]:
            retained_edge_filter = " AND edge_id NOT IN (" + _placeholders(
                detached_by_kind["edge_ids"]
            ) + ")"
            retained_edge_params = tuple(
                edges + edges + detached_by_kind["edge_ids"]
            )
        checks.append(
            (
                "retained edge supersession",
                "SELECT 1 FROM edge WHERE superseded_by IN ("
                + _placeholders(edges)
                + ") AND edge_id NOT IN ("
                + _placeholders(edges)
                + ")"
                + retained_edge_filter
                + " LIMIT 1",
                retained_edge_params,
            )
        )
    for label, query, params in checks:
        if connection.execute(query, params).fetchone() is not None:
            raise RuntimeError(f"source retirement is blocked by a {label}")


def _collect_targets(connection: sqlite3.Connection, control: object) -> dict[str, Any]:
    normalized = validate_source_retirement_control(control)
    source = _row_by_id(connection, "source", "source_id", normalized["target_source_id"])
    if source is None:
        raise KeyError(f"source-retirement target source does not exist: {normalized['target_source_id']}")

    row_hashes: dict[str, dict[str, str]] = {}
    source_scoped: dict[str, list[str]] = {}
    for kind in _TARGET_KINDS:
        table, id_field, source_field = _TARGET_TABLES[kind]
        direct_ids = _source_scoped_ids(
            connection,
            table=table,
            id_field=id_field,
            source_field=source_field,
            source_id=normalized["target_source_id"],
        )
        source_scoped[kind] = direct_ids
        declared = set(normalized["remove"][kind]) | set(normalized["retain"][kind])
        if declared != set(direct_ids):
            missing = sorted(set(direct_ids) - declared)
            outside = sorted(declared - set(direct_ids))
            # Additional removals are allowed only with a bound text fragment.
            extra_removals = set(normalized["remove"][kind]) - set(direct_ids)
            if missing or set(outside) != extra_removals:
                raise RuntimeError(
                    f"source-retirement {kind} selection does not exactly cover the target source; "
                    f"missing={missing}, outside={outside}"
                )
        rows: dict[str, str] = {}
        for object_id in normalized["remove"][kind] + normalized["retain"][kind]:
            row = _row_by_id(connection, table, id_field, object_id)
            if row is None:
                raise KeyError(f"source-retirement target no longer exists: {object_id}")
            rows[object_id] = _row_hash(row)
        row_hashes[kind] = dict(sorted(rows.items()))

    related_by_kind = {
        kind: {
            item["object_id"]: item["expected_text_fragment"]
            for item in normalized["related_removals"]
            if item["object_type"] == kind
        }
        for kind in _TARGET_KINDS
    }
    for kind in _TARGET_KINDS:
        non_source = set(normalized["remove"][kind]) - set(source_scoped[kind])
        if non_source != set(related_by_kind[kind]):
            raise RuntimeError(
                f"source-retirement external {kind} require one exact text-bound related removal"
            )
        for object_id, fragment in related_by_kind[kind].items():
            _assert_related_text(
                connection, kind=kind, object_id=object_id, fragment=fragment
            )
    if any(
        item["object_type"] not in _TARGET_KINDS
        or item["object_id"] not in normalized["remove"][item["object_type"]]
        for item in normalized["related_removals"]
    ):
        raise RuntimeError("source-retirement related removal falls outside its removal set")

    source_update_hashes: dict[str, str] = {}
    for update in normalized["source_updates"]:
        row = _row_by_id(connection, "source", "source_id", update["source_id"])
        if row is None:
            raise KeyError(f"source-retirement source update target is missing: {update['source_id']}")
        if (
            row["source_name"] != update["expected_source_name"]
            or row["source_posture"] != update["expected_source_posture"]
            or row["notes"] != update["expected_notes"]
        ):
            raise RuntimeError(
                f"source-retirement source update target drifted: {update['source_id']}"
            )
        source_update_hashes[update["source_id"]] = _row_hash(row)
    version_update_hashes: dict[str, str] = {}
    for update in normalized["source_version_updates"]:
        row = _row_by_id(
            connection, "source_version", "source_version_id", update["source_version_id"]
        )
        if row is None:
            raise KeyError(
                "source-retirement source-version update target is missing: "
                f"{update['source_version_id']}"
            )
        if (
            row["scope_text"] != update["expected_scope_text"]
            or row["posture_text"] != update["expected_posture_text"]
        ):
            raise RuntimeError(
                "source-retirement source-version update target drifted: "
                f"{update['source_version_id']}"
            )
        version_update_hashes[update["source_version_id"]] = _row_hash(row)

    supersession_detachment_hashes: dict[str, str] = {}
    for detachment in normalized["supersession_detachments"]:
        table, id_field, supersession_field = _SUPERSESSION_DETACHMENT_TARGETS[
            detachment["object_type"]
        ]
        row = _row_by_id(connection, table, id_field, detachment["object_id"])
        if row is None:
            raise KeyError(
                "source-retirement supersession detachment target is missing: "
                + detachment["object_id"]
            )
        if row[supersession_field] != detachment[
            "expected_superseded_by_object_id"
        ]:
            raise RuntimeError(
                "source-retirement supersession detachment target drifted: "
                + detachment["object_id"]
            )
        _assert_related_text(
            connection,
            kind=detachment["object_type"],
            object_id=detachment["object_id"],
            fragment=detachment["expected_text_fragment"],
        )
        supersession_detachment_hashes[
            detachment["object_type"] + ":" + detachment["object_id"]
        ] = _row_hash(row)

    targets = {
        "target_source_id": normalized["target_source_id"],
        "remove": normalized["remove"],
        "retain": normalized["retain"],
        "related_removals": normalized["related_removals"],
        "supersession_detachments": normalized["supersession_detachments"],
        "source_updates": normalized["source_updates"],
        "source_version_updates": normalized["source_version_updates"],
        "source_scoped_ids": source_scoped,
        "row_hashes": row_hashes,
        "source_update_hashes": dict(sorted(source_update_hashes.items())),
        "source_version_update_hashes": dict(sorted(version_update_hashes.items())),
        "supersession_detachment_hashes": dict(
            sorted(supersession_detachment_hashes.items())
        ),
        "derived": _derived_targets(connection, normalized["remove"]),
    }
    _assert_dependencies_clear(connection, targets)
    targets["snapshot_digest"] = digest(targets)
    return targets

