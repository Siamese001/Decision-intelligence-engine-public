"""
tests/test_recording_startup_events.py

Wave 1 test suite establishing trustworthy timing and incident regression evidence.
Validates:
1. Incident fixture reproduces 141s pre-dispatch delay and 188s confirmation delay.
2. File birth cannot satisfy PCM proof.
3. Single microphone bootstrap cannot satisfy dual-source coverage.
4. Dual-source PCM proof strictly requires verified non-zero PCM from both mic and system audio.
5. Missing source or host events fail qualification completeness.
6. Cross-process clock domain safety (prohibits cross-domain monotonic subtraction).
7. Stale session events are strictly rejected.
8. Monotonic event ordering within the same clock domain.
9. ASR remains unknown/warming until a validated readiness receipt exists.
10. Event callback registration and delivery.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    AudioSourceType,
    ClockDomain,
    CorrelatedStartupEvent,
    StartupEventChronology,
    StartupEventName,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "jarvis_startup"


class TestRecordingStartupEvents:
    """Wave 1 regression and contract verification suite."""

    def test_incident_fixture_timing_reproduction(self) -> None:
        """The fixture must reproduce 141s before dispatch and 188s to confirmation."""
        fixture_path = FIXTURES_DIR / "sanitized_incident_chronology.json"
        assert fixture_path.is_file(), f"Fixture missing at {fixture_path}"

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert data["incident_id"] == "INCIDENT-20261007-JARVIS-STARTUP"
        assert data["build_id"] == "45b10fb983e2d0fb4b6c9573787a25733e60d94e"
        assert data["measured_pre_dispatch_delay_seconds"] == 141.0
        assert data["measured_total_confirmation_delay_seconds"] == 188.0
        assert data["pre_dispatch_tool_calls_count"] == 36

        events = data["events"]
        assert len(events) >= 10

        req_event = next(e for e in events if e["event_name"] == "REQUEST_RECEIVED")
        dispatch_event = next(e for e in events if e["event_name"] == "DISPATCH_INITIATED")
        ack_event = next(e for e in events if e["event_name"] == "HOST_ACKNOWLEDGED")

        pre_dispatch_delta = (
            dispatch_event["monotonic_offset_seconds"] - req_event["monotonic_offset_seconds"]
        )
        total_ack_delta = (
            ack_event["monotonic_offset_seconds"] - req_event["monotonic_offset_seconds"]
        )

        assert pre_dispatch_delta == 141.0, f"Expected 141.0s pre-dispatch delta, got {pre_dispatch_delta}"
        assert total_ack_delta == 188.0, f"Expected 188.0s confirmation delta, got {total_ack_delta}"

    def test_file_birth_cannot_satisfy_pcm_proof(self) -> None:
        """Opening a WAV container on disk cannot satisfy PCM proof."""
        chronology = StartupEventChronology(
            session_id="sess_001",
            request_id="req_001",
            build_id="45b10fb983e2d0fb4b6c9573787a25733e60d94e",
        )

        file_birth_event = CorrelatedStartupEvent(
            event_name=StartupEventName.FILE_BIRTH_CHUNK_MASTER,
            session_id="sess_001",
            request_id="req_001",
            source="filesystem",
            clock_domain=ClockDomain.FILESYSTEM_STAT,
            monotonic_timestamp=149.631,
            is_pcm_proven=False,
            is_file_birth=True,
            pcm_bytes=0,
        )
        chronology.add_event(file_birth_event)

        assert not chronology.is_dual_source_pcm_proven(), "File birth must not satisfy PCM proof"

    def test_single_source_bootstrap_cannot_satisfy_dual_source(self) -> None:
        """Microphone-only bootstrap chunk does not satisfy dual-source coverage."""
        chronology = StartupEventChronology(
            session_id="sess_002",
            request_id="req_002",
        )

        mic_event = CorrelatedStartupEvent(
            event_name=StartupEventName.BOOTSTRAP_CHUNK_SPLICED,
            session_id="sess_002",
            request_id="req_002",
            source=AudioSourceType.MIC.value,
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=1.2,
            is_pcm_proven=True,
            is_file_birth=False,
            pcm_bytes=19200,
        )
        chronology.add_event(mic_event)

        assert not chronology.is_dual_source_pcm_proven(), (
            "Microphone bootstrap alone must not satisfy dual-source coverage"
        )

    def test_dual_source_pcm_proven_when_both_sources_write_valid_pcm(self) -> None:
        """Both microphone and system audio must write verified non-zero PCM."""
        chronology = StartupEventChronology(
            session_id="sess_003",
            request_id="req_003",
        )

        mic_event = CorrelatedStartupEvent(
            event_name=StartupEventName.FIRST_VALID_PCM,
            session_id="sess_003",
            request_id="req_003",
            source=AudioSourceType.MIC.value,
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=2.1,
            is_pcm_proven=True,
            is_file_birth=False,
            pcm_bytes=32000,
        )
        sys_event = CorrelatedStartupEvent(
            event_name=StartupEventName.FIRST_VALID_PCM,
            session_id="sess_003",
            request_id="req_003",
            source=AudioSourceType.SYSTEM_AUDIO.value,
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=2.3,
            is_pcm_proven=True,
            is_file_birth=False,
            pcm_bytes=64000,
        )

        chronology.add_event(mic_event)
        assert not chronology.is_dual_source_pcm_proven()

        chronology.add_event(sys_event)
        assert chronology.is_dual_source_pcm_proven()

    def test_missing_source_or_host_events_fail_qualification(self) -> None:
        """Missing host request, dispatch, ack, or audio sources must fail qualification."""
        chronology = StartupEventChronology(session_id="sess_004", request_id="req_004")

        # Empty chronology fails
        passed, reasons = chronology.validate_qualification_completeness()
        assert not passed
        assert any("REQUEST_RECEIVED" in r for r in reasons)
        assert any("DISPATCH_INITIATED" in r for r in reasons)
        assert any("HOST_ACKNOWLEDGED" in r for r in reasons)
        assert any("DUAL_SOURCE_DEFICIT" in r for r in reasons)

        # Add request and dispatch, but missing ack and audio
        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.REQUEST_RECEIVED,
            session_id="sess_004",
            request_id="req_004",
            source="host_chat",
            clock_domain=ClockDomain.HOST_MONOTONIC,
            monotonic_timestamp=10.0,
        ))
        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.DISPATCH_INITIATED,
            session_id="sess_004",
            request_id="req_004",
            source="host_chat",
            clock_domain=ClockDomain.HOST_MONOTONIC,
            monotonic_timestamp=11.5,
        ))

        passed, reasons = chronology.validate_qualification_completeness()
        assert not passed
        assert any("HOST_ACKNOWLEDGED" in r for r in reasons)
        assert any("DUAL_SOURCE_DEFICIT" in r for r in reasons)

    def test_cross_process_clock_domain_safety(self) -> None:
        """Directly subtracting monotonic clocks across different domains is rejected."""
        chronology = StartupEventChronology(session_id="sess_005", request_id="req_005")

        assert chronology.validate_clock_domain_subtraction(
            ClockDomain.HOST_MONOTONIC, ClockDomain.HOST_MONOTONIC
        )
        assert not chronology.validate_clock_domain_subtraction(
            ClockDomain.HOST_MONOTONIC, ClockDomain.WORKER_MONOTONIC
        )
        assert not chronology.validate_clock_domain_subtraction(
            ClockDomain.WORKER_MONOTONIC, ClockDomain.FILESYSTEM_STAT
        )

    def test_stale_session_events_rejected(self) -> None:
        """Events from a mismatched or stale session ID must raise ValueError."""
        chronology = StartupEventChronology(session_id="sess_active", request_id="req_006")

        stale_event = CorrelatedStartupEvent(
            event_name=StartupEventName.SOURCE_OPENED,
            session_id="sess_stale_prior",
            request_id="req_006",
            source="mixed",
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=5.0,
        )

        with pytest.raises(ValueError, match="STALE_SESSION_REJECTED"):
            chronology.add_event(stale_event)

    def test_monotonic_event_ordering_invariants(self) -> None:
        """Adding an event with earlier timestamp in the same domain raises ValueError."""
        chronology = StartupEventChronology(session_id="sess_007", request_id="req_007")

        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.REQUEST_RECEIVED,
            session_id="sess_007",
            request_id="req_007",
            source="host_chat",
            clock_domain=ClockDomain.HOST_MONOTONIC,
            monotonic_timestamp=100.0,
        ))

        with pytest.raises(ValueError, match="EVENT_ORDER_VIOLATION"):
            chronology.add_event(CorrelatedStartupEvent(
                event_name=StartupEventName.DISPATCH_INITIATED,
                session_id="sess_007",
                request_id="req_007",
                source="host_chat",
                clock_domain=ClockDomain.HOST_MONOTONIC,
                monotonic_timestamp=99.5,
            ))

    def test_asr_remains_warming_without_readiness_receipt(self) -> None:
        """Process liveness without a validated readiness receipt must remain warming."""
        chronology = StartupEventChronology(session_id="sess_008", request_id="req_008")

        # Worker active event without validated receipt in payload
        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.ASR_WARMING,
            session_id="sess_008",
            request_id="req_008",
            source="asr_worker",
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=152.114,
            payload={"pid": 58624, "load_ms": 597.04},
        ))

        assert not chronology.is_asr_ready(), "ASR must not be ready without a readiness receipt"

        # Now add verified ASR_READY event with receipt
        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.ASR_READY,
            session_id="sess_008",
            request_id="req_008",
            source="asr_worker",
            clock_domain=ClockDomain.WORKER_MONOTONIC,
            monotonic_timestamp=153.5,
            payload={
                "asr_readiness_receipt": {
                    "status": "READY",
                    "model": "mlx-community/whisper-large-v3-turbo",
                    "probe_latency_ms": 1035.04,
                    "verified_at": "2026-10-07T18:35:15.114Z",
                }
            },
        ))

        assert chronology.is_asr_ready()
        assert chronology.asr_ready_monotonic == 153.5

    def test_event_callback_delivery(self) -> None:
        """Registered callbacks must receive correlated startup events."""
        chronology = StartupEventChronology(session_id="sess_009", request_id="req_009")
        received_events = []

        chronology.register_callback(lambda evt: received_events.append(evt.event_name))

        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.REQUEST_RECEIVED,
            session_id="sess_009",
            request_id="req_009",
            source="host_chat",
            clock_domain=ClockDomain.HOST_MONOTONIC,
            monotonic_timestamp=1.0,
        ))
        chronology.add_event(CorrelatedStartupEvent(
            event_name=StartupEventName.DISPATCH_INITIATED,
            session_id="sess_009",
            request_id="req_009",
            source="host_chat",
            clock_domain=ClockDomain.HOST_MONOTONIC,
            monotonic_timestamp=1.8,
        ))

        assert len(received_events) == 2
        assert received_events == [
            StartupEventName.REQUEST_RECEIVED,
            StartupEventName.DISPATCH_INITIATED,
        ]

    def test_evidence_digests_and_source_metadata_integrity(self) -> None:
        """Fixture metadata files must be well-formed and bind incident evidence."""
        digests_file = FIXTURES_DIR / "evidence_digests.json"
        metadata_file = FIXTURES_DIR / "source_build_metadata.json"

        assert digests_file.is_file()
        assert metadata_file.is_file()

        digests_data = json.loads(digests_file.read_text(encoding="utf-8"))
        assert len(digests_data["evidence_sources"]) == 2
        chat_source = next(s for s in digests_data["evidence_sources"] if s["source_type"] == "CHAT_TRANSCRIPT")
        log_source = next(s for s in digests_data["evidence_sources"] if s["source_type"] == "STARTUP_TASK_LOG")

        assert len(chat_source["sha256"]) == 64
        assert len(log_source["sha256"]) == 64

        metadata_data = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert metadata_data["git_commit"] == "45b10fb983e2d0fb4b6c9573787a25733e60d94e"
        assert metadata_data["target_sla_seconds"] == 30.0
        assert metadata_data["required_audio_sources"] == ["microphone", "system_audio"]
