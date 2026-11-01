"""
tests/adversarial/test_hitl_meaningless_confidence_adversarial.py

Adversarial test suite targeting HITL confidence intervals and meaningless option formatting.
Enforces four mandatory edge-case categories per e2e-and-edge-case-coverage.md:
1. Category: Boundary payload (null samples, 0-length audio/labels, extreme bounds [0.0, 1.0]).
2. Category: Malformed input (inverted bounds lo > hi, negative n, non-numeric sample counts).
3. Category: Invariant bypass attempt (attempting to smuggle [5%–95%, n=0] past linter).
4. Category: Exact production regression (pins the exact 3 options from the user's screenshot).
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.hitl_core.margin_evaluator import _format_single_option
from dual_pipeline.hitl_governance_core.layman_clarity import (
    build_layman_option,
    format_layman_display_label,
    scan_for_jargon,
    should_render_credible_interval,
)


class TestHITLMeaninglessConfidenceAdversarial(unittest.TestCase):
    """Adversarial stress testing against meaningless interval leakage."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_boundary_payload_null_and_zero_sample_counts(self) -> None:
        """Boundary inputs with None, 0, or negative sample counts must suppress intervals."""
        self.assertFalse(should_render_credible_interval(n=None, lo=0.1, hi=0.9))
        self.assertFalse(should_render_credible_interval(n=0, lo=0.1, hi=0.9))
        self.assertFalse(should_render_credible_interval(n=-1, lo=0.1, hi=0.9))
        self.assertFalse(should_render_credible_interval(n=0, lo=0.0, hi=1.0))

    # -------------------------------------------------------------------------
    # Category 2: Malformed input
    # -------------------------------------------------------------------------
    def test_malformed_input_inverted_bounds_and_degenerates(self) -> None:
        """Malformed bounds (lo > hi, massive width) fail closed and suppress formatting."""
        # Inverted bounds
        self.assertFalse(should_render_credible_interval(n=10, lo=0.95, hi=0.05))
        # Total span across probability space
        self.assertFalse(should_render_credible_interval(n=100, lo=0.05, hi=0.95))

    # -------------------------------------------------------------------------
    # Category 3: Invariant bypass attempt
    # -------------------------------------------------------------------------
    def test_invariant_bypass_attempt_catches_jargon_brackets(self) -> None:
        """Attempts to manually format or smuggle [5%–95%, n=0] trigger jargon violations."""
        forbidden_snippets = [
            "Option A (Confidence: HIGH (90%) [5%–95%, n=0])",
            "Option B (Confidence: MEDIUM (58%) [5%–95%, n=0])",
            "Option C [10%–90%, n=0] Perform action",
        ]
        for snippet in forbidden_snippets:
            violations = scan_for_jargon(snippet)
            self.assertTrue(
                any("zero-sample" in v.lower() for v in violations),
                f"Expected zero-sample jargon violation for '{snippet}', got: {violations}",
            )

    # -------------------------------------------------------------------------
    # Category 4: Exact production regression
    # -------------------------------------------------------------------------
    def test_exact_production_regression_user_screenshot_reproduction(self) -> None:
        """Exact reproduction of the user's screenshot:

        Verifies that all 3 options for candidate pr-queue-plan-d1-refactor-branch
        are formatted cleanly WITHOUT [5%–95%, n=0].
        """
        # Option 1: 58% Recommended
        opt1: Dict[str, Any] = {
            "label": "Save working tree and land three improvements",
            "plain_english_summary": (
                "Save it, then land only the 3 improvements main does not have yet."
            ),
            "impact_statement": (
                "Smallest and safest change, no duplicate work."
            ),
            "confidence_score": 0.58,
            "confidence_pct": 58.0,
            "confidence_level": "MEDIUM",
            "tier": "MEDIUM",
            "credible_interval": [0.05, 0.95],
            "sample_size": 0,
            "recommended": True,
        }
        # Option 2: 42%
        opt2: Dict[str, Any] = {
            "label": "Save and bring all five waves current with main line",
            "plain_english_summary": (
                "Save it, bring all 5 waves up to date with main, and land everything."
            ),
            "impact_statement": (
                "Uses all finished work, but increases rewrite scope."
            ),
            "confidence_score": 0.42,
            "confidence_pct": 42.0,
            "confidence_level": "MEDIUM",
            "tier": "MEDIUM",
            "credible_interval": [0.05, 0.95],
            "sample_size": 0,
            "recommended": False,
        }
        # Option 3: 10%
        opt3: Dict[str, Any] = {
            "label": "Save only as safety backup and land nothing now",
            "plain_english_summary": (
                "Save it only as a safety backup and land none of it now."
            ),
            "impact_statement": (
                "Lowest immediate risk, but repeats earlier merge conflicts."
            ),
            "confidence_score": 0.10,
            "confidence_pct": 10.0,
            "confidence_level": "LOW",
            "tier": "LOW",
            "credible_interval": [0.05, 0.95],
            "sample_size": 0,
            "recommended": False,
        }

        for opt in (opt1, opt2, opt3):
            # Check format_layman_display_label
            display = format_layman_display_label(opt)
            self.assertNotIn("[5%–95%", display)
            self.assertNotIn("n=0", display)
            self.assertNotIn("[", display)

            # Check _format_single_option
            single_fmt = _format_single_option(0 if opt.get("recommended") else 1, opt)
            self.assertNotIn("[5%–95%", single_fmt)
            self.assertNotIn("n=0", single_fmt)
            self.assertNotIn("[", single_fmt)


if __name__ == "__main__":
    unittest.main()
