"""
Adversarial and edge-case verification for Wave 1 (Hotspot #4):
Covers >= 4 mandatory edge-case categories:
1. Boundary payload: 2-turn >=150-word monologue collapse in multi-attendee meeting; mono capture strategy passing is_stereo=False.
2. Malformed input: Chunk format mismatch (nchannels/sampwidth/framerate) rejected or padded in AudioMasterStitcher; InstantAudioSpooler fail-closed on multi-source init error.
3. Invariant bypass attempt: Quote-hint turn rewriting cannot mask pre-hint single-speaker collapse; speaker_hint ledger entries cannot spoof collapse approval; expired subject-bound receipt rejected.
4. Exact production regression: Telemetry filtering in LocalMLXAudioTranscriber._sanitize_raw_segments drops high no_speech_prob / compression_ratio hallucinations.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    create_signed_hitl_approval_entry,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    _extract_expected_subject_digest,
    check_speaker_skew_and_collapse,
)
from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt


def test_category1_boundary_two_turn_long_word_monologue_and_mono_proof_flag(tmp_path: Path) -> None:
    """Category 1 (Boundary): 2-turn >=150-word monologue triggers collapse gate; mono strategy passes is_stereo=False."""
    long_monologue = " ".join(["architecture"] * 160)
    transcript = f"[00:00:01] Speaker 1: {long_monologue}\n[00:05:00] Speaker 2: Okay."
    roster = [("Alice Vance", "PartnerAdvisory"), ("Bob Sterling", "Apex Global")]
    meta: Dict[str, Any] = {
        "project": "apex_trans",
        "meeting_name": "Monologue_Test",
        "date": "10/24/2026",
        "attendees": ["Alice Vance", "Bob Sterling"],
        "hitl_ledger_path": str(tmp_path / "empty_ledger.json"),
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(transcript, roster, meta)
    assert exc_info.value.hitl_type == "single_speaker_collapse"

    mgr = LiveAudioCaptureManager(
        chunks_dir=tmp_path / "chunks",
        master_wav=tmp_path / "master.wav",
        source_mode="system",
        enable_stereo_isolation=True,
        skip_device_discovery=True,
    )
    with patch(
        "dual_pipeline.live_meeting_recorder_core.audio_capture.AudioCaptureProofVerifier.run_full_start_proof",
        return_value={"status": "PROVEN"},
    ) as mock_proof:
        mgr._run_start_proof("screen_capture_kit")
        assert mock_proof.call_args.kwargs["is_stereo"] is False
        mgr._run_start_proof("mixed_sck")
        assert mock_proof.call_args.kwargs["is_stereo"] is True


def test_category2_malformed_chunk_params_and_spooler_no_mono_fallback(tmp_path: Path) -> None:
    """Category 2 (Malformed): AudioMasterStitcher detects channel/sampwidth mismatch; InstantAudioSpooler fails closed without mono fallback."""
    sr = 16000
    samples = (np.sin(np.linspace(0, 100, sr * 2)) * 5000).astype(np.int16)

    c0 = tmp_path / "chunk_00000.wav"
    with wave.open(str(c0), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.stack([samples, samples], axis=1).tobytes())

    c1 = tmp_path / "chunk_00001.wav"
    with wave.open(str(c1), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())

    with pytest.raises(RuntimeError, match="STITCHER-PARAM-MISMATCH"):
        AudioMasterStitcher.stitch_chunk_files([c0, c1], tmp_path / "master_strict.wav")

    receipt = AudioMasterStitcher.stitch_master_wav(
        tmp_path, tmp_path / "master_padded.wav", min_bytes=100, pad_missing_or_corrupt=True
    )
    assert receipt.success is True
    assert receipt.padded_chunks == 1
    assert 1 in receipt.details["padded_indices"]

    spooler = InstantAudioSpooler(spool_dir=tmp_path / "spool", source_mode="mixed")
    with patch(
        "dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts.start_multi_source_spooler_capture",
        side_effect=RuntimeError("SCK tap denied"),
    ):
        with pytest.raises(RuntimeError, match="SCK tap denied"):
            spooler.start_eager()
        assert spooler.is_running is False


def test_category3_invariant_bypass_quote_hints_and_spoofed_or_expired_ledger(tmp_path: Path) -> None:
    """Category 3 (Invariant Bypass): Quote hints cannot mask pre-hint collapse; speaker_hint entries and expired approvals are rejected."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text(json.dumps({"operator_decisions": []}), encoding="utf-8")

    # 10 turns where Speaker 1 has 9/10 turns (90% >= 85%), but 2 quote hints try to rewrite turns to Bob
    turns = [
        "[00:01:00] Speaker 1: Alpha milestone is on track.",
        "[00:02:00] Speaker 1: Beta deployment starts tomorrow.",
        "[00:03:00] Speaker 1: Gamma testing completed yesterday.",
        "[00:04:00] Speaker 1: Delta pipeline is verified.",
        "[00:05:00] Speaker 1: Epsilon metrics look healthy.",
        "[00:06:00] Speaker 1: Zeta database migration passed.",
        "[00:07:00] Speaker 1: Eta security audit succeeded.",
        "[00:08:00] Speaker 1: Theta release notes drafted.",
        "[00:09:00] Speaker 1: Iota performance benchmark met.",
        "[00:10:00] Speaker 2: Agreed.",
    ]
    transcript = "\n".join(turns)
    meta: Dict[str, Any] = {
        "project": "apex_trans",
        "meeting_name": "Bypass_Attempt",
        "date": "10/24/2026",
        "attendees": ["Alice Vance", "Bob Sterling"],
        "hitl_ledger_path": str(ledger_file),
        "speaker_hints": [
            {"speaker_name": "Bob Sterling", "quote": "Alpha milestone is on track"},
            {"speaker_name": "Bob Sterling", "quote": "Beta deployment starts tomorrow"},
        ],
    }

    # Attempt 1: Spoof ledger with a signed speaker_hint entry
    subj = _extract_expected_subject_digest(meta)
    assert subj is not None
    spoof_entry = create_signed_hitl_approval_entry(
        subject_sha256=subj,
        case_ids=["speaker_hint_01"],
        decision="APPROVE",
        operator_id="human_operator",
        metadata={"meeting_name": "Bypass_Attempt", "hitl_type": "speaker_hint"},
    )
    spoof_entry["category"] = "speaker_hint"
    append_signed_approval_to_ledger(spoof_entry, ledger_path=ledger_file)

    engine = AudioSpeakerAttributionEngine()
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, meta, interactive_resolver=None)
    assert exc_info.value.hitl_type == "single_speaker_collapse"

    # Attempt 2: Expired subject-bound collapse receipt must be rejected by both collapse_detector and gate1_alr
    expired_entry = create_signed_hitl_approval_entry(
        subject_sha256=subj,
        case_ids=["SC-001"],
        decision="APPROVE",
        operator_id="human_operator",
        metadata={"meeting_name": "Bypass_Attempt"},
        ttl_seconds=-3600,
    )
    ledger_file.write_text(json.dumps({"operator_decisions": []}), encoding="utf-8")
    append_signed_approval_to_ledger(expired_entry, ledger_path=ledger_file)

    with pytest.raises(HITLRequiredError):
        engine.analyze_and_resolve(transcript, meta, interactive_resolver=None)

    passed, _, failures = verify_alr_cryptographic_receipt(
        "## Ambiguity Lock Register\n| ID | Desc |\n|---|---|\n| 1 | (Operator Verified) |\n",
        meta,
        ledger_path=ledger_file,
    )
    assert passed is False
    assert any("UNAUTHENTICATED" in f or "UNRESOLVED" in f for f in failures)


def test_category4_regression_mlx_transcriber_telemetry_filtering() -> None:
    """Category 4 (Production Regression): LocalMLXAudioTranscriber._sanitize_raw_segments drops high no_speech_prob and compression_ratio segments."""
    transcriber = LocalMLXAudioTranscriber(mock=True)
    raw_segments = [
        {"start": 0.0, "end": 2.0, "text": "Valid speech turn about the architecture.", "no_speech_prob": 0.05, "compression_ratio": 1.2, "avg_logprob": -0.2},
        {"start": 2.0, "end": 4.0, "text": "Hallucinated silence text that looks normal.", "no_speech_prob": 0.85, "compression_ratio": 1.1, "avg_logprob": -0.3},
        {"start": 4.0, "end": 6.0, "text": "High compression ratio artifact segment.", "no_speech_prob": 0.10, "compression_ratio": 3.5, "avg_logprob": -0.2},
    ]
    sanitized = transcriber._sanitize_raw_segments(raw_segments, metadata={})
    assert len(sanitized) == 1
    assert sanitized[0]["text"] == "Valid speech turn about the architecture."
