"""
Tests for PR worktree dirty-file filtering, ephemeral checkpoint exclusion,
and live-meeting deliverable supersession hardening (Wave 1).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.executive_render import evaluate_stage_gates_and_disposition
from tools.pr_governance_core.live_meeting_bridge import (
    build_canonical_main_deliverable_index,
    build_sibling_worktree_deliverable_index,
    evaluate_worktree_meeting_supersession,
)
from tools.pr_governance_core.worktree import (
    _inspect_worktree_dirty,
    is_ephemeral_worktree_path,
    partition_worktree_dirty_files,
)
from tools.pr_topology_core.disposition import _check_ahead_zero_worktree_safety
from tools.pr_topology_core.matrix import BranchDiff
from tools.pr_topology_core.supersession import evaluate_dirty_file_coverage


class TestWorktreeDirtyAndMeetingSupersession(unittest.TestCase):
    """Test suite verifying Wave 1 acceptance criteria for dirty filtering and supersession."""

    def test_ephemeral_checkpoint_exclusion(self) -> None:
        """Verify ephemeral checkpoints, raw ASR dumps, and WAL files are classified as ephemeral."""
        ephemeral_cases = [
            "output/project_lens/10-09-2026/Standup_Amit/attempts/attempt_1/01_output.ckpt.md",
            "output/project_lens/10-09-2026/Standup_Amit/attempts/attempt_2/qa_gates_report.json",
            "output/project_lens/10-09-2026/Standup_Amit/referenced_artifacts/audio.raw_asr.json",
            "output/project_lens/10-09-2026/Standup_Amit/copilot_attempts.jsonl",
            "output/project_lens/10-09-2026/Standup_Amit/copilot_answers.jsonl",
            "output/project_lens/10-09-2026/Standup_Amit/judge_failure_diagnostics.json",
            "reports/qa_gates_report.json",
            "reports/pr_review_feat_foo_receipt.json",
            "storage/graph.sqlite-wal",
            "storage/graph.sqlite-shm",
        ]
        for path in ephemeral_cases:
            self.assertTrue(
                is_ephemeral_worktree_path(path),
                f"Expected '{path}' to be recognized as ephemeral worktree path",
            )

        non_ephemeral_cases = [
            "output/project_lens/10-09-2026/Standup_Amit/01_Standup_Amit_hitl_resolved_transcript.md",
            "output/project_lens/10-09-2026/Standup_Amit/02_Standup_Amit_executive_recap.md",
            "output/project_lens/10-09-2026/Standup_Amit/03_Standup_Amit_canonical_signal_projection.json",
            "output/project_lens/10-09-2026/Standup_Amit/final_release_decision.json",
            "output/project_lens/10-09-2026/Standup_Amit/metadata.json",
            "dual_pipeline/canonical_pipeline/evaluator.py",
            "tools/pr_governance.py",
            "tests/test_something.py",
            "docs/plans/feat_plan.md",
        ]
        for path in non_ephemeral_cases:
            self.assertFalse(
                is_ephemeral_worktree_path(path),
                f"Expected '{path}' NOT to be recognized as ephemeral worktree path",
            )

    def test_partition_worktree_dirty_files(self) -> None:
        """Verify dirty file list is cleanly partitioned into code, deliverables, and attempts."""
        sample_paths = [
            "dual_pipeline/evaluator.py",
            "tests/test_evaluator.py",
            "output/project_lens/10-09-2026/Standup_Amit/01_transcript.md",
            "output/project_lens/10-09-2026/Standup_Amit/02_recap.md",
            "output/project_lens/10-09-2026/Standup_Amit/attempts/attempt_1/01.ckpt.md",
            "output/project_lens/10-09-2026/Standup_Amit/referenced_artifacts/raw.raw_asr.json",
            "output/project_lens/10-09-2026/Standup_Amit/copilot_attempts.jsonl",
            "storage/graph.sqlite-wal",
        ]
        part = partition_worktree_dirty_files(sample_paths)
        self.assertEqual(len(part["authored_code_files"]), 2)
        self.assertIn("dual_pipeline/evaluator.py", part["authored_code_files"])
        self.assertIn("tests/test_evaluator.py", part["authored_code_files"])

        self.assertEqual(len(part["deliverable_files"]), 2)
        self.assertIn("output/project_lens/10-09-2026/Standup_Amit/01_transcript.md", part["deliverable_files"])

        self.assertEqual(len(part["ephemeral_attempt_files"]), 4)
        self.assertIn(
            "output/project_lens/10-09-2026/Standup_Amit/attempts/attempt_1/01.ckpt.md",
            part["ephemeral_attempt_files"],
        )
        self.assertIn("storage/graph.sqlite-wal", part["ephemeral_attempt_files"])

    def test_worktree_dirty_inspection_ignores_ephemeral_attempts(self) -> None:
        """Verify _inspect_worktree_dirty ignores ephemeral attempt files when ignore_review_artifacts is True."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            subprocess.run(["git", "init"], cwd=td_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=td_path, check=True)
            subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=td_path, check=True)

            # Create an initial commit
            readme = td_path / "README.md"
            readme.write_text("# Test Repo\n")
            subprocess.run(["git", "add", "README.md"], cwd=td_path, check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=td_path, check=True)

            # Add only ephemeral attempt files
            attempts_dir = td_path / "output" / "project_lens" / "10-09-2026" / "Standup" / "attempts" / "attempt_1"
            attempts_dir.mkdir(parents=True, exist_ok=True)
            (attempts_dir / "output_1.ckpt.md").write_text("checkpoint content")
            (attempts_dir / "qa_gates_report.json").write_text("{}")
            copilot_f = td_path / "output" / "project_lens" / "10-09-2026" / "Standup" / "copilot_attempts.jsonl"
            copilot_f.write_text("{}\n")

            res_ignored = _inspect_worktree_dirty(td_path, ignore_review_artifacts=True)
            self.assertFalse(res_ignored.dirty, "Worktree with only ephemeral attempt files should be clean")
            self.assertEqual(res_ignored.authored_files_count, 0)
            self.assertEqual(len(res_ignored.ephemeral_attempt_files), 3)

            # Now add one real code file and one deliverable
            code_f = td_path / "dual_pipeline" / "evaluator.py"
            code_f.parent.mkdir(parents=True, exist_ok=True)
            code_f.write_text("print('code')\n")
            deliv_f = td_path / "output" / "project_lens" / "10-09-2026" / "Standup" / "01_transcript.md"
            deliv_f.write_text("# Transcript\n")

            res_with_code = _inspect_worktree_dirty(td_path, ignore_review_artifacts=True)
            self.assertTrue(res_with_code.dirty)
            self.assertEqual(res_with_code.authored_files_count, 2, "Only code and deliverable should be counted")
            self.assertEqual(len(res_with_code.authored_code_files), 1)
            self.assertEqual(len(res_with_code.deliverable_files), 1)
            self.assertEqual(len(res_with_code.ephemeral_attempt_files), 3)

    def test_evaluate_worktree_meeting_supersession_on_main(self) -> None:
        """Verify evaluate_worktree_meeting_supersession succeeds even when stale code files exist."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create a PACKAGE_READY canonical deliverable on main
            canon_dir = root / "output" / "project_lens" / "10-13-2026" / "Sync_Mandy_PartnerAdvisory"
            canon_dir.mkdir(parents=True, exist_ok=True)
            (canon_dir / "final_release_decision.json").write_text(json.dumps({
                "terminal_outcome": "PACKAGE_READY",
                "date": "10-13-2026",
                "project": "project_lens",
                "meeting_name": "Sync_Mandy_PartnerAdvisory",
            }))

            # Create a simulated worktree with matching deliverables AND dirty dual_pipeline code
            wt_path = root / ".worktrees" / "live_lens_meeting"
            wt_meeting_dir = wt_path / "output" / "project_lens" / "10-13-2026" / "Sync_Mandy_PartnerAdvisory"
            wt_meeting_dir.mkdir(parents=True, exist_ok=True)
            (wt_meeting_dir / "01_transcript.md").write_text("transcript")
            (wt_meeting_dir / "metadata.json").write_text(json.dumps({
                "date": "10-13-2026",
                "project": "project_lens",
                "meeting_name": "Sync_Mandy_PartnerAdvisory",
            }))
            # Add dirty code files
            code_file = wt_path / "dual_pipeline" / "evaluator.py"
            code_file.parent.mkdir(parents=True, exist_ok=True)
            code_file.write_text("dirty healer scratch code")

            residual_files = [
                "output/project_lens/10-13-2026/Sync_Mandy_PartnerAdvisory/01_transcript.md",
                "output/project_lens/10-13-2026/Sync_Mandy_PartnerAdvisory/metadata.json",
                "dual_pipeline/evaluator.py",
            ]
            sup = evaluate_worktree_meeting_supersession(wt_path, residual_files, root)
            self.assertIsNotNone(sup)
            self.assertTrue(sup.get("is_superseded"))
            self.assertIn("PACKAGE_READY deliverable on main", sup.get("worktree_state", ""))
            self.assertIn("stale in-flight scratch edit(s) superseded", sup.get("worktree_state", ""))

    def test_evaluate_worktree_meeting_supersession_sibling_worktree(self) -> None:
        """Verify raw preview worktree is marked superseded when sibling worktree holds completed triad."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create a sibling worktree with completed triad
            sib_path = root / ".worktrees" / "live_project_lens_10_09_2026_standup_amit_nozim"
            sib_dir = sib_path / "output" / "project_lens" / "10-09-2026" / "Standup_Amit"
            sib_dir.mkdir(parents=True, exist_ok=True)
            (sib_dir / "final_release_decision.json").write_text(json.dumps({
                "terminal_outcome": "PACKAGE_READY",
                "date": "10-09-2026",
                "project": "project_lens",
                "meeting_name": "Standup_Amit",
            }))

            # Current worktree with only raw preview
            wt_path = root / ".worktrees" / "live_standup_amit"
            raw_dir = wt_path / "output" / "project_lens" / "10-09-2026" / "Standup_Amit"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "01_10-09-2026_Standup_Amit_live_transcript.md").write_text("raw live mic content")

            residual_files = [
                "output/project_lens/10-09-2026/Standup_Amit/01_10-09-2026_Standup_Amit_live_transcript.md"
            ]
            sup = evaluate_worktree_meeting_supersession(wt_path, residual_files, root)
            self.assertIsNotNone(sup)
            self.assertTrue(sup.get("is_superseded"))
            self.assertIn("sibling worktree", sup.get("worktree_state", ""))

    def test_live_worktree_dispositions_wave1(self) -> None:
        """Verify stage gates & dispositions for Wave 1 target branches."""
        # 1. Superseded meeting on main
        x_lens = {
            "branch": "feat/live-lens-meeting",
            "tier": "D.1",
            "next_action": "[PRUNE] Superseded by PACKAGE_READY deliverable on main",
            "ahead": 0,
            "behind": 402,
            "dirty": True,
            "dirty_files_count": 14,
        }
        gates = evaluate_stage_gates_and_disposition(x_lens)
        self.assertEqual(gates[3], "Decommission (No Value)")

        # 2. Raw preview superseded by sibling worktree
        x_standup_amit = {
            "branch": "feat/live-standup_amit",
            "tier": "D.2",
            "next_action": "[PRUNE] completed triad in sibling worktree",
            "ahead": 0,
            "behind": 962,
            "dirty": True,
            "dirty_files_count": 5,
        }
        gates = evaluate_stage_gates_and_disposition(x_standup_amit)
        self.assertEqual(gates[3], "Decommission (No Value)")

        # 3. Wave plan debate with absorbed hunks
        x_wave_debate = {
            "branch": "feat/wave-plan-word-limit-debate",
            "tier": "D.3",
            "next_action": "[PRUNE] All uncommitted authored files match target tip blobs or are absorbed",
            "ahead": 0,
            "behind": 169,
            "dirty": False,
        }
        gates = evaluate_stage_gates_and_disposition(x_wave_debate)
        self.assertEqual(gates[3], "Decommission (No Value)")

        # 4. Worktree with uncommitted deliverables and stale code edits
        x_salvage = {
            "branch": "feat/live-project-lens-10-09-2026-standup-amit-nozim",
            "action": "SALVAGE_DELIVERABLES",
            "next_action": "[SALVAGE] Extract 12 deliverable file(s) into ingest branch",
            "ahead": 0,
            "behind": 355,
            "dirty": True,
            "deliverable_files_count": 12,
            "has_uncommitted_deliverables": True,
            "has_stale_code_edits": True,
        }
        gates = evaluate_stage_gates_and_disposition(x_salvage)
        self.assertEqual(gates[2], "Y (Partial)")
        self.assertEqual(gates[3], "Salvage Subset & Archive")
        self.assertIn("Extract 12 deliverable file(s)", gates[4])


if __name__ == "__main__":
    unittest.main()
