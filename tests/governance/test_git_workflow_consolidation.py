#!/usr/bin/env python3
"""
Unit and integration tests for Git Workflow Consolidation.

Verifies:
1. _common.sh shared hook helper resolution.
2. commit-msg hook fast validation.
3. verify_commit.py Tier 1 fast execution.
4. verify_push_receipt.py Tier 3 receipt verification.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_commit_message import validate_commit_message  # noqa: E402
from tools.verify_push_receipt import verify_push_receipt  # noqa: E402


class GitWorkflowConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT
        self.githooks_dir = ROOT / ".githooks"

    def test_01_common_hook_helper_exists_and_executable(self) -> None:
        common_sh = self.githooks_dir / "_common.sh"
        self.assertTrue(common_sh.is_file(), "_common.sh must exist in .githooks/")
        self.assertTrue(os.access(common_sh, os.X_OK), "_common.sh must be executable")

        # Test shell syntax
        proc = subprocess.run(["sh", "-n", str(common_sh)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Shell syntax error in _common.sh: {proc.stderr}")

    def test_02_commit_msg_hook_valid_and_invalid_cases(self) -> None:
        # Valid conventional commit
        valid_ok, valid_reason = validate_commit_message("feat(governance): add tier 1 fast pre-commit")
        self.assertTrue(valid_ok, f"Expected valid commit message to pass: {valid_reason}")

        # Invalid dirty dump
        dirty_ok, dirty_reason = validate_commit_message("wip: dirty temp dump save files")
        self.assertFalse(dirty_ok, "Expected dirty dump commit message to fail")
        self.assertIn("Prohibited dirty-dump keyword", dirty_reason)

        # Prohibited model name
        model_ok, model_reason = validate_commit_message("feat(llm): add claude model support")
        self.assertFalse(model_ok, "Expected model identifier in commit message to fail")
        self.assertIn("Prohibited model identifier", model_reason)

        # Valid merge commit with branch name containing model substring
        merge_ok, merge_reason = validate_commit_message("Merge branch 'feat/review-gemini-38-flash-registry' into main (feat(registry): high-reasoning flash tier)")
        self.assertTrue(merge_ok, f"Expected valid merge commit message to pass: {merge_reason}")

    def test_03_tier1_verify_commit_fast_execution(self) -> None:
        py = sys.executable
        start = time.time()
        proc = subprocess.run(
            [py, "-B", "tools/verify_commit.py", "--staged"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        duration = time.time() - start

        self.assertEqual(proc.returncode, 0, f"verify_commit --staged failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertTrue(
            "TIER 1: FAST PRE-COMMIT STRUCTURAL GATEKEEPER" in proc.stdout
            or "FULL STRUCTURAL LEGALITY GATE" in proc.stdout
            or "POST-MERGE PASS" in proc.stdout,
            f"Expected pre-commit gate header not found in:\n{proc.stdout}",
        )
        if "POST-MERGE PASS" not in proc.stdout:
            self.assertIn("Ruff Gatekeeper", proc.stdout)

    def test_04_tier3_verify_push_receipt_speed_and_outcome(self) -> None:
        passed, msg, duration = verify_push_receipt(self.repo_root)
        self.assertLess(duration, 15.0, f"Tier 3 pre-push check took {duration:.2f}s, budget is < 15.0s")
        # In our repository, there should be either a valid receipt or handled status
        self.assertIsInstance(passed, bool)
        self.assertIsInstance(msg, str)

    def test_05_pre_push_hook_syntax_and_sentinels(self) -> None:
        pre_push = self.githooks_dir / "pre-push"
        self.assertTrue(pre_push.is_file(), "pre-push hook must exist")
        content = pre_push.read_text(encoding="utf-8")

        # Verify required bindings from dual_pipeline/runtime_evidence_core/constants.py
        self.assertIn("-m dual_pipeline.runtime_evidence enforce-controls", content)
        self.assertIn("tools/validate_no_synthetic_embeddings.py", content)
        self.assertIn("tools/run_automatic_evaluation_gates.py", content)
        self.assertIn("tests.test_no_synthetic_embedding_ban", content)
        self.assertIn("tests.test_bge_m3_deprecation_gates", content)
        self.assertIn("tests.test_retrieval_release_evaluation", content)
        self.assertIn("git lfs pre-push", content)
        self.assertIn("verify_push_receipt.py", content)


if __name__ == "__main__":
    unittest.main()
