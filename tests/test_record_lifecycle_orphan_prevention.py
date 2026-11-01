"""
tests/test_record_lifecycle_orphan_prevention.py

Wave 2: Lifecycle correctness and capture orphan prevention tests.
Verifies that:
1. Help, dry-run, and invalid arguments start zero capture processes.
2. In-memory bootstrap spooler transfers ownership to LiveMeetingRecorder cleanly.
3. Dual-source (mixed_sck) transitions cleanly without AVFoundation device contention.
4. Single capture authority is preserved for microphone and mock modes via adopt_spooler.
5. Incomplete/open chunk handoff never asserts gapless success without evidence.
6. Abort cleanup stops all owned capture processes and reaps children.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.engine import _handle_record_meeting_dry_run
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
    clear_bootstrap_spooler,
    get_bootstrap_spooler,
    is_bootstrap_eligible,
    run_fast_bootstrap,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import (
    InstantAudioSpooler,
    SpoolStatus,
)
from nee import _is_record_start_intent


class TestRecordLifecycleOrphanPrevention(unittest.TestCase):
    """Test suite for Wave 2 lifecycle correctness and orphan prevention invariants."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        clear_bootstrap_spooler(stop=True)

    def tearDown(self) -> None:
        clear_bootstrap_spooler(stop=True)
        self.temp_dir.cleanup()

    def test_record_start_intent_gating_rejects_non_capture_invocations(self) -> None:
        """Verifies _is_record_start_intent rejects help, dry-run, and non-capture flags."""
        # Non-record commands
        self.assertFalse(_is_record_start_intent([]))
        self.assertFalse(_is_record_start_intent(["query", "what is status?"]))
        self.assertFalse(_is_record_start_intent(["ingest-meeting", "--input", "foo.wav"]))

        # Help flags
        self.assertFalse(_is_record_start_intent(["record-meeting", "--help"]))
        self.assertFalse(_is_record_start_intent(["record-meeting", "-h"]))
        self.assertFalse(_is_record_start_intent(["record", "--help"]))

        # Dry-run and bypass flags
        self.assertFalse(_is_record_start_intent(["record-meeting", "--project", "p", "--meeting", "m", "--dry-run"]))
        self.assertFalse(_is_record_start_intent(["record-meeting", "--project", "p", "--meeting", "m", "--no-instant"]))
        self.assertFalse(_is_record_start_intent(["record-meeting", "--disable-fast-bootstrap", "--project", "p"]))

        # Bare command without arguments
        self.assertFalse(_is_record_start_intent(["record-meeting"]))
        self.assertFalse(_is_record_start_intent(["record"]))

        # Genuine start intents
        self.assertTrue(_is_record_start_intent(["record-meeting", "--project", "p", "--meeting", "m"]))
        self.assertTrue(_is_record_start_intent(["record", "--project", "p", "--meeting", "m", "--mock"]))

    def test_is_bootstrap_eligible_obeys_environment_and_flags(self) -> None:
        """Verifies fast bootstrap gating honors NEE_DISABLE_FAST_BOOTSTRAP and CLI flags."""
        self.assertTrue(is_bootstrap_eligible(["record-meeting", "--project", "alpha"]))

        # Disabling flags
        self.assertFalse(is_bootstrap_eligible(["record-meeting", "--help"]))
        self.assertFalse(is_bootstrap_eligible(["record-meeting", "--dry-run"]))
        self.assertFalse(is_bootstrap_eligible(["record-meeting", "--no-instant"]))
        self.assertFalse(is_bootstrap_eligible(["record-meeting", "--disable-fast-bootstrap"]))

        # Environment variable override
        with patch.dict(os.environ, {"NEE_DISABLE_FAST_BOOTSTRAP": "1"}):
            self.assertFalse(is_bootstrap_eligible(["record-meeting", "--project", "alpha"]))

    def test_bootstrap_ownership_transfer_to_live_recorder(self) -> None:
        """Verifies fast bootstrap spooler is claimed by LiveMeetingRecorder without duplicate capture."""
        # 1. Run bootstrap in mock mode
        spool_dir_str = run_fast_bootstrap(["record-meeting", "--project", "proj_a", "--meeting", "Sync", "--mock"])
        self.assertIsNotNone(spool_dir_str)
        in_mem_spooler = get_bootstrap_spooler()
        self.assertIsNotNone(in_mem_spooler)
        self.assertTrue(in_mem_spooler.is_running)

        # 2. LiveMeetingRecorder initializes and claims the bootstrap spooler
        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            recorder = LiveMeetingRecorder(
                project_name="proj_a",
                meeting_name="Sync",
                mock_mode=True,
                audio_source="mic",
                enable_copilot=False,
                output_dir=self.test_root / "output",
            )

            # In-memory global reference must be cleared
            self.assertIsNone(get_bootstrap_spooler())
            # Recorder now owns the spooler
            self.assertIs(recorder.spooler, in_mem_spooler)
            self.assertTrue(recorder.spooler.is_running)

            # 3. start_ffmpeg adopts the spooler
            recorder.start_ffmpeg()
            self.assertTrue(recorder.spooler.is_adopted)
            self.assertTrue(recorder.capture_manager.is_running)
            self.assertEqual(recorder.capture_manager.active_strategy, "spooler_adopted")

            recorder._early_abort_cleanup()
            self.assertFalse(recorder.spooler.is_running)

    def test_dry_run_reaps_bootstrap_spooler_and_leaves_zero_orphans(self) -> None:
        """Verifies dry-run halts any existing bootstrap spooler and unregisters session."""
        # Pre-seed a bootstrap spooler
        run_fast_bootstrap(["record-meeting", "--project", "proj_b", "--meeting", "DryRunSession", "--mock"])
        active_spooler = get_bootstrap_spooler()
        self.assertIsNotNone(active_spooler)

        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            recorder = LiveMeetingRecorder(
                project_name="proj_b",
                meeting_name="DryRunSession",
                mock_mode=True,
                dry_run=True,
                enable_copilot=False,
                output_dir=self.test_root / "output_dry",
            )

            # Dry-run in init must stop the bootstrap spooler
            self.assertFalse(active_spooler.is_running)
            self.assertIsNone(recorder.spooler)
            self.assertIsNone(get_bootstrap_spooler())

            # Running dry run handler must clean up slug lock and registry
            res = _handle_record_meeting_dry_run(recorder)
            self.assertEqual(res["status"], "PASS")

            # Check registry inspect returns None
            reg_manifest = recorder.session_registry.inspect(recorder.session_id)
            self.assertIsNone(reg_manifest, "Dry run must unregister its session from session_registry.")

    def test_mixed_strategy_releases_spooler_before_starting_dual_capture(self) -> None:
        """Verifies recorder.start_ffmpeg cleanly stops bootstrap spooler before dual-source capture."""
        spooler = InstantAudioSpooler(
            spool_dir=self.test_root / "spool_mixed",
            mock_mode=True,
            session_id="test_mixed_handoff",
        )
        spooler.start_eager()
        self.assertTrue(spooler.is_running)

        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            recorder = LiveMeetingRecorder(
                project_name="proj_mixed",
                meeting_name="MixedSession",
                mock_mode=False,
                audio_source="mixed",
                spooler=spooler,
                enable_copilot=False,
                output_dir=self.test_root / "output_mixed",
            )

            # Mock capture_manager.start to record that it was called cleanly without contention
            recorder.capture_manager.start = MagicMock(return_value={"status": "SPAWNED", "strategy": "mixed_sck"})

            recorder.start_ffmpeg()

            # Bootstrap spooler must have been stopped to free AVFoundation device :0
            self.assertFalse(spooler.is_running, "Bootstrap spooler must be stopped before mixed capture starts.")
            self.assertTrue(recorder.capture_manager.start.called)
            self.assertEqual(recorder.capture_manager.start.call_count, 1)

            recorder._early_abort_cleanup()

    def test_open_chunk_handoff_never_asserts_gapless_without_evidence(self) -> None:
        """Verifies handshake_and_splice does NOT certify is_gapless=True when an open chunk exists."""
        spool_dir = self.test_root / "spool_gapless"
        spool_dir.mkdir(parents=True, exist_ok=True)
        chunks_dir = self.test_root / "target_chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        spooler = InstantAudioSpooler(
            spool_dir=spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="spool_gapless_test",
        )
        # Create chunk_00000.wav (closed) and chunk_00001.wav (still open/writing)
        from dual_pipeline.live_meeting_recorder_core.instant_spooler import (
            generate_synthetic_speech_chunk,
        )

        closed_chunk = spool_dir / "chunk_00000.wav"
        open_chunk = spool_dir / "chunk_00001.wav"
        generate_synthetic_speech_chunk(closed_chunk, duration_sec=5.0)
        generate_synthetic_speech_chunk(open_chunk, duration_sec=2.0)

        # Mark spooler as actively running with chunk_00001 as open
        spooler._is_running = True
        spooler._status = SpoolStatus.ACTIVE_SPOOLING

        receipt = spooler.handshake_and_splice(target_chunks_dir=chunks_dir)

        # Because chunk_00001 is open and not in poll_available_chunks, is_gapless MUST be False
        self.assertFalse(
            receipt.is_gapless,
            "handshake_and_splice must not assert is_gapless=True when chunks remain unsealed in spool.",
        )
        self.assertEqual(receipt.migrated_chunk_count, 1)
        self.assertIn(0, receipt.migrated_chunk_indices)
        self.assertNotIn(1, receipt.migrated_chunk_indices)

        # Now stop spooler (sealing all chunks) and re-splice
        spooler.stop()
        receipt2 = spooler.handshake_and_splice(target_chunks_dir=chunks_dir)
        self.assertTrue(
            receipt2.is_gapless,
            "handshake_and_splice must certify is_gapless=True when all chunks in spool are migrated.",
        )
        self.assertEqual(receipt2.migrated_chunk_count, 2)
        self.assertIn(1, receipt2.migrated_chunk_indices)


if __name__ == "__main__":
    unittest.main()
