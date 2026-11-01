import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class TestPRTableIntelligenceAdversarial(unittest.TestCase):
    def test_pr_table_intelligence_adversarial_malformed_registry(self):
        from tools.pr_priority_engine import derive_priority, load_rubric

        rubric = load_rubric()
        bad_registry = {
            "branch1": ["Not a dict"],
            "branch2": None,
            "branch3": 123
        }

        res1 = derive_priority("branch1", [], bad_registry, rubric)
        res2 = derive_priority("branch2", [], bad_registry, rubric)
        res3 = derive_priority("branch3", [], bad_registry, rubric)

        self.assertEqual(res1["tier"], "P3.1")
        self.assertEqual(res2["tier"], "P3.1")
        self.assertEqual(res3["tier"], "P3.1")

    def test_pr_table_intelligence_adversarial_deletion_bypass(self):
        from tools.pr_topology_core import compute_dispositions, compute_topology_matrix

        matrix = compute_topology_matrix([], ROOT, "main")
        dispositions = compute_dispositions(matrix)

        self.assertIsInstance(dispositions, dict)
        self.assertEqual(len(dispositions), 0)

    def test_pr_table_intelligence_adversarial_stale_plan(self):
        from tools.pr_governance_core.inspection import _parse_waves_from_branch

        open_waves, completed_waves = _parse_waves_from_branch("fake-adversarial-branch-that-does-not-exist")
        self.assertEqual(open_waves, "?")
        self.assertEqual(completed_waves, "?")

if __name__ == '__main__':
    unittest.main()
