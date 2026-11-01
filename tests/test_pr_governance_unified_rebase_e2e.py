"""End-to-End and adversarial tests for Wave 1: Unified PR Rebase Engine, Promotion Aliases, and Task SSOT.

Covers:
1. Canonical rebase_branch_onto_main() across up-to-date, disjoint fast-forward, and conflicting branches.
2. Rebase orchestrator delegation to rebase_branch_onto_main() without duplicate git rebase wrappers.
3. Promotion aliases (cmd_promote, cmd_submit, promote_branch) routing through cmd_merge.
4. Dynamic loading of TASK_SIDECAR_MAP from config/scheduled_tasks/task_registry.json with zero duplicate dicts.
5. Lazy test resolution in tools/run_ci_gates.py (<50ms import time).
"""
from __future__ import annotations

import argparse
import ast
import importlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.promotion import cmd_promote, cmd_submit, promote_branch
from tools.pr_governance_core.sync import rebase_branch_onto_main
from tools.rebase_orchestrator import rebase_branch_serialized
from tools.scheduled_tasks_core.ssot_pointers import (
    CONSOLIDATED_TASK_ALIASES,
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    sanitize_reconciliation_manifest,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )


class TestPRGovernanceUnifiedRebaseE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="unified_rebase_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        _git(["init", "-b", "main"], self.repo_root)
        _git(["config", "user.name", "Wave 1 Tester"], self.repo_root)
        _git(["config", "user.email", "wave1@example.com"], self.repo_root)
        _git(["config", "commit.gpgsign", "false"], self.repo_root)

        (self.repo_root / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
        _git(["add", "README.md"], self.repo_root)
        _git(["commit", "-m", "chore: initial commit"], self.repo_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_canonical_rebase_disjoint_up_to_date_and_conflict(self) -> None:
        """Verifies single rebase_branch_onto_main() primitive across all 3 branch states."""
        # 1. Disjoint feature branch
        _git(["checkout", "-b", "feat/disjoint-a"], self.repo_root)
        (self.repo_root / "a.txt").write_text("feature a\n", encoding="utf-8")
        _git(["add", "a.txt"], self.repo_root)
        _git(["commit", "-m", "feat: add a"], self.repo_root)

        # 2. Conflicting feature branch
        _git(["checkout", "main"], self.repo_root)
        (self.repo_root / "shared.txt").write_text("base line\n", encoding="utf-8")
        _git(["add", "shared.txt"], self.repo_root)
        _git(["commit", "-m", "chore: add shared"], self.repo_root)

        _git(["checkout", "-b", "feat/conflict-b"], self.repo_root)
        (self.repo_root / "shared.txt").write_text("branch b line\n", encoding="utf-8")
        _git(["add", "shared.txt"], self.repo_root)
        _git(["commit", "-m", "feat: modify shared on b"], self.repo_root)

        # Advance main on shared.txt
        _git(["checkout", "main"], self.repo_root)
        (self.repo_root / "shared.txt").write_text("main line\n", encoding="utf-8")
        _git(["add", "shared.txt"], self.repo_root)
        _git(["commit", "-m", "feat: modify shared on main"], self.repo_root)

        # Rebase disjoint branch onto main
        res_disjoint = rebase_branch_onto_main(
            branch="feat/disjoint-a",
            base_branch="main",
            repo_root=self.repo_root,
            work_dir=self.repo_root,
            use_lock=True,
        )
        self.assertEqual(res_disjoint.status, "SUCCESS")
        self.assertEqual(res_disjoint.commits_rebased, 1)
        self.assertNotEqual(res_disjoint.head_sha_before, res_disjoint.head_sha_after)

        # Second call on already-rebased branch short-circuits as UP_TO_DATE
        res_up_to_date = rebase_branch_onto_main(
            branch="feat/disjoint-a",
            base_branch="main",
            repo_root=self.repo_root,
            work_dir=self.repo_root,
            use_lock=False,
        )
        self.assertEqual(res_up_to_date.status, "UP_TO_DATE")
        self.assertEqual(res_up_to_date.commits_rebased, 0)

        # Conflicting branch aborts cleanly and reports conflict_files
        res_conflict = rebase_branch_onto_main(
            branch="feat/conflict-b",
            base_branch="main",
            repo_root=self.repo_root,
            work_dir=self.repo_root,
            use_lock=False,
        )
        self.assertEqual(res_conflict.status, "CONFLICT")
        self.assertIn("shared.txt", res_conflict.conflict_files)
        status_out = _git(["status", "--porcelain"], self.repo_root).stdout.strip()
        self.assertEqual(status_out, "")

        # Invalid branch returns ERROR (boundary payload)
        res_err = rebase_branch_onto_main(
            branch="feat/nonexistent-branch",
            base_branch="main",
            repo_root=self.repo_root,
            work_dir=self.repo_root,
        )
        self.assertEqual(res_err.status, "ERROR")

    def test_rebase_orchestrator_delegates_to_single_engine(self) -> None:
        """Verifies rebase_orchestrator has no _rebase_branch_unlocked and delegates to rebase_branch_onto_main."""
        import tools.rebase_orchestrator as ro

        self.assertFalse(hasattr(ro, "_rebase_branch_unlocked"))
        _git(["checkout", "-b", "feat/orch-delegate"], self.repo_root)
        (self.repo_root / "orch.txt").write_text("orch\n", encoding="utf-8")
        _git(["add", "orch.txt"], self.repo_root)
        _git(["commit", "-m", "feat: orch file"], self.repo_root)

        res = rebase_branch_serialized(
            branch="feat/orch-delegate",
            base_branch="main",
            repo_root=self.repo_root,
        )
        self.assertEqual(res.status, "UP_TO_DATE")

    def test_promotion_verbs_are_thin_aliases_to_cmd_merge(self) -> None:
        """Verifies promote_branch, cmd_promote, and cmd_submit delegate directly to lifecycle.cmd_merge."""
        with patch("tools.pr_governance_core.lifecycle.cmd_merge", return_value=0) as mock_merge:
            args = argparse.Namespace(branch="feat/test", title="feat: test title")
            self.assertEqual(cmd_promote(args), 0)
            self.assertEqual(cmd_submit(args), 0)
            self.assertEqual(promote_branch("feat/test", "feat: test title", args=args), 0)
            self.assertEqual(mock_merge.call_count, 3)

    def test_ssot_pointers_has_zero_duplicate_task_dicts(self) -> None:
        """Verifies ssot_pointers.py loads TASK_SIDECAR_MAP from task_registry.json without hardcoded dicts."""
        ssot_path = ROOT / "tools" / "task_restoration_core" / "ssot_pointers.py"
        tree = ast.parse(ssot_path.read_text(encoding="utf-8"))
        top_level_dict_literals = [
            node for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(getattr(node, "value", None), ast.Dict)
        ]
        self.assertEqual(len(top_level_dict_literals), 0)
        self.assertEqual(len(TASK_SIDECAR_MAP), 17)
        self.assertEqual(len(CONSOLIDATED_TASK_ALIASES), 7)

        prompt = generate_ssot_pointer_prompt("pr_queue_observer", ROOT)
        self.assertIn("automations/prompts/pr_queue_observer_agent.md", prompt)

        raw_manifest = {
            "generated_at": "2026-10-28T00:00:00Z",
            "tasks": [
                {
                    "task_id": "pr_queue_observer",
                    "canonical_prompt": "automations/prompts/pr_queue_observer_agent.md",
                    "ssot_link_uri": "file:///stale/host/path.md",
                    "dereferencer_prompt": "stale",
                }
            ],
        }
        cleaned = sanitize_reconciliation_manifest(raw_manifest, ROOT)
        self.assertNotIn("generated_at", cleaned)
        self.assertTrue(cleaned["tasks"][0]["ssot_link_uri"].endswith("automations/prompts/pr_queue_observer_agent.md"))

    def test_run_ci_gates_lazy_import_under_50ms(self) -> None:
        """Verifies importing tools.run_ci_gates defers resolve_all_tests() and completes in <50ms."""
        sys.modules.pop("tools.run_ci_gates", None)
        with patch("tools.test_discovery_core.resolve_all_tests") as mock_resolve:
            t0 = time.monotonic()
            mod = importlib.import_module("tools.run_ci_gates")
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.assertEqual(mock_resolve.call_count, 0)
            self.assertLess(elapsed_ms, 50.0)
            self.assertIsNone(mod._RESOLVED_TESTS_CACHE)


if __name__ == "__main__":
    unittest.main()
