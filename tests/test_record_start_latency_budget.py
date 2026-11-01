"""
tests/test_record_start_latency_budget.py

Verification of startup latency budgets for Wave 4 Fast-Start Architecture:
1. Diarization preflight latency <= 50ms.
2. Fast bootstrap eager spooling <= 500ms.
3. AudioStrategyProfileCache hit latency <= 10ms.
4. LiveMeetingRecorder initialization latency <= 1000ms.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.audio_ingestion_core.diarization_engine import (
    check_diarization_preflight,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioStrategyProfileCache,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler


class TestRecordStartLatencyBudget(unittest.TestCase):
    def test_diarization_preflight_under_50ms(self):
        """Diarization preflight must complete in <= 50ms without loading heavy weights."""
        t0 = time.perf_counter()
        ok, reason = check_diarization_preflight()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(
            elapsed_ms,
            50.0,
            f"Diarization preflight took {elapsed_ms:.2f}ms (> 50ms budget)",
        )

    def test_instant_spooler_eager_startup_under_500ms(self):
        """Instant audio spooler must start eager capture in <= 500ms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spooler = InstantAudioSpooler(
                spool_dir=Path(tmpdir),
                segment_seconds=5,
                mock_mode=True,
            )
            t0 = time.perf_counter()
            spooler.start_eager()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            try:
                self.assertTrue(spooler.is_running)
                self.assertLess(
                    elapsed_ms,
                    500.0,
                    f"Eager spooling took {elapsed_ms:.2f}ms (> 500ms budget)",
                )
            finally:
                spooler.discard()

    def test_strategy_profile_cache_hit_under_10ms(self):
        """AudioStrategyProfileCache lookup must resolve in <= 10ms."""
        fp = AudioStrategyProfileCache.get_topology_fingerprint("mixed", ":0")
        AudioStrategyProfileCache.store(fp, "mixed_sck", None, ":0", False, False)

        t0 = time.perf_counter()
        cached = AudioStrategyProfileCache.lookup(fp)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertIsNotNone(cached)
        self.assertLess(
            elapsed_ms,
            10.0,
            f"Cache lookup took {elapsed_ms:.2f}ms (> 10ms budget)",
        )

    def test_recorder_mock_init_under_1000ms(self):
        """LiveMeetingRecorder init in mock mode must finish within 1000ms."""
        t0 = time.perf_counter()
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Budget Test Meeting",
            mock_mode=True,
            isolated_process=False,
            enable_ws=False,
            enable_copilot=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            self.assertLess(
                elapsed_ms,
                1000.0,
                f"Recorder init took {elapsed_ms:.2f}ms (> 1000ms budget)",
            )
        finally:
            recorder._early_abort_cleanup()


if __name__ == "__main__":
    unittest.main()
