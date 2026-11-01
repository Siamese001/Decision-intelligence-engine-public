"""End-to-End tests for Audio Governance, Gate F Certification, and W0 Release Audit (Wave 6)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audio.source_detector import AudioSourceDetector
from dual_pipeline.audio_capture_certificate import (
    AudioCaptureCertificate,
    AudioSourceType,
    CertificateVerdict,
    GateFEvidence,
)
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.audit_w0 import (
    AudioCompletenessGroundingError,
    validate_audio_completeness_grounding,
    validate_release_audio_certificate,
)
from dual_pipeline.transcript_stages import HITLRequiredError
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def _create_synthetic_pcm_wav(file_path: Path, channels: int = 2, sample_rate: int = 16000, duration_sec: float = 2.0) -> Path:
    """Create a valid uncompressed PCM WAV file."""
    total_samples = int(sample_rate * duration_sec)
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    data_size = total_samples * channels * 2

    with file_path.open("wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        for i in range(total_samples):
            for _ in range(channels):
                f.write(struct.pack("<h", int(2000 * ((i % 50) / 50.0))))
    return file_path


def test_audio_governance_full_lifecycle_success(tmp_path: Path):
    """E2E Test: Full audio pipeline from source file to certified W0 graph release."""
    audio_path = tmp_path / "meeting_full.wav"
    _create_synthetic_pcm_wav(audio_path, channels=2, sample_rate=16000, duration_sec=3.0)

    # 1. Source Detection
    detection = AudioSourceDetector.detect(audio_path)
    assert detection.container_format == "wav"
    assert detection.channels == 2
    assert detection.sample_rate == 16000

    # 2. Audio Signal Setup
    audio_bytes = audio_path.read_bytes()
    sha256 = hashlib.sha256(audio_bytes).hexdigest()
    duration_seconds = 3.0
    source_type = AudioSourceType.GENERIC

    # 3. Construct ForensicSignalEnvelope and evaluate Gate F
    turn = ForensicTurnSignal(
        turn_id="turn-e2e-gov-01",
        span=SampleSpan(start_sample=0, end_sample=int(duration_seconds * 16000)),
        transcript_verbatim="Discussing architectural release requirements.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Lead Architect",),
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-gov-e2e", (sha256,), (turn,))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-gov-e2e",
        source_hashes=(sha256,),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=int(duration_seconds * 16000),
        turns=(turn,),
        signal_digest=digest,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    qa_report = gatekeeper.evaluate(
        envelope,
        expected_duration_s=duration_seconds,
        source_type=source_type,
    )
    assert qa_report.audio_certificate is not None
    assert qa_report.audio_certificate.verdict == CertificateVerdict.PASS

    # 4. Circuit Breaker Evaluation
    authorized_cert = AudioCompletenessCircuitBreaker.assert_outputs_authorized(
        metadata={
            "is_audio": True,
            "audio_path": str(audio_path),
            "declared_duration_seconds": duration_seconds,
            "expected_duration_seconds": duration_seconds,
            "audio_certificate": qa_report.audio_certificate,
        }
    )
    assert authorized_cert is not None
    assert authorized_cert.verdict == CertificateVerdict.PASS

    # 5. GraphDB Recording & W0 Audit Verification
    db_path = tmp_path / "governed_graph.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE audit_event (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO source VALUES ('src-001', 'audio_meeting_recording', 'Gov Review', ?, '2026-10-01T12:00:00Z')",
        (str(audio_path),),
    )
    conn.execute(
        "INSERT INTO audit_event VALUES ('ev-001', 'gate_f_audio_completeness', ?, '2026-10-01T12:05:00Z')",
        (json.dumps({"status": "PASS", "verdict": "PASS"}),),
    )
    conn.commit()
    conn.close()

    # W0 Audio Completeness Audit
    audit_res = validate_audio_completeness_grounding(db_path, fail_closed=True)
    assert audit_res["status"] == "PASS"
    assert audit_res["audio_sources_count"] == 1
    assert audit_res["gate_f_passes"] == 1

    # W0 Release Audio Certificate Validation
    release_res = validate_release_audio_certificate(qa_report.audio_certificate, fail_closed=True)
    assert release_res["status"] == "PASS"
    assert release_res["is_certified"] is True


def test_audio_governance_full_lifecycle_failfast_block(tmp_path: Path):
    """E2E Test: Severe audio truncation triggers Gate F FAIL, circuit breaker trips, release blocked."""
    # Envelope with 2 seconds duration vs 60 seconds expected (< 5% completeness)
    turn = ForensicTurnSignal(
        turn_id="turn-trunc-01",
        span=SampleSpan(start_sample=0, end_sample=32000),
        transcript_verbatim="Truncated call hello...",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-trunc-e2e", ("dummy_sha",), (turn,))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-trunc-e2e",
        source_hashes=("dummy_sha",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=32000,
        turns=(turn,),
        signal_digest=digest,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    qa_report = gatekeeper.evaluate(
        envelope,
        expected_duration_s=60.0,
        source_type=AudioSourceType.TEAMS,
    )
    assert qa_report.audio_certificate is not None
    assert qa_report.audio_certificate.verdict == CertificateVerdict.FAIL

    # Circuit breaker must raise AudioIncompleteFailFastError
    with pytest.raises(AudioIncompleteFailFastError) as exc_info:
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(
            metadata={
                "is_audio": True,
                "audio_certificate": qa_report.audio_certificate,
            }
        )
    assert "Outputs 01, 02, and 03 are BLOCKED" in str(exc_info.value)

    # W0 Release Audit must fail closed
    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_release_audio_certificate(qa_report.audio_certificate, fail_closed=True)
    assert "is not certified" in str(exc_info.value)


def test_audio_governance_bluetooth_warning_hitl_lifecycle():
    """E2E Test: Bluetooth warning requires HITL approval before authorization and W0 release."""
    evidence = GateFEvidence(
        expected_duration_s=100.0,
        actual_duration_s=85.0,
        completeness_pct=85.0,
        source_type=AudioSourceType.BLUETOOTH,
        audio_sha256="bt_test_sha256",
        remediation_hint="Minor wireless packet loss detected",
    )
    cert_unapproved = AudioCaptureCertificate.create(
        audio_sha256="bt_test_sha256",
        verdict=CertificateVerdict.WARN,
        gate_f_evidence=evidence,
        expected_duration_s=100.0,
        actual_duration_s=85.0,
        source_type=AudioSourceType.BLUETOOTH,
        hitl_approved=False,
    )

    # 1. Unapproved: Circuit breaker trips with HITLRequiredError
    with pytest.raises(HITLRequiredError):
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(
            metadata={"is_audio": True, "audio_certificate": cert_unapproved}
        )

    # Unapproved: W0 release rejects
    with pytest.raises(AudioCompletenessGroundingError):
        validate_release_audio_certificate(cert_unapproved, fail_closed=True)

    # 2. Approved: With operator verification receipt
    cert_approved = AudioCaptureCertificate.create(
        audio_sha256="bt_test_sha256",
        verdict=CertificateVerdict.WARN,
        gate_f_evidence=evidence,
        expected_duration_s=100.0,
        actual_duration_s=85.0,
        source_type=AudioSourceType.BLUETOOTH,
        hitl_approved=True,
        hitl_receipt_id="hitl-receipt-operator-approved-001",
    )

    auth = AudioCompletenessCircuitBreaker.assert_outputs_authorized(
        metadata={"is_audio": True, "audio_certificate": cert_approved}
    )
    assert auth is not None
    assert auth.hitl_approved is True

    rel = validate_release_audio_certificate(cert_approved, fail_closed=True)
    assert rel["status"] == "PASS"
    assert rel["is_certified"] is True
