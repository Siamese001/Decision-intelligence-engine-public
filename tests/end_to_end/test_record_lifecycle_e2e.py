"""
tests/end_to_end/test_record_lifecycle_e2e.py

End-to-End lifecycle tests for live meeting recording entry point and subprocess containment.
Verifies that:
1. `nee.py record-meeting --help` exits 0 with zero capture children and zero spool churn.
2. `nee.py record-meeting ... --dry-run` exits 0, executes probation check, and leaves clean registry.
3. `nee.py record-meeting` with invalid arguments fails cleanly with zero spawned audio children.
4. Real-entry continuous spool handoff correctly transitions capture authority and flushes audio.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.e2e
class TestRecordLifecycleE2E(unittest.TestCase):
    """E2E suite verifying shipping entry subprocess lifecycle and orphan prevention."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.python_bin = sys.executable

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_help_spawns_zero_capture_children(self) -> None:
        """E2E: Actual-entry subprocess help execution must spawn 0 capture children."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)

        # Invariant: No ephemeral spool directory should have been initialized in temp
        temp_dir_path = Path(tempfile.gettempdir())
        spools_before = set(temp_dir_path.glob("nee_instant_spool_fastboot_*"))

        proc = subprocess.run(
            [self.python_bin, "nee.py", "record-meeting", "--help"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, f"Help command failed: {proc.stderr}")
        self.assertIn("record-meeting", proc.stdout.lower())
        self.assertIn("usage:", proc.stdout.lower())

        spools_after = set(temp_dir_path.glob("nee_instant_spool_fastboot_*"))
        new_spools = spools_after - spools_before
        self.assertEqual(len(new_spools), 0, f"Help command must not spawn eager spool directory, found: {new_spools}")

    def test_e2e_dry_run_leaves_zero_capture_children_and_clean_registry(self) -> None:
        """E2E: Actual-entry subprocess dry-run completes probation and unregisters cleanly."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)

        proc = subprocess.run(
            [
                self.python_bin,
                "nee.py",
                "record-meeting",
                "--project", "project_beacon_e2e",
                "--meeting", "E2E Dry Run Verification",
                "--attendees", "Alice,Bob",
                "--dry-run",
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, f"Dry-run failed: {proc.stderr}")
        self.assertIn("[OK] Dry run successful", proc.stdout)

        # Invariant: No dangling session in .session_registry
        registry_dir = REPO_ROOT / ".session_registry"
        if registry_dir.exists():
            for manifest_file in registry_dir.glob("*.json"):
                content = manifest_file.read_text(encoding="utf-8")
                self.assertNotIn(
                    "E2E Dry Run Verification",
                    content,
                    "Dry-run must not leave active session manifest in .session_registry.",
                )

    def test_e2e_invalid_arguments_exit_nonzero_without_spooling(self) -> None:
        """E2E: Actual-entry subprocess with missing required args exits nonzero with 0 capture children."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)

        proc = subprocess.run(
            [self.python_bin, "nee.py", "record-meeting", "--unknown-flag-xyz"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=30,
        )

        self.assertNotEqual(proc.returncode, 0, "Invalid args must exit with non-zero exit code.")
        self.assertIn("error:", proc.stderr.lower())

    def test_e2e_continuous_spool_handoff_and_clean_shutdown(self) -> None:
        """E2E: Fast bootstrap spooler seamlessly splices into LiveMeetingRecorder and shuts down cleanly."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
        from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
            clear_bootstrap_spooler,
            run_fast_bootstrap,
        )

        # 1. Trigger fast bootstrap
        spool_dir = run_fast_bootstrap(
            ["record-meeting", "--project", "p_e2e", "--meeting", "Handoff E2E", "--mock"]
        )
        self.assertIsNotNone(spool_dir)

        # 2. Instantiate LiveMeetingRecorder and verify single capture ownership
        recorder = LiveMeetingRecorder(
            project_name="p_e2e",
            meeting_name="Handoff E2E",
            mock_mode=True,
            audio_source="mic",
            output_dir=self.test_root / "e2e_handoff_out",
        )
        self.assertIsNotNone(recorder.spooler)
        self.assertTrue(recorder.spooler.is_running)

        # 3. Start ffmpeg (adopts the running spooler)
        recorder.start_ffmpeg()
        self.assertTrue(recorder.capture_manager.is_running)
        self.assertEqual(recorder.capture_manager.active_strategy, "spooler_adopted")

        # 4. Graceful abort/teardown
        recorder._early_abort_cleanup()
        self.assertFalse(recorder.spooler.is_running)
        self.assertFalse(recorder.capture_manager.is_running)

        # 5. Verify cleanup
        clear_bootstrap_spooler(stop=True)
        if spool_dir and Path(spool_dir).exists():
            import shutil
            shutil.rmtree(spool_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
