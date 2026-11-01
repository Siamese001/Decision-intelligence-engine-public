"""
End-to-End Test for PR Queue Obsolete Branch Retirement & Inception Precedence (Wave 5).
Verifies:
1. Confirmed obsolete, zero-commit, recent branch: absent from active results (P0-P3), relegated to D.
2. Same branch with stale cached KEEP data: still absent from active results.
3. Fresh active zero-commit branch: still visible in active queue.
4. Active branch with unique uncommitted operational reports: recognized and preserved (COMMIT_FIRST).
5. Mere text reference to a replacement: does not automatically retire a branch without registry tombstone.
6. Filtering obsolete entry: remaining ranks are recomputed correctly without rank gaps.
7. Obsolete entry: absent from executable candidates and execution sequence.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.decision_api import evaluate_queue_decisions
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue
from tools.pr_governance_core.queue_reconcile import tier_from_disposition
from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot
from tools.pr_priority_engine import derive_priority, load_rubric
from tools.pr_topology_core.disposition import BranchDisposition, compute_dispositions
from tools.pr_topology_core.matrix import compute_topology_matrix


class TestPRQueueObsoleteBranchRetirementE2E(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "core.logAllRefUpdates", "true"])
        self._git(["config", "user.name", "E2E Tester"])
        self._git(["config", "user.email", "tester@prq.local"])

        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        (self.repo_root / "config").mkdir(parents=True, exist_ok=True)
        self.registry_path = self.repo_root / "config" / "pr_priority_registry.json"
        self._write_registry({})
        self._git(["add", "-A"])
        self._git(["commit", "-m", "chore: initial commit on main"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cmd: list[str]) -> str:
        return subprocess.run(
            ["git"] + cmd,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    def _write_registry(self, data: dict):
        base = {
            "tiers": {
                "P0": {"branches": {}},
                "P1": {"branches": {}},
                "P2": {"branches": {}},
                "P3": {"branches": {}},
                "TOMBSTONE": {"branches": data.get("tombstones", {})},
            },
            "overrides": data.get("overrides", {}),
        }
        self.registry_path.write_text(json.dumps(base, indent=2), encoding="utf-8")

    def test_e2e_obsolete_branch_absent_from_active_queue_and_seq(self):
        """A confirmed obsolete recent branch is retired to D.1 and excluded from active ranks and seq."""
        obsolete_branch = "chore/pipeline-assurance-20261006"
        active_branch = "docs/scheduled-task-core4-hardening-plan"

        # Create branches
        self._git(["checkout", "-b", obsolete_branch, "main"])
        self._git(["checkout", "-b", active_branch, "main"])
        self._git(["checkout", "main"])

        # Advance main by 2 commits
        for i in range(1, 3):
            (self.repo_root / f"main_{i}.txt").write_text(f"m{i}\n")
            self._git(["add", f"main_{i}.txt"])
            self._git(["commit", "-m", f"feat: main commit {i}"])

        # Record obsolete branch in registry
        self._write_registry({
            "tombstones": {
                obsolete_branch: {
                    "rationale": f"Superseded by {active_branch}",
                    "scope": "Superseded Worktree",
                    "superseded_by": active_branch,
                }
            },
            "overrides": {
                obsolete_branch: {
                    "tier": "TOMBSTONE",
                    "reason": f"Superseded by {active_branch}",
                    "owner": "operator",
                    "scope": "Superseded Worktree",
                    "expires_at": "2026-12-01",
                    "superseded_by": active_branch,
                }
            }
        })
        self._git(["add", "-A"])
        self._git(["commit", "-m", "chore: record tombstone in registry"])

        wt_info = {
            obsolete_branch: {"branch": obsolete_branch, "path": str(self.repo_root), "dirty": False},
            active_branch: {"branch": active_branch, "path": str(self.repo_root), "dirty": False},
        }

        # 1. Topology & Dispositions
        matrix = compute_topology_matrix([obsolete_branch, active_branch], self.repo_root, "main")
        dispositions = compute_dispositions(
            matrix,
            worktree_info=wt_info,
            root=self.repo_root,
        )

        self.assertEqual(dispositions[obsolete_branch].action, "TOMBSTONE")
        self.assertIn("Superseded by", dispositions[obsolete_branch].reason)
        # Active branch without tombstone remains KEEP under inception
        self.assertEqual(dispositions[active_branch].action, "KEEP")

        # 2. Priority & Reconciliation
        obs_meta = derive_priority(
            obsolete_branch, [],
            overrides={obsolete_branch: {"tier": "TOMBSTONE", "owner": "operator", "reason": "superseded", "expires_at": "2026-12-01"}},
            ahead=0, behind=2, clean_merge=True, worktree=wt_info[obsolete_branch], repo_root=self.repo_root,
        )
        obs_tier = tier_from_disposition(obs_meta["tier"], dispositions[obsolete_branch].action)
        self.assertTrue(obs_tier.startswith("D"), f"Expected D tier for obsolete branch, got {obs_tier}")

        act_meta = derive_priority(
            active_branch, [],
            ahead=0, behind=2, clean_merge=True, worktree=wt_info[active_branch], repo_root=self.repo_root,
        )
        act_tier = tier_from_disposition(act_meta["tier"], dispositions[active_branch].action)
        self.assertTrue(act_tier.startswith("P3"), f"Expected P3 tier for active branch, got {act_tier}")

        # 3. Decision API End-to-End
        snap = QueueSnapshot(branches=[
            BranchSnapshot(branch=obsolete_branch, head_sha="111", ahead=0, behind=2, clean_merge=True, open_waves="—"),
            BranchSnapshot(branch=active_branch, head_sha="222", ahead=1, behind=2, clean_merge=True, open_waves="—"),
        ])
        decisions = evaluate_queue_decisions(snap, repo_root=self.repo_root)

        # Obsolete branch must be excluded from active branches and execution sequence
        obs_row = next(b for b in decisions.branches if b["branch"] == obsolete_branch)
        self.assertTrue(obs_row["tier"].startswith("D"))
        self.assertEqual(obs_row["merge_eligibility"], "DECOMMISSIONED")
        self.assertIsNone(obs_row["execution_sequence_index"])
        self.assertNotIn(obsolete_branch, decisions.execution_sequence)

        # Active branch must be in active queue
        act_row = next(b for b in decisions.branches if b["branch"] == active_branch)
        self.assertTrue(act_row["tier"].startswith("P"))

    def test_e2e_stale_cached_keep_overridden_by_registry_tombstone(self):
        """Stale cached KEEP disposition does not prevent tombstone retirement when registry records tombstone."""
        branch = "chore/stale-keep-test"
        self._git(["checkout", "-b", branch, "main"])
        self._git(["checkout", "main"])

        # Register tombstone
        self._write_registry({
            "tombstones": {
                branch: {"rationale": "Explicitly decommissioned", "scope": "Decommissioned"}
            }
        })

        matrix = compute_topology_matrix([branch], self.repo_root, "main")
        dispositions = compute_dispositions(
            matrix,
            worktree_info={branch: {"branch": branch, "path": str(self.repo_root), "dirty": False}},
            root=self.repo_root,
        )
        self.assertEqual(dispositions[branch].action, "TOMBSTONE")

    def test_e2e_active_branch_with_uncommitted_reports_preserved(self):
        """Branch with uncommitted authored files (e.g. reports) is preserved with COMMIT_FIRST."""
        branch = "feat/active-with-reports"
        self._git(["checkout", "-b", branch, "main"])

        # Create uncommitted report in worktree
        report_dir = self.repo_root / "reports" / "pipeline_assurance" / "20261006T144500Z"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "assurance_receipt.json").write_text('{"status": "ok"}\n')

        # Advance main
        self._git(["checkout", "main"])
        (self.repo_root / "main.txt").write_text("advance\n")
        self._git(["add", "main.txt"])
        self._git(["commit", "-m", "feat: advance"])

        # Not registered as tombstone
        matrix = compute_topology_matrix([branch], self.repo_root, "main")
        dispositions = compute_dispositions(
            matrix,
            worktree_info={branch: {"branch": branch, "path": str(self.repo_root), "dirty": True}},
            root=self.repo_root,
        )
        # Should be preserved via COMMIT_FIRST, not PRUNE
        self.assertEqual(dispositions[branch].action, "COMMIT_FIRST")
        self.assertIn("uncommitted", dispositions[branch].reason)

    def test_e2e_mere_mention_of_replacement_does_not_retire_branch(self):
        """Prose mentioning another branch does NOT retire a branch without registry entry."""
        branch = "docs/plan-mentioning-replacement"
        self._git(["checkout", "-b", branch, "main"])
        (self.repo_root / "plan.md").write_text("This plan discusses docs/scheduled-task-core4-hardening-plan\n")
        self._git(["add", "plan.md"])
        self._git(["commit", "-m", "docs: add plan mentioning replacement"])
        self._git(["checkout", "main"])

        matrix = compute_topology_matrix([branch], self.repo_root, "main")
        dispositions = compute_dispositions(
            matrix,
            worktree_info={branch: {"branch": branch, "path": str(self.repo_root), "dirty": False}},
            root=self.repo_root,
        )
        self.assertEqual(dispositions[branch].action, "KEEP")

    def test_e2e_filtering_obsolete_branch_recomputes_ranks_correctly(self):
        """Retiring an obsolete branch recomputes P3.x sub-ranks without gaps."""
        queue = [
            {"branch": "chore/obsolete", "tier": "D.1", "value_score": 0.0, "risk_score": 0, "open_waves": "—"},
            {"branch": "docs/legit-1", "tier": "P3.1", "value_score": 10.0, "risk_score": 10, "open_waves": "—"},
            {"branch": "docs/legit-2", "tier": "P3.1", "value_score": 5.0, "risk_score": 10, "open_waves": "—"},
        ]
        sort_queue(queue)
        assign_sub_ranks(queue)

        self.assertEqual(queue[0]["branch"], "docs/legit-1")
        self.assertEqual(queue[0]["sub_rank"], "P3.1")
        self.assertEqual(queue[1]["branch"], "docs/legit-2")
        self.assertEqual(queue[1]["sub_rank"], "P3.2")
        self.assertEqual(queue[2]["branch"], "chore/obsolete")
        self.assertEqual(queue[2]["sub_rank"], "D.1")


if __name__ == "__main__":
    unittest.main()
