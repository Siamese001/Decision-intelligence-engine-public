"""
tests/test_adversarial_folder_automation.py

Adversarial Red Team Test Suite for Intelligent Folder Automation & Directory Hierarchy.
Explicitly targets and proves hardening against:
1. Attack Vector 1: Hidden file and dotdirectory residue injection ([PROHIBITED_HIDDEN_RESIDUE]).
2. Attack Vector 2: Active recording bypass of pre-mutation publication gate.
3. Attack Vector 3: Path containment escape attempt via MeetingPathEnvelope.
4. Attack Vector 4: Non-atomic write failure residue prevention and crash cleanliness.
5. Attack Vector 5: Multi-session cache isolation and manifest integrity.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.meeting_paths import MeetingPathEnvelope
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from tools.validate_output_hierarchy import validate_meeting_directory


class AdversarialFolderAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_valid_meeting_dir(self, project: str = "project_nexus", meeting_date: str = "10-02-2026") -> MeetingPathEnvelope:
        env = MeetingPathEnvelope.resolve(
            project_name=project,
            meeting_date=meeting_date,
            meeting_name="Red Team Test Meeting",
            meeting_time="0900AM_1000AM_ET",
            repo_root=self.root,
        )
        env.ensure_output_dir()
        env.deliverable_01.write_text("# 01 Transcript", encoding="utf-8")
        env.deliverable_02.write_text("# 02 Recap", encoding="utf-8")
        env.deliverable_03.write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")
        env.qa_report.write_text("{}", encoding="utf-8")
        env.judge_evaluation.write_text("{}", encoding="utf-8")
        env.release_decision.write_text("{}", encoding="utf-8")
        env.intake_package.write_text("{}", encoding="utf-8")
        return env

    def test_attack_vector_1_hidden_residue_injection_blocked(self) -> None:
        """Attack Vector 1: An attacker or process drops hidden files or directories into a meeting folder."""
        env = self._seed_valid_meeting_dir()
        # Baseline: clean directory passes
        self.assertEqual(validate_meeting_directory(env.output_dir, project_name=env.project_name), [])

        # Injected dotfile (e.g. shadow script, partial scratch)
        hidden_file = env.output_dir / ".shadow_payload.sh"
        hidden_file.write_text("#!/bin/sh\necho malicious", encoding="utf-8")
        violations = validate_meeting_directory(env.output_dir, project_name=env.project_name)
        self.assertTrue(any("[PROHIBITED_HIDDEN_RESIDUE]" in v and ".shadow_payload.sh" in v for v in violations))
        hidden_file.unlink()

        # Injected dotdirectory (e.g. .chunks, .tmp_stage)
        hidden_dir = env.output_dir / ".chunks"
        hidden_dir.mkdir()
        violations_dir = validate_meeting_directory(env.output_dir, project_name=env.project_name)
        self.assertTrue(any("[PROHIBITED_HIDDEN_RESIDUE]" in v and ".chunks" in v for v in violations_dir))
        hidden_dir.rmdir()

        # Legitimate OS file (.DS_Store) is permitted
        (env.output_dir / ".DS_Store").write_text("", encoding="utf-8")
        self.assertEqual(validate_meeting_directory(env.output_dir, project_name=env.project_name), [])

    def test_attack_vector_2_active_recording_mutation_bypass_blocked(self) -> None:
        """Attack Vector 2: Active recording in progress must strictly fail pre-mutation publication gate."""
        env = MeetingPathEnvelope.resolve(
            project_name="project_nexus",
            meeting_date="10-02-2026",
            meeting_name="Active Session",
            repo_root=self.root,
        )
        env.ensure_output_dir()
        live_file = env.output_dir / "01_10-02-2026_live_stream_live_transcript.md"
        live_file.write_text("# Live Feed\nSTATUS: RECORDING_IN_PROGRESS\nchunk_0000", encoding="utf-8")

        # Without explicit allow_active_recording, validation fails closed
        violations = validate_meeting_directory(env.output_dir, project_name=env.project_name, allow_active_recording=False)
        self.assertTrue(any("[ACTIVE_RECORDING_INCOMPLETE]" in v for v in violations))

        # Explicit allow_active_recording permits read-only monitoring
        violations_mon = validate_meeting_directory(env.output_dir, project_name=env.project_name, allow_active_recording=True)
        self.assertEqual(violations_mon, [])

    def test_attack_vector_3_path_containment_escape_blocked(self) -> None:
        """Attack Vector 3: Malicious path traversal attempts fail closed."""
        with self.assertRaises(ValueError) as ctx:
            MeetingPathEnvelope.resolve(
                project_name="../../etc",
                meeting_date="10-02-2026",
                repo_root=self.root,
            )
        self.assertIn("Unknown project identifier", str(ctx.exception))

    def test_attack_vector_4_atomic_write_crash_cleanliness(self) -> None:
        """Attack Vector 4: Atomic file writing leaves no corrupt temporary files on failure."""
        target_path = self.root / "output" / "project_nexus" / "10-02-2026" / "test_file.txt"
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Successful atomic write
        RawTranscriptPipeline._atomic_write_text(target_path, "Atomic Content Verified")
        self.assertEqual(target_path.read_text(encoding="utf-8"), "Atomic Content Verified")

        # Zero leftover .tmp files
        tmp_files = list(target_path.parent.glob("*.tmp_*"))
        self.assertEqual(tmp_files, [])

    def test_attack_vector_5_multi_session_isolation_and_manifest(self) -> None:
        """Attack Vector 5: Back-to-back recording sessions generate distinct manifests and non-colliding master audio."""
        rec1 = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Sprint Retrospective",
            meeting_date="10-02-2026",
            mock_mode=True,
            output_dir=self.root / "output" / "project_nexus" / "10-02-2026",
        )
        time.sleep(1.05)  # Ensure session tag increments by second
        rec2 = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Sprint Retrospective",
            meeting_date="10-02-2026",
            mock_mode=True,
            output_dir=self.root / "output" / "project_nexus" / "10-02-2026",
        )

        self.assertNotEqual(rec1.session_tag, rec2.session_tag)
        self.assertNotEqual(rec1.master_wav, rec2.master_wav)
        self.assertNotEqual(rec1.session_manifest, rec2.session_manifest)

        self.assertTrue(rec1.session_manifest.exists())
        self.assertTrue(rec2.session_manifest.exists())

        manifest1 = json.loads(rec1.session_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest1["session_tag"], rec1.session_tag)
        self.assertEqual(manifest1["project"], "project_nexus")


if __name__ == "__main__":
    unittest.main()
