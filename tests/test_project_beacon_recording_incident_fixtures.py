"""
tests/test_project_beacon_recording_incident_fixtures.py

Tests verifying the sanitized Project Beacon incident fixtures, launch modes,
attribution formats, and evidence boundaries.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)
from tests.fixtures.project_beacon_incident import (
    load_incident_schema,
    load_sanitized_incident_fixture,
    validate_incident_fixture,
)
from tests.fixtures.project_beacon_incident.loader import (
    evaluate_claim_grounding,
    get_attribution_test_cases,
    get_evidence_boundaries,
    get_flash_launch_steps,
    get_pro_launch_steps,
    get_spool_evidence,
)


def test_fixture_schema_validation() -> None:
    """Verifies that the sanitized fixture strictly validates against the incident schema."""
    is_valid, errors = validate_incident_fixture()
    assert is_valid, f"Fixture schema validation failed: {errors}"
    assert len(errors) == 0

    schema = load_incident_schema()
    assert schema["title"] == "DigitalSherpaIncidentFixture"
    assert schema["type"] == "object"


def test_pro_session_replay_boundaries() -> None:
    """Verifies that Pro session steps preserve exact error types, exit codes, and contention warnings."""
    pro_steps = get_pro_launch_steps()
    assert len(pro_steps) == 4

    # Step 4: bare python failure
    s4 = pro_steps[0]
    assert s4["step_index"] == 4
    assert s4["exit_code"] == 1
    assert s4["error_type"] == "ModuleNotFoundError"
    assert s4["reason_code"] == "MISSING_PYDANTIC"
    assert not s4["worker_ready"]

    # Step 8: contention + PCM deficit
    s8 = pro_steps[1]
    assert s8["step_index"] == 8
    assert s8["exit_code"] == 11
    assert s8["error_type"] == "AudioCaptureUnprovenError"
    assert s8["reason_code"] == "PCM_BYTE_RATE_DEFICIT"
    assert s8["contention_detected"] is True
    assert s8["worker_ready"] is True  # MLX worker succeeded golden inference!

    # Step 15: broad pkill
    s15 = pro_steps[2]
    assert s15["step_index"] == 15
    assert s15["exit_code"] == 0

    # Step 17: retry contention + PCM deficit
    s17 = pro_steps[3]
    assert s17["step_index"] == 17
    assert s17["exit_code"] == 11
    assert s17["reason_code"] == "PCM_BYTE_RATE_DEFICIT"
    assert s17["contention_detected"] is True

    # Claim evaluation
    claim_meta = evaluate_claim_grounding("pro")
    assert claim_meta["claim_grounded"] is False
    assert "Terminal" in claim_meta["diagnosed_claim"]
    assert "contention" in claim_meta["actual_root_cause"].lower()


def test_flash_session_replay_boundaries() -> None:
    """Verifies that Flash session steps preserve dry-run leak and unmanaged spool observations."""
    flash_steps = get_flash_launch_steps()
    assert len(flash_steps) == 5

    # Step 4: help
    assert flash_steps[0]["exit_code"] == 0

    # Step 6: bare python dry-run
    assert flash_steps[1]["exit_code"] == 1
    assert flash_steps[1]["error_type"] == "ModuleNotFoundError"

    # Step 10: venv dry run returns PASS
    assert flash_steps[2]["exit_code"] == 0

    # Step 166: second venv dry run returns PASS
    assert flash_steps[3]["exit_code"] == 0

    # Step 224: session-inspect returns No active session found
    s224 = flash_steps[4]
    assert s224["step_index"] == 224
    assert s224["exit_code"] == 1
    assert s224["error_type"] == "ValueError"
    assert s224["reason_code"] == "NO_ACTIVE_SESSION"

    # Spool evidence
    spool = get_spool_evidence()
    assert spool["spool_id"] == "fastboot_1791306933_22912"
    assert spool["ffmpeg_pid"] == 22923
    assert spool["chunk_count"] == 317
    assert spool["has_real_speech"] is True
    assert spool["session_registry_active"] is False
    assert spool["header_duration_trusted"] is False
    assert spool["header_duration_seconds"] > 100000.0  # Corrupt 2236 minutes placeholder

    # Claim evaluation
    claim_meta = evaluate_claim_grounding("flash")
    assert claim_meta["claim_grounded"] is False


def test_attribution_parser_probe_target() -> None:
    """
    Directly tests ChatAttributionCollector against actual incident-style inputs.
    Verifies that dash, bare-name, and multiline formats yield target 1 record,
    matching expected speaker and quote.
    """
    cases = get_attribution_test_cases()
    assert len(cases) == 4

    collector = ChatAttributionCollector()

    for case in cases:
        raw_text = case["raw_input"]
        expected_target = case.get("target_parsed_count", 1)

        # Parse message using target parser
        parsed = collector.parse_message(raw_text)
        assert len(parsed) == expected_target, (
            f"Case {case['format_id']} expected {expected_target} parsed items, got {len(parsed)}"
        )

        record = parsed[0]
        assert record.speaker_name == case["expected_speaker"]
        assert record.quoted_text == case["expected_quote"]


def test_evidence_boundaries_integrity() -> None:
    """Verifies that evidence boundaries forbid conflating capture with transcription or permissions."""
    boundaries = get_evidence_boundaries()
    assert boundaries["capture_proof_independent_of_asr"] is True
    assert boundaries["permissions_denial_evidence_present"] is False
    assert boundaries["monotonic_zero_rejected_as_real_start"] is True
    assert boundaries["unmanaged_dry_run_classified_as_defect"] is True

    rules = boundaries["evidence_rules"]
    assert len(rules) >= 5
    joined_rules = " ".join(rules).lower()
    assert "pcm byte deficit" in joined_rules
    assert "mlx whisper worker readiness" in joined_rules
    assert "ffmpeg pid alive" in joined_rules
    assert "master wav header" in joined_rules
    assert "speaker attribution hints" in joined_rules
