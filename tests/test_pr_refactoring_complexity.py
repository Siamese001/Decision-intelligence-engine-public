"""tests/test_pr_refactoring_complexity.py

Unit tests for the refactoring complexity assessment engine.
Verifies multi-factor T-shirt sizing (XS, S, M, L, XL), layer crossing detection,
and prioritization integration via queue_sort_key.
"""
from __future__ import annotations

import unittest
from tools.pr_governance_core.complexity import (
    COMPLEXITY_RANK_MAP,
    ComplexityAssessment,
    evaluate_refactoring_complexity,
)
from tools.pr_governance_core.queue_order import queue_sort_key


class TestPRRefactoringComplexity(unittest.TestCase):
    def test_evaluate_complexity_xs(self):
        item = {
            "branch": "fix/typo",
            "churn": 12,
            "file_count": 1,
            "behind": 0,
            "clean_merge": True,
            "changed_files": ["docs/readme.md"],
            "open_waves": "—",
        }
        res = evaluate_refactoring_complexity(item)
        self.assertEqual(res.size, "XS")
        self.assertEqual(res.rank, 0)
        self.assertLessEqual(res.score, 2)
        self.assertIn("churn=12", res.rationale)

    def test_evaluate_complexity_s(self):
        item = {
            "branch": "feat/small-enhancement",
            "churn": 120,
            "file_count": 3,
            "behind": 2,
            "clean_merge": True,
            "changed_files": ["tools/helper.py", "tests/test_helper.py"],
            "open_waves": "—",
        }
        res = evaluate_refactoring_complexity(item)
        self.assertEqual(res.size, "S")
        self.assertEqual(res.rank, 1)
        self.assertTrue(3 <= res.score <= 5)

    def test_evaluate_complexity_m(self):
        item = {
            "branch": "feat/moderate-feature",
            "churn": 450,
            "file_count": 7,
            "behind": 8,
            "clean_merge": True,
            "changed_files": ["tools/pr_governance.py", "tools/pr_governance_core/render.py"],
            "open_waves": "Wave 1",
        }
        res = evaluate_refactoring_complexity(item)
        self.assertEqual(res.size, "M")
        self.assertEqual(res.rank, 2)
        self.assertTrue(8 <= res.score <= 11)

    def test_evaluate_complexity_l(self):
        item = {
            "branch": "feat/substantial-refactor",
            "churn": 1200,
            "file_count": 12,
            "behind": 8,
            "clean_merge": False,
            "changed_files": ["tools/a.py", "dual_pipeline/core.py"],
            "open_waves": "Wave 1",
        }
        res = evaluate_refactoring_complexity(item)
        self.assertEqual(res.size, "L")
        self.assertEqual(res.rank, 3)
        self.assertTrue(12 <= res.score <= 15)

    def test_evaluate_complexity_xl(self):
        item = {
            "branch": "feat/major-architecture",
            "churn": 4200,
            "file_count": 35,
            "behind": 45,
            "clean_merge": False,
            "changed_files": ["storage/connection.py", "dual_pipeline/engine.py", "tools/runner.py"],
            "open_waves": "Wave 1, Wave 2, Wave 3, Wave 4",
        }
        res = evaluate_refactoring_complexity(item)
        self.assertEqual(res.size, "XL")
        self.assertEqual(res.rank, 4)
        self.assertGreaterEqual(res.score, 13)
        self.assertIn("merge_conflicts", res.rationale)
        self.assertIn("Storage/Database (L0)", res.layer_crossings)
        self.assertIn("Core Pipeline (L1-L4)", res.layer_crossings)

    def test_numstat_fallback(self):
        item = {
            "branch": "feat/with-numstat",
            "behind": 0,
            "clean_merge": True,
        }
        numstat = {
            "dual_pipeline/parser.py": (40, 10),
            "tests/test_parser.py": (20, 5),
        }
        res = evaluate_refactoring_complexity(item, numstat=numstat)
        self.assertEqual(res.churn, 75)
        self.assertEqual(res.file_count, 2)
        self.assertIn(res.size, ("XS", "S"))

    def test_queue_sort_key_prioritizes_lower_complexity(self):
        # Two branches with same tier (P1), same theme, same risk (0), same value (10.0)
        item_xs = {
            "branch": "feat/quick-win",
            "tier": "P1",
            "risk_tier": 0,
            "value_score": 10.0,
            "complexity": "XS",
        }
        item_xl = {
            "branch": "feat/heavy-refactor",
            "tier": "P1",
            "risk_tier": 0,
            "value_score": 10.0,
            "complexity": "XL",
        }
        key_xs = queue_sort_key(item_xs)
        key_xl = queue_sort_key(item_xl)
        # item_xs should sort BEFORE item_xl (key_xs < key_xl)
        self.assertLess(key_xs, key_xl)

    def test_complexity_dataclass_serialization(self):
        assess = ComplexityAssessment(
            size="M",
            score=7,
            churn=300,
            file_count=5,
            behind=2,
            clean_merge=True,
            layer_crossings=["Tools/Governance"],
            rationale="Test rationale",
        )
        d = assess.to_dict()
        self.assertEqual(d["size"], "M")
        self.assertEqual(d["rank"], 2)
        self.assertEqual(d["score"], 7)
        self.assertEqual(d["layer_crossings"], ["Tools/Governance"])


if __name__ == "__main__":
    unittest.main()
