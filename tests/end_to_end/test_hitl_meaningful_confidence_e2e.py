"""
tests/end_to_end/test_hitl_meaningful_confidence_e2e.py

End-to-End lifecycle test suite for Meaningful Confidence Presentation.
Verifies complete lifecycle:
1. Zero-sample options (n=0, uncalibrated prior) strictly suppress bracketed intervals.
2. Calibrated options (n >= 5, tight width) render credible intervals under sparse_only.
3. Mode 'never' strictly suppresses intervals regardless of sample size.
4. Degenerate intervals (width >= 0.80) are strictly suppressed.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.hitl_core.margin_evaluator import _format_single_option
from dual_pipeline.hitl_governance_core.layman_clarity import (
    build_layman_option,
    format_layman_display_label,
    should_render_credible_interval,
)


class TestHITLMeaningfulConfidenceE2E(unittest.TestCase):
    """E2E lifecycle test verifying meaningful confidence presentation and interval suppression."""

    def test_e2e_uncalibrated_option_suppresses_bracketed_interval(self) -> None:
        """Options with n=0 or uncalibrated priors must never render bracketed intervals."""
        opt = build_layman_option(
            label="Save working tree and land three improvements",
            plain_english_summary="Preserves existing work while landing safe improvements.",
            impact_statement="Reduces risk by delivering immediate fixes without destabilizing untested layers.",
            confidence_score=0.58,
            credible_interval=(0.05, 0.95),
            sample_size=0,
            recommended=True,
        )
        display = format_layman_display_label(opt)
        self.assertIn("(Recommended)", display)
        self.assertIn("(Confidence: MEDIUM (58%))", display)
        self.assertNotIn("[", display)
        self.assertNotIn("n=0", display)
        self.assertNotIn("5%–95%", display)

        # Same option through margin_evaluator _format_single_option
        formatted = _format_single_option(0, opt)
        self.assertIn("(Confidence: 58% - MEDIUM)", formatted)
        self.assertNotIn("[", formatted)
        self.assertNotIn("n=0", formatted)

    def test_e2e_calibrated_mature_option_renders_interval_under_sparse_only(self) -> None:
        """Options with n >= 30 and tight interval width render intervals under sparse_only."""
        opt = build_layman_option(
            label="Consolidate duplicate test runners",
            plain_english_summary="Merges secondary test harness into primary runner.",
            impact_statement="Reduces CI execution time while maintaining identical coverage.",
            confidence_score=0.91,
            credible_interval=(0.86, 0.96),
            sample_size=35,
            recommended=True,
        )
        display = format_layman_display_label(opt)
        self.assertIn("(Recommended)", display)
        self.assertIn("(Confidence: HIGH (91%) [86%–96%, n=35])", display)

        # Same option through margin_evaluator _format_single_option
        opt_for_eval: Dict[str, Any] = {
            "label": "Consolidate duplicate test runners",
            "confidence": 0.91,
            "confidence_lower": 0.86,
            "confidence_upper": 0.96,
            "confidence_sample_count": 35,
        }
        formatted = _format_single_option(0, opt_for_eval)
        self.assertIn("[86%–96%, n=35]", formatted)

    def test_e2e_mode_never_suppresses_even_calibrated_intervals(self) -> None:
        """When uncertainty_display is 'never', intervals are suppressed even for high n."""
        self.assertFalse(
            should_render_credible_interval(
                n=50, lo=0.88, hi=0.94, uncertainty_display="never"
            )
        )

    def test_e2e_degenerate_interval_suppressed_regardless_of_sample_size(self) -> None:
        """Intervals spanning >= 80% width are degenerate and must be suppressed."""
        # Width 0.90 (0.05 to 0.95)
        self.assertFalse(
            should_render_credible_interval(
                n=10, lo=0.05, hi=0.95, uncertainty_display="sparse_only"
            )
        )
        self.assertFalse(
            should_render_credible_interval(
                n=10, lo=0.05, hi=0.95, uncertainty_display="always"
            )
        )


if __name__ == "__main__":
    unittest.main()
