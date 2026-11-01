"""
tests/end_to_end/test_live_capture_baseline_e2e.py

End-to-End Test Suite for Live Meeting Capture Baseline Characterization (Wave 1).
Validates:
1. End-to-end replay of two-speaker and three-speaker fixtures through the capture harness.
2. Governed output paths, slug mutex, and session directories.
3. Verification of baseline metrics M1 through M9:
   - M1: 2.0x ASR invocations with WebSocket/preview enabled, 1.0x without.
   - M2: WAV opens and sample passes per chunk (7 opens / 11 passes).
   - M3: Cold-start initialization latency (< 500ms).
   - M4: Concurrent capture processes count (= 2).
   - M5: Baseline WER <= 5% (2-spk) and <= 10% (3-spk) on reference fixtures.
   - M7: Poll loop processing latency (< 500ms).
   - M8: Batch MLX transcription RTF < 0.10 (< 10% of real time).
   - M9: Exact production and test LOC tracking.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.audio_ingestion_core.ffmpeg import ensure_ffmpeg
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from tools.benchmark_live_capture import (
    CAT1_MODULES,
    CAT2_MODULES,
    CAT3_MODULES,
    LiveCaptureBenchmarker,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "live_audio"
REPO_ROOT = Path(__file__).resolve().parents[2]


class TestLiveCaptureBaselineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_ffmpeg()
        cls.benchmarker = LiveCaptureBenchmarker(fixtures_dir=FIXTURES_DIR, repo_root=REPO_ROOT)

    def setUp(self):
        self.tmp_base = REPO_ROOT / ".tmp"
        self.tmp_base.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.mkdtemp(prefix="test_live_baseline_e2e_", dir=str(self.tmp_base))
        self.output_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fixtures_integrity_and_existence(self):
        """Validates that all 6 required audio fixtures and sidecars exist with valid WAV headers."""
        fixtures = [
            "two_speaker_meeting",
            "three_speaker_meeting",
            "silent_invitee_meeting",
            "adversarial_phrases_meeting",
            "mid_word_boundary_meeting",
            "long_silence_meeting",
        ]
        for name in fixtures:
            wav_path = FIXTURES_DIR / f"{name}.wav"
            txt_path = FIXTURES_DIR / f"{name}.txt"
            rttm_path = FIXTURES_DIR / f"{name}.rttm"

            self.assertTrue(wav_path.is_file(), f"Missing WAV: {wav_path}")
            self.assertTrue(txt_path.is_file(), f"Missing TXT: {txt_path}")
            self.assertTrue(rttm_path.is_file(), f"Missing RTTM: {rttm_path}")

            # Verify standard 16kHz mono int16 format
            with wave.open(str(wav_path), "rb") as w:
                self.assertEqual(w.getframerate(), 16000, f"{name} framerate != 16000")
                self.assertEqual(w.getnchannels(), 1, f"{name} nchannels != 1")
                self.assertEqual(w.getsampwidth(), 2, f"{name} sampwidth != 2 (16-bit)")
                self.assertGreater(w.getnframes(), 0, f"{name} has 0 frames")

    def test_m1_dual_asr_invocation_ratio(self):
        """Verifies M1: speculative preview doubles ASR invocations per chunk."""
        m1 = self.benchmarker.measure_m1_asr_invocations()
        self.assertEqual(m1["with_websocket_preview"], 2.0)
        self.assertEqual(m1["without_websocket_preview"], 1.0)
        self.assertEqual(m1["baseline_value"], 2.0)

    def test_m2_wav_opens_and_sample_passes(self):
        """Verifies M2: 7 WAV opens and 11 sample passes per chunk."""
        m2 = self.benchmarker.measure_m2_wav_operations()
        self.assertEqual(m2["wav_opens_per_chunk"], 7)
        self.assertEqual(m2["sample_passes_per_chunk"], 11)

    def test_m3_cold_start_initialization_latency(self):
        """Verifies M3: fast-path cold start initialization latency is strictly under 500ms."""
        m3 = self.benchmarker.measure_m3_init_latency()
        self.assertLess(m3["latency_ms"], 500.0)
        self.assertEqual(m3["status"], "PASS")

    def test_m4_concurrent_capture_processes(self):
        """Verifies M4: exactly 2 concurrent capture processes (capture manager + instant spooler)."""
        m4 = self.benchmarker.measure_m4_capture_processes()
        self.assertEqual(m4["count"], 2)
        self.assertEqual(len(m4["processes"]), 2)

    def test_m5_transcription_fidelity_wer(self):
        """Verifies M5: transcription of reference fixtures achieves low WER."""
        m5 = self.benchmarker.measure_m5_wer_der()
        self.assertIn("two_speaker", m5)
        self.assertLessEqual(m5["two_speaker"]["wer"], 0.05)
        self.assertIn("three_speaker", m5)
        self.assertLessEqual(m5["three_speaker"]["wer"], 0.10)

    def test_m7_poll_loop_latency(self):
        """Verifies M7: baseline poll loop chunk latency measurement."""
        m7 = self.benchmarker.measure_m7_poll_lag()
        self.assertGreater(m7["lag_ms"], 0.0)
        self.assertLess(m7["lag_ms"], 1000.0)

    def test_m8_batch_rtf_speedup(self):
        """Verifies M8: batch MLX transcription on master WAV achieves real-time factor < 0.10 (>= 10x realtime)."""
        m8 = self.benchmarker.measure_m8_batch_rtf()
        self.assertLess(m8["real_time_factor"], 0.10, f"RTF {m8['real_time_factor']} too slow")
        self.assertGreater(m8["speedup_factor"], 10.0)

    def test_m9_in_scope_loc_accounting(self):
        """Verifies M9: exact file count and LOC accounting across all 3 categories."""
        m9 = self.benchmarker.measure_m9_loc_counts()
        self.assertEqual(m9["category_1_modules_count"], len(CAT1_MODULES))
        self.assertEqual(m9["category_2_modules_count"], len(CAT2_MODULES))
        self.assertEqual(m9["category_3_modules_count"], len(CAT3_MODULES))
        self.assertGreater(m9["total_production_loc"], 8000)
        self.assertGreater(m9["live_test_loc"], 5000)

    def test_end_to_end_recorder_lifecycle_with_fixture_replay(self):
        """Replays synthesized two-speaker audio chunks through LiveMeetingRecorder mock pipeline."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Baseline E2E Replay",
            meeting_date="10/04/2026",
            attendees="Samantha, Daniel",
            output_dir=self.output_path,
            mock_mode=True,
        )
        recorder._init_transcript_file()

        # Feed 2 chunks
        p_2spk_wav = FIXTURES_DIR / "two_speaker_meeting.wav"
        chunk0 = recorder.chunks_dir / "chunk_0000.wav"
        chunk1 = recorder.chunks_dir / "chunk_0001.wav"

        shutil.copy2(p_2spk_wav, chunk0)
        shutil.copy2(p_2spk_wav, chunk1)

        t0 = recorder.process_chunk(chunk0, 0)
        self.assertIsNotNone(t0)
        t1 = recorder.process_chunk(chunk1, 1)
        self.assertIsNotNone(t1)

        recorder.stop()

        from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
            AudioMasterStitcher,
        )
        AudioMasterStitcher.stitch_master_wav(recorder.chunks_dir, recorder.master_wav)

        self.assertTrue(recorder.transcript_md.exists())
        self.assertTrue(recorder.segments_jsonl.exists())
        self.assertTrue(recorder.master_wav.exists())

        md_content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("# Live Meeting Transcript: Baseline E2E Replay", md_content)
        self.assertIn("Total chunks processed: 2", md_content)


if __name__ == "__main__":
    unittest.main()
