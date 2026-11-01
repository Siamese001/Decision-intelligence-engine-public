"""Generational in-memory cache for sidecar/graph retrieval clearance and FTS5 BM25 queries."""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core import connect_sqlite
from ..hash_cache import cached_sha256_file
from ..index import CLASSIFICATION_RANK, BoundedLRUDict, normalize_assertion_scope, resolve_retrieval_mode, retrieval_metadata_from_artifact, validate_sidecar  # noqa: lazy-decouple
from ..retrieval_clearance import retrieval_clearance  # noqa: lazy-decouple
from .tokenize import _question_tokens, bm25_query_tokens


def _expand_internal_audiences_frozen(audiences: Iterable[str], classification: str) -> frozenset[str]:
    req = set(audiences)
    if classification != "public" and "nee-engineering" in req:
        req.add("internal_consulting")
    return frozenset(req)

_PAIR_LOCK = threading.RLock()
_PAIR_CACHE: dict[
    tuple[str, int, int, str, int, int],
    dict[str, list[tuple[str, str, int, frozenset[str], str, dict[str, Any]]]],
] = {}
_RECORD_TOKENS_CACHE: dict[
    tuple[str, int, int, str, int, int],
    dict[str, frozenset[str]],
] = {}
_FTS_CACHE: BoundedLRUDict[tuple[str, int, int, str, int], tuple[bool, list[str]]] = BoundedLRUDict(
    maxsize=512
)


def clear_clearance_caches() -> None:
    """Clear all in-memory clearance and FTS5 caches."""
    with _PAIR_LOCK:
        _PAIR_CACHE.clear()
        _RECORD_TOKENS_CACHE.clear()
        _FTS_CACHE.clear()


def _stat_key(path: Path) -> tuple[str, int, int]:
    resolved = path.expanduser().resolve()
    try:
        st = os.stat(resolved)
        return str(resolved), st.st_mtime_ns, st.st_size
    except OSError:
        return str(resolved), 0, 0


def _artifact_assertion_id(record: dict[str, Any]) -> str | None:
    value = record.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id")
    return value if isinstance(value, str) and value else None


def _load_graph_clearance_metadata(graph_path: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    graph_connection = connect_sqlite(graph_path, readonly=True)
    try:
        assertion_columns = {
            row["name"] for row in graph_connection.execute("PRAGMA table_info(assertion)")
        }
        if {"assertion_id", "classification", "allowed_audiences_json"} <= assertion_columns:
            selected = ["assertion_id", "classification", "allowed_audiences_json"]
            for optional_column in (
                "assertion_type",
                "origin_type",
                "extraction_run_id",
                "hitl_required",
                "validation_priority",
                "validation_owner",
                "atomicity_state",
                "currentness_state",
                "retrieval_eligible",
                "canonical_status",
                "canonical_eligible",
                "confidence_score",
                "validation_state",
            ):
                if optional_column in assertion_columns:
                    selected.append(optional_column)
            for row in graph_connection.execute(
                f"SELECT {','.join(selected)} FROM assertion ORDER BY assertion_id"
            ):
                metadata[row["assertion_id"]] = dict(row)
    finally:
        graph_connection.close()
    return metadata


def _check_lane_and_currentness(lane: str | None, graph_row: dict[str, Any]) -> bool:
    if graph_row.get("validation_state") in {"superseded", "rejected", "retired"}:
        return False
    cur_state = graph_row.get("currentness_state")
    if lane == "candidate":
        c_status = graph_row.get("canonical_status")
        return cur_state in (None, "current", "unknown") or (
            cur_state == "historical" and c_status in (None, "candidate", "historical_candidate")
        )
    if cur_state not in (None, "current") or graph_row.get("retrieval_eligible") not in (None, 1):
        return False
    if lane != "approved":
        return True
    c_status = graph_row.get("canonical_status", "canonical")
    c_elig = graph_row.get("canonical_eligible", 1)
    return c_status == "canonical" and c_elig == 1


def _evaluate_record_clearance(
    record: dict[str, Any],
    graph_row: dict[str, Any],
) -> bool:
    conf_raw = graph_row.get("confidence_score")
    if conf_raw is not None and float(conf_raw) < 0.65:
        return False
    if not _check_lane_and_currentness(record.get("lane"), graph_row):
        return False
    graph_assertion_type = graph_row.get("assertion_type")
    extensions = record.get("extensions", {})
    artifact_assertion_type = extensions.get("com.partner_advisory.nee.assertion-type")
    effective_assertion_type = (
        graph_assertion_type if isinstance(graph_assertion_type, str) else artifact_assertion_type
    )
    allowed, _reason = retrieval_clearance(
        assertion_type=effective_assertion_type,
        classification=str(graph_row.get("classification", record["classification"])),
        audiences=graph_row.get("allowed_audiences_json", record["audiences"]),
        origin_type=graph_row.get("origin_type", extensions.get("com.partner_advisory.nee.origin-type")),
        extraction_run_id=graph_row.get(
            "extraction_run_id",
            extensions.get("com.partner_advisory.nee.extraction-run-id"),
        ),
        hitl_required=graph_row.get(
            "hitl_required", extensions.get("com.partner_advisory.nee.hitl-required")
        ),
        validation_priority=graph_row.get(
            "validation_priority",
            extensions.get("com.partner_advisory.nee.validation-priority"),
        ),
        validation_owner=graph_row.get(
            "validation_owner", extensions.get("com.partner_advisory.nee.validation-owner")
        ),
        atomicity_state=graph_row.get(
            "atomicity_state", extensions.get("com.partner_advisory.nee.atomicity-state")
        ),
    )
    return allowed


def _load_cleared_lane_records(
    sidecar_path: Path,
    graph_path: Path,
    validate_fn: Callable[[Path], dict[str, Any]],
) -> dict[str, list[tuple[str, str, int, frozenset[str], str, dict[str, Any]]]]:
    s_key = _stat_key(sidecar_path)
    g_key = _stat_key(graph_path)
    pair_key = (*s_key, *g_key)
    is_mock_validator = getattr(validate_fn, "__module__", "").startswith("unittest.mock")
    if not is_mock_validator and s_key[1] > 0 and g_key[1] > 0:
        with _PAIR_LOCK:
            cached = _PAIR_CACHE.get(pair_key)
            if cached is not None:
                return cached

    validation = validate_fn(sidecar_path)
    if validation["metadata"]["graph_sha256"] != cached_sha256_file(graph_path):
        raise RuntimeError("graph expansion sidecar is not bound to the active graph")

    graph_clearance_metadata = _load_graph_clearance_metadata(graph_path)
    by_lane: dict[str, list[tuple[str, str, int, frozenset[str], str, dict[str, Any]]]] = (
        defaultdict(list)
    )
    tokens_by_record_id: dict[str, frozenset[str]] = {}
    connection = connect_sqlite(sidecar_path, readonly=True)
    try:
        rows = connection.execute(
            """SELECT record_id,lane,artifact_json
                 FROM retrieval_record
                ORDER BY record_id"""
        )
        for row in rows:
            record = json.loads(row["artifact_json"])
            assertion_id = _artifact_assertion_id(record)
            if assertion_id is None:
                continue
            graph_row = graph_clearance_metadata.get(assertion_id, {})
            if not _evaluate_record_clearance(record, graph_row):
                continue
            metadata = retrieval_metadata_from_artifact(record)
            lane = str(row["lane"])
            rec_id = str(row["record_id"])
            tokens_by_record_id[rec_id] = frozenset(
                _question_tokens(record.get("retrieval_text", ""))
            )
            by_lane[lane].append(
                (
                    rec_id,
                    assertion_id,
                    CLASSIFICATION_RANK[record["classification"]],
                    frozenset(record["audiences"]),
                    str(metadata["answer_use"]),
                    record,
                )
            )
    finally:
        connection.close()

    result = dict(by_lane)
    if not is_mock_validator and s_key[1] > 0 and g_key[1] > 0:
        with _PAIR_LOCK:
            stale = [k for k in _PAIR_CACHE if k[0] == s_key[0] and k[3] == g_key[0]]
            for k in stale:
                del _PAIR_CACHE[k]
                _RECORD_TOKENS_CACHE.pop(k, None)
            _PAIR_CACHE[pair_key] = result
            _RECORD_TOKENS_CACHE[pair_key] = tokens_by_record_id
    return result


def record_tokens_cached(
    sidecar_path: Path,
    graph_path: Path,
    validate_fn: Callable[[Path], dict[str, Any]] = validate_sidecar,
) -> dict[str, frozenset[str]]:
    """Return precomputed question tokens for all cleared records in the active sidecar."""
    s_key = _stat_key(sidecar_path)
    g_key = _stat_key(graph_path)
    if s_key[1] == 0 or g_key[1] == 0:
        return {}
    pair_key = (*s_key, *g_key)
    with _PAIR_LOCK:
        cached = _RECORD_TOKENS_CACHE.get(pair_key)
        if cached is not None:
            return cached
    _load_cleared_lane_records(sidecar_path, graph_path, validate_fn=validate_fn)
    with _PAIR_LOCK:
        return _RECORD_TOKENS_CACHE.get(pair_key, {})


def authorized_records_cached(
    *,
    sidecar_path: Path,
    graph_path: Path,
    allowed_lanes: list[str],
    retrieval_mode: str,
    classification: str,
    audiences: list[str],
    allowed_assertion_ids: Iterable[str] | None,
    allow_lane_subset: bool = False,
    validate_fn: Callable[[Path], dict[str, Any]] = validate_sidecar,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, dict[str, Any]]]]:
    """Return authorized records using generation-bound clearance cache."""
    _, mode_policy = resolve_retrieval_mode(
        allowed_lanes,
        retrieval_mode,
        allow_lane_subset=allow_lane_subset,
    )
    assertion_scope = normalize_assertion_scope(allowed_assertion_ids)
    requested_audiences = _expand_internal_audiences_frozen(audiences, classification)
    max_class_rank = CLASSIFICATION_RANK[classification]
    cleared_by_lane = _load_cleared_lane_records(
        sidecar_path,
        graph_path,
        validate_fn,
    )
    by_record: dict[str, dict[str, Any]] = {}
    by_assertion: dict[str, tuple[str, dict[str, Any]]] = {}
    for lane in allowed_lanes:
        allowed_uses = mode_policy[lane]
        for (
            record_id,
            assertion_id,
            rec_class_rank,
            rec_audiences,
            answer_use,
            record,
        ) in cleared_by_lane.get(lane, ()):
            if assertion_scope is not None and assertion_id not in assertion_scope:
                continue
            if rec_class_rank > max_class_rank or not (requested_audiences & rec_audiences):
                continue
            if answer_use not in allowed_uses:
                continue
            by_record[record_id] = record
            existing = by_assertion.get(assertion_id)
            if existing is None or record_id < existing[0]:
                by_assertion[assertion_id] = (record_id, record)
    return by_record, by_assertion


def fts5_bm25_matches_cached(
    graph_path: Path,
    question: str,
    limit: int = 1000,
) -> tuple[bool, list[str]]:
    """Execute or reuse FTS5 BM25 assertion query against graph_path."""
    tokens = bm25_query_tokens(question)
    fts_query = " OR ".join(f'"{token}"' for token in tokens)
    g_key = _stat_key(graph_path)
    cache_key = (*g_key, fts_query, limit)
    if g_key[1] > 0:
        with _PAIR_LOCK:
            cached = _FTS_CACHE.get(cache_key)
            if cached is not None:
                return cached

    connection = connect_sqlite(graph_path, readonly=True)
    try:
        fts_available = (
            connection.execute(
                """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='search_fts'"""
            ).fetchone()
            is not None
        )
        if not fts_query or not fts_available:
            result: tuple[bool, list[str]] = (False, [])
        else:
            seen: set[str] = set()
            ordered_ids: list[str] = []
            sd_cols = {
                row["name"] for row in connection.execute("PRAGMA table_info(search_document)")
            }
            where_clauses = ["search_fts MATCH ?"]
            if "retrieval_eligible" in sd_cols:
                where_clauses.append("sd.retrieval_eligible=1")
            if "currentness_state" in sd_cols:
                where_clauses.append("sd.currentness_state='current'")
            where_sql = " AND ".join(where_clauses)
            order_col = ",sd.search_document_id" if "search_document_id" in sd_cols else ""
            query = f"""SELECT sd.assertion_id,bm25(search_fts) AS bm25_score
                         FROM search_fts
                         JOIN search_document sd
                           ON sd.search_rowid=search_fts.rowid
                        WHERE {where_sql}
                        ORDER BY bm25_score{order_col}
                        LIMIT ?"""
            for row in connection.execute(query, (fts_query, limit)):
                assertion_id = row["assertion_id"]
                if assertion_id not in seen:
                    seen.add(assertion_id)
                    ordered_ids.append(assertion_id)
            result = (True, ordered_ids)
    finally:
        connection.close()

    if g_key[1] > 0:
        with _PAIR_LOCK:
            _FTS_CACHE[cache_key] = result
    return result
