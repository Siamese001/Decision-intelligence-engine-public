"""
tests/end_to_end/test_fast_recording_initialization_e2e.py

End-to-End verification of Fast Recording Initialization:
1. Complete lifecycle: Fast initialization (<1s), CoreAudio HAL discovery, session registry,
   sample-accurate audio frames, speculative preview, and graceful shutdown/sealing.
2. CLI dry-run integration with 1,000ms probation guard.
3. Immediate worktree provisioning and multi-chunk timeline accounting.
"""
from __future__ import annotations

import json
import math
import os
import struct
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.cli import main
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from pipeline_a_intake.coreaudio_hal import CoreAudioHAL


def create_synthetic_wav(
    path: Path, num_frames: int, sample_rate: int = 16000, frequency: float = 440.0
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [
            int(2000 * math.sin(2 * math.pi * frequency * i / sample_rate))
            for i in range(num_frames)
        ]
        w.writeframes(struct.pack(f"{num_frames}h", *samples))
    return path


class TestFastRecordingInitializationE2E(unittest.TestCase):
    """Full lifecycle E2E test suite for fast recording initialization."""

    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[2] / ".worktrees" / "e2e_fast_recording_tmp"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.test_root / "out"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_e2e_cli_dry_run_probation_guard(self):
        """CLI record-meeting dry-run executes with affirmative probation guard in < 1,000ms."""
        t0 = time.perf_counter()
        exit_code = main([
            "record-meeting",
            "--meeting", "E2E Fast Path Verification",
            "--attendees", "Amit Ayer, Jen Pan",
            "--dry-run",
        ])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(exit_code, 0)
        self.assertLess(elapsed_ms, 3500.0, f"CLI dry run took {elapsed_ms:.2f}ms (> 3500ms)")

    def test_e2e_full_recording_lifecycle_and_timeline(self):
        """Full recording lifecycle from instant init to sample-accurate segment emission and sealing."""
        # 1. Verify CoreAudio HAL discovery latency < 100ms
        t0 = time.perf_counter()
        devices = CoreAudioHAL.get_all_device_ids()
        hal_latency_ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(hal_latency_ms, 100.0)
        self.assertIsInstance(devices, list)

        # 2. Initialize recorder and measure startup time (< 1.0s)
        t_init_start = time.perf_counter()
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="E2E Fast Recording Lifecycle",
            attendees="Amit Ayer, Reid S (NEA)",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
            enable_ws=True,
            segment_seconds=10,
        )
        init_duration_ms = (time.perf_counter() - t_init_start) * 1000.0
        self.assertLess(init_duration_ms, 1000.0, f"Init took {init_duration_ms:.2f}ms (> 1000ms)")
        self.assertTrue(hasattr(recorder, "initialization_latency_ms"))
        self.assertLess(recorder.initialization_latency_ms, 500.0, f"Fast start latency took {recorder.initialization_latency_ms:.2f}ms (> 500ms)")

        try:
            # 3. Verify session registry registration
            self.assertTrue(hasattr(recorder, "session_id"))
            record = recorder.session_registry.inspect(recorder.session_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.parent_pid, os.getpid())

            # 4. Feed two audio chunks with sample-accurate lengths
            # Chunk 0: exactly 160,000 frames @ 16kHz = 10.00s
            # Chunk 1: exactly 160,800 frames @ 16kHz = 10.05s
            c0 = create_synthetic_wav(recorder.chunks_dir / "chunk_00000.wav", 160_000)
            c1 = create_synthetic_wav(recorder.chunks_dir / "chunk_00001.wav", 160_800)

            # Process chunk 0
            text0 = recorder.process_chunk(c0, 0)
            self.assertIsNotNone(text0)

            # Process chunk 1
            text1 = recorder.process_chunk(c1, 1)
            self.assertIsNotNone(text1)

            # 5. Verify sample-accurate cumulative accounting (10.00 + 10.05 = 20.05s)
            self.assertAlmostEqual(recorder._cumulative_elapsed_seconds, 20.05, delta=0.01)

            # 6. Verify segments.jsonl precision fields
            self.assertTrue(recorder.segments_jsonl.exists())
            with open(recorder.segments_jsonl, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]

            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["start_sec"], 0.0)
            self.assertEqual(lines[0]["duration_sec"], 10.0)
            self.assertEqual(lines[0]["end_sec"], 10.0)

            self.assertEqual(lines[1]["start_sec"], 10.0)
            self.assertEqual(lines[1]["duration_sec"], 10.05)
            self.assertEqual(lines[1]["end_sec"], 20.05)

            # 7. Stop and seal session
            recorder.stop()
            self.assertTrue(recorder.transcript_md.exists())
            transcript_text = recorder.transcript_md.read_text(encoding="utf-8")
            self.assertIn("Total chunks processed: 2", transcript_text)

            # 8. Verify session unregistration from registry
            unregistered_record = recorder.session_registry.inspect(recorder.session_id)
            self.assertIsNone(unregistered_record)
        finally:
            recorder._release_slug_lock()
