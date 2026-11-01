"""
End-to-End Test for PR Queue Inception Protection (Wave 3).
Verifies complete lifecycle of an in-flight inception branch:
1. Repository initialization and divergence.
2. Fresh branch creation with active worktree.
3. Base branch advancing past the inception branch base.
4. PR queue priority, disposition, and reconciliation evaluation.
5. Invariant enforcement: Tier preserved in domain (P3.x), Rebase Needed, Prune forbidden.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.queue_reconcile import tier_from_disposition
from tools.pr_topology_core.disposition import compute_dispositions
from tools.pr_topology_core.matrix import compute_topology_matrix


class TestPRQueueInceptionProtectionE2E(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "core.logAllRefUpdates", "true"])
        self._git(["config", "user.name", "E2E Inception Tester"])
        self._git(["config", "user.email", "e2e@inception.local"])

        (self.repo_root / "README.md").write_text("# Core Engine\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-m", "chore: initial commit on main"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cmd: list[str], cwd: Path | None = None) -> str:
        target_cwd = cwd or self.repo_root
        res = subprocess.run(
            ["git"] + cmd,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return res.stdout.strip()

    def test_pr_queue_inception_protection_full_lifecycle_e2e(self):
        """E2E lifecycle: fresh branch created, main advances, PR queue preserves tier and forbids prune."""
        # 1. Create fresh branch from main
        branch = "feat/aidlc-operational-efficiency-council"
        self._git(["checkout", "-b", branch, "main"])

        # 2. Advance main with 3 new commits
        self._git(["checkout", "main"])
        for i in range(1, 4):
            fpath = self.repo_root / f"main_file_{i}.py"
            fpath.write_text(f"# feature {i}\n", encoding="utf-8")
            self._git(["add", fpath.name])
            self._git(["commit", "-m", f"feat(core): advance main with commit {i}"])

        # 3. Simulate active worktree mapping
        worktrees_info = {
            branch: {
                "branch": branch,
                "clean": True,
                "path": str(self.repo_root),
                "is_active": True,
            }
        }

        # 4. Build topology matrix
        matrix = compute_topology_matrix(
            [branch],
            self.repo_root,
            "main",
        )

        diff = matrix.branch_diffs[branch]
        self.assertEqual(diff.ahead, 0, "Branch has 0 commits ahead")
        self.assertEqual(diff.behind, 3, "Branch is behind main by 3 commits")

        # 5. Evaluate candidate through full PR governance disposition and reconciliation
        dispositions = compute_dispositions(
            matrix,
            worktree_info=worktrees_info,
            waves_info={branch: {"open_waves": "?"}},
            root=self.repo_root,
        )

        self.assertIn(branch, dispositions)
        disp = dispositions[branch]

        # 6. Verify governance invariants
        self.assertEqual(disp.action, "KEEP")
        self.assertIn("behind 3 commits", disp.reason)
        self.assertIn("rebase onto main", disp.reason)
        self.assertIsNone(disp.superseded_by)

        tier = tier_from_disposition("P3.1", disp.action)
        self.assertEqual(tier, "P3.1", "Active inception branch must not be demoted to tier D.1")
        self.assertNotIn("PRUNE", disp.action)
        self.assertNotIn("superseded", disp.reason.lower())


if __name__ == "__main__":
    unittest.main()
