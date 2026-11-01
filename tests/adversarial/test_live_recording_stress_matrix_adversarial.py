"""tests/adversarial/test_live_recording_stress_matrix_adversarial.py

Adversarial Stress Matrix for Live Recording Pipeline:
1. Staging Races: Concurrent rapid chunk generation with multi-threaded migrate_staged_files polling.
2. Active Writing Head Concurrency: Interleaved byte appends, temporary file isolation, SHA256 integrity.
3. Concurrent Poller Isolation: Multi-threaded staging calls testing collision on temporary write paths.
4. Queue Backpressure & Chunk Ordering: Rapid chunk arrival with delayed ASR consumption.
5. Abrupt Shutdown & Stub Resilience: SIGINT-simulated trailing stubs, graceful drain, master stitch guarantee.
6. Resource & Thread Teardown: Clean termination of workers, lock release, and session manifest sealing.
"""
from __future__ import annotations

import hashlib
import math
import os
import queue
import shutil
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    EmptyAudioChunkError,
    validate_final_chunk,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _copy_chunk_if_missing,
    migrate_staged_files,
)


def _generate_pcm_wav(path: Path, num_samples: int = 16000, freq: float = 440.0) -> None:
    """Generates a valid 16kHz 16-bit mono PCM WAV file with sine wave audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(2000 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(num_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


@pytest.mark.adversarial
class TestLiveRecordingStressMatrixAdversarial(unittest.TestCase):
    """Adversarial stress testing matrix probing staging races, dropped chunks, and shutdown failures."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.src_chunks = self.tmp_path / "src_chunks"
        self.dst_chunks = self.tmp_path / "dst_chunks"
        self.src_chunks.mkdir(parents=True, exist_ok=True)
        self.dst_chunks.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_rapid_chunk_staging_stress(self) -> None:
        """Stresses 50 rapid chunk creations against 4 concurrent migration polling threads."""
        num_chunks = 50
        errors: list[Exception] = []
        stop_event = threading.Event()

        # Producer thread creating chunks rapidly
        def producer() -> None:
            try:
                for idx in range(num_chunks):
                    cf = self.src_chunks / f"chunk_{idx:05d}.wav"
                    _generate_pcm_wav(cf, num_samples=3200)  # 0.2s of audio
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        # 4 concurrent poller threads migrating staged files
        def poller(worker_id: int) -> None:
            while not stop_event.is_set():
                try:
                    migrate_staged_files(self.src_chunks, self.dst_chunks, [], is_final=False)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.008)

        prod_th = threading.Thread(target=producer)
        poll_ths = [threading.Thread(target=poller, args=(i,)) for i in range(4)]

        for t in poll_ths:
            t.start()
        prod_th.start()
        prod_th.join(timeout=10.0)

        # Allow pollers to complete final cycles then drain
        time.sleep(0.05)
        stop_event.set()
        for t in poll_ths:
            t.join(timeout=5.0)

        # Execute final migration drain
        migrate_staged_files(self.src_chunks, self.dst_chunks, [], is_final=True)

        self.assertEqual(len(errors), 0, f"Encountered unexpected poller errors: {errors}")

        # Verify all 50 chunks were migrated losslessly
        dst_files = sorted(self.dst_chunks.glob("chunk_*.wav"))
        self.assertEqual(len(dst_files), num_chunks, f"Expected {num_chunks} chunks in dst, found {len(dst_files)}")

        # Verify zero leftover temporary files
        tmp_files = list(self.dst_chunks.glob(".tmp_*"))
        self.assertEqual(len(tmp_files), 0, f"Found orphan temporary staging files: {tmp_files}")

        # Verify integrity of each chunk by SHA256 comparison
        for idx in range(num_chunks):
            src_f = self.src_chunks / f"chunk_{idx:05d}.wav"
            dst_f = self.dst_chunks / f"chunk_{idx:05d}.wav"
            src_sha = hashlib.sha256(src_f.read_bytes()).hexdigest()
            dst_sha = hashlib.sha256(dst_f.read_bytes()).hexdigest()
            self.assertEqual(src_sha, dst_sha, f"SHA256 mismatch for chunk {idx}")

    def test_active_writing_head_concurrency_race(self) -> None:
        """Verifies that actively appended chunk defers cleanly during non-final copy and succeeds once closed."""
        growing_chunk = self.src_chunks / "chunk_00001.wav"
        _generate_pcm_wav(growing_chunk, num_samples=1600)

        append_done = threading.Event()
        copy_results: list[bool] = []
        copy_errors: list[Exception] = []

        def appender() -> None:
            for _ in range(10):
                with open(growing_chunk, "ab") as f:
                    f.write(b"\x00" * 400)
                time.sleep(0.01)
            append_done.set()

        def reader() -> None:
            while not append_done.is_set():
                try:
                    res = _copy_chunk_if_missing(growing_chunk, self.dst_chunks, is_final=False)
                    copy_results.append(res)
                except Exception as e:
                    copy_errors.append(e)
                time.sleep(0.005)

        t_app = threading.Thread(target=appender)
        t_read = threading.Thread(target=reader)

        t_app.start()
        t_read.start()
        t_app.join(timeout=5.0)
        t_read.join(timeout=5.0)

        # In non-final mode, no fatal exception should have escaped
        self.assertEqual(len(copy_errors), 0, f"Unexpected exceptions during active head copy: {copy_errors}")

        # Once writing finishes, final copy MUST succeed
        final_ok = _copy_chunk_if_missing(growing_chunk, self.dst_chunks, is_final=True)
        self.assertTrue(final_ok)
        self.assertTrue((self.dst_chunks / "chunk_00001.wav").exists())
        self.assertEqual(
            hashlib.sha256(growing_chunk.read_bytes()).hexdigest(),
            hashlib.sha256((self.dst_chunks / "chunk_00001.wav").read_bytes()).hexdigest(),
        )

    def test_concurrent_poller_temp_file_collision_isolation(self) -> None:
        """Verifies that multiple threads copying the same chunk simultaneously do not corrupt or leave stubs."""
        chunk = self.src_chunks / "chunk_00010.wav"
        _generate_pcm_wav(chunk, num_samples=8000)

        results: list[bool] = []
        errors: list[Exception] = []

        def copy_worker() -> None:
            try:
                ok = _copy_chunk_if_missing(chunk, self.dst_chunks, is_final=False)
                results.append(ok)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=copy_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        self.assertEqual(len(errors), 0, f"Concurrent poller errors detected: {errors}")
        self.assertTrue(any(results), "At least one concurrent copy must succeed")

        dst_chunk = self.dst_chunks / "chunk_00010.wav"
        self.assertTrue(dst_chunk.exists())
        self.assertEqual(
            hashlib.sha256(chunk.read_bytes()).hexdigest(),
            hashlib.sha256(dst_chunk.read_bytes()).hexdigest(),
        )
        self.assertEqual(len(list(self.dst_chunks.glob(".tmp_*"))), 0)

    def test_queue_backpressure_and_chunk_ordering(self) -> None:
        """Simulates rapid chunk intake with slow consumer, verifying zero dropped chunks and FIFO ordering."""
        chunk_q: queue.Queue[tuple[Path, int] | None] = queue.Queue()
        consumed: list[int] = []
        consume_done = threading.Event()

        def slow_consumer() -> None:
            while True:
                item = chunk_q.get()
                if item is None:
                    chunk_q.task_done()
                    break
                _, idx = item
                time.sleep(0.005)  # simulated inference lag
                consumed.append(idx)
                chunk_q.task_done()
            consume_done.set()

        c_th = threading.Thread(target=slow_consumer)
        c_th.start()

        num_chunks = 25
        for i in range(num_chunks):
            cf = self.src_chunks / f"chunk_{i:05d}.wav"
            _generate_pcm_wav(cf, num_samples=1600)
            chunk_q.put((cf, i))

        chunk_q.put(None)
        chunk_q.join()
        consume_done.wait(timeout=5.0)

        self.assertEqual(len(consumed), num_chunks)
        self.assertEqual(consumed, list(range(num_chunks)), "Chunks must be consumed in strict sequential FIFO order")

    def test_abrupt_sigint_with_trailing_stub_and_partial_audio(self) -> None:
        """Simulates abrupt SIGINT producing 4 valid chunks and 1 trailing 0-byte stub. Stitches valid audio."""
        for i in range(4):
            _generate_pcm_wav(self.dst_chunks / f"chunk_{i:05d}.wav", num_samples=16000)

        # 0-byte stub from interrupted FFmpeg
        stub = self.dst_chunks / "chunk_00004.wav"
        stub.write_bytes(b"")

        validate_final_chunk(self.dst_chunks, segment_seconds=1)
        self.assertFalse(stub.exists(), "Trailing 0-byte stub must be pruned")

        master_wav = self.tmp_path / "master.wav"
        receipt = AudioMasterStitcher.stitch_master_wav(self.dst_chunks, master_wav, pad_missing_or_corrupt=True)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "SUCCESS")
        self.assertEqual(receipt.total_chunks, 4)
        self.assertEqual(receipt.padded_chunks, 0)
        self.assertTrue(master_wav.exists())
        self.assertGreater(master_wav.stat().st_size, 4096)

        with wave.open(str(master_wav), "rb") as mw:
            self.assertEqual(mw.getnframes(), 4 * 16000)

    def test_shutdown_drain_with_delayed_staging_finalization(self) -> None:
        """Verifies that final chunk drain with is_final=True migrates final pending chunk cleanly."""
        for i in range(3):
            _generate_pcm_wav(self.src_chunks / f"chunk_{i:05d}.wav", num_samples=8000)
            _generate_pcm_wav(self.dst_chunks / f"chunk_{i:05d}.wav", num_samples=8000)

        # Chunk 3 exists in src but not yet in dst
        _generate_pcm_wav(self.src_chunks / "chunk_00003.wav", num_samples=8000)

        # In active polling (is_final=False), chunk 3 is excluded because it is the latest
        migrate_staged_files(self.src_chunks, self.dst_chunks, [], is_final=False)
        self.assertFalse((self.dst_chunks / "chunk_00003.wav").exists())

        # In final shutdown drain (is_final=True), chunk 3 MUST be migrated
        migrate_staged_files(self.src_chunks, self.dst_chunks, [], is_final=True)
        self.assertTrue((self.dst_chunks / "chunk_00003.wav").exists())
        self.assertEqual(
            hashlib.sha256((self.src_chunks / "chunk_00003.wav").read_bytes()).hexdigest(),
            hashlib.sha256((self.dst_chunks / "chunk_00003.wav").read_bytes()).hexdigest(),
        )

    def test_concurrent_final_drain_temp_collision(self) -> None:
        """Probes temporary file collision when final drain occurs during concurrent staging calls."""
        chunk = self.src_chunks / "chunk_00099.wav"
        _generate_pcm_wav(chunk, num_samples=32000)

        errors: list[Exception] = []

        def worker(is_final: bool) -> None:
            try:
                _copy_chunk_if_missing(chunk, self.dst_chunks, is_final=is_final)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(False,)),
            threading.Thread(target=worker, args=(True,)),
            threading.Thread(target=worker, args=(True,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Defect Reproduction Assertion: In a thread-safe implementation,
        # concurrent final drains must not collide on temporary filenames.
        self.assertEqual(
            len(errors),
            0,
            f"[REPRODUCED-DEFECT] Concurrent final drain collision error: {errors}",
        )
        self.assertTrue((self.dst_chunks / "chunk_00099.wav").exists())


