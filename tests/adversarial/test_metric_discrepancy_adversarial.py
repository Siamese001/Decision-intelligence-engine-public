"""
tests/adversarial/test_metric_discrepancy_adversarial.py

Adversarial test suite for Wave 3: Numerical Metric Discrepancy & Enterprise Asset Reconciler.
Tests boundary limits, noise immunity against timestamps/dates, multi-domain divergence,
and atomic presentation invariants.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.transcript_stages_core.metric_discrepancy_evaluator import (
    evaluate_metric_discrepancies,
    extract_numerical_metrics,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


class TestMetricDiscrepancyAdversarial(unittest.TestCase):
    def test_noise_immunity_against_timestamps_dates_and_counts(self) -> None:
        """Timestamps, years, and non-asset counts must not trigger false positive metric divergence."""
        clean_transcript = """00:01:12
Alex Brannan: On September 29, 2026 at 03:00 PM, we have 5 or 6 partners joining this call.
Dianne Eldridge: Yes, in 2025 we had 4 partners, and now in 2026 we have 6 partners.
"""
        extracted = extract_numerical_metrics(clean_transcript)
        # Should not extract any asset domain metrics
        self.assertEqual(len(extracted), 0)

        meta: Dict[str, Any] = {"meeting_name": "Noise Test"}
        # Must not raise HITLRequiredError
        res = evaluate_metric_discrepancies(clean_transcript, meta)
        self.assertNotIn("hitl_metric_receipts", res)

    def test_boundary_variance_threshold(self) -> None:
        """Variance strictly <= 15% passes; variance > 15% raises HITLRequiredError."""
        # 1,000 vs 860 -> (1000 - 860) / 1000 = 14% <= 15% (pass)
        pass_transcript = """00:10:00
Speaker A: We have 1,000 provisioned Gemini Enterprise licenses.
Speaker B: We verified 860 active Gemini Enterprise licenses logged in.
"""
        meta_pass: Dict[str, Any] = {"meeting_name": "Boundary Pass"}
        res = evaluate_metric_discrepancies(pass_transcript, meta_pass)
        self.assertNotIn("invalidate_prior_outputs", res)

        # 1,000 vs 800 -> (1000 - 800) / 1000 = 20% > 15% (fail/HITL)
        fail_transcript = """00:10:00
Speaker A: We have 1,000 provisioned Gemini Enterprise licenses.
Speaker B: We verified 800 active Gemini Enterprise licenses logged in.
"""
        meta_fail: Dict[str, Any] = {"meeting_name": "Boundary Fail"}
        with self.assertRaises(HITLRequiredError) as ctx:
            evaluate_metric_discrepancies(fail_transcript, meta_fail)
        self.assertEqual(ctx.exception.hitl_type, "metric_discrepancy")

    def test_extreme_divergence_partner_google_case(self) -> None:
        """The 5,750 vs 2,000 license divergence from PartnerAdvisory/Google apex_global sync triggers atomic HITL."""
        transcript = """00:22:40
Alex Brannan: We have 5,750 provisioned Gemini Enterprise licenses, with 4,750 users logged in and 1,000 unactivated.
Dianne Eldridge: For the nuclear division, there are approximately 2,000 assigned Gemini Enterprise licenses.
Drew Oakley: We have 2,000 available for immediate allocation.
"""
        meta: Dict[str, Any] = {"meeting_name": "PartnerAdvisory Google apex_global"}
        with self.assertRaises(HITLRequiredError) as ctx:
            evaluate_metric_discrepancies(transcript, meta)

        err = ctx.exception
        self.assertIn("5,750", err.prompt)
        self.assertIn("2,000", err.prompt)
        self.assertTrue(any("5,750" in opt["label"] for opt in err.options))
        self.assertTrue(any("2,000" in opt["label"] for opt in err.options))

    def test_bypass_and_confirmation_flags(self) -> None:
        """metrics_confirmed_by_operator or bypass flag prevents halting."""
        transcript = """00:10:00
Speaker A: We have 5,000 provisioned Gemini Enterprise licenses.
Speaker B: We have 1,000 assigned Gemini Enterprise licenses.
"""
        meta: Dict[str, Any] = {
            "meeting_name": "Bypass Test",
            "metrics_confirmed_by_operator": True,
        }
        res = evaluate_metric_discrepancies(transcript, meta)
        self.assertIn("metrics_confirmed_by_operator", res)


if __name__ == "__main__":
    unittest.main()
