"""Unit tests for Wave 1: HITL Confidence Intervals & Uncertainty Quantification."""
import unittest

from dual_pipeline.hitl_core.margin_evaluator import _format_single_option
from dual_pipeline.hitl_governance_core import (
    CalibratedScore,
    ConfidenceResult,
    compute_bayesian_calibrated_score,
    compute_beta_credible_interval,
)
from dual_pipeline.hitl_governance_core.bayesian import _calibrate_single_option
from dual_pipeline.hitl_governance_core.surfacing import _standardize_option


class TestHITLConfidenceIntervals(unittest.TestCase):
    """Verifies Beta posterior HDI credible interval computation and uncertainty metadata."""

    def test_calibrated_score_float_subclass_contract(self) -> None:
        """CalibratedScore must behave as a float while exposing rich uncertainty attributes."""
        score = compute_bayesian_calibrated_score(0.60, 0.0, 0.0, prior_strength=2.0)
        self.assertIsInstance(score, float)
        self.assertIsInstance(score, CalibratedScore)
        self.assertEqual(score, 0.60)
        self.assertEqual(round(score, 2), 0.60)
        self.assertAlmostEqual(score + 0.1, 0.70, places=2)

        # Check uncertainty attributes
        self.assertEqual(score.point_estimate, 0.60)
        self.assertGreaterEqual(score.lower_bound, 0.05)
        self.assertLessEqual(score.upper_bound, 0.99)
        self.assertAlmostEqual(score.interval_width, score.upper_bound - score.lower_bound, places=4)
        self.assertEqual(score.sample_count, 0)
        self.assertEqual(score.tier, "MEDIUM")
        self.assertFalse(score.is_precise)

    def test_credible_interval_narrows_with_sample_size(self) -> None:
        """As affirmations accumulate, credible interval width must narrow monotonically."""
        score_0 = compute_bayesian_calibrated_score(0.60, 0.0, 0.0, prior_strength=4.0, sample_count=0)
        score_10 = compute_bayesian_calibrated_score(0.60, 10.0, 0.0, prior_strength=4.0, sample_count=10)
        score_50 = compute_bayesian_calibrated_score(0.60, 50.0, 0.0, prior_strength=4.0, sample_count=50)

        # Interval width must decrease
        self.assertGreater(score_0.interval_width, score_10.interval_width)
        self.assertGreater(score_10.interval_width, score_50.interval_width)

        # With 50 observations, interval width should be tight (< 0.20)
        self.assertLess(score_50.interval_width, 0.20)
        self.assertTrue(score_50.is_precise)

    def test_confidence_result_conversion(self) -> None:
        """CalibratedScore cleanly converts to ConfidenceResult dataclass and dict."""
        score = compute_bayesian_calibrated_score(0.75, 10.0, 1.0, prior_strength=4.0, sample_count=11)
        res = score.to_confidence_result()
        self.assertIsInstance(res, ConfidenceResult)
        self.assertEqual(res.point_estimate, score.point_estimate)
        self.assertEqual(res.sample_count, 11)
        self.assertEqual(res.tier, "HIGH")

        d = score.to_dict()
        self.assertEqual(d["point_estimate"], score.point_estimate)
        self.assertEqual(d["sample_count"], 11)
        self.assertIn("interval_width", d)

    def test_beta_credible_interval_fallback_stability(self) -> None:
        """compute_beta_credible_interval handles extreme parameters and clamping gracefully."""
        lo, hi = compute_beta_credible_interval(alpha=100.0, beta=1.0)
        self.assertGreater(lo, 0.85)
        self.assertLessEqual(hi, 0.99)
        self.assertLess(lo, hi)

        lo_symm, hi_symm = compute_beta_credible_interval(alpha=2.0, beta=2.0)
        self.assertAlmostEqual(lo_symm + hi_symm, 1.0, delta=0.1)

    def test_calibrate_single_option_populates_interval_fields(self) -> None:
        """_calibrate_single_option enriches item dict with complete uncertainty fields."""
        opt = {"key": "reconcile_entity", "label": "Reconcile Entity", "confidence": 0.60}
        aff_w = {"reconcile_entity": 15.0}
        rej_w = {}
        aff_c = {"reconcile_entity": 15}
        rej_c = {}

        item = _calibrate_single_option(
            opt=opt,
            aff_weights=aff_w,
            rej_weights=rej_w,
            aff_counts=aff_c,
            rej_counts=rej_c,
            eff_prior_strength=4.0,
            min_bound=0.05,
            max_bound=0.99,
            half_life_days=None,
        )

        self.assertIn("confidence_lower", item)
        self.assertIn("confidence_upper", item)
        self.assertIn("confidence_interval", item)
        self.assertIn("confidence_interval_width", item)
        self.assertEqual(item["confidence_sample_count"], 15)
        self.assertEqual(len(item["confidence_interval"]), 2)
        self.assertGreater(item["confidence_score"], 0.60)
        self.assertEqual(item["confidence_level"], "HIGH")

    def test_standardize_option_handles_intervals(self) -> None:
        """_standardize_option handles uncalibrated options and parses interval strings."""
        # Uncalibrated dict option
        raw_dict = {"label": "Test Option", "confidence": 0.70}
        std_dict = _standardize_option(raw_dict)
        self.assertIn("confidence_lower", std_dict)
        self.assertIn("confidence_upper", std_dict)
        self.assertEqual(std_dict["confidence_sample_count"], 0)

        # String option with formatted interval
        formatted_str = "(Recommended) (Confidence: 82% - HIGH) [75%–89%, n=24] Accept Entity"
        std_str = _standardize_option(formatted_str)
        self.assertEqual(std_str["confidence_score"], 0.82)
        self.assertEqual(std_str["confidence_level"], "HIGH")
        self.assertEqual(std_str["confidence_lower"], 0.75)
        self.assertEqual(std_str["confidence_upper"], 0.89)
        self.assertEqual(std_str["confidence_sample_count"], 24)

    def test_format_single_option_renders_interval_when_present(self) -> None:
        """_format_single_option renders [lo%–hi%, n=K] in ask_user_question option text."""
        opt_with_ci = {
            "key": "opt_a",
            "label": "Merge duplicate records",
            "confidence": 0.85,
            "confidence_lower": 0.78,
            "confidence_upper": 0.92,
            "confidence_sample_count": 30,
        }
        formatted = _format_single_option(0, opt_with_ci)
        self.assertIn("(Recommended)", formatted)
        self.assertIn("(Confidence: 85% - HIGH)", formatted)
        self.assertIn("[78%–92%, n=30]", formatted)
        self.assertIn("Merge duplicate records", formatted)

        # Without CI
        opt_no_ci = {"key": "opt_b", "label": "Quarantine candidate", "confidence": 0.50}
        formatted_no_ci = _format_single_option(1, opt_no_ci)
        self.assertNotIn("(Recommended)", formatted_no_ci)
        self.assertIn("(Confidence: 50% - MEDIUM)", formatted_no_ci)
        self.assertNotIn("[", formatted_no_ci)


if __name__ == "__main__":
    unittest.main()
