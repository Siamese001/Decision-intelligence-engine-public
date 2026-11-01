import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

class TestPRQueueRules(unittest.TestCase):
    def test_pr_queue_rules_file_exists(self):
        rules_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
        self.assertTrue(rules_file.exists(), ".agents/rules/pr-queue-summary.md missing")
        content = rules_file.read_text(encoding="utf-8")

        self.assertIn("## 0. Pre-Queue Consolidation & Deprecation Sweep", content)
        self.assertIn("## 1. Priority Ordering", content)
        self.assertIn("## 2. Table Format", content)
        self.assertIn("The 4-Train Consolidation Architecture", content)
        self.assertIn("AST Semantic Merge Driver", content)

    def test_pr_queue_rules_required_columns(self):
        rules_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
        content = rules_file.read_text(encoding="utf-8")
        expected_header = "| Priority | Domain | Branch | Readiness | Review | Ahead | Behind |"
        self.assertIn(expected_header, content)
        self.assertIn("Diagnostic & Next Action Ledger", content)

    def test_pr_queue_rules_tier_prohibition_in_domain_column(self):
        rules_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
        content = rules_file.read_text(encoding="utf-8")
        self.assertIn("independent of P-level", content)
        self.assertIn("Allowed labels:", content)
        for prohibited in ("P0", "P1", "P2", "P3", "Frontier High-Reasoning"):
            self.assertNotIn(f"Allowed labels: {prohibited}", content)

    def test_pr_queue_rules_single_table_mandate(self):
        rules_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
        content = rules_file.read_text(encoding="utf-8")

        self.assertTrue(
            "single unified" in content.lower() or "single, unified" in content.lower(),
            "Rules must explicitly mandate a single PR Queue table."
        )
        self.assertIn("decommission candidates", content.lower(), "Rules must mention decommission candidates.")

if __name__ == '__main__':
    unittest.main()
