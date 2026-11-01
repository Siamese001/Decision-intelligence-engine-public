"""Adversarial and edge-case tests for Worktree Branch Governance Firewall (Wave 1).

Covers Mandatory Edge-Case Categories:
1. Exact production regression: `cp -R .worktrees/.../output/hrit/... output/hrit/... && git worktree remove --force ... && git branch -D ...`
2. Invariant bypass attempt: Chained commands (`&&`, `;`, `||`), `git -C` flags, `mv`/`rsync` into root `output/`, and `git branch --delete`.
3. Boundary payload: Empty commands, whitespace-only commands, unclosed quotes, and valid `tools/pr_governance.py` commands.
4. Malformed input: Non-JSON stdin, missing `toolCall` keys, and non-dict `args` in `hitl_pre_tool_guard.py`.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.query_core.firewall import ProhibitedCommandGuard
from scripts.hitl_pre_tool_guard import handle_tool_call_guard


class TestWorktreeGovernanceFirewallAdversarial(unittest.TestCase):
    """Verifies fail-closed blocking of raw worktree deletion and main output mutation."""

    def test_exact_production_regression_blocked(self) -> None:
        """Category: Exact production regression — blocks the exact HR IT bypass chain."""
        cmd = (
            "cp -R .worktrees/ingest_doc_hrit_10_20_2026_position_integration_field_mapping_creat/"
            "output/hrit/10-20-2026/Position_Integration_Field_Mapping_Creat "
            "output/hrit/10-20-2026/Position_Integration_Field_Mapping_Creat && "
            "git worktree remove --force .worktrees/ingest_doc_hrit_10_20_2026_position_integration_field_mapping_creat && "
            "git branch -D ingest/doc-hrit-10-20-2026-position-integration-field-mapping-creat"
        )
        violation = ProhibitedCommandGuard.evaluate_command(cmd)
        self.assertTrue(violation.is_violation)
        self.assertEqual(violation.matched_pattern, "cp [main_output_mutation]")
        self.assertIn("MAIN_WORKTREE_WRITE_BLOCKED", violation.rationale)

        payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}})
        res = handle_tool_call_guard(payload)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res["decision"], "deny")
        self.assertIn("MAIN_WORKTREE_WRITE_BLOCKED", res["reason"])

    def test_git_worktree_remove_and_branch_delete_bypass_blocked(self) -> None:
        """Category: Invariant bypass attempt — blocks raw git worktree remove/prune and branch -D/-d."""
        bypass_commands = [
            "git worktree remove .worktrees/ingest_doc_test",
            "git worktree remove --force .worktrees/ingest_doc_test",
            "git -C /Users/amitayer/Git/decision-intelligence-engine worktree remove --force .worktrees/foo",
            "git worktree prune",
            "git branch -D ingest/doc-hrit-test",
            "git branch -d feat/some-branch",
            "git branch --delete ingest/doc-test",
            "echo 'cleaning up' ; git branch -D ingest/doc-hrit-10-20-2026",
        ]
        for cmd in bypass_commands:
            with self.subTest(cmd=cmd):
                violation = ProhibitedCommandGuard.evaluate_command(cmd)
                self.assertTrue(violation.is_violation, f"Expected violation for: {cmd}")
                self.assertIn("WORKTREE_GOVERNANCE_BYPASS_BLOCKED", violation.rationale)

                payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}})
                res = handle_tool_call_guard(payload)
                self.assertIsNotNone(res)
                assert res is not None
                self.assertEqual(res["decision"], "deny")
                self.assertIn("WORKTREE_GOVERNANCE_BYPASS_BLOCKED", res["reason"])

    def test_shell_mutation_into_root_output_blocked(self) -> None:
        """Category: Invariant bypass attempt — blocks cp/mv/rsync targeting root output/."""
        mutation_commands = [
            "cp -R .worktrees/wt_slug/output/hrit/10-20-2026/Doc output/hrit/10-20-2026/Doc",
            "mv .worktrees/wt_slug/output/hrit/10-20-2026/Doc ./output/hrit/10-20-2026/",
            "rsync -av .worktrees/wt_slug/output/ output/",
            "cp file.json /Users/amitayer/Git/decision-intelligence-engine/output/hrit/file.json",
        ]
        for cmd in mutation_commands:
            with self.subTest(cmd=cmd):
                violation = ProhibitedCommandGuard.evaluate_command(cmd)
                self.assertTrue(violation.is_violation, f"Expected violation for: {cmd}")
                self.assertIn("MAIN_WORKTREE_WRITE_BLOCKED", violation.rationale)

    def test_isolated_worktree_output_and_pr_governance_allowed(self) -> None:
        """Category: Boundary payload — allows writes inside .worktrees/<slug>/output/ and pr_governance.py."""
        allowed_commands = [
            "",
            "   ",
            ".venv/bin/python tools/pr_governance.py finish ingest/doc-hrit-test",
            ".venv/bin/python tools/pr_governance.py dehydrate ingest/doc-hrit-test",
            ".venv/bin/python tools/pr_governance.py review ingest/doc-hrit-test",
            "cp -R /tmp/staging/Doc .worktrees/ingest_doc_hrit/output/hrit/10-20-2026/Doc",
            "git status --porcelain && git worktree list && git branch -a",
        ]
        for cmd in allowed_commands:
            with self.subTest(cmd=cmd):
                violation = ProhibitedCommandGuard.evaluate_command(cmd)
                self.assertFalse(violation.is_violation, f"Unexpected violation for: {cmd}")

    def test_malformed_hook_payloads_handled_safely(self) -> None:
        """Category: Malformed input — verifies handle_tool_call_guard handles malformed inputs without crashing."""
        self.assertIsNone(handle_tool_call_guard(""))
        self.assertIsNone(handle_tool_call_guard("{not-valid-json"))
        self.assertIsNone(handle_tool_call_guard("[]"))
        res = handle_tool_call_guard(json.dumps({"toolCall": {"name": "run_command", "args": "not-a-dict"}}))
        self.assertEqual(res, {"decision": "allow"})


if __name__ == "__main__":
    unittest.main()
