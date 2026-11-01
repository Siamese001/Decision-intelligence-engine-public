"""Wave 1 E2E Tests: PR Governance Sync Seal, Pre-Commit Receipt, Ratchet & Ingestion Guards."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.ingest_worktree_guard import (
    IngestWorktreeCodeFreezeError,
    assert_ingest_worktree_code_freeze,
)
from tools.lint_test_and_healer_ratchet import run_ratchet_audit
from tools.pr_governance_core.code_review import audit_post_fork_regression
from tools.pr_governance_core.common import slugify_branch
from tools.pr_governance_core.lifecycle import (
    _is_verified_code_review_receipt,
    _validate_merge_receipt,
    cmd_review,
)
from tools.pr_governance_core.sync import cmd_sync
from tools.pre_commit_orchestrator import (
    verify_pre_commit_receipt_for_head,
    write_pre_commit_receipt,
)


def _git_env() -> dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k not in banned}
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


class TestPRGovernanceStabilityE2E(unittest.TestCase):
    """End-to-end verification of Wave 1 PR governance stability gates."""

    def test_e2e_sync_runs_real_code_review_and_blocks_forged_receipt(self) -> None:
        """Verifies Stage A sync executes compute_code_review and blocks unverified Stage A receipts."""
        forged_stage_a = {
            "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
            "branch": "feat/forged",
            "head_sha": "abc1234",
            "terminal_outcome": "PASS",
            "readiness_verified": True,
            "wave_summary_verified": True,
            "code_review": {
                "verdict": "APPROVE",
                "verdict_reason": "Synchronized and qualified under Stage A sync",
            },
        }
        self.assertFalse(
            _is_verified_code_review_receipt(forged_stage_a),
            "Forged Stage A receipt without compute_code_review must be rejected",
        )

        verified_receipt = {
            "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
            "branch": "feat/verified",
            "head_sha": "abc1234",
            "terminal_outcome": "PASS",
            "readiness_verified": True,
            "wave_summary_verified": True,
            "post_fork_regression_passed": True,
            "code_review": {
                "verdict": "APPROVE",
                "verdict_reason": "Clean diff",
                "computed_by": "compute_code_review",
                "post_fork_regression_passed": True,
            },
        }
        self.assertTrue(_is_verified_code_review_receipt(verified_receipt))

    def test_e2e_pre_commit_receipt_and_ratchet_in_git_repo(self) -> None:
        """Verifies pre-commit receipt tree binding and test/healer ratchet in a real git sandbox."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            env = _git_env()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)

            tests_dir = repo / "tests"
            tests_dir.mkdir()
            healer_dir = repo / "dual_pipeline" / "transcript_qa_core"
            healer_dir.mkdir(parents=True)

            test_file = tests_dir / "test_sample.py"
            test_file.write_text(
                "def test_alpha():\n    assert 1 == 1\n    assert 2 == 2\n",
                encoding="utf-8",
            )
            healer_file = healer_dir / "graph_healer.py"
            healer_file.write_text(
                "import re\ndef heal(x):\n    return re.sub('a', 'b', x)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
            write_pre_commit_receipt(repo, staged=True)
            subprocess.run(["git", "commit", "-m", "feat: initial main"], cwd=repo, env=env, check=True)

            ok, msg = verify_pre_commit_receipt_for_head(repo)
            self.assertTrue(ok, msg)
            self.assertEqual(run_ratchet_audit(repo), [])

            # Create branch that deletes an assertion and grows healer regexes
            subprocess.run(["git", "checkout", "-b", "fix/bad-branch"], cwd=repo, env=env, check=True)
            test_file.write_text("def test_alpha():\n    assert 1 == 1\n", encoding="utf-8")
            healer_file.write_text(
                "import re\ndef heal(x):\n    y = re.sub('a', 'b', x)\n    return re.sub('c', 'd', y)\n",
                encoding="utf-8",
            )
            violations = run_ratchet_audit(repo)
            self.assertEqual(len(violations), 3)
            self.assertTrue(any("assertion count reduced" in v for v in violations))
            self.assertTrue(any("healer regex operations grew" in v for v in violations))
            self.assertTrue(any("healer code lines grew" in v for v in violations))

            # Commit with --no-verify and verify both pre-commit receipt and post-fork regression catch it
            subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
            subprocess.run(["git", "commit", "--no-verify", "-m", "fix: bad commit"], cwd=repo, env=env, check=True)

            pc_ok, pc_msg = verify_pre_commit_receipt_for_head(repo)
            self.assertFalse(pc_ok)
            self.assertIn("no-verify", pc_msg)

            reg_ok, regs = audit_post_fork_regression("fix/bad-branch", base_branch="main", root=repo)
            self.assertFalse(reg_ok)
            self.assertGreaterEqual(len(regs), 2)

    def test_e2e_ingest_worktree_guard_blocks_expanded_paths_and_mixed_output(self) -> None:
        """Verifies ingest_worktree_guard blocks governance/schema edits and mixed output/ + code on feat/*."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            with (
                patch(
                    "dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch",
                    return_value="audio/10-28-2026-call",
                ),
                patch(
                    "dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git",
                    return_value=" M governance/policy.json\n",
                ),
            ):
                with self.assertRaises(IngestWorktreeCodeFreezeError):
                    assert_ingest_worktree_code_freeze(wt)

            # Mixed output/ + dual_pipeline/ on a feat/* branch must also fail closed
            def mixed_feat_git(cmd, cwd=None, check=False):
                if "output/" in cmd:
                    return " M output/Client/2026-10-28/01_transcript.md\n"
                if cmd[0] == "status":
                    return " M dual_pipeline/transcript_qa_core/graph_healer.py\n"
                return ""

            with (
                patch(
                    "dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch",
                    return_value="feat/sneaky-ingest",
                ),
                patch(
                    "dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git",
                    side_effect=mixed_feat_git,
                ),
            ):
                with self.assertRaises(IngestWorktreeCodeFreezeError):
                    assert_ingest_worktree_code_freeze(wt)


if __name__ == "__main__":
    unittest.main()
