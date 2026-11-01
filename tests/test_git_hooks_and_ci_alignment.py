"""Integration test suite for Git hooks, local gate alignment, and automation governance.

Covers Wave 5 acceptance criteria:
1. Hook installation succeeds idempotently in fresh and existing worktrees with clear remediation on failure.
2. Pre-commit and pre-push verify readiness without timing out or executing redundant full test runs.
3. Local gates execute standard check commands cleanly.
4. Automation prompts use current authority references and cannot bypass required gates.
5. verify_main_readiness --dry-run simulates full 30-gate pipeline cleanly.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHOOKS_DIR = REPO_ROOT / ".githooks"
INSTALLER_SH = REPO_ROOT / "tools" / "install_repository_hooks.sh"
INSTALLER_CMD = REPO_ROOT / "tools" / "install_repository_hooks.cmd"


class TestGitHooksAndCiAlignment(unittest.TestCase):
    """Test suite for Git hooks installation, enforcement, and CI alignment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Hook Installation Idempotence & Error Remediation
    # -------------------------------------------------------------------------

    def test_install_repository_hooks_sh_idempotent(self) -> None:
        """install_repository_hooks.sh installs .githooks and is idempotent."""
        if not INSTALLER_SH.is_file():
            self.skipTest("install_repository_hooks.sh not found")

        # Initialize mock git repo
        repo_dir = self.test_dir / "mock_repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)

        # Copy .githooks and tools/install_repository_hooks.sh
        shutil.copytree(GITHOOKS_DIR, repo_dir / ".githooks")
        (repo_dir / "tools").mkdir()
        shutil.copy(INSTALLER_SH, repo_dir / "tools" / "install_repository_hooks.sh")

        installer = repo_dir / "tools" / "install_repository_hooks.sh"

        # First run: success
        res1 = subprocess.run([str(installer)], cwd=repo_dir, capture_output=True, text=True)
        self.assertEqual(res1.returncode, 0, f"First install failed: {res1.stderr}")
        self.assertIn("Repository hooks installed successfully", res1.stdout)

        # Verify git config
        cfg = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        self.assertEqual(cfg.stdout.strip(), ".githooks")

        # Second run: idempotent success
        res2 = subprocess.run([str(installer)], cwd=repo_dir, capture_output=True, text=True)
        self.assertEqual(res2.returncode, 0, f"Second install failed: {res2.stderr}")

    def test_install_repository_hooks_fails_when_required_hook_missing(self) -> None:
        """Installer must fail closed with remediation advice if a required hook is missing."""
        repo_dir = self.test_dir / "broken_repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)

        (repo_dir / ".githooks").mkdir()
        (repo_dir / "tools").mkdir()
        shutil.copy(INSTALLER_SH, repo_dir / "tools" / "install_repository_hooks.sh")

        installer = repo_dir / "tools" / "install_repository_hooks.sh"
        res = subprocess.run([str(installer)], cwd=repo_dir, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Required repository hook is missing", res.stderr)
        self.assertIn("Remediation", res.stderr)

    # -------------------------------------------------------------------------
    # 2. Pre-Commit Hook Fast Guards
    # -------------------------------------------------------------------------

    def test_pre_commit_script_contains_mandatory_fast_guards(self) -> None:
        """Pre-commit hook must include native guards for bare repo, direct-main, output drafts, and worktrees."""
        pre_commit_file = GITHOOKS_DIR / "pre-commit"
        self.assertTrue(pre_commit_file.is_file())
        content = pre_commit_file.read_text(encoding="utf-8")

        self.assertIn("core.bare", content)
        self.assertIn("Direct commits to '$CURRENT_BRANCH' are strictly prohibited", content)
        self.assertIn("Staging draft files in production output/ is prohibited", content)
        self.assertIn("Staging worktree files or gitlinks under .worktrees/ is strictly prohibited", content)
        self.assertIn("tools/pre_commit_orchestrator.py", content)

    # -------------------------------------------------------------------------
    # 3. Pre-Push Hook Dual Tier Fast/Full Mechanism
    # -------------------------------------------------------------------------

    def test_pre_push_hook_tier3_default_and_opt_in_full_audit(self) -> None:
        """Pre-push hook defaults to instant receipt verification and provides opt-in full audit."""
        pre_push_file = GITHOOKS_DIR / "pre-push"
        self.assertTrue(pre_push_file.is_file())
        content = pre_push_file.read_text(encoding="utf-8")

        self.assertIn("tools/verify_push_receipt.py", content)
        self.assertIn("NEE_FULL_PRE_PUSH_AUDIT", content)
        self.assertIn("tools/verify_main_readiness.py", content)

    # -------------------------------------------------------------------------
    # 4. verify_main_readiness Simulation Mode (--dry-run)
    # -------------------------------------------------------------------------

    def test_verify_main_readiness_dry_run_passes_all_thirty_gates(self) -> None:
        """verify_main_readiness --dry-run validates all 30 stage command definitions cleanly."""
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "verify_main_readiness.py"), "--dry-run"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Dry run failed: {res.stderr}\n{res.stdout}")
        self.assertRegex(res.stdout, r"Total Gates Defined: (30|31|32)")
        self.assertRegex(res.stdout, r"All (30|31|32) gate definitions and command structures verified successfully")

    # -------------------------------------------------------------------------
    # 5. Automation Prompts Authority Alignment
    # -------------------------------------------------------------------------

    def test_automation_prompts_reference_governed_policies(self) -> None:
        """Scheduled and automation agent prompts must not reference obsolete 15% margin for HITL."""
        prompts_dir = REPO_ROOT / "automations" / "prompts"
        self.assertTrue(prompts_dir.is_dir())

        for prompt_file in prompts_dir.glob("*.md"):
            text = prompt_file.read_text(encoding="utf-8")
            # Should not contain obsolete uncalibrated 15% ambiguity threshold
            self.assertNotIn(
                "margin <= 0.15",
                text,
                f"Obsolete threshold in {prompt_file.name}",
            )


if __name__ == "__main__":
    unittest.main()
