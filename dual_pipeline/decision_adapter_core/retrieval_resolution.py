"""Sidecar retrieval reference resolution through reference monitor."""
from __future__ import annotations

from typing import Any, Iterable

from ..authorization import AuthorizationContext
from ..core import digest
from ..reference_monitor import ReferenceMonitor
from .constants import RuntimeDecisionSourceError
from .projections import _visible_records


def resolve_visible_source_assertions(
    reference_monitor: ReferenceMonitor,
    records: list[dict[str, Any]],
    *,
    graph_assertion_ids: Iterable[str],
    evidence_ids: Iterable[str],
    audience: str,
    authorization_context: AuthorizationContext,
    known_at: str,
) -> dict[str, Any]:
    """Resolve a sidecar record's graph references through the monitor."""
    raw_assertion_ids = list(graph_assertion_ids)
    raw_evidence_ids = list(evidence_ids)
    if (
        not raw_assertion_ids
        or not raw_evidence_ids
        or any(not isinstance(item, str) or not item.strip() for item in raw_assertion_ids)
        or any(not isinstance(item, str) or not item.strip() for item in raw_evidence_ids)
    ):
        raise RuntimeDecisionSourceError(
            "retrieval graph assertion and evidence IDs must be nonempty text lists"
        )
    assertion_ids = {item.strip() for item in raw_assertion_ids}
    evidence_id_set = {item.strip() for item in raw_evidence_ids}
    if len(assertion_ids) != len(raw_assertion_ids) or len(evidence_id_set) != len(
        raw_evidence_ids
    ):
        raise RuntimeDecisionSourceError(
            "retrieval graph assertion and evidence IDs must not contain duplicates"
        )
    matched = [
        record
        for record in records
        if record["assertion_id"] in assertion_ids and record["evidence_id"] in evidence_id_set
    ]
    visible = _visible_records(
        reference_monitor,
        records=matched,
        audience=audience,
        authorization_context=authorization_context,
        known_at=known_at,
    )
    mapped_assertion_ids = {item["assertion_id"] for item in matched}
    mapped_evidence_ids = {item["evidence_id"] for item in matched}
    return {
        "matched_source_assertion_count": len(matched),
        "source_mapping_complete": (
            mapped_assertion_ids == assertion_ids and mapped_evidence_ids == evidence_id_set
        ),
        "visible_source_assertion_count": len(visible),
        "monitor_filtered_source_assertion_count": len(matched) - len(visible),
        "source_assertions": [
            {
                "source_assertion_id": item["source_assertion"]["source_assertion_id"],
                "graph_assertion_id": item["assertion_id"],
                "evidence_id": item["evidence_id"],
                "source_id": item["source_assertion"]["source_id"],
                "source_version_id": item["source_assertion"]["source_version_id"],
                "classification": item["source_assertion"]["classification"],
                "audiences": list(item["source_assertion"]["audiences"]),
                "source_as_of": item["source_assertion"]["source_as_of"],
                "recorded_at": item["recorded_at"],
                "source_envelope_digest": digest(item["source_assertion"]),
            }
            for item in visible
        ],
        "monitor_filtered_before_exposure": True,
    }
