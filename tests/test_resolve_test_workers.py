import unittest

from tools.readiness_core import _resolve_test_workers


class TestResolveTestWorkers(unittest.TestCase):

    def test_override_is_respected(self):
        # Override is given as 5, we have 10 modules
        # Should return min(override, num_modules)
        self.assertEqual(_resolve_test_workers(cpu_count=2, num_modules=10, user_override=5), 5)

    def test_override_is_capped_by_modules(self):
        # Override is 20, but only 3 modules
        # Cannot have more workers than modules
        self.assertEqual(_resolve_test_workers(cpu_count=16, num_modules=3, user_override=20), 3)

    def test_auto_scaling_base_case(self):
        # 26 CPUs, 10 modules, no override
        # Should be min(num_modules, max(1, min(8, cpu_count - 2)))
        # hw_scaled = min(8, 26 - 2) -> 8
        # min(10, 8) -> 8
        self.assertEqual(_resolve_test_workers(cpu_count=26, num_modules=10, user_override=None), 8)

    def test_auto_scaling_low_cpu_count(self):
        # 4 CPUs, 10 modules
        # hw_scaled = min(8, 4 - 2) -> 2
        # min(10, 2) -> 2
        self.assertEqual(_resolve_test_workers(cpu_count=4, num_modules=10, user_override=0), 2)

    def test_auto_scaling_minimum_one_worker(self):
        # 1 CPU, 10 modules
        # hw_scaled = max(1, min(8, 1 - 2)) -> max(1, -1) -> 1
        self.assertEqual(_resolve_test_workers(cpu_count=1, num_modules=10, user_override=None), 1)

    def test_zero_modules_returns_one(self):
        # Edge case: 0 modules to test
        # Should gracefully return 1 to avoid ZeroDivisionError upstream
        self.assertEqual(_resolve_test_workers(cpu_count=8, num_modules=0, user_override=None), 1)

if __name__ == "__main__":
    unittest.main()
