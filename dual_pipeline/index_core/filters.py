"""Facet filtering, scope normalization, vector dot products, and exact entity matching."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from dual_pipeline.core import normalize_space

from .constants import (
    MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
    MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE,
)


def normalize_object_facet_filters(
    facet_filters: dict[str, Iterable[str]] | None,
) -> dict[str, list[str]] | None:
    """Normalize an AND-across-vocabularies, OR-within-values facet filter."""

    if facet_filters is None:
        return None
    if not isinstance(facet_filters, dict) or not facet_filters:
        raise ValueError("facet_filters must be a nonempty object")
    normalized: dict[str, list[str]] = {}
    for raw_vocabulary in sorted(facet_filters):
        if not isinstance(raw_vocabulary, str):
            raise ValueError("facet filter vocabularies must be strings")
        vocabulary = raw_vocabulary.strip()
        if not vocabulary or vocabulary != raw_vocabulary:
            raise ValueError("facet filter vocabularies must be normalized and nonempty")
        raw_values = facet_filters[raw_vocabulary]
        if isinstance(raw_values, str):
            raise ValueError("facet filter values must be arrays, not strings")
        try:
            values = list(raw_values)
        except TypeError as exc:
            raise ValueError("facet filter values must be iterable") from exc
        if not values:
            raise ValueError("each facet filter vocabulary requires at least one value")
        normalized_values: list[str] = []
        for raw_value in values:
            if not isinstance(raw_value, str):
                raise ValueError("facet filter values must be strings")
            value = raw_value.strip()
            if not value or value != raw_value:
                raise ValueError(
                    "facet filter values must be normalized and nonempty"
                )
            normalized_values.append(value)
        normalized[vocabulary] = sorted(set(normalized_values))
    return normalized


def _record_ids_matching_object_facets(
    connection: sqlite3.Connection,
    facet_filters: dict[str, list[str]],
) -> set[str]:
    """Return records matching every vocabulary and any value within each."""

    matched: set[str] | None = None
    for vocabulary, values in facet_filters.items():
        placeholders = ",".join("?" for _ in values)
        current = {
            str(row["record_id"])
            for row in connection.execute(
                f"""SELECT DISTINCT record_id
                      FROM retrieval_record_object_facet
                     WHERE review_state='approved'
                       AND vocabulary=?
                       AND value IN ({placeholders})
                     ORDER BY record_id""",
                [vocabulary, *values],
            )
        }
        matched = current if matched is None else matched & current
        if not matched:
            return set()
    return matched or set()


def normalize_assertion_scope(
    assertion_ids: Iterable[str] | None,
) -> set[str] | None:
    """Normalize an optional exact assertion-ID retrieval scope."""

    if assertion_ids is None:
        return None
    if isinstance(assertion_ids, str):
        raise ValueError("allowed_assertion_ids must be an iterable, not a string")
    normalized: set[str] = set()
    for assertion_id in assertion_ids:
        if not isinstance(assertion_id, str) or not assertion_id.strip():
            raise ValueError("allowed_assertion_ids must contain nonempty strings")
        if assertion_id != assertion_id.strip():
            raise ValueError("allowed_assertion_ids must be normalized")
        normalized.add(assertion_id)
    return normalized


def _record_is_in_assertion_scope(
    artifact: dict[str, Any],
    assertion_scope: set[str],
) -> bool:
    extensions = artifact.get("extensions", {})
    assertion_id = extensions.get("com.partner_advisory.nee.graph-object-id")
    if isinstance(assertion_id, str):
        return assertion_id in assertion_scope
    facet_assertion_ids = extensions.get("com.partner_advisory.nee.graph-assertion-ids")
    if isinstance(facet_assertion_ids, list) and facet_assertion_ids:
        return all(
            isinstance(value, str) and value in assertion_scope
            for value in facet_assertion_ids
        )
    return False


def _dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _exact_entity_title_matches(
    query: str,
    authorized_records: dict[str, dict[str, Any]],
) -> list[str]:
    """Return exact named entity facets explicitly requested by the query.

    This is a deterministic navigation control, not an assertion anchor or a
    semantic-score override. It applies only when a complete governed entity
    facet title is present in the query, so an exact title request cannot rank
    behind a related facet that repeats more of the target's evidence wording.
    """

    normalized_query = normalize_space(query).casefold()
    matches: list[str] = []
    for record_id, artifact in authorized_records.items():
        if artifact.get("record_type") != "entity_facet":
            continue
        title = normalize_space(str(artifact.get("title") or "")).casefold()
        if not title:
            continue
        stripped = title.removeprefix("entity facet:").strip()
        if title in normalized_query or (len(stripped) >= 3 and stripped in normalized_query):
            matches.append(record_id)
    matches.sort(
        key=lambda rid: (
            -len(normalize_space(str(authorized_records[rid].get("title") or ""))),
            -len(authorized_records[rid].get("evidence_ids", [])),
            -len(str(authorized_records[rid].get("retrieval_text") or "")),
            rid,
        )
    )
    return matches[:MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE]


def normalize_exact_anchor_ids(
    exact_anchor_assertion_ids: Iterable[str] | None,
) -> list[str]:
    """Normalize exact anchor assertion IDs into a deduplicated list."""
    if isinstance(exact_anchor_assertion_ids, str):
        raise ValueError(
            "exact anchor assertion IDs must be an iterable, not a string"
        )
    exact_anchor_ids: list[str] = []
    seen: set[str] = set()
    for assertion_id in exact_anchor_assertion_ids or ():
        if (
            not isinstance(assertion_id, str)
            or not assertion_id.strip()
            or assertion_id != assertion_id.strip()
        ):
            raise ValueError(
                "exact anchor assertion IDs must contain normalized strings"
            )
        if assertion_id not in seen:
            exact_anchor_ids.append(assertion_id)
            seen.add(assertion_id)
    return exact_anchor_ids


def find_exact_anchor_record_ids(
    exact_anchor_ids: list[str],
    authorized_records: dict[str, dict[str, Any]],
    max_injections: int = MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
) -> list[str]:
    """Find record IDs corresponding to exact anchor assertion IDs."""
    injected_record_ids: list[str] = []
    for assertion_id in exact_anchor_ids:
        matching_record_ids = sorted(
            record_id
            for record_id, artifact in authorized_records.items()
            if artifact.get("extensions", {}).get(
                "com.partner_advisory.nee.graph-object-id"
            )
            == assertion_id
        )
        if matching_record_ids:
            injected_record_ids.append(matching_record_ids[0])
        if len(injected_record_ids) >= max_injections:
            break
    return injected_record_ids


def validate_bm25_assertion_ranks(bm25_assertion_ranks: dict[str, int] | None) -> None:
    """Validates that BM25 assertion ranks are unique positive integers."""
    if bm25_assertion_ranks is not None and (
        any(
            not isinstance(assertion_id, str)
            or not assertion_id
            or not isinstance(rank, int)
            or rank < 1
            for assertion_id, rank in bm25_assertion_ranks.items()
        )
        or len(set(bm25_assertion_ranks.values())) != len(bm25_assertion_ranks)
    ):
        raise ValueError("BM25 assertion ranks must be unique positive integers")


def compute_bm25_ranks(
    bm25_assertion_ranks: dict[str, int] | None,
    authorized_records: dict[str, dict[str, Any]],
    bm25_limit: int,
) -> dict[str, int]:
    """Computes BM25 ranks for authorized records matching provided assertion ranks."""
    if bm25_assertion_ranks is None:
        return {}
    bm25_ranks: dict[str, int] = {}
    for record_id, artifact in authorized_records.items():
        assertion_id = artifact.get("extensions", {}).get(
            "com.partner_advisory.nee.graph-object-id"
        )
        if assertion_id in bm25_assertion_ranks:
            bm25_ranks[record_id] = bm25_assertion_ranks[assertion_id]
    return dict(
        sorted(
            bm25_ranks.items(),
            key=lambda item: (item[1], item[0]),
        )[:bm25_limit]
    )


