"""
tests/end_to_end/test_live_recording_chaos_e2e.py

Layer 1: End-to-End Live Recording Crash & Disaster Recovery Test.
Verifies complete lifecycle of crash recovery: unfinalized session generation,
disaster recovery reconstruction, and verification of Stage U0 handoff artifacts.
"""
from __future__ import annotations

import json
import math
import struct
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.disaster_recovery import (
    RecoveryResult,
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


class LiveRecordingChaosE2ETests(unittest.TestCase):
    """End-to-End verification of live recording crash recovery and U0 ingestion readiness."""

    def setUp(self) -> None:
        self.test_dir = Path("/tmp") / f"test_e2e_chaos_{unittest.TestCase().id().split('.')[-1]}"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_e2e_live_recorder_crash_recovery_pipeline(self) -> None:
        """Simulates end-to-end recording crash and disaster recovery reconstruction."""
        session_dir = self.test_dir / "e2e_crashed_session"
        staging_dir = session_dir / ".staging_architecture_review_110000"
        ref_artifacts = session_dir / "referenced_artifacts"
        staging_dir.mkdir(parents=True, exist_ok=True)
        ref_artifacts.mkdir(parents=True, exist_ok=True)

        # 1. Generate 8 audio chunks (0.5s each = 4.0s total)
        for i in range(8):
            _write_synthetic_wav(staging_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        # 2. Write active session journal
        journal_p = session_dir / "session_journal.jsonl"
        with open(journal_p, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "session_start", "project": "project_nexus", "meeting": "Architecture Review",
                "date": "10/01/2026", "date_dirname": "10-01-2026",
            }) + "\n")
            for i in range(6):
                f.write(json.dumps({
                    "event": "chunk_transcribed", "chunk_idx": i, "speaker": "Amit Ayer",
                    "text": f"Architecture component discussion point {i}", "duration_sec": 0.5,
                }) + "\n")

        # 3. Add multimodal collateral screenshot
        (ref_artifacts / "nexus_topology.png").write_bytes(b"\x89PNG\r\n\x1a\nfake_topology_image")

        # 4. Trigger Disaster Recovery
        result: RecoveryResult = recover_live_session(session_dir=session_dir)

        # Assert disaster recovery succeeded
        self.assertTrue(result.success)
        self.assertIsNotNone(result.master_wav)
        self.assertTrue(result.master_wav.exists())
        self.assertEqual(result.total_chunks, 8)
        self.assertAlmostEqual(result.duration_seconds, 4.0, places=1)
        self.assertAlmostEqual(result.recovery_rate, 0.75, places=2)

        # Assert manifest was reconstructed
        self.assertIsNotNone(result.manifest_path)
        manifest_data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["status"], "RECOVERED_PARTIAL")
        self.assertTrue(manifest_data["is_recovered"])
        self.assertEqual(manifest_data["project"], "project_nexus")
        self.assertEqual(manifest_data["meeting"], "Architecture Review")

        # Assert collateral drop log was written
        self.assertIsNotNone(result.collateral_log_path)
        drops = json.loads(result.collateral_log_path.read_text(encoding="utf-8"))
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["filename"], "nexus_topology.png")
        self.assertEqual(drops[0]["attribution_mode"], "COLLATERAL_REVIEWED")

        # Assert transcript markdown has forensic feed and recovery seal
        self.assertIsNotNone(result.transcript_path)
        transcript_text = result.transcript_path.read_text(encoding="utf-8")
        self.assertIn("# Live Meeting Transcript: Architecture Review", transcript_text)
        self.assertIn("Architecture component discussion point 0", transcript_text)
        self.assertIn("DISASTER_RECOVERY_PARTIAL", transcript_text)
        self.assertIn("Total Chunks Recovered:** 8 / 8", transcript_text)

    def test_e2e_recovered_session_u0_handoff(self) -> None:
        """Verifies that recovered session artifacts satisfy Stage U0 intake requirements."""
        session_dir = self.test_dir / "e2e_u0_handoff"
        session_dir.mkdir(parents=True, exist_ok=True)

        for i in range(4):
            _write_synthetic_wav(session_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        result = recover_live_session(
            session_dir=session_dir,
            project="gemini_enablement",
            meeting="Files and Data Enablement",
            date="10/01/2026",
        )
        self.assertTrue(result.success)

        # Verify Master WAV readability and parameters
        with wave.open(str(result.master_wav), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertGreater(w.getnframes(), 0)

        # Verify Manifest JSON structure
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["project"], "gemini_enablement")
        self.assertEqual(manifest["meeting"], "Files and Data Enablement")
        self.assertEqual(manifest["clean_slug"], "Files_Data_Enablement")
        self.assertEqual(manifest["total_chunks"], 4)

        # Verify Transcript contains standard U0 structure
        transcript = result.transcript_path.read_text(encoding="utf-8")
        self.assertIn("## Forensic Dialogue Feed", transcript)
        self.assertIn("## Disaster Recovery Seal", transcript)


if __name__ == "__main__":
    unittest.main()
