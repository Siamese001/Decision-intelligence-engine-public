"""
tests/adversarial/test_ingest_cli_worktree_enforcement_adversarial.py

Adversarial test suite for Master Architectural Invariant 14:
Worktree Isolation Enforcement across CanonicalAgenticPipeline and CLI Ingress.

Covers:
1. Invariant bypass attempt (passing explicit repo root output path).
2. Nested worktree recursion attempt (running inside existing worktree).
3. Opt-out boundary (enforce_worktree=False and NEE_ENFORCE_WORKTREE=0).
4. Boundary payload & special characters in meeting names.
5. Ingress dry-run worktree isolation exposure.
"""
from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.canonical_pipeline.lifecycle import (
    calculate_target_path,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.core import ROOT


class TestIngestCliWorktreeEnforcementAdversarial(unittest.TestCase):
    """Adversarial stress testing of worktree enforcement and bypass prevention."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bypass_attempt_explicit_root_output_path_is_intercepted(self):
        """Invariant bypass attempt: Passing output_dir='output/...' in root repo is intercepted into .worktrees/."""
        mock_wt_dir = ROOT / ".worktrees" / "live_bypass_attempt"
        mock_branch = "feat/live-bypass-attempt"

        with (
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
                return_value=False,
            ),
            mock.patch(
                "dual_pipeline.canonical_pipeline.worktree_inheritance.inherit_worktree_from_metadata",
                return_value=None,
            ),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree",
                return_value=(mock_wt_dir, mock_branch),
            ) as mock_ensure,
        ):
            meta = {
                "meeting_name": "Bypass Attempt",
                "project": "project_nexus",
                "enforce_worktree": True,
            }
            # Caller attempts to bypass by explicitly pointing to ROOT/output
            expl_dir = str(ROOT / "output" / "project_nexus" / "09-29-2026" / "Bypass_Attempt")
            target = calculate_target_path(
                output_dir=expl_dir,
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Bypass_Attempt",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_called_once()
            self.assertEqual(meta["worktree_dir"], str(mock_wt_dir))
            self.assertEqual(meta["worktree_branch"], mock_branch)
            self.assertEqual(
                target,
                mock_wt_dir / "output" / "project_nexus" / "09-29-2026" / "Bypass_Attempt",
            )
            # Ensure it never points to ROOT/output
            self.assertFalse(str(target).startswith(str(ROOT / "output")))

    def test_nested_worktree_recursion_is_prevented(self):
        """Recursive nesting attempt: When already inside .worktrees/, no new .worktrees/ is created."""
        existing_wt = ROOT / ".worktrees" / "live_parent_meeting"
        existing_target = existing_wt / "output" / "project_nexus" / "09-29-2026" / "Parent_Meeting"

        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=True,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_authentic_git_worktree",
            return_value=True,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch",
            return_value="feat/live-parent_meeting",
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree"
        ) as mock_ensure:
            meta = {
                "meeting_name": "Parent Meeting",
                "project": "project_nexus",
                "enforce_worktree": True,
            }
            target = calculate_target_path(
                output_dir=str(existing_target),
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Parent_Meeting",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_not_called()
            self.assertEqual(target, existing_target)
            self.assertNotIn(".worktrees/.worktrees", str(target))

    def test_opt_out_attempts_are_blocked_and_unconditionally_isolated(self):
        """Bypass prevention: Explicit enforce_worktree=False, NEE_ENFORCE_WORKTREE=0, or --no-enforce-worktree are blocked."""
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["ingest-document", "--input", "foo.pdf", "--no-enforce-worktree"])
        self.assertEqual(ctx.exception.code, 1)

        mock_wt_dir = ROOT / ".worktrees" / "ingest_meeting_opt_out"
        mock_branch = "ingest/meeting-opt-out"

        # Case A: Metadata flag False is overridden to True and isolated
        with (
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
                return_value=False,
            ),
            mock.patch(
                "dual_pipeline.canonical_pipeline.worktree_inheritance.inherit_worktree_from_metadata",
                return_value=None,
            ),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree",
                return_value=(mock_wt_dir, mock_branch),
            ) as mock_ensure,
        ):
            meta = {"meeting_name": "Opt Out", "enforce_worktree": False}
            target = calculate_target_path(
                output_dir="auto",
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Opt_Out",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )
            mock_ensure.assert_called_once()
            self.assertTrue(meta["enforce_worktree"])
            self.assertEqual(target, mock_wt_dir / "output" / "project_nexus" / "09-29-2026" / "Opt_Out")

        # Case B: Environment variable NEE_ENFORCE_WORKTREE=0 is ignored and isolated
        with (
            mock.patch.dict(os.environ, {"NEE_ENFORCE_WORKTREE": "0"}),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
                return_value=False,
            ),
            mock.patch(
                "dual_pipeline.canonical_pipeline.worktree_inheritance.inherit_worktree_from_metadata",
                return_value=None,
            ),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree",
                return_value=(mock_wt_dir, mock_branch),
            ) as mock_ensure,
        ):
            meta = {"meeting_name": "Opt Out Env", "enforce_worktree": False}
            target = calculate_target_path(
                output_dir="auto",
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Opt_Out_Env",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )
            mock_ensure.assert_called_once()
            self.assertTrue(meta["enforce_worktree"])
            self.assertEqual(target, mock_wt_dir / "output" / "project_nexus" / "09-29-2026" / "Opt_Out_Env")

    def test_boundary_payload_special_characters_in_meeting_name(self):
        """Boundary payload: Meeting titles with punctuation, spaces, and Unicode produce clean worktree paths."""
        mock_wt_dir = ROOT / ".worktrees" / "live_phase_1_q1_review"
        mock_branch = "feat/live-phase-1-q1-review"

        with (
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
                return_value=False,
            ),
            mock.patch(
                "dual_pipeline.canonical_pipeline.worktree_inheritance.inherit_worktree_from_metadata",
                return_value=None,
            ),
            mock.patch(
                "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree",
                return_value=(mock_wt_dir, mock_branch),
            ) as mock_ensure,
        ):
            meta = {
                "meeting_name": "Phase 1: Q1 Review / Sync (Jen & Amit)!",
                "project": "project_nexus",
                "enforce_worktree": True,
            }
            target = calculate_target_path(
                output_dir="auto",
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Phase_1_Q1_Review_Jen_Amit",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_called_once()
            self.assertEqual(meta["worktree_branch"], mock_branch)
            self.assertTrue(str(target).startswith(str(mock_wt_dir)))

    def test_cli_ingress_rejects_disabled_worktree_flag(self):
        """CLI ingress verification: ingest_canonical rejects enforce_worktree=False fail-closed."""
        sample_file = self.test_root / "transcript.txt"
        sample_file.write_text("Jen: Alignment on pipeline routing.", encoding="utf-8")

        args = argparse.Namespace(
            input=str(sample_file),
            source=None,
            enforce_worktree=False,
            meeting="CLI Bypass Attempt",
            project="project_nexus",
            date="09-29-2026",
        )
        with self.assertRaisesRegex(ValueError, "FAIL_CLOSED: Worktree isolation cannot be disabled"):
            ingest_canonical(args)


if __name__ == "__main__":
    unittest.main()
