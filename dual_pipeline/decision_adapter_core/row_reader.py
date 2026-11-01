"""SQLite graph row reader and envelope normalization for decision adapter."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..reference_monitor import CLASSIFICATION_AUDIENCE_CEILINGS
from .constants import (
    RuntimeDecisionSourceError,
    _audiences,
    _json_object,
    _parsed_timestamp,
    _row_source_assertion_id,
    _sha256,
    _text,
    _timestamp,
)


def _read_rows(graph_path: Path) -> list[sqlite3.Row]:
    from ..storage.governed_context import GovernedStorageContext
    connection: sqlite3.Connection | None = None
    try:
        connection = GovernedStorageContext.connect(graph_path, readonly=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT a.assertion_id, a.subject_node_id, subject.canonical_name AS subject_name,
                      a.predicate, a.object_literal, a.scope_text, a.qualifiers_json,
                      a.primary_source_id, a.extraction_run_id, a.classification,
                      a.allowed_audiences_json, a.valid_from, a.valid_to, a.recorded_at,
                      a.updated_at, a.currentness_state, a.canonical_status,
                      a.canonical_eligible, a.retrieval_eligible, a.approval_reference,
                      a.confidence_label, a.confidence_score, a.hitl_required,
                      e.evidence_id, e.source_id AS evidence_source_id,
                      e.source_version_id AS evidence_source_version_id, e.locator_kind,
                      e.locator_value, e.direct_quote, source_version.source_fingerprint,
                      source_version.content_hash, source_version.source_date_normalized,
                      source.source_name
                 FROM assertion AS a
                 JOIN assertion_evidence AS assertion_evidence ON assertion_evidence.assertion_id = a.assertion_id
                 JOIN evidence_item AS e ON e.evidence_id = assertion_evidence.evidence_id
                 JOIN source_version AS source_version ON source_version.source_version_id = e.source_version_id
                 JOIN source AS source ON source.source_id = e.source_id
                 LEFT JOIN node AS subject ON subject.node_id = a.subject_node_id
                WHERE a.retrieval_eligible = 1
                  AND a.canonical_status = 'canonical'
                  AND a.canonical_eligible = 1
                  AND a.currentness_state = 'current'
                ORDER BY a.assertion_id, e.evidence_id"""
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeDecisionSourceError(
            "runtime graph does not expose the governed assertion/source/evidence schema"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    return rows


def _normalise_row(row: sqlite3.Row) -> dict[str, Any]:
    """Turn one graph assertion/evidence join into an immutable source envelope."""

    assertion_id = _text(row["assertion_id"], field="assertion_id")
    evidence_id = _text(row["evidence_id"], field="evidence_id")
    source_id = _text(row["primary_source_id"], field="primary_source_id")
    if source_id != _text(row["evidence_source_id"], field="evidence source_id"):
        raise RuntimeDecisionSourceError("assertion primary_source_id differs from evidence source")
    source_version_id = _text(
        row["evidence_source_version_id"], field="evidence source_version_id"
    )
    classification = _text(row["classification"], field="classification")
    if classification not in CLASSIFICATION_AUDIENCE_CEILINGS:
        raise RuntimeDecisionSourceError("assertion classification is outside the DI vocabulary")
    audiences = _audiences(row["allowed_audiences_json"])
    if set(audiences) - CLASSIFICATION_AUDIENCE_CEILINGS[classification]:
        raise RuntimeDecisionSourceError(
            "assertion audiences exceed the classification ceiling for DI"
        )
    source_as_of = _timestamp(
        row["source_date_normalized"], field="source_version.source_date_normalized"
    )
    recorded_at = _timestamp(row["recorded_at"], field="assertion.recorded_at")
    if _parsed_timestamp(recorded_at, field="assertion.recorded_at") < _parsed_timestamp(
        source_as_of, field="source_version.source_date_normalized"
    ):
        raise RuntimeDecisionSourceError("assertion recorded_at precedes source date")
    assertion_text = _text(row["object_literal"], field="assertion.object_literal")
    locator_kind = _text(row["locator_kind"], field="evidence locator_kind")
    locator_value = _text(row["locator_value"], field="evidence locator_value")
    direct_quote = _text(row["direct_quote"], field="evidence direct_quote")
    qualifier_data = _json_object(row["qualifiers_json"], field="assertion.qualifiers_json")
    valid_from = _timestamp(row["valid_from"], field="assertion.valid_from")
    valid_to_raw = row["valid_to"]
    valid_to = (
        None
        if valid_to_raw is None
        else _timestamp(valid_to_raw, field="assertion.valid_to")
    )
    updated_at = _timestamp(row["updated_at"], field="assertion.updated_at")
    source_assertion_id = _row_source_assertion_id(assertion_id, evidence_id)
    source_assertion = {
        "source_assertion_id": source_assertion_id,
        "source_id": source_id,
        "source_version_id": source_version_id,
        "snapshot_digest": _sha256(
            row["source_fingerprint"], field="source_version.source_fingerprint"
        ),
        "content_digest": _sha256(row["content_hash"], field="source_version.content_hash"),
        "locator": {
            "kind": locator_kind,
            "value": locator_value,
            "quote_digest": hashlib.sha256(direct_quote.encode("utf-8")).hexdigest(),
        },
        "extraction_run_id": _text(row["extraction_run_id"], field="assertion.extraction_run_id"),
        "classification": classification,
        "audiences": audiences,
        "source_as_of": source_as_of,
        "captured_at": recorded_at,
        "revoked_at": None,
    }
    return {
        "source_assertion": source_assertion,
        "assertion_id": assertion_id,
        "evidence_id": evidence_id,
        "subject_node_id": _text(row["subject_node_id"], field="assertion.subject_node_id"),
        "subject_name": _text(row["subject_name"], field="subject canonical_name"),
        "predicate": _text(row["predicate"], field="assertion.predicate"),
        "assertion_text": assertion_text,
        "scope_text": "" if row["scope_text"] is None else str(row["scope_text"]),
        "qualifiers": qualifier_data,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "recorded_at": recorded_at,
        "updated_at": updated_at,
        "canonical_status": _text(row["canonical_status"], field="assertion.canonical_status"),
        "canonical_eligible": bool(row["canonical_eligible"]),
        "approval_reference": (
            None
            if row["approval_reference"] is None or not str(row["approval_reference"]).strip()
            else str(row["approval_reference"]).strip()
        ),
        "confidence_label": ""
        if row["confidence_label"] is None
        else str(row["confidence_label"]).strip(),
        "confidence_score": row["confidence_score"],
        "hitl_required": bool(row["hitl_required"]),
        "source_name": _text(row["source_name"], field="source source_name"),
    }
