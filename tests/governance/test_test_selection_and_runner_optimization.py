"""
tests/governance/test_test_selection_and_runner_optimization.py

Verification tests for Wave 3:
- Impacted test selection and fail-closed runner/policy self-downgrade protection.
- Unknown path full-suite fallback.
- Test ID deduplication across single-process and multi-worker runners.
- Suite building and pattern deduplication.
"""

from __future__ import annotations

import unittest
from typing import List

from tools.readiness_planner import (
    RUNNER_AND_POLICY_PREFIXES,
    filter_impacted_test_modules,
)
from tools.run_tests_with_progress import (
    ProgressBarTestRunner,
    classify_and_build_suites,
)


class DummyTestCase(unittest.TestCase):
    __test__ = False

    def test_alpha(self) -> None:
        pass

    def test_beta(self) -> None:
        pass


class TestTestSelectionAndRunnerOptimization(unittest.TestCase):
    """Verify test selection logic, fail-closed guards, and runner deduplication."""

    def setUp(self) -> None:
        self.base_modules: List[str] = [
            "tests.test_runtime_evidence_claims",
            "tests.test_unified_agent_architecture",
            "tests.test_credentials",
            "tests.test_pipeline_a_high_signal_eval",
            "tests.test_pipeline_b_high_signal_eval",
            "tests.test_parameters",
            "tests.test_config_hasher",
            "tests.governance.test_pr_governance",
        ]

    def test_runner_and_policy_self_downgrade_protection(self) -> None:
        """Touching runner infrastructure or governance policies must force full suite."""
        for sensitive_path in RUNNER_AND_POLICY_PREFIXES:
            target = f"{sensitive_path}/submodule.py" if sensitive_path.endswith("/") else sensitive_path
            selected = filter_impacted_test_modules(
                target_files=[target],
                test_mode="impacted",
                base_modules=self.base_modules,
            )
            self.assertEqual(
                selected,
                self.base_modules,
                f"Expected full suite fallback for sensitive path: {sensitive_path}",
            )

    def test_unknown_path_fail_closed_fallback(self) -> None:
        """Touching unmapped paths must fail closed to full suite."""
        selected = filter_impacted_test_modules(
            target_files=["some_random_module/worker.py"],
            test_mode="impacted",
            base_modules=self.base_modules,
        )
        self.assertEqual(selected, self.base_modules)

    def test_known_impact_mapping_selection(self) -> None:
        """Touching mapped code must select smoke modules plus mapped tests without full fallback."""
        selected = filter_impacted_test_modules(
            target_files=["storage/connection.py"],
            test_mode="impacted",
            base_modules=self.base_modules,
        )
        self.assertIn("tests.test_credentials", selected)
        self.assertIn("tests.test_parameters", selected)
        self.assertIn("tests.test_config_hasher", selected)
        # Unrelated modules should not be pulled in
        self.assertNotIn("tests.test_pipeline_a_high_signal_eval", selected)

    def test_changed_test_file_inclusion(self) -> None:
        """Changed test files under tests/ must be included in selection."""
        selected = filter_impacted_test_modules(
            target_files=["tests/governance/test_custom_verification.py"],
            test_mode="impacted",
            base_modules=self.base_modules,
        )
        self.assertIn("tests.governance.test_custom_verification", selected)

    def test_safe_non_code_prefixes_smoke_only(self) -> None:
        """Targeting solely non-code documentation or reports must run smoke tests."""
        selected = filter_impacted_test_modules(
            target_files=["docs/plans/consolidate.md", "reports/audit.json"],
            test_mode="impacted",
            base_modules=self.base_modules,
        )
        self.assertIn("tests.test_runtime_evidence_claims", selected)
        self.assertIn("tests.test_credentials", selected)
        self.assertNotIn("tests.test_pipeline_a_high_signal_eval", selected)

    def test_classify_and_build_suites_deduplication(self) -> None:
        """Duplicate module patterns must be deduplicated during suite construction."""
        duplicated_patterns = [
            "tests.governance.test_scope_collection_regressions",
            "tests.governance.test_scope_collection_regressions",
            "tests/governance/test_scope_collection_regressions.py",
        ]
        suite, pytest_items = classify_and_build_suites(duplicated_patterns)

        def _extract_ids(s: unittest.TestSuite) -> List[str]:
            ids: List[str] = []
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    ids.extend(_extract_ids(item))
                else:
                    ids.append(item.id())
            return ids

        test_ids = _extract_ids(suite)
        self.assertEqual(len(test_ids), len(set(test_ids)), "Suite contains duplicate test IDs")

    def test_progress_bar_runner_deduplicates_suite(self) -> None:
        """ProgressBarTestRunner must deduplicate duplicated test cases in a TestSuite."""
        suite = unittest.TestSuite()
        suite.addTest(DummyTestCase("test_alpha"))
        suite.addTest(DummyTestCase("test_alpha"))  # Duplicate
        suite.addTest(DummyTestCase("test_beta"))
        suite.addTest(DummyTestCase("test_beta"))   # Duplicate

        runner = ProgressBarTestRunner(verbosity=0)
        # Execute run (runner deduplicates internally)
        result = runner.run(suite)
        self.assertEqual(result.testsRun, 2, "Duplicate test instances must not execute multiple times")


if __name__ == "__main__":
    unittest.main()
