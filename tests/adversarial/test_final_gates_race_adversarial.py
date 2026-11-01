import random
import time
import unittest
from pathlib import Path

from tools.readiness_concurrent import _execute_final_gates_concurrent


class TestFinalGatesRaceAdversarial(unittest.TestCase):
    def setUp(self):
        self.gate_28 = ("Stage 28", ["echo", "28"])
        self.gate_29 = ("Stage 29", ["echo", "29"])
        self.total_stages = 29
        self.head_sha = "dummy_sha"
        self.target_files = []
        self.verbose = False
        self.repo_root = Path("/tmp")

    def test_adversarial_race_condition_gate_interleaving(self):
        """Ensure results are strictly ordered 28 then 29 even if 29 finishes way earlier."""
        def mock_variable_latency(name, cmd):
            if name == "Stage 28":
                time.sleep(0.05)  # 28 is slow
            else:
                time.sleep(0.01)  # 29 is fast
            return True, f"Success {name}", 0.01

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_variable_latency
        )
        self.assertTrue(passed)
        self.assertEqual(len(results), 2)
        # Even though 29 finishes first, 28 MUST be the first result
        self.assertEqual(results[0]["stage_index"], 28)
        self.assertEqual(results[0]["stage_name"], "Stage 28")
        self.assertEqual(results[1]["stage_index"], 29)
        self.assertEqual(results[1]["stage_name"], "Stage 29")

    def test_adversarial_race_condition_on_shared_state(self):
        """Ensure that mutating shared variables inside mock_run doesn't break execution."""
        shared_state = {"counter": 0}

        def mock_run_with_mutation(name, cmd):
            # Adversarial: induce sleep to allow race
            time.sleep(random.uniform(0.01, 0.05))
            val = shared_state["counter"]
            time.sleep(0.01)
            shared_state["counter"] = val + 1
            return True, f"Success {name} {shared_state['counter']}", 0.01

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_run_with_mutation
        )
        self.assertTrue(passed)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["stage_index"], 28)
        self.assertEqual(results[1]["stage_index"], 29)
        # Note: we don't care if counter lost updates because they are executed concurrently in threads.
        # We only care that the gates runner itself doesn't crash or interleave the receipt format.

    def test_adversarial_stdout_corruption_handling(self):
        """Ensure massive stdout output from one gate doesn't corrupt the other gate's return."""
        def mock_heavy_stdout(name, cmd):
            if name == "Stage 28":
                return True, "A" * 10000, 0.01
            else:
                return True, "B" * 5, 0.01

        results, passed = _execute_final_gates_concurrent(
            self.gate_28, self.gate_29, self.total_stages, False, self.head_sha,
            self.target_files, self.verbose, self.repo_root, mock_heavy_stdout
        )
        self.assertTrue(passed)
        # 28 should be truncated to 500 chars exactly
        self.assertEqual(len(results[0]["output_summary"]), 500)
        self.assertEqual(results[0]["output_summary"], "A" * 500)

        # 29 should be untouched
        self.assertEqual(len(results[1]["output_summary"]), 5)
        self.assertEqual(results[1]["output_summary"], "B" * 5)

if __name__ == "__main__":
    unittest.main()
