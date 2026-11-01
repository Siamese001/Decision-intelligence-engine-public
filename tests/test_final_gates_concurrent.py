import unittest
from pathlib import Path

from tools.readiness_concurrent import _execute_final_gates_concurrent


class TestFinalGatesConcurrent(unittest.TestCase):
    def setUp(self):
        self.gate_28 = ("Stage 28", ["echo", "28"])
        self.gate_29 = ("Stage 29", ["echo", "29"])
        self.total_stages = 29
        self.head_sha = "dummy_sha"
        self.target_files = []
        self.verbose = False
        self.repo_root = Path("/tmp")

    def test_both_pass(self):
        def mock_run_stage(name, cmd):
            return True, f"Success {name}", 0.1

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_run_stage
        )
        self.assertTrue(passed)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["stage_index"], 28)
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[1]["stage_index"], 29)
        self.assertEqual(results[1]["status"], "PASS")

    def test_gate_28_fail(self):
        def mock_run_stage(name, cmd):
            if name == "Stage 28":
                return False, "Fail 28", 0.1
            return True, "Success 29", 0.1

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_run_stage
        )
        self.assertFalse(passed)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[1]["status"], "PASS")

    def test_gate_29_fail(self):
        def mock_run_stage(name, cmd):
            if name == "Stage 29":
                return False, "Fail 29", 0.1
            return True, "Success 28", 0.1

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_run_stage
        )
        self.assertFalse(passed)
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[1]["status"], "FAIL")

    def test_both_fail(self):
        def mock_run_stage(name, cmd):
            return False, f"Fail {name}", 0.1

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_run_stage
        )
        self.assertFalse(passed)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[1]["status"], "FAIL")

if __name__ == "__main__":
    unittest.main()
