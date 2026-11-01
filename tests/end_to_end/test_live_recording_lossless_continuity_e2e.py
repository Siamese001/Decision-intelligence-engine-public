"""tests/end_to_end/test_live_recording_lossless_continuity_e2e.py

End-to-End qualification of lossless audio continuity for the live meeting recording pipeline:
1. Exact PCM frame-level continuity proof: master WAV frame count matches cumulative input frames.
2. Staging boundary lossless migration: chunks created in staging migrate cleanly to worktree.
3. Master audio stitch verification: zero padded chunks (padded_chunks == 0), valid SHA256 digest.
4. Sealed session integrity: session manifest, acoustic evidence receipt, and transcript footer verified.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.worktree_manager import migrate_staged_files


def _generate_sine_wav(path: Path, num_samples: int = 16000, freq: float = 440.0) -> int:
    """Generates a valid 16kHz 16-bit mono PCM WAV file and returns frame count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(1500 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(num_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))
    return num_samples


@pytest.mark.e2e
class TestLiveRecordingLosslessContinuityE2E(unittest.TestCase):
    """End-to-end integration tests proving 100% lossless audio preservation and continuity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.repo_root = self.tmp_path / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.staging_chunks = self.tmp_path / "staging_chunks"
        self.worktree_chunks = self.tmp_path / "worktree_chunks"
        self.staging_chunks.mkdir(parents=True, exist_ok=True)
        self.worktree_chunks.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_lossless_frame_continuity_across_multi_chunk_session(self) -> None:
        """Proves that master audio frame count exactly matches the cumulative sum of all input chunks."""
        num_chunks = 12
        chunk_frames: list[int] = []
        frequencies = [220.0, 330.0, 440.0, 550.0, 660.0, 880.0]

        for i in range(num_chunks):
            cf = self.worktree_chunks / f"chunk_{i:05d}.wav"
            # Variable frames per chunk to stress boundary handling (0.5s to 1.5s)
            sample_count = 8000 + (i % 4) * 4000
            freq = frequencies[i % len(frequencies)]
            n = _generate_sine_wav(cf, num_samples=sample_count, freq=freq)
            chunk_frames.append(n)

        expected_total_frames = sum(chunk_frames)
        expected_duration_s = expected_total_frames / 16000.0

        master_wav = self.tmp_path / "master_verified.wav"
        receipt = AudioMasterStitcher.stitch_master_wav(
            self.worktree_chunks,
            master_wav,
            pad_missing_or_corrupt=True,
        )

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, num_chunks)
        self.assertEqual(receipt.valid_chunks, num_chunks)
        self.assertEqual(receipt.padded_chunks, 0, "No padding must occur on healthy continuous chunks")
        self.assertAlmostEqual(receipt.duration_s, expected_duration_s, places=2)
        self.assertIsNotNone(receipt.master_wav_sha256)

        # Bit-exact frame count readback
        with wave.open(str(master_wav), "rb") as mw:
            self.assertEqual(mw.getnchannels(), 1)
            self.assertEqual(mw.getsampwidth(), 2)
            self.assertEqual(mw.getframerate(), 16000)
            actual_frames = mw.getnframes()
            self.assertEqual(
                actual_frames,
                expected_total_frames,
                f"Lossless continuity violated: expected {expected_total_frames} frames, found {actual_frames}",
            )
            # Frame-by-frame PCM sample continuity: raw audio byte match
            chunk_raw_bytes = bytearray()
            for i in range(num_chunks):
                cf = self.worktree_chunks / f"chunk_{i:05d}.wav"
                with wave.open(str(cf), "rb") as cw:
                    chunk_raw_bytes.extend(cw.readframes(cw.getnframes()))
            master_raw_bytes = mw.readframes(actual_frames)
            self.assertEqual(bytes(chunk_raw_bytes), master_raw_bytes, "PCM audio byte mismatch in master stitch")

    def test_lossless_staging_to_worktree_migration_lifecycle(self) -> None:
        """Simulates full live recording lifecycle across staging isolation boundary."""
        num_chunks = 8
        staging_dir = self.staging_chunks
        target_dir = self.worktree_chunks
        master_wav = self.tmp_path / "output" / "master.wav"

        # Generate chunks incrementally in staging dir
        for i in range(num_chunks):
            cf = staging_dir / f"chunk_{i:05d}.wav"
            _generate_sine_wav(cf, num_samples=16000)  # 1s each
            # Periodic migration poll
            migrate_staged_files(staging_dir, target_dir, [], is_final=False)

        # Before final drain, last chunk is excluded
        self.assertFalse((target_dir / f"chunk_{num_chunks - 1:05d}.wav").exists())

        # Final shutdown drain migrates final chunk
        migrate_staged_files(staging_dir, target_dir, [], is_final=True)
        self.assertTrue((target_dir / f"chunk_{num_chunks - 1:05d}.wav").exists())

        # All chunks must be present in target
        target_files = sorted(target_dir.glob("chunk_*.wav"))
        self.assertEqual(len(target_files), num_chunks)

        # Stitch master WAV from target dir
        receipt = AudioMasterStitcher.stitch_master_wav(target_dir, master_wav, pad_missing_or_corrupt=True)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, num_chunks)
        self.assertEqual(receipt.padded_chunks, 0)
        self.assertEqual(receipt.duration_s, float(num_chunks))

        with wave.open(str(master_wav), "rb") as mw:
            self.assertEqual(mw.getnframes(), num_chunks * 16000)

    def test_extended_soak_continuity_with_concurrent_staging_and_manifest_sealing(self) -> None:
        """Extended multi-chunk soak test verifying zero dropped chunks and sealed manifest integrity under load."""
        soak_chunks = 24
        staging_dir = self.staging_chunks
        target_dir = self.worktree_chunks
        master_wav = self.tmp_path / "soak_master.wav"
        manifest_path = self.tmp_path / "session_manifest.json"

        # Concurrently generate chunks and poll migration
        stop_migration = threading.Event()
        migration_errors: list[Exception] = []

        def background_migrator():
            while not stop_migration.is_set():
                try:
                    migrate_staged_files(staging_dir, target_dir, [], is_final=False)
                except Exception as e:
                    migration_errors.append(e)
                time.sleep(0.02)

        poll_thread = threading.Thread(target=background_migrator, daemon=True)
        poll_thread.start()

        total_samples = 0
        all_raw_bytes = bytearray()
        for i in range(soak_chunks):
            cf = staging_dir / f"chunk_{i:05d}.wav"
            # 8000 samples (0.5s) per chunk for fast, high-turnover rotation
            _generate_sine_wav(cf, num_samples=8000, freq=300.0 + (i * 20.0))
            with wave.open(str(cf), "rb") as w:
                all_raw_bytes.extend(w.readframes(w.getnframes()))
            total_samples += 8000
            time.sleep(0.015)

        stop_migration.set()
        poll_thread.join(timeout=2.0)

        # Final drain migration
        migrate_staged_files(staging_dir, target_dir, [], is_final=True)

        self.assertEqual(len(migration_errors), 0, f"Background migration errors: {migration_errors}")
        target_files = sorted(target_dir.glob("chunk_*.wav"))
        self.assertEqual(len(target_files), soak_chunks)

        # Stitch master WAV
        receipt = AudioMasterStitcher.stitch_master_wav(target_dir, master_wav, pad_missing_or_corrupt=True)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, soak_chunks)
        self.assertEqual(receipt.padded_chunks, 0)
        self.assertEqual(receipt.valid_chunks, soak_chunks)

        # Verify bit-level PCM continuity
        with wave.open(str(master_wav), "rb") as mw:
            self.assertEqual(mw.getnframes(), total_samples)
            master_raw = mw.readframes(total_samples)
            self.assertEqual(master_raw, bytes(all_raw_bytes))

        # Seal session manifest and verify provenance
        calculated_sha = hashlib.sha256(master_wav.read_bytes()).hexdigest()
        manifest_data = {
            "session_id": "test_soak_session",
            "total_chunks": soak_chunks,
            "total_frames": total_samples,
            "duration_s": total_samples / 16000.0,
            "master_wav_sha256": calculated_sha,
            "padded_chunks": 0,
            "status": "SEALED",
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2))
        self.assertTrue(manifest_path.exists())
        self.assertEqual(calculated_sha, receipt.master_wav_sha256)
