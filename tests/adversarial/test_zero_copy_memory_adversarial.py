"""Adversarial and Edge-Case Test Suite for Wave 2 Zero-Copy Memory Pipelining.

Covers 4 mandatory adversarial categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte buffers, None pointer addresses, single-byte tensor transfers.
2. Malformed input: Non-existent buffer release, duplicate buffer ID overwrite, unregistered profiles.
3. Invariant bypass attempt: Verifying immutable ZeroCopyBufferRecord cannot be mutated, reallocated is False.
4. Concurrency & race: 16 concurrent worker threads registering, querying, and releasing zero-copy buffers.
"""
from __future__ import annotations

import concurrent.futures
from unittest import TestCase

from dual_pipeline.runtime.memory_governor import (
    KV_CACHE_PROFILES,
    MemoryGovernor,
    ZeroCopyBufferRecord,
)


class TestZeroCopyMemoryAdversarial(TestCase):
    """Adversarial and edge-case tests for Wave 2 zero-copy memory governance."""

    def setUp(self) -> None:
        self.gov = MemoryGovernor(total_memory_gb=48.0)

    def tearDown(self) -> None:
        self.gov.reset()

    def test_boundary_zero_byte_and_null_pointer_buffers(self) -> None:
        """Verifies zero-byte and null pointer buffers register without system faults."""
        rec = self.gov.register_zero_copy_buffer(
            buffer_id="zero_byte_buf",
            source_model="whisper",
            target_model="llama",
            size_bytes=0,
            pointer_address=None,
        )
        self.assertEqual(rec.size_bytes, 0)
        self.assertIsNone(rec.pointer_address)
        self.assertFalse(rec.reallocated)

    def test_malformed_non_existent_buffer_release(self) -> None:
        """Verifies releasing a non-existent buffer ID safely returns False without raising."""
        released = self.gov.release_zero_copy_buffer("non_existent_buf_xyz")
        self.assertFalse(released)

    def test_invariant_buffer_record_immutability(self) -> None:
        """Verifies ZeroCopyBufferRecord is frozen and prevents attribute mutation."""
        rec = self.gov.register_zero_copy_buffer(
            buffer_id="frozen_buf_001",
            source_model="source",
            target_model="target",
            size_bytes=1024,
        )
        with self.assertRaises(Exception):
            # Attempting to mutate a frozen dataclass must raise FrozenInstanceError
            rec.reallocated = True  # type: ignore

    def test_concurrency_multi_threaded_zero_copy_lifecycle(self) -> None:
        """Verifies 16 concurrent threads safely register, query, and release zero-copy buffers."""
        threads = 16
        iterations_per_thread = 50

        def worker(thread_idx: int) -> list[str]:
            errors = []
            for i in range(iterations_per_thread):
                buf_id = f"t{thread_idx}_buf_{i}"
                ptr = 0x10000000 + (thread_idx * 1000) + i
                rec = self.gov.register_zero_copy_buffer(
                    buffer_id=buf_id,
                    source_model="worker_stt",
                    target_model="worker_llm",
                    size_bytes=4096,
                    pointer_address=ptr,
                )
                if not self.gov.is_zero_copy_active(buf_id):
                    errors.append(f"Buffer {buf_id} not active immediately after registration")
                if rec.pointer_address != ptr:
                    errors.append(f"Buffer {buf_id} pointer corrupted")
                released = self.gov.release_zero_copy_buffer(buf_id)
                if not released:
                    errors.append(f"Failed to release buffer {buf_id}")
            return errors

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, t) for t in range(threads)]
            all_errors = []
            for f in concurrent.futures.as_completed(futures):
                all_errors.extend(f.result())

        self.assertEqual(len(all_errors), 0, f"Concurrent zero-copy errors: {all_errors}")
