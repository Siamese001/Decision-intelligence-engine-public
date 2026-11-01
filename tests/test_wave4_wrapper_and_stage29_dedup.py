"""Wave 4 tests verifying Stage 29 test deduplication, ratchet filtering, and PR wrapper reuse."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_skills_budget_governance as skills_budget_mod
from tools.lint_test_and_healer_ratchet import run_ratchet_audit
from tools.pr_governance_core import lifecycle
from tools.verify_commit import check_fast_hierarchy_and_plan
from tools.verify_tier1_cached_replay import verify_tier1_cached_replay


class TestWave4WrapperAndStage29Dedup(unittest.TestCase):
    """Verify Wave 4 deduplications across Stage 29 tests, ratchet audit, and PR review wrapper."""

    def test_skills_budget_test_suite_caches_live_report_once(self) -> None:
        """TestSkillsBudgetGovernance.setUpClass caches live report to avoid 5x disk scans."""
        skills_budget_mod.TestSkillsBudgetGovernance.setUpClass()
        self.assertIsNotNone(skills_budget_mod.TestSkillsBudgetGovernance._live_report)
        self.assertTrue(skills_budget_mod.TestSkillsBudgetGovernance._live_report.is_overall_compliant)

    def test_ratchet_audit_skips_git_show_for_non_test_non_healer_files(self) -> None:
        """run_ratchet_audit should not invoke _read_main_blob for unrelated files."""
        with patch("tools.lint_test_and_healer_ratchet._read_main_blob") as mock_blob:
            violations = run_ratchet_audit(
                Path("."),
                explicit_files=["tools/readiness_core.py", "docs/readme.md"],
            )
            self.assertEqual(violations, [])
            mock_blob.assert_not_called()

    def test_pr_review_lifecycle_passes_clean_merge_and_reuses_regression_result(self) -> None:
        """lifecycle.py passes clean_merge to compute_code_review and reuses post_fork_regression_result."""
        src = inspect.getsource(lifecycle)
        self.assertIn('clean_merge=info["clean_merge"]', src)
        self.assertIn('getattr(code_rev, "post_fork_regression_result", None)', src)

        # Also verify check_fast_hierarchy_and_plan uses in-process run_ratchet_audit
        vc_src = inspect.getsource(check_fast_hierarchy_and_plan)
        self.assertIn("run_ratchet_audit", vc_src)
        self.assertNotIn("tools/lint_test_and_healer_ratchet.py", vc_src)

    def test_tier1_cached_replay_reuses_in_process_default_receipt(self) -> None:
        """Second default call to verify_tier1_cached_replay reuses verified receipt."""
        ok1, r1 = verify_tier1_cached_replay()
        self.assertTrue(ok1)
        self.assertEqual(r1["status"], "PASS")
        with patch("tools.verify_tier1_cached_replay._verify_copilot_session_ae00fe03_replay") as mock_cp:
            ok2, r2 = verify_tier1_cached_replay()
            self.assertTrue(ok2)
            self.assertEqual(r2["status"], "PASS")
            mock_cp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
