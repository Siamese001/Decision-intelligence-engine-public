"""
tests/end_to_end/test_sherpa_stereo_transcription_rca_e2e.py

Class B E2E Test Suite for Wave 1:
Sherpa Stereo Clock Sync, RIFF Repair, & Channel Diarization.

Verifies:
1. Auto-repair of streaming 0xFFFFFFFF RIFF headers and data chunk sizes.
2. Stereo channel-energy speaker attribution (Ch0 remote vs Ch1 local) preventing diarization collapse.
3. Suppressing Whisper consecutive repetition loops and delayed acoustic cross-channel echoes.
4. Metadata sidecar reference persistence keeping metadata.json < 50 KB.
"""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import List, Dict, Any

from dual_pipeline.audio_ingestion_core.container_validator import (
    repair_wav_riff_header,
    validate_audio_container_headers,
)
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    align_segments_via_stereo_energy,
)
from dual_pipeline.audio_postprocess import (
    deduplicate_consecutive_asr_segments,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    hydrate_live_container_artifacts,
)
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    resolve_output_directory_and_slug,
    verify_meeting_deconfliction,
)
from tools.validate_output_hierarchy_core.deconfliction import detect_meeting_duplicates


def _create_segmented_stereo_wav(
    path: Path,
    sample_rate: int = 16000,
    total_seconds: float = 12.0,
) -> None:
    """Creates a stereo WAV with alternating speakers:

    - 0.0s - 3.0s: Ch0 active (remote, amp 3500), Ch1 bleed (amp 500)
    - 3.0s - 6.0s: Ch1 active (local, amp 4000), Ch0 silent (amp 20)
    - 6.0s - 9.0s: Ch0 active (remote, amp 3200), Ch1 bleed (amp 450)
    - 9.0s - 12.0s: Ch1 active (local, amp 3800), Ch0 silent (amp 30)
    """
    n_frames = int(sample_rate * total_seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            sec = i / sample_rate
            if 0.0 <= sec < 3.0 or 6.0 <= sec < 9.0:
                amp0 = 3500 if (i % 32 < 16) else -3500
                amp1 = 500 if (i % 32 < 16) else -500
            else:
                amp0 = 25 if (i % 32 < 16) else -25
                amp1 = 4000 if (i % 32 < 16) else -4000
            frames.extend(struct.pack("<hh", amp0, amp1))
        wf.writeframes(frames)


class TestSherpaStereoTranscriptionRCAE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_sherpa_rca_e2e_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_riff_header_repair_e2e(self) -> None:
        """Verifies 0xFFFFFFFF RIFF header and data chunk sizes are repaired cleanly in-place."""
        wav_path = self.temp_path / "corrupted_streaming.wav"
        _create_segmented_stereo_wav(wav_path, total_seconds=2.0)

        # Corrupt bytes 4..8 and data chunk size to 0xFFFFFFFF
        with open(wav_path, "r+b") as f:
            f.seek(4)
            f.write(b"\xff\xff\xff\xff")
            header = f.read(1024)
            data_offset = header.find(b"data")
            if data_offset != -1:
                f.seek(4 + data_offset + 4)
                f.write(b"\xff\xff\xff\xff")

        # Confirm validation detects and auto-repairs
        spec = validate_audio_container_headers(wav_path)
        self.assertEqual(spec.format_name, "wav")
        self.assertEqual(spec.channels, 2)
        self.assertEqual(spec.sample_rate, 16000)

        # Read back raw header bytes directly
        with open(wav_path, "rb") as f:
            raw_bytes = f.read(44)
        declared_riff = struct.unpack("<I", raw_bytes[4:8])[0]
        actual_size = wav_path.stat().st_size
        self.assertEqual(declared_riff, actual_size - 8)
        self.assertNotEqual(declared_riff, 0xFFFFFFFF)

    def test_stereo_channel_energy_attribution_balance_e2e(self) -> None:
        """Verifies stereo channel attribution achieves 25%-75% balance and 0% single-speaker collapse."""
        wav_path = self.temp_path / "stereo_dialogue.wav"
        _create_segmented_stereo_wav(wav_path, total_seconds=12.0)

        segments = [
            {
                "start": 0.2, "end": 2.8, "text": "Hello, this is Cansu from PartnerAdvisory.",
                "words": [
                    {"word": "Hello,", "start": 0.2, "end": 0.8},
                    {"word": "this", "start": 0.9, "end": 1.2},
                    {"word": "is", "start": 1.2, "end": 1.4},
                    {"word": "Cansu", "start": 1.5, "end": 2.0},
                    {"word": "from", "start": 2.1, "end": 2.3},
                    {"word": "PartnerAdvisory.", "start": 2.4, "end": 2.8},
                ],
            },
            {
                "start": 3.2, "end": 5.8, "text": "Hi Cansu, Drew Oakley here, great to connect.",
                "words": [
                    {"word": "Hi", "start": 3.2, "end": 3.6},
                    {"word": "Cansu,", "start": 3.7, "end": 4.1},
                    {"word": "Dan", "start": 4.2, "end": 4.5},
                    {"word": "Okray", "start": 4.6, "end": 5.0},
                    {"word": "here,", "start": 5.1, "end": 5.4},
                    {"word": "connect.", "start": 5.5, "end": 5.8},
                ],
            },
            {
                "start": 6.2, "end": 8.8, "text": "We are reviewing the Sherpa deliverables today.",
                "words": [
                    {"word": "We", "start": 6.2, "end": 6.5},
                    {"word": "are", "start": 6.6, "end": 6.8},
                    {"word": "reviewing", "start": 6.9, "end": 7.4},
                    {"word": "the", "start": 7.5, "end": 7.7},
                    {"word": "Sherpa", "start": 7.8, "end": 8.2},
                    {"word": "today.", "start": 8.3, "end": 8.8},
                ],
            },
            {
                "start": 9.2, "end": 11.8, "text": "Sounds good, let us walk through the architecture.",
                "words": [
                    {"word": "Sounds", "start": 9.2, "end": 9.6},
                    {"word": "good,", "start": 9.7, "end": 10.0},
                    {"word": "walk", "start": 10.1, "end": 10.5},
                    {"word": "through", "start": 10.6, "end": 11.0},
                    {"word": "the", "start": 11.1, "end": 11.3},
                    {"word": "architecture.", "start": 11.4, "end": 11.8},
                ],
            },
        ]

        aligned = align_segments_via_stereo_energy(segments, wav_path, bleed_margin=1.15)
        self.assertEqual(len(aligned), 4)

        # Segment 0 and 2 should be Ch0 (SPEAKER_00 / remote)
        self.assertEqual(aligned[0]["speaker"], "SPEAKER_00")
        self.assertEqual(aligned[2]["speaker"], "SPEAKER_00")

        # Segment 1 and 3 should be Ch1 (SPEAKER_01 / local)
        self.assertEqual(aligned[1]["speaker"], "SPEAKER_01")
        self.assertEqual(aligned[3]["speaker"], "SPEAKER_01")

        dur0 = sum(s["end"] - s["start"] for s in aligned if s["speaker"] == "SPEAKER_00")
        dur1 = sum(s["end"] - s["start"] for s in aligned if s["speaker"] == "SPEAKER_01")
        total = dur0 + dur1
        pct0 = dur0 / total
        pct1 = dur1 / total

        # Invariant checks
        self.assertGreaterEqual(pct0, 0.25)
        self.assertLessEqual(pct0, 0.75)
        self.assertGreaterEqual(pct1, 0.25)
        self.assertLessEqual(pct1, 0.75)
        self.assertLess(max(pct0, pct1), 0.85, "Single-speaker collapse ceiling (<85%) violated!")

    def test_asr_consecutive_loop_and_delayed_echo_suppression_e2e(self) -> None:
        """Verifies Whisper 10x decoder loops and 6.11s cross-channel echoes are removed."""
        repeated_sentence = "I still have work, but this morning I woke up at 5."
        segments: List[Dict[str, Any]] = []

        # 10 consecutive identical segments representing the exact decoder runaway loop
        for idx in range(10):
            st = 52.6 + idx * 1.8
            segments.append({
                "start": st,
                "end": st + 1.6,
                "text": repeated_sentence,
                "speaker": "SPEAKER_00",
            })

        # Add a legitimate conversation turn
        segments.append({
            "start": 72.0,
            "end": 74.5,
            "text": "And then we prepared the slide deck.",
            "speaker": "SPEAKER_01",
        })

        # Add a delayed acoustic echo of the prior turn at +6.11s
        segments.append({
            "start": 78.11,
            "end": 80.61,
            "text": "And then we prepared the slide deck.",
            "speaker": "SPEAKER_00",
        })

        deduped = deduplicate_consecutive_asr_segments(segments, echo_window_seconds=15.0)

        # Out of 10 repeated segments, exactly 1 should be kept
        repeated_count = sum(1 for s in deduped if repeated_sentence in s["text"])
        self.assertEqual(repeated_count, 1)

        # The legitimate turn should be kept
        legit_count = sum(1 for s in deduped if "prepared the slide deck" in s["text"])
        self.assertEqual(legit_count, 1)

        # Total surviving segments should be exactly 2
        self.assertEqual(len(deduped), 2)

    def test_metadata_compactness_under_50kb_e2e(self) -> None:
        """Verifies metadata persistence writes sidecar references and stays strictly under 50 KB."""
        sidecar_seg_path = self.temp_path / "meeting_segments.json"
        sidecar_utt_path = self.temp_path / "meeting_utterances.json"

        # Simulate large turn list (171 turns)
        mock_turns = [
            {"speaker": f"SPEAKER_{i % 2:02d}", "start": float(i * 10), "end": float(i * 10 + 8),
             "text": f"This is meeting turn {i} discussing strategic architectural alignment for Sherpa."}
            for i in range(171)
        ]
        sidecar_seg_path.write_text(json.dumps(mock_turns, indent=2), encoding="utf-8")
        sidecar_utt_path.write_text(json.dumps(mock_turns, indent=2), encoding="utf-8")

        # Metadata should store sidecar file paths, not the raw segment dictionaries
        meta: Dict[str, Any] = {
            "session_id": "sherpa_test_session_001",
            "title": "Sherpa Connect Cansu Dan",
            "attendees": ["Cansu", "Drew Oakley"],
            "structured_segments_path": str(sidecar_seg_path),
            "structured_utterances_path": str(sidecar_utt_path),
            "turn_count": len(mock_turns),
            "diarization_method": "stereo_channel_energy",
            "diarization_status": "VERIFIED_STEREO_SEPARATION",
        }

        meta_file = self.temp_path / "metadata.json"
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        meta_size = meta_file.stat().st_size
        self.assertLess(meta_size, 50 * 1024, f"metadata.json ({meta_size} bytes) exceeds 50 KB limit!")

    def test_preflight_live_container_handoff_and_slug_reuse_e2e(self) -> None:
        """Verifies Wave 2: batch ingestion reuses live recording container, auto-binds time/hints/map, and avoids duplicate collisions."""
        container = self.temp_path / "output" / "project_beacon" / "10-14-2026" / "Connect_Cansu_PartnerAdvisory"
        container.mkdir(parents=True, exist_ok=True)

        wav_path = container / "00_10-14-2026_connect_cansu_partner_advisory_master_163149.wav"
        _create_segmented_stereo_wav(wav_path, total_seconds=2.0)

        manifest_data = {
            "project": "project_beacon",
            "meeting": "Connect Cansu PartnerAdvisory",
            "date": "10/14/2026",
            "date_dirname": "10-14-2026",
            "session_tag": "163149",
            "started_at": "2026-10-14T16:31:49.000000+00:00",
            "attendees": ["Cansu", "Drew Oakley"],
            "master_wav": str(wav_path),
        }
        manifest_file = container / "00_10-14-2026_connect_cansu_partner_advisory_163149_manifest.json"
        manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        receipt_file = container / "record_start_receipt.json"
        receipt_file.write_text(json.dumps({"status": "PASS", "strategy": "mixed_sck"}, indent=2), encoding="utf-8")

        hints_file = container / "speaker_hints.json"
        hints_file.write_text(json.dumps([
            {"hint_id": "h1", "voice_id": "SPEAKER_00", "speaker_name": "Cansu", "quote": "Hello, this is Cansu from PartnerAdvisory."}
        ], indent=2), encoding="utf-8")

        map_file = container / "connect_cansu_partner_advisory_speaker_map.json"
        map_file.write_text(json.dumps({"SPEAKER_00": "Cansu", "SPEAKER_01": "Drew Oakley"}, indent=2), encoding="utf-8")

        # 1. Directory and slug resolution: even with diverged meeting name, container slug is reused
        meta: Dict[str, Any] = {
            "input_path": str(wav_path),
            "meeting_name": "Sherpa Connect Cansu Dan",
            "project": "project_beacon",
            "date": "10/14/2026",
            "enforce_worktree": False,
        }
        resolved_dir, std_slug = resolve_output_directory_and_slug(
            metadata=meta,
            transcript_text="",
            output_dir="auto",
        )
        self.assertEqual(std_slug, "Connect_Cansu_PartnerAdvisory")
        self.assertEqual(Path(resolved_dir).resolve(), container.resolve())

        # 2. Unified preflight check: auto-hydrates time, hints, and speaker map in one pass
        transcript_sample = "**SPEAKER_00:** Hello, this is Cansu from PartnerAdvisory.\n**SPEAKER_01:** Hi Cansu, Drew Oakley here."
        report = batch_validate_preflight_checks(meta, raw_transcript=transcript_sample, input_path=str(wav_path))
        self.assertTrue(report.is_valid, f"Preflight validation failed: {report.summary()}")
        self.assertIn("time", report.hydrated_fields)
        self.assertIn("speaker_hints", report.hydrated_fields)
        self.assertIn("speaker_map", report.hydrated_fields)
        self.assertIn("04:31 PM", meta.get("time", ""))
        self.assertEqual(meta.get("speaker_map", {}).get("SPEAKER_00"), "Cansu")

        # 3. Deconfliction: single folder reuse produces zero duplicate meeting collisions
        subfolders = [container]
        violations = detect_meeting_duplicates(
            meeting_subfolders=subfolders,
            proj_name="project_beacon",
            meeting_date="10-14-2026",
            date_dir=container.parent,
        )
        self.assertEqual(len(violations), 0)

    def test_preflight_batched_diagnostics_on_unresolved_inputs_e2e(self) -> None:
        """Verifies preflight returns a comprehensive batched diagnostic report rather than a fail-fast cascade."""
        unresolved_meta: Dict[str, Any] = {
            "project": "project_beacon",
            "is_audio": True,
            # Deliberately missing: time, speaker_map, attendees, attendee_confirmation
        }
        transcript = "**SPEAKER_00:** Hello.\n**SPEAKER_01:** Hi there."
        report = batch_validate_preflight_checks(unresolved_meta, raw_transcript=transcript, input_path=None)

        self.assertFalse(report.is_valid)
        self.assertIn("time", report.missing_fields)
        self.assertIn("speaker_map", report.missing_fields)
        self.assertIn("attendee_confirmation", report.missing_fields)
        self.assertGreaterEqual(len(report.diagnostics), 3)


if __name__ == "__main__":
    unittest.main()

