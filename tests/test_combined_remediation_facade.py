"""Unit tests for dual_pipeline.combined_remediation facade.

Verifies symbol exports, policy constants, and function callability.
"""
from __future__ import annotations

import unittest

import dual_pipeline.combined_remediation as combined_facade


class TestCombinedRemediationFacade(unittest.TestCase):
    def test_policy_constants_exported(self) -> None:
        self.assertEqual(
            combined_facade.COMBINED_REMEDIATION_POLICY_VERSION, "2026-08-04.1"
        )
        self.assertEqual(
            combined_facade.COMBINED_REMEDIATION_CONTROL_KEY,
            "com.partner_advisory.nee.combined-remediation-control",
        )
        self.assertTrue(hasattr(combined_facade, "SOURCE_RETIREMENT_POLICY_VERSION"))
        self.assertTrue(
            hasattr(combined_facade, "PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION")
        )

    def test_core_classes_exported(self) -> None:
        self.assertTrue(hasattr(combined_facade, "EmbeddingBackend"))
        self.assertTrue(hasattr(combined_facade, "ReleaseManager"))
        self.assertTrue(hasattr(combined_facade, "RunRecorder"))

    def test_remediation_functions_callable(self) -> None:
        expected_functions = [
            "validate_combined_remediation_control",
            "build_combined_remediation_plan",
            "apply_combined_remediation_plan",
            "prepare_combined_remediation_release",
            "record_combined_remediation_release_events",
            "validate_source_retirement_control",
            "build_source_retirement_plan",
            "apply_source_retirement_plan",
            "validate_project_status_reclassification_control",
            "build_project_status_reclassification_plan",
            "apply_project_status_reclassification_plan",
        ]
        for fn in expected_functions:
            self.assertTrue(
                callable(getattr(combined_facade, fn, None)),
                f"Expected function {fn} to be callable from combined_remediation facade",
            )


if __name__ == "__main__":
    unittest.main()
