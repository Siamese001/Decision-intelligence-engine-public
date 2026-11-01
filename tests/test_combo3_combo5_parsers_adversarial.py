"""
Adversarial and edge-case tests for Wave 2: Combinations 3 & 5
Covers:
1. Boundary payload / Overflow & exhaustion (leading/trailing >60s silence gaps in Gate F)
2. Invariant bypass attempt (AudioCompletenessCircuitBreaker.verify_upstream_certificate with cert=None, CLI --notes + --output3-only vs --document + --notes)
3. Malformed input / Exact production regression (hyphenated names with corporate suffixes, VTT comma names, ISO-8601 timestamps)
"""
from __future__ import annotations

import argparse
import pytest

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audio_capture_certificate import CertificateVerdict
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.cli.ingest import _apply_semantic_flags, _validate_semantic_flag_conflicts
from dual_pipeline.transcript_pipeline_core.format_parsers import (
    _parse_vtt_line_speaker_and_text,
    format_timestamp,
)
from dual_pipeline.transcript_pipeline_core.normalizer import _normalize_speaker_name
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def test_circuit_breaker_rejects_missing_certificate_when_not_mock() -> None:
    """Invariant bypass attempt: verify_upstream_certificate fails closed when cert is None unless mock_mode=True."""
    with pytest.raises(AudioIncompleteFailFastError, match="cert is None"):
        AudioCompletenessCircuitBreaker.verify_upstream_certificate(None, metadata={"is_audio": True}, mock_mode=False)

    # Allowed in explicit mock mode
    assert AudioCompletenessCircuitBreaker.verify_upstream_certificate(None, metadata={"is_audio": True}, mock_mode=True) is None


def test_gate_f_detects_leading_and_trailing_excessive_silence_gaps() -> None:
    """Boundary payload: Gate F catches >60s leading or trailing silence gaps that previously escaped inter-turn checks."""
    sr = 16000
    # Single turn starting at 75s (75s leading silence) and ending at 80s in a 160s recording (80s trailing silence)
    turn = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=75 * sr, end_sample=80 * sr),
        transcript_verbatim="Short speech segment in the middle of a dropped recording.",
        speaker_cluster_ids=("Speaker 1",),
        identity_state="cluster_only",
        commitment_state="committed",
    )
    source_hash = "sha256:" + ("b" * 64)
    digest = ForensicSignalEnvelope.compute_signal_digest(
        recording_id="leading_trailing_gap_test",
        source_hashes=(source_hash,),
        turns=(turn,),
    )
    envelope = ForensicSignalEnvelope(
        schema_version="2.1.0",
        recording_id="leading_trailing_gap_test",
        source_hashes=(source_hash,),
        canonical_sample_rate_hz=sr,
        timeline_length_samples=160 * sr,
        turns=(turn,),
        signal_digest=digest,
    )
    gatekeeper = UpstreamAudioQAGatekeeper()
    report = gatekeeper.evaluate(envelope, expected_duration_s=160.0)
    assert report.audio_certificate is not None
    assert report.audio_certificate.verdict == CertificateVerdict.FAIL
    gap_durations = [g.duration_sec for g in report.audio_certificate.gate_f_evidence.silence_gaps]
    assert 75.0 in gap_durations
    assert 80.0 in gap_durations


def test_speaker_name_hyphen_preservation_and_corporate_suffix_stripping() -> None:
    """Exact production regression: intra-word hyphens preserved while spaced/em-dash suffixes are stripped."""
    assert _normalize_speaker_name("Sarah Al-Mansoor — Apex Global Enterprises") == "Sarah Al-Mansoor"
    assert _normalize_speaker_name("Jean-Luc Picard - PartnerAdvisory") == "Jean-Luc Picard"
    assert _normalize_speaker_name("Kim-Marie O'Connor - Vendor; external") == "Kim-Marie O'Connor"
    assert _normalize_speaker_name("Al-Mansoor, Sarah") == "Sarah Al-Mansoor"

    spk, txt = _parse_vtt_line_speaker_and_text("Al-Mansoor, Sarah: The pipeline is ready.")
    assert spk == "Sarah Al-Mansoor"
    assert txt == "The pipeline is ready."

    assert format_timestamp("2026-10-25T09:14:22.500Z") == "09:14:22"
    assert format_timestamp("PT1H05M09S") == "01:05:09"


def test_cli_notes_and_output3_only_compatible_while_document_and_notes_conflict() -> None:
    """Verify --notes + --output3-only is permitted while --document + --notes raises ValueError."""
    _validate_semantic_flag_conflicts(is_doc=False, is_notes=True, is_out3=True)
    with pytest.raises(ValueError, match="Cannot specify both --document and --notes"):
        _validate_semantic_flag_conflicts(is_doc=True, is_notes=True, is_out3=False)

    args = argparse.Namespace(
        document=False,
        command="ingest-meeting",
        notes=True,
        copilot_notes=False,
        output3_only=True,
        input="meeting_notes.md",
        regenerate=None,
    )
    meta: dict = {}
    _apply_semantic_flags(meta, args)
    assert meta["notes_mode"] is True
    assert meta["output3_only"] is True
    assert meta["outputs"] == ["03"]
