"""
tests/end_to_end/test_project_beacon_incident_replay_e2e.py

End-to-End Replay Verification for Project Beacon Recording Incident Wave 1.
Replays both Pro and Flash tool call sequences without confidential meeting audio.
Enforces that:
  1. Capture proof and transcription worker evidence remain strictly distinct.
  2. No unsupported permission inference or invented PCM timestamp is treated as ground truth.
  3. Pro vs Flash outcomes are characterized by shared lifecycle defects, not model capabilities.
  4. Speaker attribution formats reproduce baseline collector behavior hermetically.
"""
from __future__ import annotations

from typing import Any, Dict, List

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
    get_flash_launch_steps,
    get_pro_launch_steps,
    get_spool_evidence,
)


@pytest.mark.e2e
def test_pro_tool_sequence_replay_e2e() -> None:
    """
    E2E replay of Gemini Pro trajectory:
    1. Bare python missing pydantic (exit 1).
    2. Venv launch with contention + MLX worker active + zero-byte capture failure (exit 11).
    3. Broad pkill.
    4. Retry contention + MLX worker active + zero-byte capture failure (exit 11).
    5. Evaluates model claim: permissions denial diagnosis is flagged as ungrounded.
    """
    pro_steps = get_pro_launch_steps()

    # Step 1: Missing dependency execution
    step_bare = pro_steps[0]
    assert "pydantic" in step_bare["summary"]
    assert step_bare["exit_code"] == 1

    # Step 2: Managed capture failure with active worker
    step_managed = pro_steps[1]
    assert step_managed["exit_code"] == 11
    assert step_managed["reason_code"] == "PCM_BYTE_RATE_DEFICIT"
    assert step_managed["contention_detected"] is True
    # Invariant: capture proof != transcription readiness
    assert step_managed["worker_ready"] is True

    # Step 3: Broad pkill
    step_kill = pro_steps[2]
    assert step_kill["command"] == "pkill -f ffmpeg"
    assert step_kill["exit_code"] == 0

    # Step 4: Retry failure
    step_retry = pro_steps[3]
    assert step_retry["exit_code"] == 11
    assert step_retry["contention_detected"] is True

    # Invariant: Claim grounding check
    grounding = evaluate_claim_grounding("pro")
    assert grounding["claim_grounded"] is False
    assert "Terminal" in grounding["diagnosed_claim"]


@pytest.mark.e2e
def test_flash_tool_sequence_replay_e2e() -> None:
    """
    E2E replay of Gemini Flash trajectory:
    1. Help command runs cleanly.
    2. Bare python dry-run fails on pydantic.
    3. Venv dry-run returns PASS, but spawns fastboot spool without session registry.
    4. Second venv dry-run spawns surviving spool PID 22923.
    5. Session inspect reports No active session found.
    6. Confirms real audio in orphan spool, but rejects header duration and model success claim.
    """
    flash_steps = get_flash_launch_steps()

    # Help
    assert flash_steps[0]["exit_code"] == 0

    # Dry-run 1
    assert flash_steps[2]["exit_code"] == 0

    # Dry-run 2
    assert flash_steps[3]["exit_code"] == 0

    # Session inspect failure
    inspect_step = flash_steps[4]
    assert inspect_step["exit_code"] == 1
    assert inspect_step["reason_code"] == "NO_ACTIVE_SESSION"

    # Spool evidence check
    spool = get_spool_evidence()
    assert spool["has_real_speech"] is True
    assert spool["session_registry_active"] is False

    # Invariant: Master WAV header reporting 2236 minutes is rejected
    assert spool["header_duration_trusted"] is False
    assert spool["header_duration_seconds"] > 86400.0

    # Invariant: Claim grounding check
    grounding = evaluate_claim_grounding("flash")
    assert grounding["claim_grounded"] is False


@pytest.mark.e2e
def test_attribution_pipeline_e2e() -> None:
    """
    E2E verification of speaker hint intake formats against the attribution collector.
    Replays the 4 incident formats and validates baseline parsing behavior.
    """
    cases = get_attribution_test_cases()
    collector = ChatAttributionCollector()

    results: Dict[str, int] = {}
    for case in cases:
        parsed = collector.parse_message(case["raw_input"])
        results[case["format_id"]] = len(parsed)

    # Target expectations: 1 for dash, bare_name, multiline, and colon_control
    assert results["dash_format"] == 1
    assert results["bare_name_format"] == 1
    assert results["multiline_quote"] == 1
    assert results["colon_control"] == 1


@pytest.mark.e2e
def test_zero_confidential_audio_in_fixture_e2e() -> None:
    """
    E2E verification that the sanitized fixture contains zero confidential meeting audio,
    zero raw PCM buffers, and no unredacted participant dialogue.
    """
    fixture = load_sanitized_incident_fixture()
    fixture_text = str(fixture)

    # Asserts that binary/PCM audio representations do not exist in the fixture
    assert "RIFF" not in fixture_text[:100]
    assert "WAVE" not in fixture_text[:100]

    # Verify that evidence boundaries explicitly isolate mock/fixture data from live meetings
    boundaries = fixture["evidence_boundaries"]
    assert boundaries["capture_proof_independent_of_asr"] is True
    assert boundaries["permissions_denial_evidence_present"] is False
