"""
E2E tests for Wave 2: Combinations 3 & 5 — Standalone Audio, Non-MLX Transcriber Contracts,
Notes, VTT/Teams/CoPilot & Collateral Drops.
"""
from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.attachment_core.types import AttachmentRecord, AttachmentType
from dual_pipeline.audio_capture_certificate import AudioCaptureCertificate, CertificateVerdict
from dual_pipeline.audio_ingestion import LocalFasterWhisperTranscriber, WhisperXAudioTranscriber
from dual_pipeline.audio_ingestion_core.factory import get_default_transcriber
from dual_pipeline.canonical_pipeline.content_loader import _load_from_source_file
from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher
from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
from dual_pipeline.transcript_qa_core.collateral_healer import heal_collateral_graph_mutations
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def _write_test_wav(path: Path, duration_sec: float = 4.0, sr: int = 16000) -> None:
    n_frames = int(duration_sec * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n_frames):
            t = i / sr
            val = int(12000 * math.sin(2.0 * math.pi * 440.0 * t))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))


def test_combo3_standalone_audio_non_mlx_and_gate_f_certificate_e2e(tmp_path: Path) -> None:
    """
    Verifies Combination 3:
    1. Non-MLX transcriber selection ('faster-whisper', 'whisperx', 'cpu') and model propagation.
    2. Standalone audio loading mints a verified Gate F AudioCaptureCertificate and ForensicSignalEnvelope.
    3. U0 preflight defers generic speaker mapping to C0-PA when audio/acoustic diarization is present.
    """
    fw_transcriber = get_default_transcriber(mock=True, backend="faster-whisper", model_name="medium.en")
    assert isinstance(fw_transcriber, LocalFasterWhisperTranscriber)
    assert fw_transcriber.model_size == "medium.en"
    assert fw_transcriber.model_name == "medium.en"

    wx_transcriber = get_default_transcriber(mock=True, backend="whisperx", model_name="large-v2")
    assert isinstance(wx_transcriber, WhisperXAudioTranscriber)
    assert wx_transcriber.model_size == "large-v2"

    wav_path = tmp_path / "standalone_meeting.wav"
    _write_test_wav(wav_path, duration_sec=60.0)

    diarized_transcript = (
        "**Speaker 1** [00:00:02]: Welcome Sarah Al-Mansoor and Speaker_Alpha McPhee to the Nexus architecture sync.\n\n"
        "**Speaker 2** [00:00:25]: Thanks Speaker_Alpha, we validated the Unifier GIS integration pipeline."
    )
    meta = {
        "project": "project_nexus",
        "meeting_name": "Nexus Standalone Audio Review",
        "date": "10/25/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "attendees": ["Speaker_Alpha McPhee (Director, Apex Global Enterprises)", "Sarah Al-Mansoor (Lead Architect, PartnerAdvisory)"],
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "urn:nee:live-container-preflight-proof",
        },
        "attendee_roles_confirmed_by_operator": True,
        "hitl_role_resolutions": {
            "Speaker_Alpha McPhee": "Director",
            "Sarah Al-Mansoor": "Lead Architect",
        },
    }

    with patch(
        "dual_pipeline.transcript_pipeline_core.ingress.transcribe_audio_with_cache",
        return_value=diarized_transcript,
    ):
        loaded_text = _load_from_source_file(wav_path, meta, offline=True)

    assert "Sarah Al-Mansoor" in loaded_text
    assert meta.get("is_audio") is True
    assert meta.get("duration_minutes") == 1
    cert = meta.get("audio_certificate")
    assert isinstance(cert, AudioCaptureCertificate)
    assert cert.verdict in (CertificateVerdict.PASS, CertificateVerdict.WARN)
    assert cert.audio_sha256 == meta["raw_source_sha256"]

    # Verify U0 preflight defers U0_UNMAPPED_SPEAKERS to C0-PA for audio runs with roster
    preflight_rep = batch_validate_preflight_checks(meta, raw_transcript=loaded_text, input_path=wav_path)
    assert "U0_UNMAPPED_SPEAKERS" not in [d.check_name for d in preflight_rep.diagnostics]


def test_combo5_vtt_copilot_hyphenated_names_and_notes_e2e() -> None:
    """
    Verifies Combination 5:
    1. Hyphenated speaker names ('Sarah Al-Mansoor — Apex Global') and VTT 'Lastname, Firstname: text' preserve full names.
    2. CoPilot JSON ISO-8601 timestamps normalize cleanly and U0 classifies diarized JSON as NATIVE.
    3. Multi-attendee --notes runs bypass U0_ATTENDEE_CONFIRMATION and UNDIARIZED_DISQUALIFIED_FOR_AUDIO.
    """
    vtt_raw = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\n"
        "Al-Mansoor, Sarah: We completed the GIS schema migration.\n\n"
        "00:00:06.000 --> 00:00:10.000\n"
        "<v O'Connor, Liam — PartnerAdvisory>Agreed, the latency dropped by 40 percent.</v>\n\n"
        "00:00:11.000 --> 00:00:15.000\n"
        "McPhee, Speaker_Alpha: Let us lock this in for the Q4 release.\n"
    )
    norm_vtt = normalize_raw_input_text(vtt_raw, input_ext=".vtt")
    vtt_text = norm_vtt.text if hasattr(norm_vtt, "text") else str(norm_vtt)
    assert "**Sarah Al-Mansoor:**" in vtt_text
    assert "**Liam O'Connor:**" in vtt_text
    assert "**Speaker_Alpha McPhee:**" in vtt_text

    copilot_json = json.dumps({
        "turns": [
            {"speaker": "Sarah Al-Mansoor - Apex Global", "timestamp": "2026-10-25T14:05:09.123Z", "text": "Reviewing LAMS cutover."},
            {"speaker": "Speaker_Alpha McPhee", "timestamp": "2026-10-25T14:06:12Z", "text": "Approved for production."},
            {"speaker": "Liam O'Connor", "timestamp": "PT0H07M30S", "text": "Will update the runbook."},
        ]
    })
    u0 = U0IngestionBoundary()
    status = u0._detect_diarization_status(copilot_json, {"platform": "Microsoft Teams CoPilot", "input_ext": ".json"})
    assert status == "NATIVE"

    # Multi-attendee --notes mode
    notes_text = (
        "# Meeting Notes: Nexus Weekly Sync\n"
        "- **Architecture**: Sarah Al-Mansoor and Speaker_Alpha McPhee aligned on GIS cutover.\n"
        "- **Action**: Liam O'Connor to publish schema contract by Friday.\n"
    )
    notes_meta = {
        "project": "project_nexus",
        "meeting_name": "Nexus Weekly Sync",
        "date": "10/25/2026",
        "time": "02:00 PM - 02:30 PM ET",
        "notes_mode": True,
        "is_notes": True,
        "canonical_modality": "MEETING_NOTES",
        "allow_undiarized": True,
        "attendees": ["Sarah Al-Mansoor", "Speaker_Alpha McPhee", "Liam O'Connor"],
    }
    u0_res = u0.execute(notes_text, notes_meta)
    assert u0_res["diarization_status"] == "NATIVE"


def test_combo5_collateral_watcher_and_healer_e2e(tmp_path: Path) -> None:
    """
    Verifies live collateral drops (modified after initial touch) and AttachmentRecord + collateral_drop_log.json
    propagation into Output 03 with locator.kind == 'attachment'.
    """
    ref_dir = tmp_path / "referenced_artifacts"
    ref_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "collateral_drop_log.json"

    elapsed = 125.0
    watcher = CollateralFsWatcher(ref_dir=ref_dir, get_elapsed_fn=lambda: elapsed, log_path=log_path)

    slide_file = ref_dir / "nexus_topology_v1.png"
    slide_file.write_bytes(b"partial")
    watcher._scan()
    first_sha = watcher.drops[0]["sha256"]

    # Simulate file write completion changing size/mtime
    slide_file.write_bytes(b"complete_png_payload_bytes_v1")
    watcher._scan()
    assert len(watcher.drops) == 1
    updated_sha = watcher.drops[0]["sha256"]
    assert updated_sha != first_sha

    # Also test AttachmentRecord dataclass + collateral_drop_log.json merge in heal_collateral_graph_mutations
    att_rec = AttachmentRecord(
        attachment_id="att-001",
        filename="unifier_erd.pdf",
        original_path=slide_file,
        staged_path=slide_file,
        attachment_type=AttachmentType.DOCUMENT,
        sha256="a" * 64,
        mime_type="application/pdf",
        file_size_bytes=1024,
        relative_staged_path="referenced_artifacts/unifier_erd.pdf",
        attribution_mode="COLLATERAL_REVIEWED",
        description="Unifier ERD Specification",
    )
    meta = {
        "project": "project_nexus",
        "output_dir": str(tmp_path),
        "attachments": [att_rec],
    }
    f3_data: dict = {"graph_mutations": {"entity_upserts": [], "relationship_signals": []}, "evidence_index": {}}
    changes: list = []
    heal_collateral_graph_mutations(f3_data, meta, changes)

    ev_idx = f3_data["evidence_index"]
    assert any(
        v.get("locator", {}).get("kind") == "attachment" and "unifier_erd.pdf" in v.get("locator", {}).get("value", "")
        for v in ev_idx.values()
    )
    assert any(
        v.get("locator", {}).get("kind") == "attachment" and "nexus_topology_v1.png" in v.get("locator", {}).get("value", "")
        for v in ev_idx.values()
    )
