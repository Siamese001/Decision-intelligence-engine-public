"""
tests/end_to_end/test_disaster_recovery_honesty_e2e.py

End-to-End Test Suite for Disaster Recovery Honesty & Integrity (Wave 2).
Validates:
1. Complete disaster recovery reconstruction lifecycle on interrupted/crashed sessions.
2. Truthful calculation of recovery rates (prohibiting fabricated 100% assertions on partial data).
3. Elimination of placeholder prose ("Recovered audio dialogue from...") in favor of [UNTRANSCRIBED].
4. Elimination of hardcoded fallback dates ("10-01-2026") in favor of dynamic ISO dates.
5. Master WAV stitching integrity across valid audio chunks.
6. Contract-valid session manifest and segments JSONL output.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

from dual_pipeline.audio_ingestion_core.ffmpeg import ensure_ffmpeg
from dual_pipeline.live_meeting_recorder_core.disaster_recovery import (
    recover_live_session,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestDisasterRecoveryHonestyE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_ffmpeg()

    def setUp(self):
        self.tmp_base = REPO_ROOT / ".tmp"
        self.tmp_base.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.mkdtemp(prefix="test_dr_honesty_e2e_", dir=str(self.tmp_base))
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_chunk(self, path: Path, duration_sec: float = 2.0, rate: int = 16000) -> None:
        """Helper to create a valid 16kHz mono PCM WAV file."""
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            frames = int(duration_sec * rate)
            w.writeframes(b"\x10\x00" * frames)

    def test_e2e_partial_recovery_lifecycle(self):
        """
        Executes end-to-end recovery on a crashed session where 5 of 8 chunks were transcribed.
        Verifies:
        - 5/8 (62.5%) recovery rate reported honestly.
        - Status is RECOVERED_PARTIAL in manifest and DISASTER_RECOVERY_PARTIAL in seal.
        - 3 chunks labeled [UNTRANSCRIBED].
        - Master WAV stitched from all 8 valid chunks.
        """
        session_dir = self.temp_path / "crashed_session"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        for i in range(8):
            self._create_sample_chunk(chunks_dir / f"chunk_{i:04d}.wav", duration_sec=1.5)

        # Journal with only 5 chunks transcribed (0, 1, 2, 3, 4)
        journal = session_dir / "session_journal.jsonl"
        with open(journal, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "session_start", "project": "project_nexus", "meeting": "Sprint Alignment"}) + "\n")
            for i in range(5):
                f.write(json.dumps({
                    "event": "chunk_transcribed",
                    "chunk_idx": i,
                    "text": f"Transcribed speech content for turn {i}.",
                    "speaker": "Samantha",
                }) + "\n")

        output_dir = self.temp_path / "recovered_output"
        result = recover_live_session(
            session_dir=session_dir,
            output_dir=output_dir,
            project="project_nexus",
            meeting="Sprint Alignment",
            date="10/04/2026",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.total_chunks, 8)
        self.assertEqual(result.recovered_turns, 5)
        self.assertAlmostEqual(result.recovery_rate, 5 / 8, places=3)
        self.assertAlmostEqual(result.duration_seconds, 12.0, places=1)

        # Inspect manifest
        self.assertIsNotNone(result.manifest_path)
        manifest_data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["status"], "RECOVERED_PARTIAL")
        self.assertEqual(manifest_data["valid_chunks_count"], 8)
        self.assertEqual(manifest_data["stitched_chunks_count"], 8)
        self.assertAlmostEqual(manifest_data["recovery_rate"], 0.625, places=3)

        # Inspect transcript
        self.assertIsNotNone(result.transcript_path)
        md = result.transcript_path.read_text(encoding="utf-8")
        self.assertIn("DISASTER_RECOVERY_PARTIAL", md)
        self.assertIn("62.5%", md)
        self.assertNotIn("Recovered audio dialogue from", md)
        self.assertIn("[UNTRANSCRIBED CHUNK 5]", md)
        self.assertIn("[UNTRANSCRIBED CHUNK 6]", md)
        self.assertIn("[UNTRANSCRIBED CHUNK 7]", md)

        # Inspect master WAV
        self.assertIsNotNone(result.master_wav)
        self.assertTrue(result.master_wav.is_file())
        with wave.open(str(result.master_wav), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertGreater(w.getnframes(), 0)

    def test_e2e_complete_recovery_lifecycle(self):
        """
        Executes end-to-end recovery on a crashed session where 4 of 4 chunks were transcribed.
        Verifies:
        - 100.0% recovery rate.
        - Status is RECOVERED and DISASTER_RECOVERY_SUCCESSFUL.
        """
        session_dir = self.temp_path / "complete_session"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        for i in range(4):
            self._create_sample_chunk(chunks_dir / f"chunk_{i:04d}.wav", duration_sec=2.0)

        # Journal with all 4 transcribed
        journal = session_dir / "session_journal.jsonl"
        with open(journal, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "session_start", "project": "project_partner_advisory", "meeting": "Architecture Review"}) + "\n")
            for i in range(4):
                f.write(json.dumps({
                    "event": "chunk_transcribed",
                    "chunk_idx": i,
                    "text": f"Architectural discussion point {i}.",
                    "speaker": "Daniel",
                }) + "\n")

        output_dir = self.temp_path / "recovered_complete_output"
        result = recover_live_session(
            session_dir=session_dir,
            output_dir=output_dir,
            project="project_partner_advisory",
            meeting="Architecture Review",
            date="10/04/2026",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.total_chunks, 4)
        self.assertEqual(result.recovered_turns, 4)
        self.assertEqual(result.recovery_rate, 1.0)

        manifest_data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["status"], "RECOVERED")

        md = result.transcript_path.read_text(encoding="utf-8")
        self.assertIn("DISASTER_RECOVERY_SUCCESSFUL", md)
        self.assertIn("100.0%", md)
        self.assertNotIn("[UNTRANSCRIBED", md)

    def test_date_fallback_uses_dynamic_date_not_hardcoded_constant(self):
        """Validates that date normalization errors fall back to dynamic today's date rather than '10-01-2026'."""
        session_dir = self.temp_path / "bad_date_session"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        self._create_sample_chunk(chunks_dir / "chunk_0000.wav")

        result = recover_live_session(
            session_dir=session_dir,
            output_dir=self.temp_path / "bad_date_out",
            date="invalid-unparseable-date",
        )

        self.assertTrue(result.success)
        now_dirname = datetime.now().strftime("%m-%d-%Y")

        self.assertIn(now_dirname, str(result.master_wav.name))
        # Ensure hardcoded fallback 10-01-2026 is NOT used
        if now_dirname != "10-01-2026":
            self.assertNotIn("10-01-2026", str(result.master_wav.name))


if __name__ == "__main__":
    unittest.main()
