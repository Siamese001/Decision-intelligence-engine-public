"""
tests/test_worktree_coverage_agent_prompt_e2e.py

End-to-End verification of the Worktree Coverage Agent prompt and runner logic:
1. Validates prompt file structure, mode semantics, and safety invariants.
2. Simulates scheduled mode: detects orphan deliverables and yields HITL_REQUIRED.
3. Simulates scheduled mode: clean repository yields COMPLETE.
4. Simulates interactive mode: exercises token-bound safe pruning workflow.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.audit_worktree_hygiene_core.pruner import (  # noqa: E402
    cleanup_orphaned_worktree_directories,
)
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory  # noqa: E402


class TestWorktreeCoverageAgentPromptE2E(unittest.TestCase):
    """End-to-end tests for the worktree coverage agent prompt and execution modes."""

    def setUp(self) -> None:
        self.prompt_path = REPO_ROOT / "automations" / "prompts" / "worktree_coverage_agent.md"
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Agent Tester"], cwd=self.repo, check=True, capture_output=True)

        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prompt_file_structure_and_invariants(self) -> None:
        """Prompt file exists, specifies scheduled/interactive modes and core safety invariants."""
        self.assertTrue(self.prompt_path.exists(), "Prompt file does not exist")
        content = self.prompt_path.read_text(encoding="utf-8")
        lines = content.strip().splitlines()

        self.assertEqual(lines[0].strip(), "MODE: scheduled", "Prompt must default to MODE: scheduled")
        self.assertIn("MODE: interactive", content)
        self.assertIn("Zero Data Loss Invariant", content)
        self.assertIn("Confirmation-Token Bound Pruning", content)
        self.assertIn("Atomic HITL Invariant", content)
        self.assertIn("HITL_REQUIRED", content)
        self.assertIn("COMPLETE", content)
        self.assertIn("INV-WT-COVERAGE", content)

    def test_scheduled_mode_with_orphan_deliverables_requires_hitl(self) -> None:
        """Scheduled mode simulation: when orphan deliverables exist, status is HITL_REQUIRED."""
        # Create an orphan directory with an uncommitted deliverable
        orphan = self.wt_dir / "live_uncommitted_meeting"
        deliv = orphan / "output" / "01_transcript.md"
        deliv.parent.mkdir(parents=True, exist_ok=True)
        deliv.write_text("# Meeting Transcript\nUncommitted facts", encoding="utf-8")

        inv = build_worktree_inventory(self.repo)
        self.assertGreater(inv["counts"]["orphan_directories"], 0)

        # Scheduled mode logic evaluation:
        has_orphan_deliverables = (
            inv["counts"]["orphan_deliverables"] > 0
            or any(wt.get("classification") == "ORPHAN_DELIVERABLES" for wt in inv.get("worktrees", []))
        )
        self.assertTrue(has_orphan_deliverables)

        # Terminal outcome determination for scheduled mode
        terminal_outcome = "HITL_REQUIRED" if has_orphan_deliverables else "COMPLETE"
        self.assertEqual(terminal_outcome, "HITL_REQUIRED")

        # Deliverables must NOT have been deleted
        self.assertTrue(deliv.exists(), "Deliverable must be preserved untouched")

    def test_scheduled_mode_clean_repo_yields_complete(self) -> None:
        """Scheduled mode simulation: clean repository with no orphan deliverables yields COMPLETE."""
        inv = build_worktree_inventory(self.repo)
        has_orphan_deliverables = (
            inv["counts"]["orphan_deliverables"] > 0
            or any(wt.get("classification") == "ORPHAN_DELIVERABLES" for wt in inv.get("worktrees", []))
        )
        self.assertFalse(has_orphan_deliverables)

        terminal_outcome = "HITL_REQUIRED" if has_orphan_deliverables else "COMPLETE"
        self.assertEqual(terminal_outcome, "COMPLETE")

    def test_interactive_mode_token_bound_pruning(self) -> None:
        """Interactive mode simulation: dry-run token is required to quarantine empty stubs, deliverables protected."""
        # Create an empty stub
        stub = self.wt_dir / "live_empty_stub"
        stub.mkdir(exist_ok=True)

        # 1. Unconfirmed execution runs in dry-run mode (stub is untouched)
        dry_run_res = cleanup_orphaned_worktree_directories(self.repo, confirm_token=None)
        self.assertTrue(any("dry-run" in r for r in dry_run_res))
        self.assertTrue(stub.exists(), "Stub must not be removed during dry-run")

        # 2. Confirmed execution quarantines the stub safely
        quarantined = cleanup_orphaned_worktree_directories(self.repo, confirm_token="CONFIRMED_STRICT_PRUNE")
        self.assertIn("live_empty_stub", quarantined)
        self.assertFalse(stub.exists(), "Stub should be moved to quarantine")

        # 3. Deliverables refusal invariant: orphan with deliverables is NEVER quarantined
        deliverable_dir = self.wt_dir / "live_vital_meeting"
        deliv_file = deliverable_dir / "output" / "01_transcript.md"
        deliv_file.parent.mkdir(parents=True, exist_ok=True)
        deliv_file.write_text("# Vital Notes\n", encoding="utf-8")

        refused = cleanup_orphaned_worktree_directories(self.repo, confirm_token="CONFIRMED_STRICT_PRUNE")
        self.assertNotIn("live_vital_meeting", refused)
        self.assertTrue(deliverable_dir.exists(), "Directory with deliverables must NOT be quarantined")
        self.assertTrue(deliv_file.exists(), "Deliverable file must remain intact")


if __name__ == "__main__":
    unittest.main()
