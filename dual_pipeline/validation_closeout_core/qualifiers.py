"""Qualifiers serialization, edge description normalization, and row state projections."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .constants import (
    _ASSERTION_FIELDS,
    _QUEUE_FIELDS,
    CLOSEOUT_POLICY_VERSION,
)


def _queue_state(row: sqlite3.Row) -> dict[str, Any]:
    return {field: row[field] for field in _QUEUE_FIELDS}


def _assertion_state(row: sqlite3.Row) -> dict[str, Any]:
    return {field: row[field] for field in _ASSERTION_FIELDS}


def _closeout_qualifiers(raw: str, *, resolution: str, closed_at: str) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["nee_validation_closeout"] = {
        "policy_version": CLOSEOUT_POLICY_VERSION,
        "disposition": "not_substantiated",
        "resolution": resolution,
        "closed_at": closed_at,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _confirmation_qualifiers(
    raw: str,
    *,
    resolution: str,
    confirmed_at: str,
    approval_reference: str,
    disposition: str = "confirmed_mapping",
) -> str:
    """Add an exact-scope human validation receipt to an assertion."""

    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["nee_validation_closeout"] = {
        "policy_version": CLOSEOUT_POLICY_VERSION,
        "disposition": disposition,
        "resolution": resolution,
        "confirmed_at": confirmed_at,
        "approval_reference": approval_reference,
        "boundary": (
            "Confirms only the named source-field alignment for internal NEE use; "
            "it does not establish formal reporting lines, decision rights, or a broader RACI."
        ),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _confirmation_assertion_state(row: sqlite3.Row) -> dict[str, Any]:
    """Fields that must remain unchanged until an approved confirmation applies."""

    return {
        "assertion_id": row["assertion_id"],
        "canonical_status": row["canonical_status"],
        "currentness_state": row["currentness_state"],
        "canonical_eligible": row["canonical_eligible"],
        "retrieval_eligible": row["retrieval_eligible"],
        "validation_state": row["validation_state"],
        "confidence_label": row["confidence_label"],
        "confidence_score": row["confidence_score"],
        "hitl_required": row["hitl_required"],
        "validation_priority": row["validation_priority"],
        "validation_owner": row["validation_owner"],
        "approval_reference": row["approval_reference"],
        "primary_source_id": row["primary_source_id"],
        "qualifiers_json": row["qualifiers_json"],
    }


def _confirmation_edge_state(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "edge_id": row["edge_id"],
        "description": row["description"],
        "evidence_posture": row["evidence_posture"],
        "confidence": row["confidence"],
        "validation_priority": row["validation_priority"],
        "validation_owner": row["validation_owner"],
        "hitl_required": row["hitl_required"],
        "source_id": row["source_id"],
    }


def _confirmed_edge_description(description: str | None) -> str:
    """Retain raw-source limitations while replacing the stale open-HITL language."""

    text = (description or "").strip()
    stale = (
        "This structured role comes from the candidate one-column-left normalization "
        "of raw cells F16/G16/H16 and requires confirmation."
    )
    resolved = (
        "The one-column-left normalization of raw cells F16/G16/H16 was confirmed "
        "by the approved human decision on 2026-07-27 for internal NEE mapping use."
    )
    if stale in text:
        return text.replace(stale, resolved)
    return (
        f"{text} The approved human decision on 2026-07-27 confirms this source-field "
        "alignment for internal NEE mapping use."
    ).strip()
