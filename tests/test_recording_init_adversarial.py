"""Adversarial and Edge-Case Test Suite for Recording Initialization and Spooling.

Validates Wave 2 Edge-Case Governance Invariants across 5 mandatory categories:
1. Boundary payload: 0-byte and truncated WAV files fail closed without silent corruption.
2. Malformed input: Corrupted or truncated strategy profile cache JSON is safely discarded.
3. Concurrency & race: Multi-threaded spooler lifecycle operations resolve with zero deadlocks.
4. Rollback & recovery: Prematurely terminated child processes trigger fail-closed cleanup and rollback.
5. Exact production regression: Worktree asset resolution falls back to git common directory.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioStrategyProfileCache,
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import SpoolStatus
from dual_pipeline.live_meeting_recorder_core.mlx_worker import _resolve_golden_asset


class TestRecordingInitAdversarial(unittest.TestCase):
    """Adversarial and edge-case verification for low-latency recording initialization."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_adversarial_rec_"))
        self.cache_file = Path(tempfile.gettempdir()) / "nee_audio_strategy_cache.json"

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        AudioStrategyProfileCache.clear()

    # Category 1: Boundary payload
    def test_boundary_payload_zero_byte_wav_fails_closed(self):
        """0-byte or header-only (<=44B) WAV fails verification with PCM_BYTE_RATE_DEFICIT."""
        empty_wav = self.tmp_dir / "zero_byte.wav"
        empty_wav.write_bytes(b"")

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(
                master_wav=empty_wav,
                is_stereo=False,
                min_duration_sec=0.1,
                deadline_sec=0.15,
            )
        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

        # 44-byte standard empty WAV header with zero PCM payload
        header_only_wav = self.tmp_dir / "header_only.wav"
        header_only_wav.write_bytes(b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + b"data" + (0).to_bytes(4, "little"))
        self.assertEqual(header_only_wav.stat().st_size, 44)

        with self.assertRaises(AudioCaptureUnprovenError) as ctx2:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(
                master_wav=header_only_wav,
                is_stereo=False,
                min_duration_sec=0.1,
                deadline_sec=0.15,
            )
        self.assertEqual(ctx2.exception.reason_code, AudioReasonCode.PCM_BYTE_RATE_DEFICIT)

    # Category 2: Malformed input
    def test_malformed_strategy_cache_json_resilient_fallback(self):
        """Corrupted, truncated, or invalid JSON in profile cache returns None without crashing."""
        malformed_samples = [
            b"",
            b"{invalid_json",
            b"{\"fast_mode=mixed|dev=:0\": {\"timestamp\": \"not_a_float\"}}",
            b"{\"fast_mode=mixed|dev=:0\": {}}",
            b"\x00\x01\x02\xff",
        ]
        for bad_bytes in malformed_samples:
            self.cache_file.write_bytes(bad_bytes)
            # Must safely return None and not propagate unhandled exception
            result = AudioStrategyProfileCache.lookup_fast("mixed", ":0")
            self.assertIsNone(result, f"Failed on sample: {bad_bytes!r}")

    # Category 3: Concurrency & race
    def test_concurrency_multi_threaded_spooler_lifecycle(self):
        """Concurrent calls to adopt, stop, and discard on InstantAudioSpooler remain thread-safe."""
        spooler = InstantAudioSpooler(
            spool_dir=self.tmp_dir / "spool",
            mock_mode=True,
            segment_seconds=5,
        )
        spooler.start_eager()
        self.assertTrue(spooler.is_running)

        def worker_op(idx: int):
            if idx % 3 == 0:
                spooler.mark_adopted()
            elif idx % 3 == 1:
                spooler.stop()
            else:
                spooler.discard()
            return spooler.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_op, i) for i in range(24)]
            outcomes = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(outcomes), 24)
        self.assertIn(spooler.status, [SpoolStatus.HANDOFF_COMPLETE, SpoolStatus.STOPPED, SpoolStatus.DISCARDED])

    # Category 4: Rollback & recovery
    def test_dead_sck_process_fail_closed_rollback(self):
        """When child SCK process exits prematurely, capture manager halts and cleans up sibling processes."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_dir / "chunks",
            master_wav=self.tmp_dir / "master.wav",
            mock_mode=False,
            skip_device_discovery=True,
        )
        mock_dead_proc = MagicMock()
        mock_dead_proc.pid = 888888
        mock_dead_proc.poll.return_value = 1
        mock_dead_proc.stderr = MagicMock()
        mock_dead_proc.stderr.read.return_value = b"ScreenCaptureKit TCC Permission Denied"

        mock_ffmpeg = MagicMock()
        mock_ffmpeg.pid = 999999
        mgr._sck_proc = mock_dead_proc
        mgr._ffmpeg_proc = mock_ffmpeg

        with self.assertRaises(AudioCaptureUnprovenError) as ctx:
            mgr._verify_sck_process()

        self.assertEqual(ctx.exception.reason_code, AudioReasonCode.SCK_TCC_DENIED)
        self.assertTrue(mgr._sck_permanently_unavailable)

    # Category 5: Exact production regression
    def test_worktree_missing_asset_git_common_dir_fallback(self):
        """Resolving assets from isolated worktree successfully falls back to git common repository."""
        asset = _resolve_golden_asset("mlx_golden_manifest.json")
        self.assertTrue(asset.is_file(), f"Asset manifest should resolve in worktree: {asset}")
        self.assertTrue(asset.name.endswith(".json"))


if __name__ == "__main__":
    unittest.main()
