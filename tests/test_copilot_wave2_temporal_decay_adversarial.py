"""
Wave 2 adversarial and edge-case tests for predicate-aware graph temporal decay.
Covers:
1. Boundary payloads (missing timestamps, empty dates, zero reference epoch)
2. Malformed inputs (corrupt ISO strings, negative epochs, non-dict assertions)
3. Overflow & future timestamp clamping (assertions dated in the future relative to reference)
4. Supporting evidence metadata fallback when assertion lacks direct source_date_normalized
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever_ranking import (
    compute_temporal_decay,
    parse_assertion_epoch,
    resolve_assertion_epoch,
)


@dataclass
class EdgeTopology:
    nodes: Dict[str, Any] = field(default_factory=dict)
    assertions: Dict[str, Any] = field(default_factory=dict)
    supporting_evidence: Dict[str, List[str]] = field(default_factory=dict)
    evidence_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def test_malformed_and_missing_dates_fallback_gracefully() -> None:
    assert parse_assertion_epoch(None) == 0.0
    assert parse_assertion_epoch("") == 0.0
    assert parse_assertion_epoch("not-a-date") == 0.0
    assert parse_assertion_epoch("2026-99-99") == 0.0
    assert parse_assertion_epoch(-500) == 0.0

    # Valid US MM-DD-YYYY and ISO formats parse cleanly
    ep_us = parse_assertion_epoch("10-25-2026")
    ep_iso = parse_assertion_epoch("2026-10-25")
    assert ep_us > 0.0
    assert abs(ep_us - ep_iso) < 1.0

    # Missing timestamp returns neutral 0.85 multiplier without crashing
    ref = parse_assertion_epoch("2026-10-27")
    assert compute_temporal_decay({"predicate": "HAS_RISK"}, ref) == 0.85
    assert compute_temporal_decay(None, ref) == 1.0  # type: ignore[arg-type]


def test_future_timestamp_clamped_without_overflow() -> None:
    ref = parse_assertion_epoch("2026-10-20")
    future_ep = parse_assertion_epoch("2027-05-01")
    asrt = {"predicate": "DELIVERS", "_observed_epoch": future_ep}
    mult = compute_temporal_decay(asrt, ref)
    # age_days is clamped to 0.0, so freshness boost is bounded at 1.15
    assert 1.14 <= mult <= 1.16


def test_evidence_metadata_date_fallback() -> None:
    topo = EdgeTopology(
        nodes={
            "project:alpha": {
                "node_id": "project:alpha",
                "canonical_name": "Alpha",
                "node_type": "workstream",
                "engagement_id": "alpha",
            }
        },
        assertions={
            "a1": {
                "assertion_id": "a1",
                "subject_node_id": "project:alpha",
                "object_node_id": "project:alpha",
                "predicate": "DELIVERS",
                "claim_text": "Alpha delivers milestone.",
                "canonical_status": "canonical",
                "engagement_id": "alpha",
                # No source_date_normalized on assertion itself
            }
        },
        supporting_evidence={"a1": ["ev_101"]},
        evidence_meta={"ev_101": {"source_date_normalized": "2026-10-24"}},
    )
    ep = resolve_assertion_epoch(topo.assertions["a1"], topo)
    assert ep == parse_assertion_epoch("2026-10-24")

    retriever = CopilotGraphRetriever(topology=topo, engagement_id="alpha", session_id="s1", generation=1)
    assert retriever._reference_epoch == ep
