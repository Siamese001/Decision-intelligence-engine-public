"""
Adversarial and Edge-Case Test for PR Queue Obsolete Branch Retirement & Inception Precedence.

Covers:
1. Boundary payload: Corrupt/empty registry, missing files, null candidate overrides.
2. Invariant bypass attempt: Inception immunity attempting to override an explicit tombstone.
3. Concurrency & state: Dirty worktree with 0 commits ahead flagged as tombstone - retired to D.1, files preserved.
4. Exact production regression: chore/pipeline-assurance-20261006 pinned as tombstone while having 0 commits ahead and uncommitted files.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.queue_reconcile import tier_from_disposition
from tools.pr_priority_engine import derive_priority
from tools.pr_topology_core.disposition import (
    BranchDisposition,
    _extract_registry_tombstones,
    compute_dispositions,
)
from tools.pr_topology_core.matrix import BranchDiff, TopologyMatrix


class TestPRQueueObsoleteBranchAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=self.root, check=True, capture_output=True)
        (self.root / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: init"], cwd=self.root, check=True, capture_output=True)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        self.reg_path = self.root / "config" / "pr_priority_registry.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_adversarial_corrupt_or_empty_registry_fails_closed(self):
        """A corrupt or empty registry falls back safely without unhandled exception."""
        # 1. Non-existent registry
        t1 = _extract_registry_tombstones(self.root, overrides=None)
        self.assertEqual(t1, {})

        # 2. Corrupt JSON registry
        self.reg_path.write_text("{invalid json", encoding="utf-8")
        t2 = _extract_registry_tombstones(self.root, overrides=None)
        self.assertEqual(t2, {})

        # 3. Empty JSON registry
        self.reg_path.write_text("{}", encoding="utf-8")
        t3 = _extract_registry_tombstones(self.root, overrides=None)
        self.assertEqual(t3, {})

    def test_adversarial_inception_immunity_cannot_bypass_tombstone(self):
        """Inception immunity must never override an explicit operator tombstone record."""
        branch = "chore/pipeline-assurance-20261006"
        tomb_data = {
            branch: {
                "tier": "TOMBSTONE",
                "reason": "Superseded by docs/scheduled-task-core4-hardening-plan",
                "owner": "operator",
                "expires_at": "2026-12-01",
            }
        }
        self.reg_path.write_text(json.dumps({"tiers": {"TOMBSTONE": {"branches": tomb_data}}, "overrides": tomb_data}))

        matrix = TopologyMatrix(
            branches=[branch],
            base_branch="main",
            branch_diffs={
                branch: BranchDiff(
                    branch=branch,
                    ahead=0,
                    behind=5,
                    files=["reports/pipeline_assurance/test.json"],
                    file_count=1,
                )
            },
            superseded_candidates=[],
        )

        # Worktree is active, dirty, age < 48h (inception)
        wt_info = {
            branch: {
                "branch": branch,
                "path": str(self.root),
                "dirty": True,
                "created_at_utc": "2026-10-06T12:00:00Z",
            }
        }

        dispositions = compute_dispositions(
            matrix,
            worktree_info=wt_info,
            root=self.root,
            lifecycle_overrides=tomb_data,
        )

        self.assertIn(branch, dispositions)
        self.assertEqual(dispositions[branch].action, "TOMBSTONE")
        self.assertNotIn("KEEP", dispositions[branch].action)

        # Tier must be reconciled to D.1, not P3.x
        tier = tier_from_disposition("P3.1", dispositions[branch].action)
        self.assertEqual(tier, "D.1")

    def test_exact_production_regression_pipeline_assurance_tombstone(self):
        """Exact production regression: chore/pipeline-assurance-20261006 is classified as D.1 (TOMBSTONE)."""
        branch = "chore/pipeline-assurance-20261006"
        overrides = {
            branch: {
                "tier": "TOMBSTONE",
                "reason": "Superseded by docs/scheduled-task-core4-hardening-plan",
                "owner": "operator",
                "scope": "Superseded Worktree",
                "expires_at": "2026-12-01",
                "superseded_by": "docs/scheduled-task-core4-hardening-plan",
            }
        }

        # Derive priority with ahead=0 and dirty worktree
        meta = derive_priority(
            branch,
            [],
            overrides=overrides,
            ahead=0,
            behind=5,
            clean_merge=True,
            worktree={"branch": branch, "dirty": True, "path": str(self.root)},
            repo_root=self.root,
            allow_synthetic=True,
        )

        self.assertEqual(meta["tier"], "D.1")
        self.assertEqual(meta["base_tier"], "TOMBSTONE")
        self.assertEqual(meta["rule_id"], "R0.tombstone")
        self.assertFalse(meta["tier"].startswith("P"))


if __name__ == "__main__":
    unittest.main()
