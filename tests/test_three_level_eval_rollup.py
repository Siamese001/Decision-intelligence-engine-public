"""Unit tests for Three-Level (L1 Micro -> L2 Meso -> L3 Macro) Evaluation Rollup Engine.

Validates the hierarchical dependency chain and fail-closed veto gating:
- Level 1: Micro Foundational Mechanics
- Level 2: Meso Pipeline Functional & Security Invariants (Pipelines A-D, C-19 Firewall)
- Level 3: Macro Strategic Alpha ($1.2M Executive Cognitive Exoskeleton)
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.rollup import (
    Level1MicroScorecard,
    Level2MesoScorecard,
    Level3MacroScorecard,
    ThreeLevelRollupEngine,
)


class TestThreeLevelEvalRollup(unittest.TestCase):
    """Test suite validating the 3-level evaluation rollup and hierarchical veto gates."""

    def test_level_1_micro_scorecard_nominal_and_failure(self) -> None:
        """Test Level 1 micro scorecard computation for nominal and failure scenarios."""
        # Nominal case
        l1 = Level1MicroScorecard(
            trajectory_efficiency=95.0,
            schema_compliance_rate=1.0,
            dsp_math_resilience=0.9,
            json_repair_success_rate=1.0,
        )
        l1.compute()
        self.assertTrue(l1.passed)
        self.assertGreaterEqual(l1.score, 90.0)
        self.assertIn("nominal", l1.diagnostic)

        # Failure case: schema compliance breach
        l1_fail = Level1MicroScorecard(
            trajectory_efficiency=95.0,
            schema_compliance_rate=0.8,
            dsp_math_resilience=0.9,
            json_repair_success_rate=1.0,
        )
        l1_fail.compute()
        self.assertFalse(l1_fail.passed)
        self.assertIn("Micro foundation failed", l1_fail.diagnostic)

        # Failure case: json repair failure
        l1_json_fail = Level1MicroScorecard(
            trajectory_efficiency=95.0,
            schema_compliance_rate=1.0,
            dsp_math_resilience=0.9,
            json_repair_success_rate=0.5,
        )
        l1_json_fail.compute()
        self.assertFalse(l1_json_fail.passed)

    def test_level_2_meso_scorecard_and_c19_firewall_veto(self) -> None:
        """Test Level 2 meso scorecard and non-negotiable C-19 career firewall veto."""
        # Nominal case
        l2 = Level2MesoScorecard(
            pipeline_a_passed=True,
            pipeline_b_passed=True,
            pipeline_c_passed=True,
            pipeline_d_passed=True,
            c19_career_firewall_leaks=0,
        )
        l2.compute()
        self.assertTrue(l2.passed)
        self.assertEqual(l2.score, 100.0)

        # Pipeline failure case
        l2_fail = Level2MesoScorecard(
            pipeline_a_passed=True,
            pipeline_b_passed=False,
            pipeline_c_passed=True,
            pipeline_d_passed=True,
            c19_career_firewall_leaks=0,
        )
        l2_fail.compute()
        self.assertFalse(l2_fail.passed)
        self.assertEqual(l2_fail.score, 75.0)

        # Rule C-19 Career Firewall Leak VETO (must fail even if score would be high)
        l2_c19_leak = Level2MesoScorecard(
            pipeline_a_passed=True,
            pipeline_b_passed=True,
            pipeline_c_passed=True,
            pipeline_d_passed=True,
            c19_career_firewall_leaks=1,
        )
        l2_c19_leak.compute()
        self.assertFalse(l2_c19_leak.passed)
        self.assertIn("Career Leverage Firewall breached", l2_c19_leak.diagnostic)

    def test_level_3_macro_scorecard_business_case(self) -> None:
        """Test Level 3 macro scorecard validating $1.2M executive alpha."""
        # Nominal case
        l3 = Level3MacroScorecard(
            dimensions_passed_count=10,
            total_dimensions=10,
            overall_strategic_score=95.0,
            maturity_level=5.0,
            verdict="Level 5: Executive Cognitive Exoskeleton ($1.2M Benchmark)",
        )
        l3.compute()
        self.assertTrue(l3.passed)
        self.assertIn("Systemic strategic alpha achieved", l3.diagnostic)

        # Failure case: missing dimensions
        l3_fail = Level3MacroScorecard(
            dimensions_passed_count=8,
            total_dimensions=10,
            overall_strategic_score=70.0,
            maturity_level=3.5,
        )
        l3_fail.compute()
        self.assertFalse(l3_fail.passed)
        self.assertIn("Strategic alpha failed", l3_fail.diagnostic)

    def test_hierarchical_veto_lock_level_1_failure(self) -> None:
        """Level 1 failure must block Level 2 and Level 3 with BLOCKED_AT_LEVEL_1_MICRO."""
        tier_results = {
            "tier_1_trajectory": {
                "efficiency_ratio": 0.5,
                "schema_compliance_rate": 0.7,  # < 1.0 -> fails L1
            }
        }
        pipeline_results = {
            "pipeline_a": {"passed": True},
            "pipeline_b": {"passed": True},
            "pipeline_c": {"passed": True},
            "pipeline_d": {"passed": True},
        }
        strategic_results = {
            "overall_score": 100.0,
            "maturity_level": 5.0,
            "verdict": "Level 5: Executive Cognitive Exoskeleton",
            "dimensions": {f"dim_{i}": {"passed": True} for i in range(1, 11)},
        }

        report = ThreeLevelRollupEngine.evaluate_rollup(
            tier_results, pipeline_results, strategic_results
        )

        self.assertFalse(report.overall_pass)
        self.assertFalse(report.hierarchical_lock_passed)
        self.assertIn("BLOCKED_AT_LEVEL_1_MICRO", report.certification_status)

    def test_hierarchical_veto_lock_level_2_failure(self) -> None:
        """Level 2 failure (e.g. C-19 leak) must block Level 3 with BLOCKED_AT_LEVEL_2_MESO."""
        tier_results = {
            "tier_1_trajectory": {
                "efficiency_ratio": 1.0,
                "schema_compliance_rate": 1.0,
            }
        }
        pipeline_results = {
            "pipeline_a": {"passed": True},
            "pipeline_b": {
                "passed": True,
                "c19_career_firewall_leak_count": 2,  # > 0 -> fails L2
            },
            "pipeline_c": {"passed": True},
            "pipeline_d": {"passed": True},
        }
        strategic_results = {
            "overall_score": 100.0,
            "maturity_level": 5.0,
            "verdict": "Level 5: Executive Cognitive Exoskeleton",
            "dimensions": {f"dim_{i}": {"passed": True} for i in range(1, 11)},
        }

        report = ThreeLevelRollupEngine.evaluate_rollup(
            tier_results, pipeline_results, strategic_results
        )

        self.assertFalse(report.overall_pass)
        self.assertFalse(report.hierarchical_lock_passed)
        self.assertIn("BLOCKED_AT_LEVEL_2_MESO", report.certification_status)

    def test_utility_only_status_when_macro_business_case_fails(self) -> None:
        """When L1 and L2 pass but L3 fails, status must be UTILITY_ONLY fail-closed."""
        tier_results = {
            "tier_1_trajectory": {
                "efficiency_ratio": 1.0,
                "schema_compliance_rate": 1.0,
            }
        }
        pipeline_results = {
            "pipeline_a": {"passed": True},
            "pipeline_b": {"passed": True, "c19_career_firewall_leak_count": 0},
            "pipeline_c": {"passed": True},
            "pipeline_d": {"passed": True},
        }
        # Macro strategic evaluation fails (only 5 dimensions passed, low score)
        strategic_results = {
            "overall_score": 50.0,
            "maturity_level": 2.0,
            "verdict": "Level 2: Basic Pipeline Utility",
            "dimensions": {
                f"dim_{i}": {"passed": i <= 5} for i in range(1, 11)
            },
        }

        report = ThreeLevelRollupEngine.evaluate_rollup(
            tier_results, pipeline_results, strategic_results
        )

        self.assertFalse(report.overall_pass)
        self.assertTrue(report.hierarchical_lock_passed)  # Hierarchical lock passed because L1 & L2 passed
        self.assertIn("UTILITY_ONLY", report.certification_status)
        self.assertIn("fails $1.2M Strategic Exoskeleton", report.certification_status)

    def test_certified_level_5_exoskeleton_when_all_three_levels_pass(self) -> None:
        """When L1, L2, and L3 all pass, status must be CERTIFIED_LEVEL_5_EXOSKELETON."""
        tier_results = {
            "tier_1_trajectory": {
                "efficiency_ratio": 1.0,
                "schema_compliance_rate": 1.0,
            }
        }
        pipeline_results = {
            "pipeline_a": {"passed": True, "details": {"waveform_eval": {"score": 1.0}}},
            "pipeline_b": {
                "passed": True,
                "details": {"json_repair_eval": {"success_rate": 1.0}},
                "c19_career_firewall_leak_count": 0,
            },
            "pipeline_c": {"passed": True},
            "pipeline_d": {"passed": True},
        }
        strategic_results = {
            "overall_score": 96.0,
            "maturity_level": 5.0,
            "verdict": "Level 5: Executive Cognitive Exoskeleton ($1.2M Benchmark)",
            "dimensions": {f"dim_{i}": {"passed": True} for i in range(1, 11)},
        }

        report = ThreeLevelRollupEngine.evaluate_rollup(
            tier_results, pipeline_results, strategic_results
        )

        self.assertTrue(report.overall_pass)
        self.assertTrue(report.hierarchical_lock_passed)
        self.assertIn("CERTIFIED_LEVEL_5_EXOSKELETON", report.certification_status)

        # Test dictionary serialization
        d = report.to_dict()
        self.assertTrue(d["overall_pass"])
        self.assertEqual(d["level_1_micro"]["score"], 100.0)
        self.assertEqual(d["level_2_meso"]["score"], 100.0)
        self.assertEqual(d["level_3_macro"]["score"], 96.0)


if __name__ == "__main__":
    unittest.main()
