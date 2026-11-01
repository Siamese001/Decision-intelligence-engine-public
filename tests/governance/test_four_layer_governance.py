#!/usr/bin/env python3
"""
Unit and Integration Tests for the Four-Layer Governance Enforcement Architecture.

Validates:
  Layer 1: Agent Rules (.agents/rules/pr-governance.md & AGENTS.md)
  Layer 2: Pre-Turn & Pre-Tool Hooks (hooks.json PreToolUse firewalls)
  Layer 3: Post-Turn & Post-Tool Hooks (hooks.json PostToolUse inspectors)
  Layer 4: Native Git Lifecycle Hooks (.githooks/ & tools/verify_merge_receipt.py)
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.verify_merge_receipt import verify_merge_receipt

ROOT = Path(__file__).resolve().parents[2]
HOOKS_CONFIG = Path.home() / ".gemini" / "config" / "hooks.json"


class TestLayer1AgentRules(unittest.TestCase):
    """Layer 1: Cognitive Contract Governance Tests."""

    def test_pr_governance_rules_file_exists_and_contains_invariants(self) -> None:
        rules_path = ROOT / ".agents" / "rules" / "pr-governance.md"
        self.assertTrue(rules_path.exists(), "pr-governance.md must exist")
        content = rules_path.read_text(encoding="utf-8")

        self.assertIn("## Strict Worktree & PR Promotion Invariants", content)
        self.assertIn("1. **Root Workspace Checkout Ban**:", content)
        self.assertIn("2. **Zero Dirty-Dump Commits**:", content)
        self.assertIn("3. **Indivisible Meeting Triad Contract**:", content)
        self.assertIn("4. **Production Output Purity**:", content)
        self.assertIn("5. **Linear Rebase Only**:", content)

    def test_agents_md_references_pr_governance(self) -> None:
        agents_path = ROOT / "AGENTS.md"
        self.assertTrue(agents_path.exists(), "AGENTS.md must exist")
        content = agents_path.read_text(encoding="utf-8")
        self.assertIn(".agents/rules/pr-governance.md", content, "AGENTS.md must reference pr-governance.md")


class TestLayer2PreToolHooks(unittest.TestCase):
    """Layer 2: Pre-Tool Firewall Tests."""

    def setUp(self) -> None:
        self.assertTrue(HOOKS_CONFIG.exists(), f"hooks.json must exist at {HOOKS_CONFIG}")
        self.hooks_data = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))

    def test_pre_tool_firewall_definitions_exist(self) -> None:
        self.assertIn("governed-git-command-firewall", self.hooks_data)
        git_hook = self.hooks_data["governed-git-command-firewall"]["PreToolUse"][0]
        self.assertEqual(git_hook["matcher"], "run_command")

        self.assertIn("governed-output-path-firewall", self.hooks_data)
        out_hook = self.hooks_data["governed-output-path-firewall"]["PreToolUse"][0]
        self.assertEqual(out_hook["matcher"], "write_to_file")

    def _execute_hook(self, hook_cmd: str, payload: dict) -> dict:
        proc = subprocess.run(
            ["sh", "-c", hook_cmd],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout.strip())

    def test_git_firewall_blocks_root_branch_switch(self) -> None:
        hook_cmd = self.hooks_data["governed-git-command-firewall"]["PreToolUse"][0]["hooks"][0]["command"]

        # Primary workspace root (does not contain .worktrees)
        primary_root = str(ROOT).split("/.worktrees")[0]

        # Block checkout in root
        res = self._execute_hook(hook_cmd, {
            "CommandLine": "git checkout feat/some-feature",
            "Cwd": primary_root,
        })
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Branch switching in root workspace is prohibited", res.get("reason", ""))

        # Allow switch/checkout inside .worktrees/
        res = self._execute_hook(hook_cmd, {
            "CommandLine": "git checkout feat/some-feature",
            "Cwd": str(ROOT / ".worktrees" / "feat_some_feature"),
        })
        self.assertEqual(res.get("decision"), "allow")

        # Allow git checkout -b (branch creation) even in root
        res = self._execute_hook(hook_cmd, {
            "CommandLine": "git checkout -b feat/new-feature",
            "Cwd": str(ROOT),
        })
        self.assertEqual(res.get("decision"), "allow")

    def test_git_firewall_blocks_dirty_dump_commit(self) -> None:
        hook_cmd = self.hooks_data["governed-git-command-firewall"]["PreToolUse"][0]["hooks"][0]["command"]

        for dirty_msg in ["chore: commit dirty files", "wip: save changes", "chore: temp dump", "stash changes"]:
            res = self._execute_hook(hook_cmd, {
                "CommandLine": f"git commit -m '{dirty_msg}'",
                "Cwd": str(ROOT),
            })
            self.assertEqual(res.get("decision"), "deny", f"Should deny: {dirty_msg}")
            self.assertIn("Bulk dirty-dump commits are prohibited", res.get("reason", ""))

        # Allow clean conventional commit
        res = self._execute_hook(hook_cmd, {
            "CommandLine": "git commit -m 'feat(governance): add four-layer enforcement'",
            "Cwd": str(ROOT),
        })
        self.assertEqual(res.get("decision"), "allow")

    def test_output_path_firewall_blocks_draft_and_temp(self) -> None:
        hook_cmd = self.hooks_data["governed-output-path-firewall"]["PreToolUse"][0]["hooks"][0]["command"]

        # Block draft in output
        res = self._execute_hook(hook_cmd, {
            "TargetFile": str(ROOT / "output/project_lens/09-18-2026/01_draft.md"),
        })
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Rule C-05", res.get("reason", ""))

        # Block temp in output
        res = self._execute_hook(hook_cmd, {
            "TargetFile": str(ROOT / "output/temp_meeting/notes.txt"),
        })
        self.assertEqual(res.get("decision"), "deny")

        # Allow valid output deliverable
        res = self._execute_hook(hook_cmd, {
            "TargetFile": str(ROOT / "output/project_lens/09-18-2026/Slug/01_hitl_resolved_transcript.md"),
        })
        self.assertEqual(res.get("decision"), "allow")


class TestLayer3PostToolHooks(unittest.TestCase):
    """Layer 3: Post-Tool Inspector Tests."""

    def setUp(self) -> None:
        self.assertTrue(HOOKS_CONFIG.exists())
        self.hooks_data = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))

    def test_post_tool_inspector_definition_exists(self) -> None:
        self.assertIn("governed-clean-tree-inspector", self.hooks_data)
        inspector = self.hooks_data["governed-clean-tree-inspector"]["PostToolUse"][0]
        self.assertEqual(inspector["matcher"], "run_command")

    def test_post_tool_inspector_executes_safely(self) -> None:
        hook_cmd = self.hooks_data["governed-clean-tree-inspector"]["PostToolUse"][0]["hooks"][0]["command"]
        # Non-merge command produces no output
        proc = subprocess.run(
            ["sh", "-c", hook_cmd],
            input=json.dumps({"CommandLine": "git status", "Cwd": str(ROOT)}),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(proc.stdout.strip(), "")


class TestLayer4GitLifecycleHooks(unittest.TestCase):
    """Layer 4: Native Git Lifecycle Hooks & Receipt Verification Tests."""

    def test_commit_msg_hook_fast_path(self) -> None:
        hook_script = ROOT / ".githooks" / "commit-msg"
        self.assertTrue(hook_script.exists() and os.access(hook_script, os.X_OK))

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("chore: save dirty files\n")
            f.flush()
            temp_path = f.name

        try:
            # Should reject dirty dump
            res = subprocess.run(["sh", str(hook_script), temp_path], capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Prohibited dirty-dump keyword", res.stdout + res.stderr)

            # Should reject model identifier
            with open(temp_path, "w") as f2:
                f2.write("feat(eval): benchmark on [gpt-6] frontier\n")
            res = subprocess.run(["sh", str(hook_script), temp_path], capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("violates Rule C-07", res.stdout + res.stderr)

            # Should accept conventional commit
            with open(temp_path, "w") as f3:
                f3.write("feat(governance): implement four-layer governance architecture\n")
            res = subprocess.run(["sh", str(hook_script), temp_path], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
        finally:
            os.unlink(temp_path)

    def test_pre_commit_hook_staged_drafts_rejection(self) -> None:
        hook_script = ROOT / ".githooks" / "pre-commit"
        self.assertTrue(hook_script.exists() and os.access(hook_script, os.X_OK))
        content = hook_script.read_text(encoding="utf-8")
        self.assertIn("STAGED_DRAFTS=", content)
        self.assertIn("output/.*draft", content)

    def test_verify_merge_receipt_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
            (tmp_root / "reports").mkdir()

            branch = "feat/sample-merge-test"
            slug = "feat_sample_merge_test"
            receipt_file = tmp_root / "reports" / f"pr_review_{slug}_receipt.json"

            # 1. Missing receipt fails
            ok, msg = verify_merge_receipt(branch=branch, repo_root=tmp_root)
            self.assertFalse(ok)
            self.assertIn("Missing required PR Review Receipt", msg)

            # 2. Non-passing receipt fails
            receipt_file.write_text(json.dumps({"terminal_outcome": "CONDITIONAL_PASS"}), encoding="utf-8")
            ok, msg = verify_merge_receipt(branch=branch, repo_root=tmp_root)
            self.assertFalse(ok)
            self.assertIn("did not pass", msg)

            # 3. Passing receipt succeeds
            receipt_file.write_text(json.dumps({"terminal_outcome": "PASS"}), encoding="utf-8")
            ok, msg = verify_merge_receipt(branch=branch, repo_root=tmp_root)
            self.assertTrue(ok)
            self.assertIn("outcome: PASS", msg)


if __name__ == "__main__":
    unittest.main()
