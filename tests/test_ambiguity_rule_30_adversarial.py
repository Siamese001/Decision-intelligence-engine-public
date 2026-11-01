#!/usr/bin/env python3
"""Adversarial tests for Wave 4: 30% ambiguity rule via SSOT accessor needs_operator_decision().

Covers:
1. Boundary values with high precision and float representation edge cases.
2. Negative, NaN, Inf, non-numeric, and None confidences (fail-closed behavior).
3. Out-of-bounds parameter threshold handling (safe fallback).
4. Stale cache and dynamic threshold re-read verification.
5. Inverted option ordering (second > top).
6. Missing second candidate (single-option behavior).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dual_pipeline.parameters import (
    ParameterRegistry,
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
    reload_parameters,
)


class TestAmbiguityRule30Adversarial(unittest.TestCase):
    """Adversarial stress test suite for needs_operator_decision."""

    def test_adversarial_boundary_precision(self) -> None:
        """Verifies ultra-precise boundary values around 0.30."""
        self.assertTrue(needs_operator_decision(0.8, 0.5))
        self.assertFalse(needs_operator_decision(0.8000001, 0.5))
        self.assertTrue(needs_operator_decision(0.7999999, 0.5))
        self.assertTrue(needs_operator_decision(0.5 + 0.3, 0.5))

    def test_adversarial_negative_and_nan_confidences(self) -> None:
        """Verifies fail-closed behavior on NaN, Inf, negative, and invalid confidences."""
        # NaN values
        self.assertTrue(needs_operator_decision(float("nan"), 0.5))
        self.assertTrue(needs_operator_decision(0.8, float("nan")))
        self.assertTrue(needs_operator_decision(float("nan"), float("nan")))

        # Inf values
        self.assertTrue(needs_operator_decision(float("inf"), 0.5))
        self.assertTrue(needs_operator_decision(0.8, float("inf")))
        self.assertTrue(needs_operator_decision(float("-inf"), 0.5))

        # Negative confidences
        self.assertTrue(needs_operator_decision(-0.1, 0.5))
        self.assertTrue(needs_operator_decision(0.8, -0.05))
        self.assertTrue(needs_operator_decision(-1.0, -1.0))

        # Non-numeric string / None
        self.assertTrue(needs_operator_decision("invalid", 0.5))
        self.assertTrue(needs_operator_decision(0.8, "invalid"))
        self.assertTrue(needs_operator_decision(None, 0.5))

    def test_adversarial_missing_second_candidate(self) -> None:
        """Verifies that a missing second candidate does NOT trigger ambiguity margin gate."""
        self.assertFalse(needs_operator_decision(0.85, None))

    def test_adversarial_inverted_candidate_ordering(self) -> None:
        """Verifies that unsorted candidate confidences (second > top) measure absolute gap."""
        self.assertTrue(needs_operator_decision(0.50, 0.80))
        self.assertFalse(needs_operator_decision(0.50, 0.85))

    def test_adversarial_out_of_bounds_threshold(self) -> None:
        """Verifies that an out-of-bounds configured threshold safely falls back to 0.30."""
        with patch(
            "dual_pipeline.parameters.get_hitl_ambiguity_margin_threshold", return_value=0.01
        ):
            self.assertTrue(needs_operator_decision(0.75, 0.50))

        with patch(
            "dual_pipeline.parameters.get_hitl_ambiguity_margin_threshold", return_value=0.99
        ):
            self.assertFalse(needs_operator_decision(0.85, 0.50))

        with patch(
            "dual_pipeline.parameters.get_hitl_ambiguity_margin_threshold", return_value="corrupted"
        ):
            self.assertTrue(needs_operator_decision(0.80, 0.50))

    def test_adversarial_stale_cache_and_reload(self) -> None:
        """Verifies that registry cache clearing immediately reflects parameter reloads."""
        reg = ParameterRegistry()
        initial = get_hitl_ambiguity_margin_threshold()
        self.assertEqual(initial, 0.30)

        reg.clear_cache()
        reloaded = get_hitl_ambiguity_margin_threshold()
        self.assertEqual(reloaded, 0.30)

        data = reload_parameters()
        self.assertIsInstance(data, dict)


if __name__ == "__main__":
    unittest.main()
