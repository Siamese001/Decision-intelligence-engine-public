"""Unit tests for targeted differential remediation state machine."""
from __future__ import annotations

import unittest

from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


class DifferentialRemediationTests(unittest.TestCase):
    def test_compute_differential_patch_scope_isolated_failure(self) -> None:
        sm = SelectiveRemediationStateMachine(max_attempts=3)
        qa_report = {
            "output_03_canonical_signal_projection": {
                "passed": False,
                "checks": [
                    {"name": "check_entity_locators", "passed": False, "details": "Missing locator on entity:1"},
                    {"name": "check_predicates", "passed": True},
                ],
            }
        }
        judge_report = {
            "output_1_evaluation": {"verdict": "PASS"},
            "output_2_evaluation": {"verdict": "PASS"},
            "output_3_evaluation": {"verdict": "PASS"},
        }
        scope = sm.compute_differential_patch_scope(qa_report, judge_report)
        self.assertTrue(scope["is_differential_patchable"])
        self.assertEqual(scope["failed_count"], 1)
        self.assertEqual(scope["patch_strategy"], "TARGETED_MICRO_PATCH")

    def test_compute_differential_patch_scope_exceeds_threshold(self) -> None:
        sm = SelectiveRemediationStateMachine(max_attempts=3)
        qa_report = {
            "output_03_canonical_signal_projection": {
                "passed": False,
                "checks": [
                    {"name": "check_entity_locators", "passed": False, "details": "Error 1"},
                    {"name": "check_predicates", "passed": False, "details": "Error 2"},
                    {"name": "check_types", "passed": False, "details": "Error 3"},
                ],
            }
        }
        judge_report = {
            "output_1_evaluation": {"verdict": "FAIL", "dimension_scores": {"grounding": 50}},
        }
        scope = sm.compute_differential_patch_scope(qa_report, judge_report)
        self.assertFalse(scope["is_differential_patchable"])
        self.assertGreater(scope["failed_count"], 2)
        self.assertEqual(scope["patch_strategy"], "GLOBAL_RESYNTHESIS")
