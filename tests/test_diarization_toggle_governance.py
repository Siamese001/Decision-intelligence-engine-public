"""
tests/test_diarization_toggle_governance.py

Wave 1 Acceptance Gate: Speaker Diarization Governance & Reason-Coded Gate.
Validates that speaker diarization is strictly ON by default, and disabling diarization
mandates an authorized, defended reason code (SINGLE_SPEAKER_CONFIRMED,
DIARIZATION_FAILED_IRRECOVERABLY, SOURCE_ALREADY_ATTRIBUTED), failing closed otherwise.
"""
import pytest

from dual_pipeline.transcript_stages_core.hitl_guard import (
    DiarizationDefenseReason,
    validate_diarization_toggle,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_default_diarization_is_on():
    meta = {}
    res = validate_diarization_toggle(meta)
    assert res.get("speaker_diarization") is True
    assert "diarization_defense_code" not in res


@pytest.mark.parametrize("flag_key", ["speaker_diarization", "diarize", "enable_diarization", "diarization"])
def test_unauthorized_toggle_off_raises_value_error(flag_key):
    meta = {flag_key: False}
    with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
        validate_diarization_toggle(meta)


def test_unauthorized_diarization_status_off_raises_value_error():
    meta = {"diarization_status": "OFF"}
    with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
        validate_diarization_toggle(meta)


def test_invalid_defense_reason_raises_value_error():
    meta = {
        "speaker_diarization": False,
        "diarization_off_reason": "UNSUPPORTED_REASON_CODE",
    }
    with pytest.raises(ValueError, match="Invalid diarization defense reason 'UNSUPPORTED_REASON_CODE'"):
        validate_diarization_toggle(meta)


@pytest.mark.parametrize("reason", [
    DiarizationDefenseReason.SINGLE_SPEAKER_CONFIRMED,
    DiarizationDefenseReason.DIARIZATION_FAILED_IRRECOVERABLY,
    DiarizationDefenseReason.SOURCE_ALREADY_ATTRIBUTED,
])
def test_valid_defense_reason_succeeds(reason):
    meta = {
        "speaker_diarization": False,
        "diarization_off_reason": reason.value,
    }
    res = validate_diarization_toggle(meta)
    assert res["diarization_defense_verified"] is True
    assert res["diarization_defense_code"] == reason.value
    assert res["diarization_status"] == "UNDIARIZED"


def test_u0_ingestion_boundary_enforces_diarization_defense():
    u0 = U0IngestionBoundary()
    raw_transcript = "00:00:01 Speaker: Hello team.\n00:00:05 Speaker: Moving to agenda."

    # 1. Attempt to bypass/disable without reason -> must raise
    invalid_meta = {
        "meeting_name": "Test Meeting",
        "date": "09-24-2026",
        "time": "10:00 AM",
        "classification": "internal",
        "speaker_diarization": False,
    }
    with pytest.raises(ValueError, match="Speaker diarization is enforced ON by default"):
        u0.execute(raw_transcript, invalid_meta)

    # 2. Defend with valid reason -> must pass
    valid_meta = {
        "meeting_name": "Test Meeting",
        "date": "09-24-2026",
        "time": "10:00 AM",
        "classification": "internal",
        "speaker_diarization": False,
        "diarization_off_reason": "SINGLE_SPEAKER_CONFIRMED",
    }
    out = u0.execute(raw_transcript, valid_meta)
    assert out["metadata"]["diarization_defense_verified"] is True
    assert out["metadata"]["diarization_defense_code"] == "SINGLE_SPEAKER_CONFIRMED"
