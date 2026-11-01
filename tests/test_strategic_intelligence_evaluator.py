"""Unit & Integration Tests for Executive Strategic Intelligence Evaluator ($1.2M Benchmark).

Validates the 10 Strategic Intelligence Dimensions:
1. Enterprise Architecture & Business Capability Depth
2. Multi-Hop Connection Mining & Latent Linkage
3. Cross-Silo Contradiction & Redundancy Detection (CONFLICTS_WITH)
4. Dual-Audience Federation & Consulting Commercial Edge
5. Conway's Law, Politics & Organizational Topology
6. Ingestion Signal vs. Noise & Epistemic Alpha
7. Bi-Temporal Rigor & Trust Foundation
8. Dialectical Decision Intel & Multi-Criteria Trade-offs (MCDA)
9. Board-Level Synthesis & Next Best Action (NBA)
10. Multi-Client Federation & Scalable Knowledge Asset
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.strategic import (
    ExecutiveStrategicEvaluator,
    MCDACriteriaWeights,
)


class TestStrategicIntelligenceEvaluator(unittest.TestCase):
    """Test suite certifying the 10 dimensions of the Executive Cognitive Exoskeleton."""

    def setUp(self) -> None:
        self.evaluator = ExecutiveStrategicEvaluator()

    def test_dimension_1_ea_capability_depth(self) -> None:
        """Verify L1-L3 capability hierarchy and complete ADR evaluation."""
        dim_score = self.evaluator.evaluate_dimension_1_ea_capability_depth()
        self.assertTrue(dim_score.passed)
        self.assertEqual(dim_score.score, 100.0)
        self.assertTrue(dim_score.details["hierarchy_complete"])
        self.assertTrue(dim_score.details["adr_complete"])

        # Negative case: missing activity and incomplete ADR
        incomplete_payload = {"l1_domain": "Core", "adr": {}}
        neg_score = self.evaluator.evaluate_dimension_1_ea_capability_depth(incomplete_payload)
        self.assertFalse(neg_score.passed)

    def test_dimension_2_latent_multi_hop(self) -> None:
        """Verify discovery of 4-to-5 hop emergent systemic dependencies."""
        dim_score = self.evaluator.evaluate_dimension_2_latent_multi_hop()
        self.assertTrue(dim_score.passed)
        self.assertGreaterEqual(dim_score.details["max_discovered_hops"], 4)

        # Negative case: shallow 2-hop chain
        shallow_edges = [("A", "depends_on", "B"), ("B", "depends_on", "C")]
        neg_score = self.evaluator.evaluate_dimension_2_latent_multi_hop(shallow_edges)
        self.assertFalse(neg_score.passed)

    def test_dimension_3_cross_silo_contradictions(self) -> None:
        """Verify proactive discovery of conflicting architectures (CONFLICTS_WITH)."""
        dim_score = self.evaluator.evaluate_dimension_3_cross_silo_contradictions()
        self.assertTrue(dim_score.passed)
        self.assertEqual(dim_score.score, 100.0)
        self.assertGreaterEqual(dim_score.details["count"], 1)
        self.assertEqual(dim_score.details["conflicts_detected"][0]["type"], "CONFLICTS_WITH")

        # Negative case: compatible architectures
        compatible = [
            {"id": "a", "architecture": "batch", "ingestion_sla_seconds": 3600},
            {"id": "b", "architecture": "batch", "ingestion_sla_seconds": 3600},
        ]
        neg_score = self.evaluator.evaluate_dimension_3_cross_silo_contradictions(compatible)
        self.assertFalse(neg_score.passed)

    def test_dimension_4_dual_audience_commercial(self) -> None:
        """Verify PartnerAdvisory commercial levers are firewalled from client delivery."""
        clean_brief = "The project team confirmed Q3 delivery of Substation Delta."
        advisory = {"sow_guardrails": "Capped at $450k", "ip_harvesting": "Accelerator reuse"}
        dim_score = self.evaluator.evaluate_dimension_4_dual_audience_commercial(clean_brief, advisory)
        self.assertTrue(dim_score.passed)
        self.assertEqual(len(dim_score.details["client_leaks"]), 0)

        # Negative case: commercial terms leak into client brief
        leaked_brief = "We need to capture higher margin and force a sow renegotiation."
        neg_score = self.evaluator.evaluate_dimension_4_dual_audience_commercial(leaked_brief, advisory)
        self.assertFalse(neg_score.passed)

    def test_dimension_5_conways_law_politics(self) -> None:
        """Verify tracking of informal vetoes, key-person fragility, and stakeholder stances."""
        dim_score = self.evaluator.evaluate_dimension_5_conways_law_politics()
        self.assertTrue(dim_score.passed)
        self.assertTrue(dim_score.details["stances_tracked"])
        self.assertTrue(dim_score.details["vetoes_tracked"])
        self.assertTrue(dim_score.details["fragility_tracked"])

    def test_dimension_6_epistemic_alpha(self) -> None:
        """Verify extraction of implicit trade-offs and preserved dissent."""
        dim_score = self.evaluator.evaluate_dimension_6_epistemic_alpha()
        self.assertTrue(dim_score.passed)
        self.assertTrue(dim_score.details["trade_offs_found"])
        self.assertTrue(dim_score.details["dissent_preserved"])

    def test_dimension_7_bitemporal_rigor(self) -> None:
        """Verify W3 valid-time vs transaction-time and provider-free boundary."""
        dim_score = self.evaluator.evaluate_dimension_7_bitemporal_rigor()
        self.assertTrue(dim_score.passed)
        self.assertTrue(dim_score.details["bitemporal_complete"])
        self.assertTrue(dim_score.details["provider_free"])

    def test_dimension_8_mcda_tradeoffs(self) -> None:
        """Verify MCDA weighted multi-criteria scoring and rank decoupling."""
        weights = MCDACriteriaWeights()
        self.assertTrue(weights.validate())
        dim_score = self.evaluator.evaluate_dimension_8_mcda_tradeoffs(weights=weights)
        self.assertTrue(dim_score.passed)
        self.assertEqual(dim_score.details["top_option"], "Sidecar SQLite WAL")
        self.assertGreater(dim_score.details["top_score"], 80.0)

    def test_dimension_9_board_level_nba(self) -> None:
        """Verify financialized executive framing and dollarized Cost of Delay ($/month)."""
        dim_score = self.evaluator.evaluate_dimension_9_board_level_nba()
        self.assertTrue(dim_score.passed)
        self.assertGreater(dim_score.details["cost_of_delay_usd"], 0.0)
        self.assertGreater(dim_score.details["capex_protection_usd"], 0.0)

    def test_dimension_10_multiclient_federation(self) -> None:
        """Verify anonymized cross-client blueprint abstraction without data leakage."""
        dim_score = self.evaluator.evaluate_dimension_10_multiclient_federation()
        self.assertTrue(dim_score.passed)
        self.assertTrue(dim_score.details["leak_free"])
        self.assertGreaterEqual(dim_score.details["patterns_abstracted"], 2)

    def test_full_executive_exoskeleton_evaluation(self) -> None:
        """Verify full 10-dimension run yields a Level 5 Executive Exoskeleton scorecard."""
        scorecard = self.evaluator.run_full_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertEqual(len(scorecard.dimension_scores), 10)
        self.assertGreaterEqual(scorecard.overall_score, 90.0)
        self.assertGreaterEqual(scorecard.maturity_level, 4.5)
        self.assertIn("Level 5", scorecard.verdict)
        data = scorecard.to_dict()
        self.assertIn("dimensions", data)
        self.assertEqual(len(data["dimensions"]), 10)

    def test_evaluate_from_authentic_pipeline_deliverables(self) -> None:
        """Verify evaluation against authentic meeting deliverables in output/project_nexus/09-14-2026/."""
        p1 = "output/project_nexus/09-14-2026/Nexus_Scope/01_Nexus_Scope_09-14-2026_1021AM_1031AM_ET_hitl_resolved_transcript.md"
        p2 = "output/project_nexus/09-14-2026/Nexus_Scope/02_Nexus_Scope_09-14-2026_1021AM_1031AM_ET_executive_recap.md"
        p3 = "output/project_nexus/09-14-2026/Nexus_Scope/03_Nexus_Scope_09-14-2026_1021AM_1031AM_ET_canonical_signal_projection.json"

        scorecard = self.evaluator.evaluate_from_pipeline_deliverables(p1, p2, p3)
        self.assertTrue(scorecard.passed)
        self.assertEqual(len(scorecard.dimension_scores), 10)
        self.assertGreaterEqual(scorecard.overall_score, 80.0)
        self.assertGreaterEqual(scorecard.maturity_level, 4.5)

        # Confirm Rule C-19 Firewall passed with 0 leaks in authentic artifacts
        dim_4 = scorecard.dimension_scores["dim_4_dual_audience"]
        self.assertTrue(dim_4.passed)
        self.assertEqual(dim_4.details["total_leaks"], 0)

    def test_evaluate_from_pipeline_deliverables_detects_firewall_leak(self) -> None:
        """Verify that career leverage terms injected into Output 02 cause Dim 4 to fail immediately."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "01_test.md"
            p2 = Path(tmpdir) / "02_test.md"
            p3 = Path(tmpdir) / "03_test.json"

            p1.write_text("# Clean Transcript\nSpeaker: Hello.", encoding="utf-8")
            # Leak career leverage terms into executive recap
            p2.write_text(
                "# Meeting Recap\nTargeting executive compensation band and personal advancement.",
                encoding="utf-8",
            )
            p3.write_text('{"schema_version": "5.7", "evidence_index": {}}', encoding="utf-8")

            scorecard = self.evaluator.evaluate_from_pipeline_deliverables(p1, p2, p3)
            dim_4 = scorecard.dimension_scores["dim_4_dual_audience"]
            self.assertFalse(dim_4.passed)
            self.assertEqual(dim_4.score, 0.0)
            self.assertGreater(dim_4.details["total_leaks"], 0)


if __name__ == "__main__":
    unittest.main()

