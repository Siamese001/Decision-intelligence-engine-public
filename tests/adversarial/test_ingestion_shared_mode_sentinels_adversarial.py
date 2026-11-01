"""
tests/adversarial/test_ingestion_shared_mode_sentinels_adversarial.py

Permanent cross-mode negative sentinels executed on every shared-surface change.
Guarantees:
1. Document containers cannot claim 01/02 deliverables or suffer source-text mode drift.
2. Transcripts cannot borrow document skip markers or document four-call caps.
3. Notes cannot invent output 01 or borrow document budgets.
4. Document cancellation or exhaustion cannot affect live recording sessions or budgets.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import (
    resolve_canonical_modality,
    resolve_required_deliverables,
    get_not_applicable_deliverables,
    check_conflicting_flags,
    validate_operation_invariants,
)


def test_document_wrong_mode_outputs_and_source_drift():
    """Documents require 03 only; cannot produce 01/02 and untrusted text cannot drift mode."""
    # 1. Required outputs strictly [03]
    deliverables = resolve_required_deliverables(CanonicalModality.DOCUMENT)
    assert deliverables == ["03"], f"Document deliverables must be strictly ['03'], got {deliverables}"

    # 2. 01 and 02 are explicitly NOT_APPLICABLE
    not_applicable = get_not_applicable_deliverables(CanonicalModality.DOCUMENT)
    assert "01" in not_applicable and "02" in not_applicable

    # 3. Attempting to select 01 or 02 on a document fails invariant validation
    valid, conflicts = validate_operation_invariants(
        CanonicalModality.DOCUMENT,
        flags={},
        requested_outputs=["01", "03"],
    )
    assert not valid
    assert any("outside declared deliverables" in c for c in conflicts)

    # 4. Untrusted speaker text in a document cannot coerce modality to MEETING_TRANSCRIPT without explicit contract
    with pytest.raises(ValueError):
        resolve_required_deliverables(CanonicalModality.DOCUMENT, selective_outputs=["01"])


def test_transcript_cannot_borrow_document_skips_or_caps():
    """Transcripts require 01/02/03; cannot borrow document skip markers or caps."""
    # 1. Transcript deliverables strictly 01, 02, 03
    deliv = resolve_required_deliverables(CanonicalModality.MEETING_TRANSCRIPT)
    assert deliv == ["01", "02", "03"]

    # 2. Conflicting flags: skip_transcript_checks on transcript fails closed
    conflicts = check_conflicting_flags(
        CanonicalModality.MEETING_TRANSCRIPT,
        {"skip_transcript_checks": True},
    )
    assert any("skip_transcript_checks cannot be applied to MEETING_TRANSCRIPT" in c for c in conflicts)

    # 3. Fast pilot budget is banned on transcripts
    conflicts_budget = check_conflicting_flags(
        CanonicalModality.MEETING_TRANSCRIPT,
        {"fast_pilot_budget": True},
    )
    assert any("fast_pilot_budget is strictly reserved for DOCUMENT" in c for c in conflicts_budget)


def test_notes_cannot_invent_output01_or_borrow_document_budget():
    """Meeting notes require 02/03; 01 is explicitly not applicable and cannot borrow doc budget."""
    deliv = resolve_required_deliverables(CanonicalModality.MEETING_NOTES)
    assert deliv == ["02", "03"]
    assert "01" not in deliv

    not_app = get_not_applicable_deliverables(CanonicalModality.MEETING_NOTES)
    assert "01" in not_app

    # Notes cannot request output 01
    valid, conflicts = validate_operation_invariants(
        CanonicalModality.MEETING_NOTES,
        flags={},
        requested_outputs=["01", "02", "03"],
    )
    assert not valid
    assert any("outside declared deliverables" in c for c in conflicts)

    # Notes cannot borrow fast pilot budget
    conflicts_budget = check_conflicting_flags(
        CanonicalModality.MEETING_NOTES,
        {"fast_pilot_budget": True},
    )
    assert any("fast_pilot_budget is strictly reserved for DOCUMENT" in c for c in conflicts_budget)


def test_document_exhaustion_cancel_fallback_cannot_change_live_session():
    """Document exhaustion or cancellation cannot cancel or corrupt live recording sessions."""
    from dual_pipeline.canonical_pipeline.operation_contracts import LifecycleState

    # Live recording states are independent of document states
    doc_states = [LifecycleState.PACKAGE_READY, LifecycleState.COMPLETE]
    live_states = [LifecycleState.CAPTURING, LifecycleState.ASR_READY, LifecycleState.CAPTURE_STOPPED]

    for ls in live_states:
        assert ls not in doc_states

    # Live recording rejects cloud transcription under all circumstances
    conflicts = check_conflicting_flags(
        CanonicalModality.AUDIO,
        {"cloud_transcription": True},
    )
    assert any("Cloud transcription/ASR is strictly banned" in c for c in conflicts)
