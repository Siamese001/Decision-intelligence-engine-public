"""Focused acceptance checks for Wave 1's versioned observer contract."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.pr_queue_contract_core.refactor_profile import CONTRACT, _check_contract, _check_sources


class TestRefactorOutputContract(unittest.TestCase):
    def test_contract_has_exact_nine_column_order_and_modes(self):
        result = _check_contract()
        self.assertEqual(result["status"], "PASS", result)

    def test_contract_requires_supported_benefit_priority_order(self):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(data["priority_themes"][0], "cost_reduction")
        self.assertTrue(data["priority_policy"]["plans_are_not_measured_savings"])

    def test_archived_reference_prompts_are_hash_preserved(self):
        result = _check_sources()
        self.assertEqual(result["status"], "PASS", result)


if __name__ == "__main__":
    unittest.main()
