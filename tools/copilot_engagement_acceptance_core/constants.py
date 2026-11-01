"""
tools/copilot_engagement_acceptance_core/constants.py

Declarative test suites, deliverables, and frozen fixture paths for
Copilot engagement acceptance waves.
"""
from __future__ import annotations

from typing import Dict, List

WAVE_TEST_FILES: Dict[int, List[str]] = {
    1: [
        "tests/test_copilot_engagement_wave1.py",
        "tests/end_to_end/test_copilot_engagement_wave1_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave1_adversarial.py",
    ],
    2: [
        "tests/test_copilot_engagement_wave2.py",
        "tests/end_to_end/test_copilot_engagement_wave2_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave2_adversarial.py",
    ],
    3: [
        "tests/test_copilot_engagement_wave3.py",
        "tests/end_to_end/test_copilot_engagement_wave3_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave3_adversarial.py",
    ],
    4: [
        "tests/test_copilot_engagement_wave4.py",
        "tests/end_to_end/test_copilot_engagement_wave4_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave4_adversarial.py",
    ],
    5: [
        "tests/test_copilot_engagement_wave5.py",
        "tests/end_to_end/test_copilot_engagement_wave5_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave5_adversarial.py",
    ],
}

WAVE_DELIVERABLES: Dict[int, List[str]] = {
    1: [
        "tools/run_copilot_engagement_acceptance.py",
        "tests/test_copilot_engagement_wave1.py",
        "tests/end_to_end/test_copilot_engagement_wave1_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave1_adversarial.py",
        "tests/manifests/copilot_engagement_acceptance.json",
        "tests/fixtures/copilot_engagement/incident_answers_snapshot.jsonl",
        "tests/fixtures/copilot_engagement/session_snapshot.json",
        "tests/fixtures/copilot_engagement/evidence_manifest.json",
        "tests/fixtures/copilot_engagement/engagement_fixtures.json",
        "tests/fixtures/copilot_engagement/loader.py",
    ],
    2: [
        "dual_pipeline/live_meeting_recorder_core/session_identity.py",
        "dual_pipeline/live_meeting_recorder_core/session_registry.py",
        "dual_pipeline/live_meeting_recorder.py",
        "dual_pipeline/live_meeting_recorder_core/live_buffer_reader.py",
        "dual_pipeline/cli/copilot_session.py",
        "dual_pipeline/cli/parser.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_engine.py",
        "tests/test_copilot_engagement_wave2.py",
        "tests/end_to_end/test_copilot_engagement_wave2_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave2_adversarial.py",
        "tests/manifests/copilot_engagement_acceptance.json",
    ],
    3: [
        "dual_pipeline/live_meeting_recorder_core/copilot_connection_policy.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_neighborhood.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_retriever.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_engine.py",
        "tests/test_copilot_engagement_wave3.py",
        "tests/end_to_end/test_copilot_engagement_wave3_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave3_adversarial.py",
        "tests/manifests/copilot_engagement_acceptance.json",
    ],
    4: [
        "dual_pipeline/live_meeting_recorder_core/copilot_clause_verifier.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_validator.py",
        "dual_pipeline/live_meeting_recorder_core/streaming_copilot.py",
        "dual_pipeline/live_meeting_recorder_core/copilot.py",
        "dual_pipeline/live_meeting_recorder_core/copilot_engine.py",
        "tests/test_copilot_engagement_wave4.py",
        "tests/end_to_end/test_copilot_engagement_wave4_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave4_adversarial.py",
        "tests/manifests/copilot_engagement_acceptance.json",
    ],
    5: [
        "reports/copilot_engagement/rollback_instructions.md",
        "tests/test_copilot_engagement_wave5.py",
        "tests/end_to_end/test_copilot_engagement_wave5_e2e.py",
        "tests/adversarial/test_copilot_engagement_wave5_adversarial.py",
        "tests/manifests/copilot_engagement_acceptance.json",
    ],
}

FROZEN_FIXTURE_PATHS: List[str] = [
    "tests/fixtures/copilot_engagement/incident_answers_snapshot.jsonl",
    "tests/fixtures/copilot_engagement/session_snapshot.json",
    "tests/fixtures/copilot_engagement/evidence_manifest.json",
    "tests/fixtures/copilot_engagement/engagement_fixtures.json",
]
