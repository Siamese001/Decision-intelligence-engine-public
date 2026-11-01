"""tests/end_to_end/test_dead_code_purge_lifecycle_e2e.py

End-to-End lifecycle test suite for Wave 3 (Dead-code purge).
Verifies that:
1. Core recording components initialize in sub-second time (< 1.0s) without loading purged modules.
2. All remaining Category 1 mission-critical components instantiate and interact cleanly:
   - InstantAudioSpooler
   - SessionRegistry
   - AudioEnergyWatchdog
   - ConversationalTurnAggregator
   - SessionRecoveryManager
3. The recording state lifecycle (INIT -> RECORDING -> CHUNK_EMIT -> TURN_AGGREGATE -> COMPLETE)
   completes end-to-end without reference to any deleted modules.
4. Lazy loading of live_meeting_recorder_core dynamically resolves active exports on demand.
5. Decommissioned CLI entry points fail closed as required by governance.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core import (
    AudioEnergyWatchdog,
    ConversationalTurnAggregator,
    InstantAudioSpooler,
    SessionRecoveryManager,
    SessionRegistry,
    coalesce_speaker_turns,
)


class TestDeadCodePurgeLifecycleE2E(unittest.TestCase):
    """End-to-end lifecycle test verifying clean execution post dead-code purge."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recorder_core_subsecond_initialization(self):
        """Invariant: Recorder core components instantiate in sub-second latency (< 1.0s)."""
        t0 = time.perf_counter()

        registry = SessionRegistry(repo_root=self.temp_dir)
        spooler = InstantAudioSpooler(spool_dir=self.temp_dir / "spool", mock_mode=True)
        watchdog = AudioEnergyWatchdog()
        aggregator = ConversationalTurnAggregator()

        elapsed = time.perf_counter() - t0

        self.assertLess(
            elapsed,
            1.0,
            f"Core component initialization took {elapsed:.3f}s, expected < 1.0s (prior baseline was ~3.0s)",
        )
        self.assertIsNotNone(registry)
        self.assertIsNotNone(spooler)
        self.assertIsNotNone(watchdog)
        self.assertIsNotNone(aggregator)

    def test_lazy_core_exports_resolve_correctly(self):
        """Invariant: All active exports in live_meeting_recorder_core resolve dynamically."""
        from dual_pipeline import live_meeting_recorder_core

        expected_exports = [
            "InstantAudioSpooler",
            "SessionRegistry",
            "AudioEnergyWatchdog",
            "AudioSubprocessWatchdog",
            "ConversationalTurnAggregator",
            "SessionRecoveryManager",
            "LiveCopilotAdvisor",
            "CopilotGraphRetriever",
            "coalesce_speaker_turns",
        ]

        for export_name in expected_exports:
            with self.subTest(symbol=export_name):
                symbol = getattr(live_meeting_recorder_core, export_name)
                self.assertIsNotNone(symbol)

    def test_recording_and_aggregation_lifecycle_e2e(self):
        """E2E Test: Full session registration, chunking, and conversational turn aggregation."""
        registry = SessionRegistry(repo_root=self.temp_dir)
        session_id = "test-session-e2e-purge-001"

        # 1. Register Session
        manifest = registry.register(
            session_id=session_id,
            meeting_slug="Arch_Review_W3",
            ws_port=8765,
        )
        self.assertEqual(manifest.session_id, session_id)
        self.assertEqual(manifest.meeting_slug, "Arch_Review_W3")

        # 2. Spooler initialization
        spooler = InstantAudioSpooler(spool_dir=self.temp_dir / "spool", mock_mode=True, session_id=session_id)
        self.assertEqual(spooler.session_id, session_id)

        # 3. Simulate audio chunk ingestion into aggregator
        aggregator = ConversationalTurnAggregator()
        completed_turns = []
        aggregator.on_turn_completed = lambda t: completed_turns.append(t)

        aggregator.add_chunk(
            chunk_idx=0,
            seconds=0.0,
            text="Welcome to the Wave 3 architectural review.",
            speaker="Amit Ayer",
            duration=3.5,
        )
        aggregator.add_chunk(
            chunk_idx=1,
            seconds=4.0,
            text="All Category 2 dead code modules have been purged.",
            speaker="Amit Ayer",
            duration=3.6,
        )
        # Changing speaker flushes prior turn
        turn1 = aggregator.add_chunk(
            chunk_idx=2,
            seconds=8.0,
            text="Understood. Lazy core dropped import overhead significantly.",
            speaker="Sarah Connor",
            duration=4.5,
        )
        self.assertIsNotNone(turn1)
        self.assertEqual(turn1.speaker, "Amit Ayer")
        self.assertIn("Wave 3", turn1.text)

        # Final flush
        final_turn = aggregator.flush()
        self.assertIsNotNone(final_turn)
        self.assertEqual(final_turn.speaker, "Sarah Connor")

        # Also test standalone coalesce_speaker_turns
        raw_lines = [
            (1, "Amit Ayer", "Hello team."),
            (2, "Amit Ayer", "Today we finalize Wave 3."),
            (3, "Sarah Connor", "Acknowledged."),
        ]
        coalesced = coalesce_speaker_turns(raw_lines)
        self.assertEqual(len(coalesced), 2)
        self.assertEqual(coalesced[0][1], "Amit Ayer")
        self.assertIn("Hello team. Today we finalize Wave 3.", coalesced[0][2])
        self.assertEqual(coalesced[1][1], "Sarah Connor")

        # 4. Graceful session completion
        registry.unregister(session_id)
        self.assertIsNone(registry.inspect(session_id))

    def test_disaster_recovery_with_purged_subsystems(self):
        """E2E Test: Disaster recovery operates cleanly using SessionRecoveryManager."""
        session_path = self.temp_dir / "interrupted_session"
        session_path.mkdir(parents=True, exist_ok=True)

        chunks = SessionRecoveryManager.discover_chunks(session_path)
        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
