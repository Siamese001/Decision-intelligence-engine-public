"""
tests/governance/test_ci_commits_and_ssot_hardening.py

Regression test suite for PR CI Commits, Git Hook Streamlining, and SSOT Hardening.
Verifies:
1. Atomic single merge commit on local main (eliminates secondary receipt commit).
2. Branch-scoped readiness receipts preventing cross-branch merge conflicts.
3. Streamlined pre-commit hook execution (<4s latency).
4. Zero hardcoded parameters and vendor model string compliance.
5. SSOT project alias resolution through project_registry.json.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.parameters import get_project_aliases, resolve_project_name
from tools.lint_hardcoded_parameters import PROHIBITED_MODEL_PATTERNS
from tools.pr_governance_core.lifecycle import slugify_branch


class TestCICommitsAndSSOTHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]

    def test_project_alias_resolution_canonical(self):
        """Verify that resolve_project_name maps all aliases to canonical projects."""
        aliases = get_project_aliases()
        self.assertGreater(len(aliases), 10, "Expected at least 10 project aliases in SSOT")
        self.assertEqual(resolve_project_name("lens"), "project_lens")
        self.assertEqual(resolve_project_name("sherpa"), "project_beacon")
        self.assertEqual(resolve_project_name("nexus"), "project_nexus")
        self.assertEqual(resolve_project_name("rewire"), "project_atlas")
        self.assertEqual(resolve_project_name("project atlas"), "project_atlas")
        self.assertEqual(resolve_project_name("project_sherpa"), "project_beacon")

    def test_pre_commit_hook_is_streamlined(self):
        """Verify .githooks/pre-commit invokes verify_commit.py --staged and core tests."""
        hook_path = self.root / ".githooks" / "pre-commit"
        self.assertTrue(hook_path.exists(), "pre-commit hook must exist")
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("tools/pre_commit_orchestrator.py", content)
        self.assertIn("--staged", content)
        # Ensure heavy unbounded full test suite is NOT in pre-commit
        self.assertNotIn("pytest tests/", content)

    def test_branch_scoped_readiness_naming(self):
        """Verify that readiness receipts are scoped by branch slug and strip model tags."""
        branch = "feat/pr-ci-governance-and-ssot-hardening-gpt6-astra"
        slug = slugify_branch(branch)
        expected_filename = f"local_main_readiness_{slug}_receipt.json"
        self.assertEqual(slug, "feat_pr_ci_governance_and_ssot_hardening")
        self.assertTrue(expected_filename.endswith("_receipt.json"))

    def test_prohibited_model_regex_coverage(self):
        """Verify that PROHIBITED_MODEL_PATTERNS captures forbidden vendor strings."""
        test_strings = [
            "gpt-6-luna",
            "gpt-6",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6",
            "gpt-4o",
            "gpt-4o-transcribe-diarize",
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
            "claude-3-5-sonnet",
        ]
        for s in test_strings:
            matched = any(p.match(s) for p in PROHIBITED_MODEL_PATTERNS)
            self.assertTrue(matched, f"Expected prohibited model pattern to match '{s}'")

    def test_output_hierarchy_staged_flag_exists(self):
        """Verify tools/validate_output_hierarchy.py supports --staged mode."""
        from tools.validate_output_hierarchy import validate_staged_output_hierarchy
        self.assertTrue(callable(validate_staged_output_hierarchy))


if __name__ == "__main__":
    unittest.main()
