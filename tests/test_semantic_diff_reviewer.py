"""
Unit Tests for Multi-Agent Semantic Diff & Plan Review Panel (Wave 2).
Validates PlanFidelityAuditor, LogicDetective, StorageConcurrencyAuditor,
RedTeamCritic, and SemanticDiffReviewPanel RFC 8785 receipt generation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.semantic_diff_reviewer import (
    LogicDetective,
    PlanFidelityAuditor,
    RedTeamCritic,
    SemanticDiffReviewPanel,
    StorageConcurrencyAuditor,
    _parse_unified_diff,
)


class TestSemanticDiffReviewer(unittest.TestCase):
    def test_parse_unified_diff(self):
        """Diff parser correctly associates line numbers and added content."""
        sample_diff = """diff --git a/tools/demo.py b/tools/demo.py
--- a/tools/demo.py
+++ b/tools/demo.py
@@ -10,2 +10,4 @@
 existing_line = 1
+new_line_1 = 2
+new_line_2 = 3
"""
        parsed = _parse_unified_diff(sample_diff)
        self.assertIn("tools/demo.py", parsed)
        lines = parsed["tools/demo.py"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], (11, "new_line_1 = 2"))
        self.assertEqual(lines[1], (12, "new_line_2 = 3"))

    def test_plan_fidelity_auditor(self):
        """PlanFidelityAuditor detects matched and missing declared deliverables."""
        plan_text = """
# Plan
Deliverables:
- `tools/new_feature.py`
- `tools/another_module.py`
- `tests/test_new_feature.py`
"""
        diff_files = ["tools/new_feature.py", "tests/test_new_feature.py"]
        auditor = PlanFidelityAuditor()
        score, findings, summary = auditor.audit(diff_files, plan_text)

        self.assertAlmostEqual(score, 2 / 3, places=2)
        self.assertEqual(summary["matched_deliverables_count"], 2)
        self.assertEqual(summary["missing_deliverables_count"], 1)
        self.assertIn("tools/another_module.py", summary["missing_files"])
        self.assertTrue(any("tools/another_module.py" in f.message for f in findings))

    def test_logic_detective_hazards(self):
        """LogicDetective catches bare excepts, direct os.environ, and unguarded loops."""
        added = {
            "module.py": [
                (10, "except:"),
                (20, "token = os.environ['SECRET_KEY']"),
                (30, "while True:"),
            ]
        }
        detective = LogicDetective()
        findings, summary = detective.audit(added)

        messages = [f.message for f in findings]
        self.assertTrue(any("Bare exception" in m for m in messages))
        self.assertTrue(any("Direct os.environ" in m for m in messages))
        self.assertTrue(any("while True loop" in m for m in messages))

    def test_storage_concurrency_auditor_hazards(self):
        """StorageConcurrencyAuditor catches unparameterized SQL and raw mutations outside UWG."""
        added = {
            "app/storage.py": [
                (15, "conn.execute(f\"SELECT * FROM users WHERE id = '{user_id}'\")"),
                (25, "cursor.execute(\"INSERT INTO records VALUES (1, 2)\")"),
                (35, "sqlite3.connect('test.db')"),
            ]
        }
        auditor = StorageConcurrencyAuditor()
        findings, summary = auditor.audit(added)

        severities = {f.severity for f in findings}
        self.assertIn("CRITICAL", severities)
        messages = [f.message for f in findings]
        self.assertTrue(any("Unparameterized SQL" in m for m in messages))
        self.assertTrue(any("Raw SQLite mutation" in m for m in messages))
        self.assertTrue(any("without explicit timeout" in m for m in messages))

    def test_red_team_critic_hazards(self):
        """RedTeamCritic catches subprocess without timeout and shell=True."""
        added = {
            "tools/runner.py": [
                (12, "subprocess.run(['git', 'status'])"),
                (22, "subprocess.run('rm -rf tmp', shell=True)"),
            ]
        }
        critic = RedTeamCritic()
        findings, summary = critic.audit(added)

        messages = [f.message for f in findings]
        self.assertTrue(any("without explicit timeout" in m for m in messages))
        self.assertTrue(any("shell=True" in m for m in messages))

    def test_semantic_diff_review_panel_end_to_end(self):
        """SemanticDiffReviewPanel executes all 4 agents and emits RFC 8785 receipt."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "implementation_plan.md").write_text(
                "Deliverables:\n- `tools/good_feature.py`\n", encoding="utf-8"
            )
            sample_diff = """diff --git a/tools/good_feature.py b/tools/good_feature.py
--- a/tools/good_feature.py
+++ b/tools/good_feature.py
@@ -0,0 +1,5 @@
+def compute_value(x: int) -> int:
+    return x * 2
+
"""
            panel = SemanticDiffReviewPanel(root=root)
            report = panel.run_review(
                branch="feat/test-panel",
                base_branch="main",
                diff_text=sample_diff,
            )

            self.assertEqual(report.verdict, "PASS")
            self.assertEqual(report.plan_fidelity_score, 1.0)
            self.assertTrue(report.receipt_digest)

            # Receipt was persisted
            receipt_path = root / "reports" / "semantic_code_review_feat_test_panel_receipt.json"
            self.assertTrue(receipt_path.exists())

    def test_plan_fidelity_auditor_pure_deliverables_exempt(self):
        """Pure deliverable branches under output/ and reports/ are exempt from code plan audit."""
        plan_text = "Deliverables:\n- `tools/some_code.py`\n"
        diff_files = [
            "output/project_lens/10-16-2026/Ed_Amit/01_transcript.md",
            "output/project_lens/10-16-2026/Ed_Amit/02_recap.md",
            "reports/coverage_receipt.json",
        ]
        auditor = PlanFidelityAuditor()
        score, findings, summary = auditor.audit(diff_files, plan_text)
        self.assertEqual(score, 1.0)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "INFO")
        self.assertIn("Pure deliverable branch", findings[0].message)


if __name__ == "__main__":
    unittest.main()
