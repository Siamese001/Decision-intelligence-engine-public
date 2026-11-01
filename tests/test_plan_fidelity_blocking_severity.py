"""Unit tests for PlanFidelityAuditor severity elevation and review blocking."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.semantic_diff_reviewer import (
    PlanFidelityAuditor,
    ReviewFinding,
    SemanticDiffReviewPanel,
)


class TestPlanFidelityBlockingSeverity(unittest.TestCase):
    def setUp(self):
        self.auditor = PlanFidelityAuditor()

    def test_low_fidelity_triggers_critical_severity_without_partial_scope(self):
        plan_text = """
# Feature Plan
Deliverables:
- `tools/mod1.py`
- `tools/mod2.py`
- `tools/mod3.py`
- `tools/mod4.py`
- `tools/mod5.py`
"""
        diff_files = ["tools/mod1.py"]  # 1/5 = 20%
        score, findings, summary = self.auditor.audit(diff_files, plan_text)

        self.assertAlmostEqual(score, 0.2, places=2)
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        self.assertTrue(len(critical_findings) > 0)
        self.assertTrue(any(f.file == "implementation_plan.md" for f in critical_findings))
        self.assertTrue(any("Low Plan Fidelity" in f.message for f in critical_findings))

    def test_low_fidelity_with_partial_scope_declaration_downgrades_to_warning(self):
        plan_text = """
# Feature Plan
Status: [PARTIAL_SCOPE] - Wave 1 only.
Deliverables:
- `tools/mod1.py`
- `tools/mod2.py`
- `tools/mod3.py`
- `tools/mod4.py`
- `tools/mod5.py`
"""
        diff_files = ["tools/mod1.py"]  # 1/5 = 20%
        score, findings, summary = self.auditor.audit(diff_files, plan_text)

        self.assertAlmostEqual(score, 0.2, places=2)
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        self.assertEqual(len(critical_findings), 0, "Partial scope declaration must prevent CRITICAL severity")
        warning_findings = [f for f in findings if f.severity == "WARNING"]
        self.assertTrue(len(warning_findings) > 0)

    def test_medium_fidelity_emits_warning(self):
        plan_text = """
# Feature Plan
Deliverables:
- `tools/mod1.py`
- `tools/mod2.py`
- `tools/mod3.py`
- `tools/mod4.py`
- `tools/mod5.py`
"""
        diff_files = ["tools/mod1.py", "tools/mod2.py", "tools/mod3.py"]  # 3/5 = 60%
        score, findings, summary = self.auditor.audit(diff_files, plan_text)

        self.assertAlmostEqual(score, 0.6, places=2)
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        self.assertEqual(len(critical_findings), 0)
        warning_findings = [f for f in findings if f.severity == "WARNING"]
        self.assertEqual(len(warning_findings), 2)  # mod4 and mod5

    def test_high_fidelity_emits_info(self):
        plan_text = """
# Feature Plan
Deliverables:
- `tools/mod1.py`
- `tools/mod2.py`
- `tools/mod3.py`
- `tools/mod4.py`
- `tools/mod5.py`
"""
        diff_files = [
            "tools/mod1.py",
            "tools/mod2.py",
            "tools/mod3.py",
            "tools/mod4.py",
        ]  # 4/5 = 80%
        score, findings, summary = self.auditor.audit(diff_files, plan_text)

        self.assertAlmostEqual(score, 0.8, places=2)
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        warning_findings = [f for f in findings if f.severity == "WARNING"]
        self.assertEqual(len(critical_findings), 0)
        self.assertEqual(len(warning_findings), 0)
        info_findings = [f for f in findings if f.severity == "INFO"]
        self.assertEqual(len(info_findings), 1)

    def test_panel_review_blocks_on_low_plan_fidelity(self):
        plan_text = """
# Feature Plan
Deliverables:
- `tools/alpha.py`
- `tools/beta.py`
- `tools/gamma.py`
"""
        raw_diff = """diff --git a/tools/alpha.py b/tools/alpha.py
new file mode 100644
--- /dev/null
+++ b/tools/alpha.py
@@ -0,0 +1,5 @@
+def alpha():
+    return True
"""
        panel = SemanticDiffReviewPanel(root=Path("."))
        with patch("tools.semantic_diff_reviewer._read_plan_content", return_value=plan_text), \
             patch("tools.semantic_diff_reviewer.run_git", return_value="11111111"):
            report = panel.run_review(
                "feat/test-branch",
                base_branch="main",
                diff_text=raw_diff,
                persist_receipt=False,
            )
            self.assertEqual(report.verdict, "BLOCKED")
            self.assertAlmostEqual(report.plan_fidelity_score, 1 / 3, places=2)
            criticals = [f for f in report.findings if f.severity == "CRITICAL"]
            self.assertTrue(len(criticals) > 0)


if __name__ == "__main__":
    unittest.main()
