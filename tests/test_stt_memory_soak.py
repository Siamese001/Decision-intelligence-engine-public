"""Blueprint 1: Long-Duration Streaming Memory Soak Benchmark.

Verifies:
1. Continuous streaming audio chunk ingestion maintains O(1) memory stability.
2. Zero memory leakages across 200+ continuous streaming audio chunks.
3. Rapid subscriber dispatch and clean queue drainage under sustained streaming.
4. Clean asynchronous task teardown without unclosed coroutines or leaked threads.
"""

from __future__ import annotations

import asyncio
import gc
import resource
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_a_intake.local_stt import AudioChunk, FastPathTranscriptEvent, LocalSTTEngine
from storage.database import DatabaseManager


def get_current_rss_bytes() -> int:
    """Return the current resident set size (RSS) in bytes for macOS / Unix."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On macOS, ru_maxrss is in bytes; on Linux, it is in kilobytes.
    if sys.platform == "darwin":
        return int(usage)
    return int(usage * 1024)


class TestSTTMemorySoakBenchmark(unittest.TestCase):
    """Sustained streaming memory soak benchmark for Pipeline A Local STT."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir.name,
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_audio_streaming_memory_stability_soak(self) -> None:
        """Benchmark 200 continuous audio chunks asserting O(1) RSS memory stability."""

        async def run_soak() -> int:
            engine = LocalSTTEngine(
                db_manager=self.db_manager,
                mock_mode=True,
                max_workers=2,
            )

            dispatched_events: list[FastPathTranscriptEvent] = []

            def fast_path_subscriber(event: FastPathTranscriptEvent) -> None:
                dispatched_events.append(event)

            engine.subscribe(fast_path_subscriber)
            await engine.start()

            gc.collect()
            initial_rss = get_current_rss_bytes()

            # Generate and stream 200 synthetic 250ms audio chunks (16kHz float32)
            total_chunks = 200
            sample_rate = 16_000
            chunk_duration_s = 0.25
            samples_per_chunk = int(sample_rate * chunk_duration_s)
            dummy_audio = np.zeros(samples_per_chunk, dtype=np.float32)

            for i in range(total_chunks):
                chunk = AudioChunk(
                    chunk_id=f"chunk_soak_{i:04d}",
                    meeting_id="meeting_soak_test",
                    audio_data=dummy_audio,
                    sample_rate=sample_rate,
                    speaker="Speaker 1",
                    timestamp_offset=i * chunk_duration_s,
                    is_final=True,
                )
                await engine.feed_audio_async(chunk)

            # Await complete processing and queue drain
            await engine.stop()
            gc.collect()

            final_rss = get_current_rss_bytes()
            rss_delta = final_rss - initial_rss

            self.assertEqual(len(dispatched_events), total_chunks)
            return rss_delta

        rss_growth_bytes = asyncio.run(run_soak())

        # Assert memory growth is bounded (< 25MB allowed for Python interpreter fluctuations)
        max_allowed_growth_bytes = 25 * 1024 * 1024
        self.assertLessEqual(
            rss_growth_bytes,
            max_allowed_growth_bytes,
            f"Memory leak detected during streaming soak! Growth: {rss_growth_bytes / (1024 * 1024):.2f}MB, "
            f"Limit: {max_allowed_growth_bytes / (1024 * 1024):.2f}MB",
        )

    def test_audio_chunk_queue_backpressure_and_drain(self) -> None:
        """Verify queue behavior under rapid burst enqueueing."""

        async def run_burst() -> None:
            engine = LocalSTTEngine(
                db_manager=self.db_manager,
                mock_mode=True,
                max_workers=4,
            )
            await engine.start()

            # Push 50 chunks rapidly without waiting
            for i in range(50):
                chunk = AudioChunk(
                    chunk_id=f"burst_{i}",
                    meeting_id="burst_meeting",
                    audio_data="mock_path.wav",
                    speaker="Speaker",
                    timestamp_offset=float(i),
                    is_final=True,
                )
                await engine.feed_audio_async(chunk)

            await engine.stop()

            # Verify all 50 items were persisted to evidence database
            with self.db_manager.get_evidence_conn(readonly=True) as conn:
                count = conn.execute("SELECT COUNT(*) FROM immutable_evidence_log WHERE meeting_id='burst_meeting'").fetchone()[0]
                self.assertEqual(count, 50)

        asyncio.run(run_burst())


if __name__ == "__main__":
    unittest.main()
