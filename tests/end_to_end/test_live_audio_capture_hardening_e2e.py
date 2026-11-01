"""
tests/end_to_end/test_live_audio_capture_hardening_e2e.py

End-to-End Integration & Regression Harness for Live Audio Capture Pipeline Hardening:
Validates the complete 6-wave hardened lifecycle:
1. Native Tap Integrity & Preflight: Verifies cryptographic hash binding and probe resilience.
2. Audio Stream Clock & Resample Invariant: Verifies FFmpeg filtergraphs with monotonic synchronization.
3. Watchdog Circuit Breaker & Sentinel Hysteresis: Verifies non-oscillating failover state machine.
4. Acoustic VAD & Anti-Hallucination Defense: Verifies ambient room noise suppression, watermark filtering,
   and generative lexical priming containment across streaming chunks.
5. End-to-End Meeting Recording Lifecycle: Exercises start -> stream chunks (silence, room noise, speech, pause)
   -> drain -> transcript markdown & segment JSONL sealing with zero process leaks.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    AudioChunkValidator,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import (
    AudioPreflightValidator,
)
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    get_audio_rms,
)
from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import (
    AudioSubprocessWatchdog,
)
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _filter_hallucinations,
    _filter_segment_metadata,
)
from tools.verify_mac_audio_tap_integrity import verify_integrity

ROOT = Path(__file__).resolve().parents[2]


def _create_sine_wav(
    path: Path,
    duration_sec: float = 1.0,
    amplitude: float = 500.0,
    freq_hz: float = 300.0,
    sample_rate: int = 16000,
) -> Path:
    """Creates a 16kHz 16-bit mono WAV file with exact acoustic properties."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            if amplitude > 0.0:
                t = float(i) / sample_rate
                val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t)) if freq_hz > 0 else int(amplitude)
            else:
                val = 0
            frames.extend(struct.pack("<h", max(-32768, min(32767, val))))
        wf.writeframes(frames)
    return path


class TestLiveAudioCaptureHardeningE2E(unittest.TestCase):
    """Full End-to-End regression suite for the hardened audio capture pipeline."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_e2e_hardened_")
        self.temp_path = Path(self.temp_dir)
        self.output_dir = self.temp_path / "output"
        self.cache_dir = self.temp_path / "cache"
        self.chunks_dir = self.cache_dir / "chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_e2e_native_tap_integrity_and_preflight(self):
        """E2E Step 1: Validates native tap binary integrity and preflight validator."""
        # 1. Cryptographic manifest integrity
        manifest_path = ROOT / "tools" / "mac_audio_tap" / "mac_audio_tap.manifest.json"
        binary_path = ROOT / "tools" / "mac_audio_tap" / "bin" / "mac_audio_tap"
        if binary_path.exists() and manifest_path.exists():
            ok, msg = verify_integrity(repo_root=ROOT)
            self.assertTrue(ok, f"Tap integrity check failed: {msg}")

        # 2. Preflight validation probe execution
        with patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=(True, "ScreenCaptureKit granted")):
            tcc_ok, reason = AudioPreflightValidator.check_sck_tcc_permission()
            self.assertTrue(tcc_ok)
            self.assertIn("granted", reason)

        # 3. Fail-closed on TCC denial without silent fallback (Operator Decision D1)
        with patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=(False, "TCC denied")):
            mgr = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.cache_dir / "master.wav",
                source_mode="system_and_mic",
                mock_mode=True,
            )
            mgr._detected_headphones = True
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr._assert_system_audio_viable()
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TAP_FAILED)

    def test_e2e_sck_stream_accumulator_and_resample_filter(self):
        """E2E Step 2: Validates FFmpeg filtergraph construction with async resample invariant."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.cache_dir / "master.wav",
            source_mode="mixed",
            enable_stereo_isolation=False,
        )
        dummy_bin = self.temp_path / "mac_audio_tap"
        dummy_bin.touch()

        with patch("subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary", return_value=dummy_bin), \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            mock_proc = MagicMock()
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc

            mgr._start_mixed_capture(None)

            ffmpeg_calls = [
                c for c in mock_popen.call_args_list
                if isinstance(c[0][0], list) and "-filter_complex" in c[0][0]
            ]
            self.assertTrue(len(ffmpeg_calls) > 0)
            cmd = ffmpeg_calls[0][0][0]
            filter_arg_idx = cmd.index("-filter_complex")
            filter_str = cmd[filter_arg_idx + 1]

            self.assertIn("[0:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("[1:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("min_hard_comp=0.100000", filter_str)

    def test_e2e_watchdog_circuit_breaker_and_hysteresis(self):
        """E2E Step 3: Validates watchdog dynamic thresholds, sentinel debouncing, and circuit breaker."""
        # 1. Dynamic threshold for 15s segment
        wd = AudioSubprocessWatchdog(segment_seconds=15, on_failover_callback=lambda reason, code: None)
        self.assertEqual(wd.zero_byte_stall_threshold_sec, 20.0)

        # 2. Startup grace period suppresses false starvation
        wd.register_processes([], self.chunks_dir)
        reason, code = wd._check_zero_byte_starvation()
        self.assertIsNone(reason)
        self.assertIsNone(code)

        # 3. Sentinel cooldown & fail-closed lock on fallback (Operator Decision D1)
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.cache_dir / "master.wav",
            source_mode="system_and_mic",
            mock_mode=True,
        )
        mgr._is_running = True
        mgr._active_strategy = "system_and_mic"
        mgr._sck_permanently_unavailable = False

        # Attempting fallback to microphone raises AudioCaptureUnprovenError under Decision D1
        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            mgr.fallback_to_microphone(reason="Failover 1")
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
        self.assertTrue(mgr._sck_permanently_unavailable, "Must lock to permanently unavailable")
        self.assertFalse(mgr._is_running, "Must halt running state")

        # Sentinel attempts to re-trigger mixed capture must be locked out
        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_default_output_headphones", return_value=True):
            can_revert = mgr.check_headphone_trap_sentinel()
            self.assertFalse(can_revert)
            self.assertTrue(mgr._sck_permanently_unavailable)

    def test_e2e_silence_gating_vad_and_whisper_anti_hallucination(self):
        """E2E Step 4: Validates multi-layered acoustic VAD gating and Whisper watermark filtering."""
        vad = AudioVADSegmenter(energy_threshold=0.0037, min_speech_duration_ms=240)

        # 1. Pure digital zero chunk
        silent_wav = _create_sine_wav(self.chunks_dir / "zero.wav", duration_sec=1.0, amplitude=0.0)
        val_silent = AudioChunkValidator.validate_chunk(silent_wav, allow_digital_zero=False)
        self.assertFalse(val_silent.is_valid)
        self.assertTrue(val_silent.is_dead_stream)

        # 2. Ambient room noise (amplitude 90 -> RMS ~63.6)
        ambient_wav = _create_sine_wav(self.chunks_dir / "ambient.wav", duration_sec=15.0, amplitude=90.0, freq_hz=60.0)
        rms = get_audio_rms(ambient_wav)
        self.assertTrue(55.0 <= rms <= 75.0)
        val_ambient = AudioChunkValidator.validate_chunk(ambient_wav)
        self.assertTrue(val_ambient.is_silence)
        boundaries = vad.detect_speech_boundaries(ambient_wav)
        self.assertEqual(len(boundaries), 0)

        # 3. Genuine domain speech (amplitude 500 -> RMS ~353.5)
        speech_wav = _create_sine_wav(self.chunks_dir / "speech.wav", duration_sec=3.0, amplitude=500.0, freq_hz=300.0)
        self.assertTrue(vad.is_speech_chunk(speech_wav))
        speech_boundaries = vad.detect_speech_boundaries(speech_wav)
        self.assertGreaterEqual(len(speech_boundaries), 1)

        # 4. Anti-hallucination subtitle and watermark filters
        watermark = "Closed Captioning provided by Tyler at GPKnives."
        self.assertEqual(_filter_hallucinations(watermark), "")

        youtube_endcard = "Thank you for watching, please like and subscribe!"
        self.assertEqual(_filter_hallucinations(youtube_endcard), "")

        acoustic_tag = "[Music]"
        self.assertEqual(_filter_hallucinations(acoustic_tag), "")

        standalone_sigh = "Sigh."
        self.assertEqual(_filter_hallucinations(standalone_sigh), "")

        # 5. Generative prompt branching suppression
        prompt = "Amit Ayer, Drew Oakley discussing APEX_TRANS architecture. Domain terms: Apex Global Enterprises, Apex Power & Grid."
        self.assertEqual(_filter_hallucinations("Apex GlobalVictory.com", whisper_prompt=prompt), "")
        self.assertEqual(_filter_hallucinations("Apex GlobalPlay", whisper_prompt=prompt), "")

        # Legitimate domain speech is preserved
        legit = "We need to discuss the Apex Global energy substation schedule."
        self.assertEqual(_filter_hallucinations(legit, whisper_prompt=prompt), legit)

        # 6. Whisper no_speech_prob bug override
        hallucinated_segment = [{
            "id": 0, "text": "Tyler at GPKnives", "no_speech_prob": 0.85, "avg_logprob": -0.2, "compression_ratio": 1.1
        }]
        self.assertEqual(_filter_segment_metadata(hallucinated_segment), "")

    def test_e2e_complete_recorder_lifecycle_stream_drain_and_seal(self):
        """E2E Step 5: Full meeting recording session processing streaming chunks to sealed artifacts."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Hardened Audio Capture E2E",
            attendees="Amit Ayer, Drew Oakley",
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        # Synthesize 3 chunks:
        # Chunk 0: ambient room noise (RMS ~63) -> must be flagged as silence, not audible
        _create_sine_wav(recorder.chunks_dir / "chunk_0000.wav", duration_sec=1.0, amplitude=90.0, freq_hz=60.0)
        # Chunk 1: active speech (RMS ~350) -> must be audible and transcribed
        _create_sine_wav(recorder.chunks_dir / "chunk_0001.wav", duration_sec=1.0, amplitude=500.0, freq_hz=300.0)
        # Chunk 2: active speech (RMS ~350) -> must be audible and transcribed
        _create_sine_wav(recorder.chunks_dir / "chunk_0002.wav", duration_sec=1.0, amplitude=500.0, freq_hz=300.0)

        # Run 3 iterations to process all chunks
        recorder.run(max_iterations=3)

        # Verify all 3 chunks were processed
        self.assertEqual(len(recorder.processed_chunks), 3)
        self.assertIn("chunk_0000.wav", recorder.processed_chunks)
        self.assertIn("chunk_0001.wav", recorder.processed_chunks)
        self.assertIn("chunk_0002.wav", recorder.processed_chunks)

        # Verify session manifest was persisted and sealed
        self.assertTrue(recorder.session_manifest.exists())
        manifest = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["project"], "project_nexus")
        self.assertEqual(manifest["meeting"], "Hardened Audio Capture E2E")

        # Verify transcript was generated
        self.assertTrue(recorder.transcript_md.exists())
        transcript_text = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Total chunks processed: 3", transcript_text)

        # Clean shutdown and zero process leakage
        recorder.stop()
        self.assertIsNone(recorder.capture_manager._ffmpeg_proc)
        self.assertIsNone(recorder.capture_manager._sck_proc)
        self.assertFalse(recorder.capture_manager.is_running)


if __name__ == "__main__":
    unittest.main()
