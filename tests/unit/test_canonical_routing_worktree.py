"""
tests/unit/test_canonical_routing_worktree.py

Unit tests for CanonicalAgenticPipeline routing worktree isolation (Wave 1).
Verifies that calculate_target_path and resolve_routing enforce isolated git worktrees
when enforce_worktree is enabled, and avoid nested worktree recursion when already inside one.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from dual_pipeline.canonical_pipeline.lifecycle import (
    calculate_target_path,
    resolve_routing,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.core import ROOT


class TestCanonicalRoutingWorktree(unittest.TestCase):
    """Verifies worktree routing behaviors in canonical pipeline lifecycle."""

    def setUp(self) -> None:
        self._orig_env = os.environ.get("NEE_ENFORCE_WORKTREE")
        os.environ.pop("NEE_ENFORCE_WORKTREE", None)

    def tearDown(self) -> None:
        if self._orig_env is not None:
            os.environ["NEE_ENFORCE_WORKTREE"] = self._orig_env
        else:
            os.environ.pop("NEE_ENFORCE_WORKTREE", None)

    def test_calculate_target_path_enforces_worktree_when_requested(self):
        """calculate_target_path routes to .worktrees/live_<slug>/output/... when enforce_worktree is True."""
        mock_wt_dir = ROOT / ".worktrees" / "live_standup_sync"
        mock_branch = "feat/live-standup-sync"

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
                "meeting_name": "Standup Sync",
                "enforce_worktree": True,
            }
            target = calculate_target_path(
                output_dir="auto",
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Standup_Sync",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_called_once()
            self.assertEqual(meta.get("worktree_dir"), str(mock_wt_dir))
            self.assertEqual(meta.get("worktree_branch"), mock_branch)
            self.assertEqual(
                target,
                mock_wt_dir / "output" / "project_nexus" / "09-29-2026" / "Standup_Sync",
            )

    def test_calculate_target_path_enforces_worktree_even_when_enforce_worktree_false(self):
        """calculate_target_path unconditionally enforces worktree provisioning even when enforce_worktree is False or NEE_ENFORCE_WORKTREE=0."""
        mock_wt_dir = ROOT / ".worktrees" / "live_standup_sync"
        mock_branch = "feat/live-standup-sync"
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
            meta = {
                "meeting_name": "Standup Sync",
                "enforce_worktree": False,
            }
            target = calculate_target_path(
                output_dir="auto",
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Standup_Sync",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_called_once()
            self.assertTrue(meta.get("enforce_worktree"))
            self.assertEqual(meta.get("worktree_dir"), str(mock_wt_dir))
            self.assertEqual(meta.get("worktree_branch"), mock_branch)
            self.assertEqual(
                target,
                mock_wt_dir / "output" / "project_nexus" / "09-29-2026" / "Standup_Sync",
            )

    def test_calculate_target_path_preserves_path_when_already_in_worktree(self):
        """calculate_target_path does not double-provision when already inside a worktree."""
        existing_wt = ROOT / ".worktrees" / "live_existing_meeting"
        existing_output = existing_wt / "output" / "project_nexus" / "09-29-2026" / "Existing_Meeting"

        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=True,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_authentic_git_worktree",
            return_value=True,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch",
            return_value="feat/live-existing_meeting",
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree"
        ) as mock_ensure:
            meta = {
                "meeting_name": "Existing Meeting",
                "enforce_worktree": True,
            }
            target = calculate_target_path(
                output_dir=str(existing_output),
                proj="project_nexus",
                date_fn="09-29-2026",
                slug="Existing_Meeting",
                no_subfolder=False,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
            )

            mock_ensure.assert_not_called()
            self.assertEqual(target, existing_output)

    def test_resolve_routing_populates_worktree_metadata(self):
        """resolve_routing populates worktree metadata and returns worktree-isolated target directory."""
        mock_wt_dir = ROOT / ".worktrees" / "live_debrief_test"
        mock_branch = "feat/live-debrief-test"

        with (
            mock.patch("dual_pipeline.canonical_pipeline.lifecycle._check_meeting_collision"),
            mock.patch("pathlib.Path.mkdir"),
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
            ),
        ):
            meta = {
                "project": "project_nexus",
                "meeting_name": "Debrief Test",
                "date": "09-29-2026",
                "enforce_worktree": True,
            }
            resolved_p = resolve_routing(meta, output_dir="auto", modality=CanonicalModality.MEETING_TRANSCRIPT)

            self.assertEqual(meta["worktree_dir"], str(mock_wt_dir))
            self.assertEqual(meta["worktree_branch"], mock_branch)
            self.assertTrue(str(resolved_p).startswith(str(mock_wt_dir)))


if __name__ == "__main__":
    unittest.main()
