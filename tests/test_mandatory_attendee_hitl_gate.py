"""
tests/test_mandatory_attendee_hitl_gate.py

Wave 3 Acceptance Gate: Mandatory Attendee HITL Confirmation Gate.
Validates that ingesting audio files or raw transcripts strictly mandates an atomic
HITL decision to confirm the attendee list before proceeding, failing closed otherwise.
"""
import pytest

from dual_pipeline.transcript_stages_core.attendee_gate import (
    extract_candidate_attendees,
    find_unrecognized_single_token_attendees,
    is_audio_or_raw_source,
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    C0ConformanceGate,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_is_audio_or_raw_source_detection():
    assert is_audio_or_raw_source({"_from_audio_transcription": True}) is True
    assert is_audio_or_raw_source({"source_type": "AUDIO_ONLY_UNDIARIZED"}) is True
    assert is_audio_or_raw_source({"source_modality": "RAW_TRANSCRIPT"}) is True
    assert is_audio_or_raw_source({"input_ext": ".m4a"}) is True
    assert is_audio_or_raw_source({"source_type": "STRUCTURED_DOC"}) is False


def test_extract_candidate_attendees_various_sources():
    # From roster dict
    meta1 = {"roster": {"PartnerAdvisory": ["Amit Ayer (Lead)", "Jen"]}}
    cands1 = extract_candidate_attendees(meta1)
    assert "Amit Ayer" in cands1
    assert "Jen" in cands1

    # From speaker map
    meta2 = {"speaker_map": {"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Mandy"}}
    cands2 = extract_candidate_attendees(meta2)
    assert cands2 == ["Amit Ayer", "Mandy"]

    # Fallback to unknown speaker
    meta_empty = {}
    cands3 = extract_candidate_attendees(meta_empty)
    assert cands3 == ["Unknown Speaker(s)"]


def test_mandatory_confirmation_blocks_unconfirmed_audio():
    meta = {
        "_from_audio_transcription": True,
        "meeting_name": "Architecture Review",
        "roster": ["Amit Ayer", "Mandy"],
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        verify_mandatory_attendee_confirmation(meta)

    err = exc_info.value
    assert getattr(err, "hitl_type", None) == "attendee_confirmation"
    assert "Mandatory Stage U0 Attendee Confirmation Gate" in str(err)
    assert hasattr(err, "options")
    assert len(err.options) >= 2


def test_mandatory_confirmation_passes_when_confirmed():
    meta = {
        "_from_audio_transcription": True,
        "meeting_name": "Architecture Review",
        "roster": ["Amit Ayer", "Mandy"],
    }
    # Record confirmation
    receipt = record_attendee_confirmation(meta, ["Amit Ayer", "Mandy"])
    assert receipt["decision_type"] == "ATTENDEE_CONFIRMATION"
    assert meta["attendees_confirmed_by_operator"] is True

    # Now verification must pass cleanly
    verify_mandatory_attendee_confirmation(meta)


def test_c0_conformance_gate_enforces_attendee_confirmation():
    gate = C0ConformanceGate()
    # 1. Unconfirmed audio -> blocks
    l0_ctx_unconfirmed = {
        "metadata": {
            "_from_audio_transcription": True,
            "meeting_name": "Audio Meeting",
        },
        "transcript_text": "Amit: Hello",
    }
    with pytest.raises(HITLRequiredError):
        gate.execute(l0_ctx_unconfirmed)

    # 2. Confirmed audio -> passes
    l0_ctx_confirmed = {
        "metadata": {
            "_from_audio_transcription": True,
            "attendees_confirmed_by_operator": True,
            "attendee_confirmation_receipt": {
                "status": "RESOLVED_HITL",
                "subject_sha256": "subj_c0_confirmed_123",
            },
            "meeting_name": "Audio Meeting",
        },
        "transcript_text": "Amit: Hello",
    }
    out = gate.execute(l0_ctx_confirmed)
    assert out["conformance_checked"] is True


def test_unrecognized_single_token_attendees_detection():
    # Multi-token or recognized entities should not be flagged as unrecognized single tokens
    meta_clean = {
        "roster": ["Amit Ayer", "Lan Tran", "Calvin Gilmartin"],
        "source_type": "copilot_notes",
        "is_notes": True,
    }
    assert find_unrecognized_single_token_attendees(meta_clean) == []

    # Single-token unrecognized entities must be detected
    meta_unrecognized = {
        "roster": ["Calvin", "Manasa", "Miriam"],
        "source_type": "copilot_notes",
        "is_notes": True,
    }
    unrec = find_unrecognized_single_token_attendees(meta_unrecognized)
    assert "Manasa" in unrec
    assert "Miriam" in unrec
    assert "Calvin" in unrec


def test_copilot_notes_with_unrecognized_single_token_blocks():
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "meeting_name": "EDO Work Sync",
        "roster": ["Calvin", "Manasa", "Miriam"],
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        verify_mandatory_attendee_confirmation(meta)

    err = exc_info.value
    assert getattr(err, "hitl_type", None) == "attendee_confirmation"
    assert "unrecognized single-token attendee(s)" in str(err)
    assert hasattr(err, "options")
    assert len(err.options) >= 2
    assert "Manasa" in err.unmapped_speakers
    assert "Miriam" in err.unmapped_speakers
    assert "Calvin" in err.unmapped_speakers


def test_copilot_notes_with_unrecognized_single_token_passes_when_confirmed():
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "meeting_name": "EDO Work Sync",
        "roster": ["Calvin", "Manasa", "Miriam"],
    }
    receipt = record_attendee_confirmation(meta, ["Calvin Gilmartin", "Manasa", "Miriam"])
    assert receipt["decision_type"] == "ATTENDEE_CONFIRMATION"
    assert meta["attendees_confirmed_by_operator"] is True

    # Must now pass without raising
    verify_mandatory_attendee_confirmation(meta)


def test_u0_ingestion_blocks_notes_with_unrecognized_single_token():
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "notes_mode": True,
        "meeting_name": "EDO Work Sync",
        "roster": ["Manasa", "Miriam"],
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        U0IngestionBoundary._inspect_roster_completeness(meta)

    err = exc_info.value
    assert getattr(err, "hitl_type", None) == "title_clarification" or "role" in str(err).lower()


def test_txt_transcript_source_detection_and_mandatory_gate():
    # .txt input without Stage 01 checkpoint must be detected as raw transcript
    meta_txt = {"input_path": "/tmp/meeting_transcript.txt", "meeting_name": "Sync"}
    assert is_audio_or_raw_source(meta_txt) is True

    with pytest.raises(HITLRequiredError) as exc_info:
        verify_mandatory_attendee_confirmation(meta_txt)
    assert exc_info.value.hitl_type == "attendee_confirmation"

    # With verified Stage 01 checkpoint receipt, is_audio_or_raw_source becomes False
    meta_frozen = {
        "input_path": "/tmp/meeting_transcript.txt",
        "meeting_name": "Sync",
        "stage01_checkpoint_passed": True,
        "roster": ["Amit Ayer", "Lan Tran"],
    }
    assert is_audio_or_raw_source(meta_frozen) is False
    # Verified Stage 01 with clean multi-token roster passes without error
    verify_mandatory_attendee_confirmation(meta_frozen)


def test_prepopulated_sidecar_cannot_bypass_dialogue_single_token_speakers():
    # Pre-populating sidecar attendees with multi-token names must not bypass
    # single-token speakers in raw transcript dialogue
    meta = {
        "meeting_name": "Cloud Strategy",
        "attendees": ["Amanda Knarr", "Calvin Gilmartin"],
        "source_type": "copilot_notes",
        "is_notes": True,
    }
    transcript = "Amanda: I will review the cloud architecture.\nCalvin: I'll coordinate EDO resources."

    with pytest.raises(HITLRequiredError) as exc_info:
        verify_mandatory_attendee_confirmation(meta, transcript)

    err = exc_info.value
    assert err.hitl_type == "attendee_confirmation"
    assert "Amanda" in err.unmapped_speakers
    assert "Calvin" in err.unmapped_speakers


def test_find_unrecognized_flags_when_surname_absent_from_transcript():
    meta = {
        "attendees": ["Amanda Knarr"],
        "source_type": "copilot_notes",
        "is_notes": True,
    }
    # Dialogue has only single-token 'Amanda' and does not mention 'Knarr'
    transcript = "Amanda: We have consensus on the proposal."
    unrec = find_unrecognized_single_token_attendees(meta, transcript)
    assert "Amanda" in unrec

    # Dialogue explicitly mentions the full name 'Amanda Knarr'
    transcript_full = "Amanda Knarr: We have consensus on the proposal."
    unrec_full = find_unrecognized_single_token_attendees(meta, transcript_full)
    assert "Amanda" not in unrec_full


