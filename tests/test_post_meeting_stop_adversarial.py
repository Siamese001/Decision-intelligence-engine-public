"""
tests/test_post_meeting_stop_adversarial.py

Adversarial test suite for Wave 1: Post-meeting stop lifecycle, rapid stop races,
0-chunk aborts, alias project invariance, and missing field repoints.
"""

from __future__ import annotations

import concurrent.futures
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingSessionHandle,
    RecordingStartRequest,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    repoint_recorder_paths,
)
from dual_pipeline.project_aliases import resolve_project_name


def _create_dummy_wav(path: Path, num_samples: int = 16000, value: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{num_samples}h", *[value] * num_samples))


class TestPostMeetingStopAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_stop_adv_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rapid_concurrent_stop_race(self) -> None:
        """Verify calling stop() concurrently across threads does not throw or corrupt state."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Concurrent Stop Test",
            output_dir=self.temp_path,
            mock_mode=True,
            attendees="Alice, Bob",
        )
        chunk0 = recorder.chunks_dir / "chunk_0000.wav"
        _create_dummy_wav(chunk0, num_samples=16000)
        recorder.processed_chunks.add("chunk_0000.wav")

        handle = RecordingSessionHandle(
            request=RecordingStartRequest(project_name="project_nexus", meeting_name="Concurrent Stop Test"),
            session_id=recorder.session_id,
            repo_root=self.temp_path,
        )
        handle.recorder = recorder

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(handle.stop) for _ in range(5)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        self.assertEqual(len(results), 5)
        for r in results:
            self.assertEqual(r.status, "STOPPED")
        self.assertTrue(recorder.master_wav.exists())

    def test_zero_chunk_abort_vs_multichunk_graceful_stop(self) -> None:
        """Verify 0-chunk abort cleans up cleanly without crashing on empty audio stitching."""
        # 1. Zero-chunk session abort
        rec_zero = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Zero Chunk Abort",
            output_dir=self.temp_path / "zero",
            mock_mode=True,
            attendees="Alice",
        )
        # Abort before any chunks processed
        rec_zero._early_abort_cleanup()
        self.assertTrue(getattr(rec_zero, "_is_stopped", False))
        self.assertFalse(rec_zero.master_wav.exists())

        # 2. Multi-chunk session graceful stop
        rec_multi = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Multi Chunk Normal",
            output_dir=self.temp_path / "multi",
            mock_mode=True,
            attendees="Alice, Bob",
        )
        c0 = rec_multi.chunks_dir / "chunk_0000.wav"
        c1 = rec_multi.chunks_dir / "chunk_0001.wav"
        _create_dummy_wav(c0, num_samples=16000)
        _create_dummy_wav(c1, num_samples=16000)
        rec_multi.processed_chunks.add("chunk_0000.wav")
        rec_multi.processed_chunks.add("chunk_0001.wav")

        rec_multi.stop()
        self.assertTrue(rec_multi.master_wav.exists())
        self.assertGreater(rec_multi.master_wav.stat().st_size, 0)

    def test_project_alias_invariance(self) -> None:
        """Verify that various alias project inputs resolve canonically to project_lens."""
        from unittest.mock import patch

        aliases = ["LENS", "lens", "project_lens", "LENS-AI", "lens-business"]
        for idx, a in enumerate(aliases):
            canonical = resolve_project_name(a)
            self.assertEqual(
                canonical,
                "project_lens",
                f"Alias '{a}' failed to resolve to canonical 'project_lens'",
            )

            with patch("dual_pipeline.live_meeting_recorder.ROOT", self.temp_path):
                rec = LiveMeetingRecorder(
                    project_name=a,
                    meeting_name=f"Alias Check {idx}",
                    output_dir=None,
                    mock_mode=True,
                    attendees="Daniel Sabet",
                )
                try:
                    self.assertEqual(rec.project_name, "project_lens")
                    self.assertIn("project_lens", str(rec.output_dir))
                finally:
                    rec._release_slug_lock()

    def test_repoint_recorder_paths_missing_optional_fields(self) -> None:
        """Verify repoint_recorder_paths handles missing optional components without KeyError or AttributeError."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Repoint Resilience",
            output_dir=self.temp_path,
            mock_mode=True,
            attendees="Solo",
        )
        # Deliberately remove optional fields
        recorder.speaker_hint_manager = None
        recorder.speaker_hint_queue = None
        recorder.chat_attributions = None
        recorder.capture_manager = None
        recorder.session_manifest = None

        new_wt_dir = self.temp_path / "new_worktree"
        new_wt_dir.mkdir(parents=True, exist_ok=True)

        # Repoint should complete without exception
        repoint_recorder_paths(recorder, target_worktree_dir=new_wt_dir)
        self.assertEqual(recorder.worktree_dir, new_wt_dir)
        recorder._release_slug_lock()

    def test_partial_segments_do_not_poison_master_asr_cache(self) -> None:
        """
        Verify BUG-02 adversarial guard: when stitched master WAV has 10 chunks (50s)
        but only 1 chunk (5s) was processed into segments.jsonl, seed_master_asr_from_recorder
        refuses to poison .raw_asr.json.
        """
        from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import StitchReceipt
        from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder

        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Partial Cache Guard",
            output_dir=self.temp_path / "partial_guard",
            mock_mode=True,
            attendees="Alice, Bob",
        )
        try:
            recorder.segments_jsonl.parent.mkdir(parents=True, exist_ok=True)
            recorder.segments_jsonl.write_text(
                json.dumps({"start_sec": 0.0, "end_sec": 5.0, "text": "Only chunk zero", "speaker": "Alice"}) + "\n",
                encoding="utf-8",
            )
            recorder.processed_chunks = {"chunk_00000.wav"}
            receipt = StitchReceipt(
                success=True,
                valid_chunks=10,
                duration_s=50.0,
                status="SUCCESS",
                total_chunks=10,
                padded_chunks=0,
                master_wav_sha256="a" * 64,
                master_wav_path=str(recorder.master_wav),
            )

            seeded = seed_master_asr_from_recorder(recorder, receipt)
            self.assertFalse(seeded)
            self.assertEqual(len(list(recorder.cache_dir.glob("*.raw_asr.json"))), 0)
        finally:
            recorder._release_slug_lock()

    def test_spooler_master_wav_promotion_with_riff_repair_and_sha256_sync(self) -> None:
        """
        Verify BUG-02 & BUG-04: when spooler.master_wav is larger than stitched chunks and has
        an unfinalized 0xFFFFFFFF RIFF header, stop() promotes it, repairs the RIFF header,
        and updates StitchReceipt.master_wav_sha256 to match the repaired disk file.
        """
        import hashlib
        from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler

        spool_dir = self.temp_path / "spool_riff"
        spooler = InstantAudioSpooler(
            spool_dir=spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="spool_riff_test",
        )
        # Create 1 chunk (1s) and a larger master_spool.wav (3s) with unfinalized 0xFFFFFFFF RIFF size
        _create_dummy_wav(spool_dir / "chunk_00000.wav", num_samples=16000, value=1000)
        _create_dummy_wav(spooler.master_wav, num_samples=16000 * 3, value=1100)
        raw_bytes = bytearray(spooler.master_wav.read_bytes())
        raw_bytes[4:8] = b"\xff\xff\xff\xff"
        raw_bytes[40:44] = b"\xff\xff\xff\xff"
        spooler.master_wav.write_bytes(bytes(raw_bytes))

        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="RIFF Repair Sync",
            output_dir=self.temp_path / "riff_sync",
            mock_mode=True,
            attendees="Alice, Bob",
            spooler=spooler,
        )
        try:
            recorder.stop()
            self.assertTrue(recorder.master_wav.exists())
            repaired_bytes = recorder.master_wav.read_bytes()
            self.assertNotEqual(repaired_bytes[4:8], b"\xff\xff\xff\xff")
            self.assertNotEqual(repaired_bytes[40:44], b"\xff\xff\xff\xff")
            disk_sha = hashlib.sha256(repaired_bytes).hexdigest()
            self.assertIsNotNone(recorder.stitch_receipt)
            self.assertEqual(recorder.stitch_receipt.master_wav_sha256, disk_sha)
            self.assertAlmostEqual(recorder.stitch_receipt.duration_s, 3.0, places=2)
        finally:
            recorder._release_slug_lock()


if __name__ == "__main__":
    unittest.main()

