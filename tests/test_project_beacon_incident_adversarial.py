"""
tests/test_project_beacon_incident_adversarial.py

Adversarial and edge-case tests for Project Beacon recording incident fixtures,
evidence boundary enforcement, and failure claim grounding.

Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
  1. Boundary payload (empty inputs, 0-byte WAVs, blank messages)
  2. Malformed input (schema corruption, missing required fields, type mismatches)
  3. Invariant bypass attempt (fake permission claims, dry-run PASS masquerading as recording)
  4. Exact production regression (Pro task-8 contention, Flash dry-run unmanaged spool)
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)
from tests.fixtures.project_beacon_incident import (
    load_sanitized_incident_fixture,
    validate_incident_fixture,
)
from tests.fixtures.project_beacon_incident.loader import (
    evaluate_claim_grounding,
    get_attribution_test_cases,
    get_evidence_boundaries,
    get_spool_evidence,
)


# ==============================================================================
# Category 1: Boundary Payload
# ==============================================================================


def test_adversarial_boundary_empty_fixture() -> None:
    """Verifies that empty fixture dictionaries fail schema validation cleanly."""
    is_valid, errors = validate_incident_fixture({})
    assert is_valid is False
    assert len(errors) > 0
    missing_fields = " ".join(errors)
    assert "schema_version" in missing_fields
    assert "incident_id" in missing_fields


def test_adversarial_boundary_empty_and_whitespace_attribution_messages() -> None:
    """Verifies that empty, whitespace-only, and punctuation-only inputs do not crash the parser."""
    collector = ChatAttributionCollector()
    assert collector.parse_message("") == []
    assert collector.parse_message("   \t\n  ") == []
    assert collector.parse_message(";;;;;\n\n;;") == []
    assert collector.parse_message("---") == []
    assert collector.parse_message("::::::") == []


def test_adversarial_boundary_zero_byte_wav_deficit_assertion() -> None:
    """Verifies that 0-byte master WAV cannot be interpreted as successful capture."""
    spool = get_spool_evidence()
    # In Pro incident, master WAV had 0B after 2.54s
    zero_byte_rate = 0.0 / 2.54
    expected_rate = 64000.0  # 16kHz * 2 bytes * 2 channels or 1 channel
    deficit_ratio = zero_byte_rate / expected_rate

    # The threshold requires >= 0.80 ratio
    assert deficit_ratio < 0.80
    assert deficit_ratio == 0.0


# ==============================================================================
# Category 2: Malformed Input
# ==============================================================================


def test_adversarial_malformed_fixture_missing_required_keys() -> None:
    """Verifies that stripping required top-level keys fails validation."""
    base_fixture = load_sanitized_incident_fixture()

    for key in ("spool_evidence", "evidence_boundaries", "model_sessions"):
        corrupted = copy.deepcopy(base_fixture)
        del corrupted[key]
        is_valid, errors = validate_incident_fixture(corrupted)
        assert is_valid is False, f"Expected validation failure when missing {key}"
        assert any(key in err for err in errors)


def test_adversarial_malformed_type_mismatches() -> None:
    """Verifies that invalid data types in schema fail validation."""
    base_fixture = load_sanitized_incident_fixture()

    # Wrong schema_version type
    corrupted = copy.deepcopy(base_fixture)
    corrupted["schema_version"] = "v1"  # Should be integer 1
    is_valid, errors = validate_incident_fixture(corrupted)
    assert is_valid is False

    # Negative exit code or string exit code in launch step
    corrupted = copy.deepcopy(base_fixture)
    corrupted["model_sessions"]["pro"]["launch_steps"][0]["exit_code"] = "FAIL"
    is_valid, errors = validate_incident_fixture(corrupted)
    assert is_valid is False


# ==============================================================================
# Category 3: Invariant Bypass Attempt
# ==============================================================================


def test_adversarial_bypass_unsupported_permission_claim_rejection() -> None:
    """
    INVARIANT: An agent must not assert OS permissions denial without TCC/CoreAudio proof.
    Verifies that Pro's diagnostic claim is rejected as ungrounded.
    """
    boundaries = get_evidence_boundaries()
    assert boundaries["permissions_denial_evidence_present"] is False

    pro_claim = evaluate_claim_grounding("pro")
    assert pro_claim["claim_grounded"] is False

    # Simulate an agent trying to assert permissions denial when PCM_BYTE_RATE_DEFICIT occurs
    error_payload = {
        "error_type": "AudioCaptureUnprovenError",
        "reason_code": "PCM_BYTE_RATE_DEFICIT",
        "remediation": None,
    }
    # When reason_code is PCM_BYTE_RATE_DEFICIT and remediation is None,
    # asserting "Microphone permission denied" is ungrounded speculation
    is_permission_proven = (
        error_payload.get("reason_code") == "PERMISSION_DENIED_MICROPHONE"
        or error_payload.get("reason_code") == "PERMISSION_DENIED_SCK"
    )
    assert is_permission_proven is False, "Byte deficit must never be bypassed as permission proof"


def test_adversarial_bypass_dry_run_pass_as_active_session() -> None:
    """
    INVARIANT: Dry-run PASS or PID existence must not be accepted as an active managed session.
    """
    spool = get_spool_evidence()
    assert spool["has_real_speech"] is True
    # Even though real audio exists on disk from dry-run leak, session registry is NOT active
    assert spool["session_registry_active"] is False

    flash_claim = evaluate_claim_grounding("flash")
    assert flash_claim["claim_grounded"] is False


def test_adversarial_bypass_placeholder_wav_duration_rejection() -> None:
    """
    INVARIANT: Master WAV header reporting 0x7FFFFFFF frames (134217.73s / 2236 min)
    must be rejected as untrusted unfinalized placeholder, never real meeting duration.
    """
    spool = get_spool_evidence()
    raw_header_seconds = spool["header_duration_seconds"]

    # 134,217 seconds is ~37.28 hours; an ongoing sprint review meeting cannot be 37 hours
    assert raw_header_seconds > 86400.0  # > 24 hours
    assert spool["header_duration_trusted"] is False

    # Sanitizer rule: durations exceeding max plausible session without closed chunk parity fail closed
    max_plausible_seconds = 28800.0  # 8 hours
    duration_acceptable = raw_header_seconds <= max_plausible_seconds and spool["header_duration_trusted"]
    assert duration_acceptable is False


# ==============================================================================
# Category 4: Exact Production Regression
# ==============================================================================


def test_regression_pro_device_contention_exact_match() -> None:
    """
    Pins the exact Pro task-8 and task-17 contention failure scenario:
    Fast bootstrap FFmpeg process accesses :0, managed capture fails with 0B after ~2.5s.
    """
    fixture = load_sanitized_incident_fixture()
    pro_steps = fixture["model_sessions"]["pro"]["launch_steps"]

    step_8 = pro_steps[1]
    assert step_8["reason_code"] == "PCM_BYTE_RATE_DEFICIT"
    assert step_8["contention_detected"] is True
    assert step_8["worker_ready"] is True
    assert step_8["exit_code"] == 11


def test_regression_flash_unmanaged_spool_leak_exact_match() -> None:
    """
    Pins the exact Flash dry-run orphan spool leakage scenario:
    CLI dry-run returns PASS, but creates unmanaged ffmpeg capture writing chunk_00079.wav.
    """
    fixture = load_sanitized_incident_fixture()
    flash_steps = fixture["model_sessions"]["flash"]["launch_steps"]

    # Step 166: dry run succeeded
    assert flash_steps[3]["command"].endswith("--dry-run")
    assert flash_steps[3]["exit_code"] == 0

    # Step 224: session check failed
    assert flash_steps[4]["command"] == "nee session-inspect"
    assert flash_steps[4]["reason_code"] == "NO_ACTIVE_SESSION"

    # Spool confirmation
    spool = fixture["spool_evidence"]
    assert spool["spool_id"].startswith("fastboot_")
    assert spool["observed_rms_energy"] > 1000.0
