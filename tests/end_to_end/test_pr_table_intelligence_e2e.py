import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

class TestPRTableIntelligenceE2E(unittest.TestCase):
    def test_pr_table_intelligence_e2e_render(self):
        cmd = ["python", "tools/pr_governance.py", "list", "--json"]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"pr_governance list failed: {proc.stderr}")

        data = json.loads(proc.stdout)
        self.assertIsInstance(data, list)

        if len(data) > 0:
            first = data[0]
            self.assertIn("sub_rank", first)
            self.assertIn("display_tier", first)
            self.assertIn("next_action", first)
            self.assertIn("tier", first)

    def test_pr_table_intelligence_e2e_receipt(self):
        receipt_path = ROOT / "reports" / "pr_table" / "e2e_receipt.json"
        cmd = ["python", "tools/pr_governance.py", "list", "--receipt", str(receipt_path)]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"pr_governance list --receipt failed: {proc.stderr}")

        self.assertTrue(receipt_path.exists())
        with open(receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)

        self.assertEqual(receipt_data["terminal_outcome"], "PASS")
        self.assertIn("branches", receipt_data)

if __name__ == '__main__':
    unittest.main()
