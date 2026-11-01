#!/usr/bin/env python3
"""
Unit tests for Pipeline A.1 Intake (`pipeline_a_intake/local_stt.py`).
Verifies asynchronous continuous audio chunk ingestion, non-blocking writes to
`immutable_evidence_log` & `durable_work_queue`, and ephemeral broadcast (Law 3 & 4).
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from pipeline_a_intake.local_stt import (
    AudioChunk,
    FastPathTranscriptEvent,
    LocalSTTEngine,
)
from storage.database import DatabaseManager, WorkItemState


class TestFastPathIntake(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Pipeline A fast-path continuous intake and WAL queue."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mgr = DatabaseManager(base_dir=self.temp_dir)
        self.engine = LocalSTTEngine(db_manager=self.mgr, mock_mode=True)

    async def asyncTearDown(self) -> None:
        if self.engine:
            with contextlib.suppress(Exception):
                await self.engine.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_async_audio_chunk_flow(self) -> None:
        """Verify continuous audio chunk feed, ephemeral broadcast, and WAL lease."""
        events_received: list[FastPathTranscriptEvent] = []

        def on_event(event: FastPathTranscriptEvent):
            events_received.append(event)

        self.engine.subscribe(on_event)
        await self.engine.start()

        # Feed two chunks
        chunk1 = AudioChunk(
            chunk_id="chk_001",
            meeting_id="meet_fast_01",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Amit Ayer",
            timestamp_offset=0.0,
            metadata={"mock_text": "We need to ensure zero biometric data leakage."},
        )
        chunk2 = AudioChunk(
            chunk_id="chk_002",
            meeting_id="meet_fast_01",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Lead Engineer",
            timestamp_offset=5.0,
            metadata={"mock_text": "Local metal acceleration handles the STT layer."},
        )

        self.engine.feed_audio(chunk1)
        self.engine.feed_audio(chunk2)

        # Allow worker loop to process
        await asyncio.sleep(0.1)

        # Verify broadcast
        self.assertEqual(len(events_received), 2)
        self.assertEqual(events_received[0].speaker, "Amit Ayer")
        self.assertEqual(events_received[0].text, "We need to ensure zero biometric data leakage.")
        self.assertTrue(events_received[0].is_ephemeral)  # Law 3

        self.assertEqual(events_received[1].speaker, "Lead Engineer")
        self.assertEqual(events_received[1].text, "Local metal acceleration handles the STT layer.")

        # Verify Database WAL writes (Law 4)
        leased = self.mgr.lease_work_batch(batch_size=10, worker_id="test_worker")
        self.assertEqual(len(leased), 2)

        # Check evidence log records
        self.assertEqual(leased[0][1].speaker, "Amit Ayer")
        self.assertEqual(leased[0][1].text, "We need to ensure zero biometric data leakage.")
        self.assertEqual(leased[0][0].state, WorkItemState.PROCESSING)

        self.assertEqual(leased[1][1].speaker, "Lead Engineer")
        self.assertEqual(leased[1][1].text, "Local metal acceleration handles the STT layer.")

    async def test_non_blocking_feed(self) -> None:
        """Verify feeding audio chunks is non-blocking (<50ms for 10 chunks)."""
        await self.engine.start()

        t0 = time.perf_counter()
        for i in range(10):
            self.engine.feed_audio(
                AudioChunk(
                    chunk_id=f"chk_perf_{i}",
                    meeting_id="meet_perf",
                    audio_data=np.zeros(1600, dtype=np.float32),
                    speaker="Speaker",
                    timestamp_offset=float(i * 2),
                    metadata={"mock_text": f"Performance packet {i}"},
                )
            )
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.05)  # Highly non-blocking

        # Stop engine to gracefully drain all items
        await self.engine.stop()

        # Verify all 10 items persisted to SQLite WAL
        leased = self.mgr.lease_work_batch(batch_size=20, worker_id="test_perf")
        self.assertEqual(len(leased), 10)


if __name__ == "__main__":
    unittest.main()
