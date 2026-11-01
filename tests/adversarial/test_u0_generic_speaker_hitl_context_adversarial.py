"""
tests/adversarial/test_u0_generic_speaker_hitl_context_adversarial.py

Wave 2 Adversarial & Edge-Case Suite covering:
1. Boundary payloads (single-turn dialogue at index 0 with unavailable neighboring turns).
2. Invariant bypass attempts (unmapped generic speakers without speaker_map or hints).
3. Rollback & recovery classification (all HITLRequiredError subclasses classify as HUMAN_WAIT in DLQ).
"""
from __future__ import annotations

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    PreflightBatchReport,
    PreflightDiagnostic,
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
    raise_if_preflight_failed,
)
from dual_pipeline.dlq.lifecycle import FailureCategory, classify_failure
from dual_pipeline.speaker_attribution_core.models import SpeakerAttributionCollisionError
from dual_pipeline.speaker_attribution_core.review_context_render import (
    validate_visible_speaker_context,
)


def test_boundary_single_turn_generic_speaker_context_renders_unavailable_neighbors() -> None:
    """
    Boundary payload: Single-turn transcript with generic speaker 'Speaker' at turn index 0
    still builds a compliant 5-row review context with [Unavailable — start/end of recording].
    """
    meta = {
        "project": "project_beacon",
        "meeting_name": "Single Turn Boundary",
        "date": "09/09/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "attendees": [{"name": "logan Miller", "role": "Chief AI and Data Officer", "org": "Apex Global Enterprises"}],
        "attendees_confirmed_by_operator": True,
        "attendee_roles_confirmed_by_operator": True,
    }
    dialogue = "[00:00] **Speaker:** Single isolated turn in recording."

    report = batch_validate_preflight_checks(meta, raw_transcript=dialogue)
    assert not report.is_valid
    assert "speaker_map" in report.missing_fields
    assert len(report.review_contexts) == 1
    validate_visible_speaker_context(report.review_contexts[0]["prompt"])


def test_invariant_bypass_partial_speaker_map_still_blocks_unmapped_cluster() -> None:
    """
    Invariant bypass attempt: Providing speaker_map for SPEAKER_00 while leaving SPEAKER_01
    unmapped must still fail closed with U0_UNMAPPED_SPEAKERS and build context for SPEAKER_01.
    """
    meta = {
        "project": "project_beacon",
        "meeting_name": "Partial Map Bypass Attempt",
        "date": "09/09/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "speaker_map": {"SPEAKER_00": "David Childers"},
        "attendees": [
            {"name": "David Childers", "role": "Scrum Master", "org": "Apex Global Enterprises"},
            {"name": "Brandon Que", "role": "Project Lead", "org": "Apex Global Enterprises"},
        ],
        "attendees_confirmed_by_operator": True,
        "attendee_roles_confirmed_by_operator": True,
    }
    dialogue = (
        "[00:01] **SPEAKER_00:** Mapped first turn.\n"
        "[00:05] **SPEAKER_01:** Unmapped second turn requiring review.\n"
        "[00:10] **SPEAKER_00:** Follow-up turn."
    )

    report = batch_validate_preflight_checks(meta, raw_transcript=dialogue)
    assert not report.is_valid
    assert "speaker_map" in report.missing_fields
    assert len(report.review_contexts) == 1
    assert report.review_contexts[0]["speaker_cluster"] == "SPEAKER_01"

    with pytest.raises(UnifiedPreflightCascadeError) as exc_info:
        raise_if_preflight_failed(report)
    assert len(exc_info.value.review_contexts) == 1


def test_dlq_lifecycle_classifies_all_hitl_subclasses_as_human_wait() -> None:
    """
    Rollback & recovery: Verify UnifiedPreflightCascadeError and SpeakerAttributionCollisionError
    both classify as FailureCategory.HUMAN_WAIT rather than FailureCategory.UNKNOWN.
    """
    report = PreflightBatchReport(
        is_valid=False,
        missing_fields=["speaker_map"],
        diagnostics=[
            PreflightDiagnostic(
                check_name="U0_UNMAPPED_SPEAKERS",
                severity="BLOCKING",
                field_name="speaker_map",
                message="Unmapped generic speakers.",
                remediation="Resolve speaker map.",
            )
        ],
    )
    cascade_err = UnifiedPreflightCascadeError(report)
    collision_err = SpeakerAttributionCollisionError(
        "Duplicate speaker assignment",
        unmapped_speakers=["SPEAKER_01"],
    )

    assert classify_failure(cascade_err) == FailureCategory.HUMAN_WAIT
    assert classify_failure(collision_err) == FailureCategory.HUMAN_WAIT
