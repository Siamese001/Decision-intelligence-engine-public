"""
dual_pipeline/intake_validators.py

Fail-closed pre-write validation for knowledge graph ingestion.
Enforces bi-temporal constraints, canonical predicate direction,
RFC 3986 provenance URI locators, and mandatory evidence bindings.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

FORBIDDEN_INVERSE_PREDICATES: frozenset[str] = frozenset({
    "SUPPORTED_BY",
    "CONTRADICTED_BY",
    "SUPERSEDED_BY",
    "SUPERCEDED_BY",
})

CANONICAL_PREDICATE_INVERSIONS: dict[str, str] = {
    "SUPPORTED_BY": "SUPPORTS",
    "CONTRADICTED_BY": "CONTRADICTS",
    "SUPERSEDED_BY": "SUPERSEDES",
    "SUPERCEDED_BY": "SUPERSEDES",
}

VALID_URI_SCHEMES: frozenset[str] = frozenset({
    "file",
    "urn",
    "http",
    "https",
    "gs",
    "s3",
})

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _parse_timestamp(value: str, field_name: str) -> datetime:
    """Parses ISO timestamp or YYYY-MM-DD date into datetime."""
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) == 10 and _DATE_PREFIX_RE.match(text):
        return datetime.strptime(text, "%Y-%m-%d")
    iso_text = text.replace("Z", "+00:00")
    return datetime.fromisoformat(iso_text)


def validate_assertion_bitemporal(record: Mapping[str, Any]) -> None:
    """Validates bi-temporal completeness and supersession integrity.

    Requires:
    1. Non-null, valid ISO/date valid_from.
    2. valid_to >= valid_from if valid_to is specified.
    3. superseded_by_assertion_id must be non-null if superseded_at is set.
    4. superseded_at must be set if superseded_by_assertion_id is set.
    """
    assertion_id = record.get("assertion_id", "<unknown>")
    valid_from = record.get("valid_from")
    if valid_from is None or not str(valid_from).strip():
        raise ValueError(
            f"Assertion '{assertion_id}' missing required temporal bound 'valid_from'"
        )

    vf_dt = _parse_timestamp(str(valid_from), f"Assertion '{assertion_id}' valid_from")

    valid_to = record.get("valid_to")
    if valid_to is not None and str(valid_to).strip():
        vt_dt = _parse_timestamp(str(valid_to), f"Assertion '{assertion_id}' valid_to")
        if vt_dt < vf_dt:
            raise ValueError(
                f"Assertion '{assertion_id}' invalid temporal interval: "
                f"valid_to ({valid_to}) precedes valid_from ({valid_from})"
            )

    superseded_at = record.get("superseded_at")
    superseded_by = record.get("superseded_by_assertion_id")
    has_superseded_at = superseded_at is not None and bool(str(superseded_at).strip())
    has_superseded_by = superseded_by is not None and bool(str(superseded_by).strip())

    if has_superseded_at and not has_superseded_by:
        raise ValueError(
            f"Assertion '{assertion_id}' broken supersession chain: "
            f"superseded_at is set ({superseded_at}) but superseded_by_assertion_id is null"
        )
    if has_superseded_by and not has_superseded_at:
        raise ValueError(
            f"Assertion '{assertion_id}' incomplete supersession: "
            f"superseded_by_assertion_id is set ({superseded_by}) but superseded_at is null"
        )


def validate_assertion_predicate(predicate: str) -> None:
    """Enforces canonical forward predicate direction.

    Rejects inverse predicates like SUPPORTED_BY.
    """
    if not predicate or not predicate.strip():
        raise ValueError("Assertion predicate must not be blank")

    pred_norm = predicate.strip().upper()
    if pred_norm in FORBIDDEN_INVERSE_PREDICATES:
        canonical = CANONICAL_PREDICATE_INVERSIONS.get(pred_norm, "canonical forward predicate")
        raise ValueError(
            f"Inverse predicate '{predicate}' is forbidden by best practices. "
            f"Use forward predicate '{canonical}' instead."
        )


def validate_source_provenance_uri(uri: str | None) -> str:
    """Validates source cold_archive_ref complies with RFC 3986 URI format.

    Rejects bare local paths or un-schemed references.
    """
    if uri is None or not str(uri).strip():
        raise ValueError("Source provenance URI cannot be null or empty")

    rendered = str(uri).strip()
    parsed = urlparse(rendered)
    if not parsed.scheme or parsed.scheme.lower() not in VALID_URI_SCHEMES:
        raise ValueError(
            f"Invalid source provenance URI '{rendered}': "
            f"must have valid scheme in {sorted(VALID_URI_SCHEMES)} (RFC 3986)"
        )
    if parsed.scheme.lower() == "urn" and not parsed.path:
        raise ValueError(
            f"Invalid URN provenance URI '{rendered}': missing URN namespace/path"
        )
    return rendered


def validate_assertion_evidence_binding(
    assertion_id: str, evidence_ids: Sequence[str]
) -> None:
    """Enforces atomic evidence binding invariant.

    Every assertion must link to at least one evidence item.
    """
    if not assertion_id or not assertion_id.strip():
        raise ValueError("Assertion ID must not be blank")
    if not evidence_ids or len(evidence_ids) == 0:
        raise ValueError(
            f"Assertion '{assertion_id}' violates provenance completeness: "
            "at least one evidence item binding is required"
        )
    for idx, eid in enumerate(evidence_ids):
        if not eid or not str(eid).strip():
            raise ValueError(
                f"Assertion '{assertion_id}' has blank evidence_id at index {idx}"
            )


def validate_assertion_record(
    record: Mapping[str, Any],
    evidence_ids: Sequence[str] | None = None,
) -> None:
    """Composite pre-write validator for an assertion record."""
    predicate = record.get("predicate", "")
    validate_assertion_predicate(str(predicate))
    validate_assertion_bitemporal(record)
    if evidence_ids is not None:
        validate_assertion_evidence_binding(str(record.get("assertion_id", "")), evidence_ids)
