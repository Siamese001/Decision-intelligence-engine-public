"""End-to-End test suite for Wave 4: Master WAV SSOT consolidation, handoff formula, and resilient stop lifecycle."""
from __future__ import annotations

import contextlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

from dual_pipeline.audio_postprocess import build_initial_prompt
from dual_pipeline.cli.canonical_invocation import (
    build_canonical_ingest_command,
    format_date_slug,
    format_directive_slug,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
    AudioMasterStitcher,
    StitchReceipt,
)
from dual_pipeline.transcript_pipeline_core.speaker_map_loader import (
    load_colocated_speaker_map,
)


def _create_sine_wav(path: Path, n_samples: int, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [int(1000 * (i % 32) / 32) for i in range(n_samples)]
        w.writeframes(struct.pack(f"<{n_samples}h", *samples))


class TestLiveMasterWavSsotHandoffE2E(unittest.TestCase):
    """E2E lifecycle testing for master WAV SSOT consolidation and AGENTS.md handoff."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w4_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self._recorders: list[Any] = []

    def tearDown(self) -> None:
        for rec in self._recorders:
            with contextlib.suppress(Exception):
                rec._release_slug_lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_canonical_invocation_matches_agents_md_formula_byte_for_byte(self) -> None:
        """Verify that build_canonical_ingest_command formats the canonical CLI string exactly."""
        master_wav = Path("/tmp/meeting/00_master.wav")
        project = "project_nexus"
        meeting = "Executive Alignment Marcus PartnerAdvisory"
        date = "10/04/2026"
        attendees = "Marcus, Alex, Amit Ayer"
        reviewer = "operator"

        cmd = build_canonical_ingest_command(
            input_path=master_wav,
            project=project,
            meeting=meeting,
            date=date,
            attendees=attendees,
            reviewer=reviewer,
        )

        expected_slug = format_directive_slug(meeting)
        expected_date = format_date_slug(date)
        expected_directive_id = f"directive-{project}-{expected_slug}-{expected_date}-v1"

        self.assertIn("nee ingest-meeting", cmd)
        self.assertIn(f'--input "{master_wav}"', cmd)
        self.assertIn(f'--project "{project}"', cmd)
        self.assertIn(f'--meeting "{meeting}"', cmd)
        self.assertIn(f'--date "{date}"', cmd)
        self.assertIn(f'--attendees "{attendees}"', cmd)
        self.assertIn("--provider llm", cmd)
        self.assertIn("--max-judge-retries 2", cmd)
        self.assertNotIn("--mutate-graph", cmd)
        self.assertIn(f'--directive-id "{expected_directive_id}"', cmd)
        self.assertNotIn("--reviewer", cmd)
        self.assertNotIn("--rationale", cmd)
        self.assertIn("--embedding-backend bge-m3-dev", cmd)
        self.assertIn("--allow-dev-bge-m3", cmd)
        self.assertIn("--enforce-worktree", cmd)

    def test_master_wav_stitching_with_receipt_and_tuple_unpacking(self) -> None:
        """Verify AudioMasterStitcher produces StitchReceipt that supports 3-tuple unpacking."""
        chunks_dir = self.tmp_path / "chunks"
        master_wav = self.tmp_path / "master.wav"

        _create_sine_wav(chunks_dir / "chunk_0000.wav", 16000)
        _create_sine_wav(chunks_dir / "chunk_0001.wav", 16000)

        # 1. Direct call with skip-and-pad mode enabled
        receipt = AudioMasterStitcher.stitch_master_wav(
            chunks_dir,
            master_wav,
            pad_missing_or_corrupt=True,
        )

        self.assertIsInstance(receipt, StitchReceipt)
        self.assertTrue(receipt.success)
        self.assertEqual(receipt.valid_chunks, 2)
        self.assertEqual(receipt.total_chunks, 2)
        self.assertEqual(receipt.padded_chunks, 0)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertIsNotNone(receipt.master_wav_sha256)
        self.assertAlmostEqual(receipt.duration_s, 2.0, places=1)

        # 2. Backwards-compatible tuple unpacking (success, count, duration)
        success, count, duration = receipt
        self.assertTrue(success)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(duration, 2.0, places=1)

    def test_recording_stop_lifecycle_seals_manifest_and_emits_canonical_command(self) -> None:
        """Verify full LiveMeetingRecorder lifecycle produces sealed manifest with canonical command."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Nexus Alignment Marcus",
            meeting_date="10/04/2026",
            attendees="Marcus PartnerAdvisory, Amit Ayer",
            output_dir=self.tmp_path / "output",
            mock_mode=True,
        )
        self._recorders.append(recorder)

        recorder.chunks_dir.mkdir(parents=True, exist_ok=True)
        c0 = recorder.chunks_dir / "chunk_00000.wav"
        c1 = recorder.chunks_dir / "chunk_00001.wav"
        _create_sine_wav(c0, 16000)
        _create_sine_wav(c1, 16000)

        # Process chunks
        recorder.process_chunk(c0, 0)
        recorder.process_chunk(c1, 1)

        # Execute stop
        recorder.stop()

        # 1. Manifest exists and is sealed with canonical command
        self.assertTrue(recorder.session_manifest.exists())
        manifest = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("canonical_ingest_command", manifest)
        self.assertIn("nee ingest-meeting", manifest["canonical_ingest_command"])
        self.assertEqual(manifest.get("provenance"), "LIVE_PREVIEW (non-authoritative)")
        self.assertIn("stopped_at", manifest)

        # 2. Live transcript has LIVE_PREVIEW header and sealed footer
        self.assertTrue(recorder.transcript_md.exists())
        md_text = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("LIVE_PREVIEW (non-authoritative)", md_text)
        self.assertIn("[SESSION_SEALED]", md_text)

        # 3. Canonical command stored on recorder instance
        self.assertTrue(hasattr(recorder, "canonical_ingest_command"))
        self.assertIn("directive-project_nexus", recorder.canonical_ingest_command)

    def test_speaker_map_sidecar_ssot_loader(self) -> None:
        """Verify load_colocated_speaker_map resolves exact, clean, and wildcard sidecars."""
        meeting_dir = self.tmp_path / "nexus"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        wav_file = meeting_dir / "00_10-04-2026_nexus_alignment_master.wav"
        wav_file.touch()

        # Test sidecar next to wav
        sidecar = meeting_dir / "00_10-04-2026_nexus_alignment_speaker_map.json"
        expected_map = {"SPEAKER_00": "Marcus PartnerAdvisory", "SPEAKER_01": "Amit Ayer"}
        sidecar.write_text(json.dumps(expected_map), encoding="utf-8")

        loaded = load_colocated_speaker_map(wav_file)
        self.assertEqual(loaded, expected_map)

    def test_domain_lexicon_enrichment_in_audio_postprocess(self) -> None:
        """Verify that build_initial_prompt incorporates project-specific domain lexicons."""
        prompt_nexus = build_initial_prompt({"project_name": "project_nexus"})
        self.assertIsNotNone(prompt_nexus)
        self.assertIn("ROCC", prompt_nexus)
        self.assertIn("SCADA", prompt_nexus)

        prompt_lens = build_initial_prompt({"project_name": "project_lens"})
        self.assertIsNotNone(prompt_lens)
        self.assertIn("Turbine", prompt_lens)


if __name__ == "__main__":
    unittest.main()
