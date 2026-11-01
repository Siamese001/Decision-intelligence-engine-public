"""
tests/test_recording_source_handoff.py

Wave 3: Verifies single-capture authority, source handoff, sample continuity,
and proof verification against invalid/zeroed streams.
Validates:
1. Multi-source spooler spawn preserves requested source_mode.
2. Spooler adoption preserves process identity without process restart.
3. Adopted spooler stop() does not kill active capture processes.
4. Sample continuity across handoff (sequential chunk indices, no dropped chunks).
5. Unadopted spooler drains properly and allows fresh capture start.
6. Proof verifier rejects header-only WAV (PCM_BYTE_RATE_DEFICIT).
7. Proof verifier rejects digital zero mono mic (MIC_DIGITAL_ZERO).
8. Proof verifier rejects digital zero stereo mic channel even when SCK is active.
9. Proof verifier rejects stalled continuous growth (AUDIO_GROWTH_STALLED).
10. Proof verifier accepts valid mixed acoustic audio.
11. Spooler discard cleans only owned resources and preserves adopted directories.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
)
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    generate_synthetic_speech_chunk,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import (
    InstantAudioSpooler,
    SpoolStatus,
)


class TestRecordingSourceHandoff(unittest.TestCase):
    """Verifies single-capture ownership, handoff continuity, and proof verification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.output_dir = self.test_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.spool_dir = self.test_root / "spool"
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_multi_source_spooler_spawn_preserves_source_mode(self) -> None:
        """InstantAudioSpooler factory and constructor must preserve requested source_mode."""
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="test_spool_mixed",
            source_mode="mixed",
        )
        try:
            self.assertEqual(spooler.source_mode, "mixed")
            self.assertTrue(spooler.is_running)
            self.assertEqual(spooler.status, SpoolStatus.ACTIVE_SPOOLING)
        finally:
            spooler.stop()

    def test_spooler_adoption_preserves_process_identity(self) -> None:
        """Adopting a running spooler must preserve process identity and avoid process restart."""
        spooler = InstantAudioSpooler(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=False,
            session_id="test_adopt_identity",
            source_mode="mixed",
        )
        mock_ffmpeg = MagicMock()
        mock_ffmpeg.pid = 42001
        mock_ffmpeg.poll.return_value = None

        mock_sck = MagicMock()
        mock_sck.pid = 42002
        mock_sck.poll.return_value = None

        spooler._ffmpeg_proc = mock_ffmpeg
        spooler._sck_proc = mock_sck
        spooler._is_running = True

        capture_mgr = LiveAudioCaptureManager(
            chunks_dir=self.test_root / "target_chunks",
            master_wav=self.test_root / "master.wav",
            source_mode="mixed",
            mock_mode=False,
        )

        capture_mgr.adopt_spooler(spooler)

        # Assert identical process handles and PIDs
        self.assertIs(capture_mgr._ffmpeg_proc, mock_ffmpeg)
        self.assertIs(capture_mgr._sck_proc, mock_sck)
        self.assertEqual(capture_mgr._ffmpeg_proc.pid, 42001)
        self.assertEqual(capture_mgr._sck_proc.pid, 42002)
        self.assertTrue(spooler.is_adopted)

        # Stopping the adopted spooler must NOT terminate the adopted processes
        spooler.stop()
        mock_ffmpeg.send_signal.assert_not_called()
        mock_ffmpeg.terminate.assert_not_called()
        mock_sck.send_signal.assert_not_called()
        mock_sck.terminate.assert_not_called()

    def test_sample_continuity_across_handoff(self) -> None:
        """Handoff must transfer spooled chunks gaplessly and update recorder start_chunk_idx."""
        spooler = InstantAudioSpooler(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="test_sample_continuity",
        )
        spooler.spool_dir.mkdir(parents=True, exist_ok=True)

        # Generate 2 synthetic chunks in spool dir
        chunk_0 = spooler.spool_dir / "chunk_00000.wav"
        chunk_1 = spooler.spool_dir / "chunk_00001.wav"
        generate_synthetic_speech_chunk(chunk_0, duration_sec=5.0)
        generate_synthetic_speech_chunk(chunk_1, duration_sec=5.0)

        target_chunks_dir = self.test_root / "target_chunks"
        receipt = spooler.handshake_and_splice(target_chunks_dir=target_chunks_dir)

        self.assertEqual(receipt.migrated_chunk_count, 2)
        self.assertEqual(receipt.migrated_chunk_indices, [0, 1])
        self.assertTrue(receipt.is_gapless)
        self.assertTrue((target_chunks_dir / "chunk_00000.wav").exists())
        self.assertTrue((target_chunks_dir / "chunk_00001.wav").exists())

        # When LiveMeetingRecorder is initialized with this spooler, it starts at chunk 2
        recorder = LiveMeetingRecorder(
            meeting_name="Test Continuity Session",
            output_dir=self.output_dir,
            spooler=spooler,
            mock_mode=True,
        )
        self.assertEqual(recorder.start_chunk_idx, 2)

    def test_unadopted_spooler_drain_and_fresh_start(self) -> None:
        """An unadopted or stopped spooler drains its chunks and capture manager starts fresh."""
        spooler = InstantAudioSpooler(
            spool_dir=self.spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="test_unadopted_drain",
        )
        spooler.spool_dir.mkdir(parents=True, exist_ok=True)
        chunk_0 = spooler.spool_dir / "chunk_00000.wav"
        generate_synthetic_speech_chunk(chunk_0, duration_sec=5.0)
        # Mark stopped so recorder will not adopt running authority
        spooler._is_running = False

        recorder = LiveMeetingRecorder(
            meeting_name="Test Drain Session",
            output_dir=self.output_dir,
            spooler=spooler,
            mock_mode=True,
        )
        recorder.start_ffmpeg()
        self.assertTrue(recorder.running)
        self.assertIsNotNone(recorder.start_receipt)
        self.assertEqual(recorder.start_chunk_idx, 1)

    def test_proof_verifier_rejects_header_only_wav(self) -> None:
        """Proof verifier must fail closed with PCM_BYTE_RATE_DEFICIT on header-only WAV."""
        header_only_wav = self.test_root / "header_only.wav"
        # Standard 44-byte RIFF WAV header with 0 data bytes
        riff_header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36, b"WAVE", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16, b"data", 0,
        )
        header_only_wav.write_bytes(riff_header)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(
                pcm_bytes=riff_header,
                is_stereo=False,
                mock_mode=False,
            )
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

    def test_proof_verifier_rejects_digital_zero_mic(self) -> None:
        """Proof verifier must fail closed with MIC_DIGITAL_ZERO when microphone samples are all zero."""
        # 1 second of 16kHz s16le silence (16000 samples of 0x0000)
        silence_bytes = b"\x00\x00" * 16000

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(
                pcm_bytes=silence_bytes,
                is_stereo=False,
                mock_mode=False,
            )
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.MIC_DIGITAL_ZERO)
        self.assertIn("P2_MIC_NON_DIGITAL_ZERO", ctx.exception.stage)

    def test_proof_verifier_rejects_digital_zero_mic_in_stereo(self) -> None:
        """In stereo mode (SCK ch0, Mic ch1), active SCK cannot mask a digital-zero microphone."""
        n_samples = 16000
        # SCK has active tone (sine wave)
        sck_samples = [int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n_samples)]
        # Mic channel is all zeros (TCC silent block)
        mic_samples = [0] * n_samples

        # Interleave channels: [sck0, mic0, sck1, mic1, ...]
        interleaved = []
        for s, m in zip(sck_samples, mic_samples):
            interleaved.extend([s, m])

        stereo_bytes = struct.pack(f"<{len(interleaved)}h", *interleaved)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(
                pcm_bytes=stereo_bytes,
                is_stereo=True,
                mock_mode=False,
            )
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.MIC_DIGITAL_ZERO)

    def test_proof_verifier_rejects_stalled_growth(self) -> None:
        """Proof verifier must detect mid-session stalls and raise AUDIO_GROWTH_STALLED."""
        master_wav = self.test_root / "stalled_master.wav"
        master_wav.write_bytes(b"DATA" * 100)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_continuous_growth(
                master_wav=master_wav,
                last_size=400,
                last_growth_time=time.time() - 10.0,
                stall_threshold_sec=3.0,
                mock_mode=False,
            )
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)

    def test_proof_verifier_accepts_valid_mixed_audio(self) -> None:
        """Proof verifier succeeds and records evidence on valid acoustic audio."""
        n_samples = 16000
        # Valid acoustic signal (sine wave with variance)
        active_samples = [int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n_samples)]
        pcm_bytes = struct.pack(f"<{n_samples}h", *active_samples)

        res = AudioCaptureProofVerifier.verify_mic_non_digital_zero(
            pcm_bytes=pcm_bytes,
            is_stereo=False,
            mock_mode=False,
        )
        self.assertTrue(res["proven"])
        self.assertGreater(res["mic_rms"], 1000.0)
        self.assertGreater(res["peak_to_peak"], 5000)

    def test_spooler_discard_cleans_only_owned_directory(self) -> None:
        """Spooler discard removes only its own spool directory and preserves others."""
        dir_a = self.test_root / "spool_a"
        dir_b = self.test_root / "spool_b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        spooler_a = InstantAudioSpooler(spool_dir=dir_a, session_id="session_a", mock_mode=True)
        spooler_b = InstantAudioSpooler(spool_dir=dir_b, session_id="session_b", mock_mode=True)

        spooler_a.discard()
        self.assertFalse(dir_a.exists())
        self.assertTrue(dir_b.exists())

        # If adopted, discard must NOT delete directory
        dir_c = self.test_root / "spool_c"
        dir_c.mkdir(parents=True, exist_ok=True)
        spooler_c = InstantAudioSpooler(spool_dir=dir_c, session_id="session_c", mock_mode=True)
        spooler_c.mark_adopted()
        spooler_c.discard()
        self.assertTrue(dir_c.exists())
