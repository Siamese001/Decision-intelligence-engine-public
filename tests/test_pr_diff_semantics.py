"""tests/test_pr_diff_semantics.py

Unit tests for the PR Queue Governance Semantic Diff Classifier.
Verifies accurate categorization across:
- EMPTY_STUB
- DOCS_ONLY_UNSTARTED
- DOCS_ONLY_ACTIVE
- CODE_ACTIVE
"""
from __future__ import annotations

import unittest
from tools.pr_governance_core.diff_semantics import (
    classify_branch_diff,
    is_code_file,
    is_doc_file,
)


class TestPRDiffSemantics(unittest.TestCase):
    def test_file_type_helpers(self):
        self.assertTrue(is_doc_file("docs/plans/WAVE_PLAN_TEST.md"))
        self.assertTrue(is_doc_file("README.md"))
        self.assertFalse(is_doc_file("dual_pipeline/engine.py"))

        self.assertTrue(is_code_file("dual_pipeline/engine.py"))
        self.assertTrue(is_code_file("tests/test_foo.py"))
        self.assertTrue(is_code_file("tools/pr_governance.py"))
        self.assertFalse(is_code_file("docs/plans/some_plan.md"))

    def test_empty_stub_classification(self):
        res = classify_branch_diff([])
        self.assertEqual(res.category, "EMPTY_STUB")
        self.assertFalse(res.is_docs_only)
        self.assertFalse(res.is_unstarted_plan)
        self.assertEqual(res.code_files_count, 0)
        self.assertEqual(res.doc_files_count, 0)

    def test_code_active_classification(self):
        changed = ["dual_pipeline/engine.py", "tests/test_engine.py", "docs/overview.md"]
        res = classify_branch_diff(changed)
        self.assertEqual(res.category, "CODE_ACTIVE")
        self.assertFalse(res.is_docs_only)
        self.assertEqual(res.code_files_count, 2)
        self.assertEqual(res.doc_files_count, 1)

    def test_docs_only_unstarted_plan_from_markers(self):
        changed = ["docs/plans/WAVE_PLAN_EFFICIENCY_GAP_REMEDIATION.md"]
        plan_content = (
            "# Wave Plan\n\n"
            "> **Status:** PLAN ONLY. No implementation authorized. All ten waves are NOT_STARTED.\n"
        )
        res = classify_branch_diff(
            changed,
            file_content_getter=lambda p: plan_content,
        )
        self.assertEqual(res.category, "DOCS_ONLY_UNSTARTED")
        self.assertTrue(res.is_docs_only)
        self.assertTrue(res.is_unstarted_plan)
        self.assertEqual(res.code_files_count, 0)
        self.assertEqual(res.doc_files_count, 1)

    def test_docs_only_unstarted_plan_from_wave_status(self):
        changed = ["docs/plans/some_future_plan.md"]
        res = classify_branch_diff(
            changed,
            open_waves="Wave 1, Wave 2",
            completed_waves="—",
        )
        self.assertEqual(res.category, "DOCS_ONLY_UNSTARTED")
        self.assertTrue(res.is_docs_only)
        self.assertTrue(res.is_unstarted_plan)

    def test_docs_only_active_plan(self):
        changed = ["docs/plans/active_plan.md"]
        res = classify_branch_diff(
            changed,
            open_waves="Wave 2",
            completed_waves="Wave 1",
        )
        self.assertEqual(res.category, "DOCS_ONLY_ACTIVE")
        self.assertTrue(res.is_docs_only)
        self.assertFalse(res.is_unstarted_plan)

    def test_extract_file_hunks(self):
        from pathlib import Path
        import subprocess
        from tools.pr_governance_core.diff_semantics import extract_file_hunks

        sample_diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,5 +10,8 @@ def calculate_metric():\n"
            "     x = 1\n"
            "+    y = 2\n"
            "+    return x + y\n"
        )
        fake_runner = lambda cmd, root: subprocess.CompletedProcess(cmd, 0, stdout=sample_diff)
        hunks = extract_file_hunks("main", "feat/test", Path("/tmp"), git_runner=fake_runner)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "foo.py")
        self.assertEqual(hunks[0].enclosing_symbol, "def calculate_metric():")
        self.assertEqual(hunks[0].added_lines, ["    y = 2", "    return x + y"])
        self.assertTrue(bool(hunks[0].hunk_digest))

    def test_hunk_absorption_distinguishes_superseded_vs_active(self):
        from pathlib import Path
        import subprocess
        from tools.pr_governance_core.diff_semantics import _check_recent_merge_overlap

        # Merge commit log with 1 overlapping merge
        merges_stdout = "abc123456789\tMerge branch 'feat/shared-feature' into main\n"
        diff_files_stdout = "foo.py\nbar.py\n"

        # Case A: 100% of added lines already exist in main -> superseded
        def runner_absorbed(cmd, root):
            if "log" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=merges_stdout)
            if "--name-only" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=diff_files_stdout)
            if "-U0" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="@@ -1 +1 @@\n+line_in_main\n")
            if "show" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="line_in_main\nother\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        is_sup, reason = _check_recent_merge_overlap("feat/shared-feature", {"foo.py", "bar.py"}, Path("/tmp"), git_runner=runner_absorbed)
        self.assertTrue(is_sup)
        self.assertIn("hunk absorption: 100.0%", reason)

        # Case B: Added lines do NOT exist in main (active feature work in shared file) -> NOT superseded
        def runner_active(cmd, root):
            if "log" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=merges_stdout)
            if "--name-only" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=diff_files_stdout)
            if "-U0" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="@@ -1 +1 @@\n+brand_new_feature_logic\n")
            if "show" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="line_in_main\nother\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        is_sup2, reason2 = _check_recent_merge_overlap("feat/shared-feature", {"foo.py", "bar.py"}, Path("/tmp"), git_runner=runner_active)
        self.assertFalse(is_sup2)
        self.assertIsNone(reason2)


if __name__ == "__main__":
    unittest.main()
