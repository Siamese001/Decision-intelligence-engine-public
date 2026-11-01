"""
tests/adversarial/test_live_recording_chaos_harness.py

Layer 1: Adversarial Chaos Recovery & Disaster Resilience Harness.
Exercises unfinalized session disaster recovery under process SIGKILL, mid-session device swaps,
corrupt chunk corruption, and concurrent image paste races.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.disaster_recovery import (
    recover_live_session,
)


def _write_synthetic_wav(path: Path, duration_sec: float = 0.5, framerate: int = 16000) -> Path:
    """Generates a valid 16kHz mono PCM WAV file with audible tone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_frames = int(framerate * duration_sec)
    samples = bytearray()
    for i in range(num_frames):
        val = int(16000.0 * math.sin(2.0 * math.pi * 440.0 * i / framerate))
        samples.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, framerate, num_frames, "NONE", "not compressed"))
        w.writeframes(bytes(samples))
    return path


class LiveRecordingChaosHarnessAdversarialTests(unittest.TestCase):
    """Adversarial stress harness for disaster recovery and unfinalized session healing."""

    def setUp(self) -> None:
        self.test_dir = Path("/tmp") / f"test_chaos_{unittest.TestCase().id().split('.')[-1]}"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sigkill_unfinalized_session_recovery(self) -> None:
        """Simulates SIGKILL mid-session: 10 chunks, partial journal, images, no master WAV."""
        session_dir = self.test_dir / "session_sigkill"
        staging_chunks = session_dir / ".staging_strategy_sync_103000"
        ref_artifacts = session_dir / "referenced_artifacts"
        staging_chunks.mkdir(parents=True, exist_ok=True)
        ref_artifacts.mkdir(parents=True, exist_ok=True)

        # 1. Create 10 raw audio chunks (0.5s each -> 5.0s total)
        for i in range(10):
            _write_synthetic_wav(staging_chunks / f"chunk_{i:04d}.wav", duration_sec=0.5)

        # 2. Write partial session_journal.jsonl (simulating crash before completion)
        journal_p = session_dir / "session_journal.jsonl"
        with open(journal_p, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "session_start", "meeting": "Strategy Sync", "project": "chaos_test",
                "date": "10/01/2026", "date_dirname": "10-01-2026",
            }) + "\n")
            for i in range(8):
                f.write(json.dumps({
                    "event": "chunk_transcribed", "chunk_idx": i, "speaker": "Amit Ayer",
                    "text": f"Strategic roadmap item {i}", "duration_sec": 0.5,
                }) + "\n")

        # 3. Drop 2 collateral screenshots
        (ref_artifacts / "architecture_arch.png").write_bytes(b"\x89PNG\r\n\x1a\nchaos_image_1")
        (ref_artifacts / "budget_plan.png").write_bytes(b"\x89PNG\r\n\x1a\nchaos_image_2")

        # Execute disaster recovery
        result = recover_live_session(session_dir=session_dir)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.master_wav)
        self.assertTrue(result.master_wav.exists())
        self.assertEqual(result.total_chunks, 10)
        self.assertAlmostEqual(result.duration_seconds, 5.0, places=1)
        self.assertAlmostEqual(result.recovery_rate, 0.8, places=2)

        # Assert manifest was reconstructed with RECOVERED_PARTIAL status
        self.assertIsNotNone(result.manifest_path)
        manifest_data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["status"], "RECOVERED_PARTIAL")
        self.assertTrue(manifest_data["is_recovered"])
        self.assertEqual(manifest_data["total_chunks"], 10)

        # Assert collateral drop log was reconstructed
        self.assertIsNotNone(result.collateral_log_path)
        self.assertTrue(result.collateral_log_path.exists())
        drops = json.loads(result.collateral_log_path.read_text(encoding="utf-8"))
        self.assertEqual(len(drops), 2)
        for d in drops:
            self.assertEqual(d["attribution_mode"], "COLLATERAL_REVIEWED")

        # Assert transcript was sealed with disaster recovery footer
        self.assertIsNotNone(result.transcript_path)
        transcript_content = result.transcript_path.read_text(encoding="utf-8")
        self.assertIn("DISASTER_RECOVERY_PARTIAL", transcript_content)
        self.assertIn("Total Chunks Recovered:** 10 / 10", transcript_content)

    def test_mid_session_device_dropout_chaos(self) -> None:
        """Simulates device failover where chunks were split across multiple directories."""
        session_dir = self.test_dir / "session_dropout"
        epoch1_dir = session_dir / ".staging_epoch1"
        epoch2_dir = session_dir / ".staging_epoch2"
        epoch1_dir.mkdir(parents=True, exist_ok=True)
        epoch2_dir.mkdir(parents=True, exist_ok=True)

        # Epoch 1: chunks 0..4
        for i in range(5):
            _write_synthetic_wav(epoch1_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        # Epoch 2: chunks 5..9 (after failover switch)
        for i in range(5, 10):
            _write_synthetic_wav(epoch2_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        # Execute recovery
        result = recover_live_session(session_dir=session_dir, meeting="Device Failover Test")
        self.assertTrue(result.success)
        self.assertEqual(result.total_chunks, 10)
        self.assertAlmostEqual(result.duration_seconds, 5.0, places=1)

        # Verify stitched WAV has exactly 10 frames worth of audio
        with wave.open(str(result.master_wav), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getnframes(), 16000 * 5)

    def test_corrupt_chunk_skipping_in_recovery(self) -> None:
        """Simulates a session with a 0-byte corrupt chunk amidst valid chunks."""
        session_dir = self.test_dir / "session_corrupt_chunk"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        # Chunks 0, 1, 2 valid (0.5s)
        for i in range(3):
            _write_synthetic_wav(chunks_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        # Chunk 3 corrupted (0-byte file)
        (chunks_dir / "chunk_0003.wav").write_bytes(b"")

        # Chunks 4, 5 valid (0.5s)
        for i in range(4, 6):
            _write_synthetic_wav(chunks_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        result = recover_live_session(session_dir=session_dir)
        self.assertTrue(result.success)
        # Corrupt chunk was skipped, 5 valid chunks recovered
        self.assertEqual(result.total_chunks, 5)
        self.assertAlmostEqual(result.duration_seconds, 2.5, places=1)
        self.assertEqual(result.recovery_rate, 0.0)

    def test_concurrent_image_paste_and_crash_recovery(self) -> None:
        """Simulates concurrent collateral drops right before process termination."""
        session_dir = self.test_dir / "session_concurrent_paste"
        ref_artifacts = session_dir / "referenced_artifacts"
        ref_artifacts.mkdir(parents=True, exist_ok=True)

        _write_synthetic_wav(session_dir / "chunk_0000.wav", duration_sec=0.5)
        _write_synthetic_wav(session_dir / "chunk_0001.wav", duration_sec=0.5)

        # Three pasted images
        img1 = ref_artifacts / "slide_1.png"
        img2 = ref_artifacts / "slide_2.png"
        img3 = ref_artifacts / "flowchart.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\nslide1_data")
        img2.write_bytes(b"\x89PNG\r\n\x1a\nslide2_data")
        img3.write_bytes(b"\x89PNG\r\n\x1a\nflowchart_data")

        result = recover_live_session(session_dir=session_dir)
        self.assertTrue(result.success)
        self.assertTrue(result.collateral_log_path.exists())

        drops = json.loads(result.collateral_log_path.read_text(encoding="utf-8"))
        self.assertEqual(len(drops), 3)
        filenames = {d["filename"] for d in drops}
        self.assertEqual(filenames, {"slide_1.png", "slide_2.png", "flowchart.png"})

    def test_cli_invocation_recovery(self) -> None:
        """Executes tools/recover_live_session.py CLI subcommand as a subprocess."""
        session_dir = self.test_dir / "session_cli"
        session_dir.mkdir(parents=True, exist_ok=True)
        _write_synthetic_wav(session_dir / "chunk_0000.wav", duration_sec=0.5)
        _write_synthetic_wav(session_dir / "chunk_0001.wav", duration_sec=0.5)

        cli_script = Path(__file__).resolve().parent.parent.parent / "nee.py"
        cmd = [sys.executable, str(cli_script), "recover-session", "--session-dir", str(session_dir), "--meeting", "CLI Recovery Test"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        self.assertIn("DISASTER RECOVERY COMPLETE", proc.stdout)
        self.assertIn("Total Chunks Found:   2", proc.stdout)



if __name__ == "__main__":
    unittest.main()
