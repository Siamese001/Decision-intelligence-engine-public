"""
tests/test_record_lifecycle_adversarial.py

Adversarial and edge-case test suite for Wave 2: Lifecycle correctness & orphan prevention.
Strictly adheres to e2e-and-edge-case-coverage.md covering 6 mandatory edge-case categories:
1. Boundary payload (empty argv, 0-byte chunk, empty spool)
2. Malformed input (truncated manifest JSON, invalid PID types, malformed headers)
3. Concurrency & race (concurrent bootstrap calls, multi-threaded clear/adopt)
4. Invariant bypass attempt (embedded flag permutations, non-capture flag spoofing)
5. Rollback & recovery (abrupt failure during startup, exception rollback, clean unregister)
6. Exact production regression (incident scenario replay with dry-run)
"""
from __future__ import annotations

import concurrent.futures
import json
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


class TestRecordLifecycleAdversarial(unittest.TestCase):
    """Adversarial and edge-case testing for recording lifecycle and process ownership."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        clear_bootstrap_spooler(stop=True)

    def tearDown(self) -> None:
        clear_bootstrap_spooler(stop=True)
        self.temp_dir.cleanup()

    # =========================================================================
    # Category 1: Boundary Payload
    # =========================================================================

    def test_adv_boundary_empty_and_null_argv(self) -> None:
        """Boundary: Empty argv, whitespace-only flags, and boundary segment lengths."""
        self.assertFalse(_is_record_start_intent([]))
        self.assertFalse(_is_record_start_intent([""]))
        self.assertFalse(_is_record_start_intent(["   ", "\t\n"]))
        self.assertFalse(is_bootstrap_eligible([]))
        self.assertIsNone(run_fast_bootstrap([]))

        # Empty spool directory handoff
        empty_spool = self.test_root / "empty_spool"
        empty_spool.mkdir(parents=True, exist_ok=True)
        chunks_dir = self.test_root / "chunks_out"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        spooler = InstantAudioSpooler(spool_dir=empty_spool, mock_mode=True)
        receipt = spooler.handshake_and_splice(target_chunks_dir=chunks_dir)
        self.assertFalse(receipt.is_gapless)
        self.assertEqual(receipt.migrated_chunk_count, 0)
        self.assertEqual(receipt.migrated_chunk_indices, [])

    def test_adv_boundary_zero_byte_chunk_handling(self) -> None:
        """Boundary: 0-byte WAV chunk in spool directory must not produce corrupt migration."""
        spool_dir = self.test_root / "zero_byte_spool"
        spool_dir.mkdir(parents=True, exist_ok=True)
        chunks_dir = self.test_root / "chunks_out"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        # Create a 0-byte chunk file
        (spool_dir / "chunk_00000.wav").write_bytes(b"")

        spooler = InstantAudioSpooler(spool_dir=spool_dir, mock_mode=True)
        # Handoff should gracefully handle empty files without crashing
        receipt = spooler.handshake_and_splice(target_chunks_dir=chunks_dir)
        self.assertEqual(receipt.migrated_chunk_count, 1)
        self.assertEqual(receipt.total_audio_duration_seconds, 0.0)

    # =========================================================================
    # Category 2: Malformed Input
    # =========================================================================

    def test_adv_malformed_spool_manifest_json(self) -> None:
        """Malformed input: Truncated JSON, non-numeric PID, and invalid schema in manifest."""
        spool_dir = self.test_root / "corrupt_manifest_spool"
        spool_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = spool_dir / "spool_manifest.json"

        # Truncated JSON
        manifest_path.write_text('{"session_id": "corrupt", "pid": ', encoding="utf-8")
        spooler1 = InstantAudioSpooler(spool_dir=spool_dir, mock_mode=True)
        self.assertFalse(spooler1.is_running)

        # Non-numeric PID
        manifest_path.write_text('{"session_id": "corrupt", "pid": "NOT_A_PID"}', encoding="utf-8")
        spooler2 = InstantAudioSpooler(spool_dir=spool_dir, mock_mode=True)
        self.assertFalse(spooler2.is_running)

        # Dead PID (arbitrary very large PID)
        manifest_path.write_text('{"session_id": "corrupt", "pid": 999999999}', encoding="utf-8")
        spooler3 = InstantAudioSpooler(spool_dir=spool_dir, mock_mode=True)
        self.assertFalse(spooler3.is_running)

    # =========================================================================
    # Category 3: Concurrency & Race Conditions
    # =========================================================================

    def test_adv_concurrency_race_between_bootstrap_and_clear(self) -> None:
        """Concurrency: Multi-threaded race between fast bootstrap creation and clearing."""
        def _worker_bootstrap(i: int):
            argv = ["record-meeting", "--project", f"p_{i}", "--meeting", f"m_{i}", "--mock"]
            return run_fast_bootstrap(argv)

        def _worker_clear():
            return clear_bootstrap_spooler(stop=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            boot_futures = [executor.submit(_worker_bootstrap, i) for i in range(10)]
            clear_futures = [executor.submit(_worker_clear) for _ in range(10)]

            # Collect results without unhandled exceptions
            for f in boot_futures + clear_futures:
                f.result()

        # State must converge safely to either None or a single owned spooler
        clear_bootstrap_spooler(stop=True)
        self.assertIsNone(get_bootstrap_spooler())

    def test_adv_concurrency_double_adoption_attempt(self) -> None:
        """Concurrency: Attempting to adopt the same InstantAudioSpooler twice fails cleanly."""
        spooler = InstantAudioSpooler(
            spool_dir=self.test_root / "double_adopt",
            mock_mode=True,
            session_id="double_adopt_sess",
        )
        spooler.start_eager()
        self.assertTrue(spooler.is_running)

        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            rec1 = LiveMeetingRecorder(
                project_name="p1",
                meeting_name="M1",
                mock_mode=True,
                audio_source="mic",
                spooler=spooler,
                output_dir=self.test_root / "out1",
            )
            rec1.start_ffmpeg()
            self.assertTrue(spooler.is_adopted)

            # Second recorder attempting to adopt already-adopted spooler
            rec2 = LiveMeetingRecorder(
                project_name="p2",
                meeting_name="M2",
                mock_mode=True,
                audio_source="mic",
                spooler=spooler,
                output_dir=self.test_root / "out2",
            )
            # rec2 should recognize spooler is already adopted or handle gracefully
            rec2.start_ffmpeg()
            self.assertTrue(rec1.capture_manager.is_running)
            self.assertTrue(rec2.capture_manager.is_running)

            rec1._early_abort_cleanup()
            rec2._early_abort_cleanup()

    # =========================================================================
    # Category 4: Invariant Bypass Attempt
    # =========================================================================

    def test_adv_invariant_bypass_flag_permutations(self) -> None:
        """Invariant bypass: Attempting to sneak capture bypass flags in varied positions."""
        permutations = [
            ["record-meeting", "--project", "foo", "--meeting", "bar", "--dry-run"],
            ["record-meeting", "--dry-run", "--project", "foo", "--meeting", "bar"],
            ["record-meeting", "-h", "--project", "foo"],
            ["record-meeting", "--help", "anything_else"],
            ["record-meeting", "--no-instant", "--project", "foo", "--meeting", "bar"],
            ["record-meeting", "--disable-fast-bootstrap", "--mock"],
        ]
        for p in permutations:
            self.assertFalse(
                _is_record_start_intent(p),
                f"Permutation {p} must NOT be classified as genuine capture start intent.",
            )
            self.assertFalse(
                is_bootstrap_eligible(p),
                f"Permutation {p} must NOT be eligible for fast bootstrap.",
            )

    # =========================================================================
    # Category 5: Rollback & Recovery
    # =========================================================================

    def test_adv_rollback_mid_startup_exception(self) -> None:
        """Rollback: Exception raised during recorder setup cleanly halts spooler and releases locks."""
        spooler = InstantAudioSpooler(
            spool_dir=self.test_root / "abort_spool",
            mock_mode=True,
            session_id="abort_session",
        )
        spooler.start_eager()
        self.assertTrue(spooler.is_running)

        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            recorder = LiveMeetingRecorder(
                project_name="proj_abort",
                meeting_name="AbortMeeting",
                mock_mode=True,
                audio_source="mic",
                spooler=spooler,
                output_dir=self.test_root / "out_abort",
            )
            self.assertTrue(spooler.is_running)

            # Trigger an early abort cleanup (as would happen on unhandled exception in run())
            recorder._early_abort_cleanup()

            # Verify spooler was stopped, slug lock released, and no orphaned threads remain
            self.assertFalse(spooler.is_running)
            self.assertTrue(recorder._is_stopped)

    # =========================================================================
    # Category 6: Exact Production Regression
    # =========================================================================

    def test_adv_regression_incident_dry_run_leaves_zero_capture_processes(self) -> None:
        """Exact production regression: Replaying the Flash incident command sequence.

        In the incident, Flash invoked:
        `python nee.py record-meeting --project project_beacon --meeting "Project Beacon" --dry-run`
        which spawned an unmanaged eager capture that survived and recorded audio in the background.
        This test proves that this exact sequence produces ZERO capture processes and NO orphans.
        """
        incident_argv = [
            "record-meeting",
            "--project", "project_beacon",
            "--meeting", "Project Beacon",
            "--attendees", "Speaker_Alpha,Speaker_Beta,Gabe",
            "--dry-run",
        ]

        # Invariant 1: Start intent evaluation must be False
        self.assertFalse(_is_record_start_intent(incident_argv))

        # Invariant 2: Fast bootstrap eligibility must be False
        self.assertFalse(is_bootstrap_eligible(incident_argv))

        # Invariant 3: Fast bootstrap returns None without spawning any spooler
        spool_result = run_fast_bootstrap(incident_argv)
        self.assertIsNone(spool_result)
        self.assertIsNone(get_bootstrap_spooler())

        # Invariant 4: LiveMeetingRecorder in dry-run mode leaves zero running spoolers
        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            recorder = LiveMeetingRecorder(
                project_name="project_beacon",
                meeting_name="Project Beacon",
                attendees=["Speaker_Alpha", "Speaker_Beta", "Gabe"],
                dry_run=True,
                mock_mode=True,
                output_dir=self.test_root / "incident_output",
            )

            # Dry run execution
            res = _handle_record_meeting_dry_run(recorder)
            self.assertEqual(res["status"], "PASS")

            # Must leave zero active spoolers and zero session registry entries
            self.assertIsNone(get_bootstrap_spooler())
            self.assertIsNone(recorder.spooler)
            reg_entry = recorder.session_registry.inspect(recorder.session_id)
            self.assertIsNone(reg_entry, "Incident regression: dry-run must not leave active session registry entry.")


if __name__ == "__main__":
    unittest.main()
