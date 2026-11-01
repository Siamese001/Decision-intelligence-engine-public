#!/usr/bin/env python3
"""
tests/test_scheduled_task_gate_enrollment.py

Regression test suite ensuring scheduled task modules are properly enrolled
in PR readiness gates (_build_core_test_modules) and CI test discovery (resolve_all_tests).
Prevents silent test escape when prompts, task configs, or runner engines are modified.
"""
from __future__ import annotations

import unittest

from tools.ci_gates_core.base_tests import BASE_DETERMINISTIC_TESTS
from tools.readiness_core import _build_core_test_modules
from tools.test_discovery_core.discovery import resolve_all_tests


class TestScheduledTaskGateEnrollment(unittest.TestCase):
    """Verifies that scheduled task changes trigger gate execution and profile discovery."""

    def test_scheduled_task_changes_trigger_module_enrollment(self) -> None:
        """Modifying prompt, config, or core runner files enrolls scheduled task test modules."""
        target_sets = [
            ["automations/prompts/pr_queue_observer_agent.md"],
            ["config/scheduled_tasks/scheduled_tasks.json"],
            ["tools/scheduled_task_core/runner.py"],
            ["dual_pipeline/hitl_governance_core/scheduler_adapter.py"],
        ]

        expected_modules = {
            "tests.test_scheduled_task_contracts",
            "tests.test_scheduled_task_authority",
            "tests.test_scheduled_task_evidence",
            "tests.test_scheduled_task_remediation",
            "tests.test_scheduled_task_adapters",
            "tests.test_scheduled_task_lifecycle",
            "tests.test_scheduled_task_gate_enrollment",
            "tests.adversarial.test_scheduled_task_reliability_adversarial",
        }

        for targets in target_sets:
            enrolled = _build_core_test_modules(targets, full=False)
            for mod in expected_modules:
                self.assertIn(
                    mod,
                    enrolled,
                    f"Module {mod} was not enrolled when modifying {targets}",
                )

    def test_unrelated_changes_omit_scheduled_task_modules(self) -> None:
        """Modifying unrelated files does not enroll scheduled task test modules in scoped mode."""
        unrelated_targets = ["dual_pipeline/core.py", "storage/connection.py"]
        enrolled = _build_core_test_modules(unrelated_targets, full=False)

        self.assertNotIn("tests.test_scheduled_task_contracts", enrolled)
        self.assertNotIn("tests.test_scheduled_task_adapters", enrolled)
        self.assertNotIn("tests.test_scheduled_task_authority", enrolled)

    def test_full_mode_enrolls_scheduled_task_modules(self) -> None:
        """Full mode verification unconditionally enrolls scheduled task modules."""
        enrolled = _build_core_test_modules([], full=True)

        self.assertIn("tests.test_scheduled_task_contracts", enrolled)
        self.assertIn("tests.test_scheduled_task_adapters", enrolled)
        self.assertIn("tests.test_scheduled_task_authority", enrolled)

    def test_scheduled_task_tests_resolve_to_deterministic_profile(self) -> None:
        """Scheduled task tests must resolve under 'deterministic' profile, not 'e2e'."""
        det_tests, e2e_tests, bge_tests = resolve_all_tests(
            base_deterministic=BASE_DETERMINISTIC_TESTS
        )

        expected_deterministic = [
            "tests.test_scheduled_task_contracts",
            "tests.test_scheduled_task_authority",
            "tests.test_scheduled_task_evidence",
            "tests.test_scheduled_task_remediation",
            "tests.test_scheduled_task_adapters",
            "tests.test_scheduled_task_lifecycle",
            "tests.test_scheduled_task_gate_enrollment",
            "tests.adversarial.test_scheduled_task_reliability_adversarial",
        ]

        for mod in expected_deterministic:
            self.assertIn(
                mod,
                det_tests,
                f"{mod} missing from deterministic test resolution",
            )
            self.assertNotIn(
                mod,
                e2e_tests,
                f"{mod} incorrectly resolved to e2e profile",
            )
            self.assertNotIn(
                mod,
                bge_tests,
                f"{mod} incorrectly resolved to bge profile",
            )


if __name__ == "__main__":
    unittest.main()
