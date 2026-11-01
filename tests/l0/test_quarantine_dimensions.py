"""Test suite for Law 5 (Multi-Dimensional Soft Quarantine)."""
from __future__ import annotations

from dual_pipeline.l0.contracts import QuarantineReason
from dual_pipeline.l0.quarantine import (
    apply_quarantine_to_dict,
    evaluate_quarantine_profile,
)


def test_independent_quarantine_dimensions():
    """Verifies that each dimension is evaluated independently."""
    # Low confidence triggers quarantine
    p1 = evaluate_quarantine_profile(
        strategic_value=0.90,
        evidence_quality=0.90,
        confidence=0.40,  # Below 0.60
        tacticality=0.20,
    )
    assert p1.is_quarantined is True
    assert p1.quarantine_reason == QuarantineReason.LOW_CONFIDENCE.value

    # High tacticality triggers quarantine even with high confidence
    p2 = evaluate_quarantine_profile(
        strategic_value=0.80,
        evidence_quality=0.85,
        confidence=0.95,
        tacticality=0.90,  # Above 0.85 ceiling
    )
    assert p2.is_quarantined is True
    assert p2.quarantine_reason == QuarantineReason.HIGH_TACTICALITY.value

    # High quality strategic item passes unquarantined
    p3 = evaluate_quarantine_profile(
        strategic_value=0.85,
        evidence_quality=0.90,
        confidence=0.92,
        tacticality=0.30,
    )
    assert p3.is_quarantined is False
    assert p3.quarantine_reason == QuarantineReason.NONE.value


def test_quarantine_application_to_dict():
    """Verifies non-destructive tagging of entity dictionaries."""
    entity = {"node_id": "n_100", "name": "Kafka Cluster"}
    profile = evaluate_quarantine_profile(0.7, 0.8, 0.9, 0.2)
    updated = apply_quarantine_to_dict(entity, profile)

    assert updated["node_id"] == "n_100"
    assert updated["is_quarantined"] is False
    assert updated["confidence"] == 0.9
    assert updated["tacticality"] == 0.2
