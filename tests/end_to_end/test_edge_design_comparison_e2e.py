"""End-to-end test suite for Wave 5 edge design head-to-head evaluation under real BGE-M3."""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.retrieval_eval_core.edge_design_evaluator import (
    SYSTEM_A,
    SYSTEM_B,
    SYSTEM_C,
    SYSTEM_D,
    EdgeDesignEvaluator,
)

ROOT = Path(__file__).resolve().parents[2]


class TestEdgeDesignComparisonE2E(unittest.TestCase):
    """E2E verification of 4-system head-to-head evaluation on Wave 2 QRELS."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_path = (
            ROOT
            / ".local/dual_pipeline/generations/generation-graph-consolidation-d488dbeff02817be07106935301f/graph.sqlite"
        )
        cls.sidecar_path = (
            ROOT
            / ".local/dual_pipeline/generations/generation-graph-consolidation-d488dbeff02817be07106935301f/sidecar.sqlite"
        )
        cls.qrels_path = ROOT / "reports" / "edge_qrels_dataset.json"
        cls.embeddings_path = ROOT / "reports" / "edge_wave5_bge_embeddings.npz"

        if (
            not cls.graph_path.exists()
            or not cls.sidecar_path.exists()
            or not cls.qrels_path.exists()
            or not cls.embeddings_path.exists()
        ):
            raise unittest.SkipTest("Required database, QRELS, or pre-encoded BGE embeddings missing.")

        cls.evaluator = EdgeDesignEvaluator(
            cls.graph_path, cls.sidecar_path, cls.qrels_path, cls.embeddings_path
        )
        cls.results, cls.table = cls.evaluator.run_all_systems()
        cls.res_by_id = {r.system_id: r for r in cls.results}

    def test_all_four_systems_evaluated(self) -> None:
        """Verify all 4 architectures are evaluated and reported."""
        self.assertEqual(len(self.results), 4)
        for sid in (SYSTEM_A, SYSTEM_B, SYSTEM_C, SYSTEM_D):
            self.assertIn(sid, self.res_by_id)

    def test_system_b_typed_traversal_dominance(self) -> None:
        """System B (Anchor + Typed Traversal) must decisively outperform System A and System D."""
        res_a = self.res_by_id[SYSTEM_A]
        res_b = self.res_by_id[SYSTEM_B]
        res_d = self.res_by_id[SYSTEM_D]

        # System B solves System A's rank-1 blindness
        self.assertGreater(res_b.edge_s1, 0.50)
        self.assertEqual(res_a.edge_s1, 0.0)
        self.assertGreater(res_b.edge_s5, res_a.edge_s5)

        # System B outperforms System D on S@1, S@5, and S@10
        self.assertGreater(res_b.edge_s1, res_d.edge_s1)
        self.assertGreater(res_b.edge_s5, res_d.edge_s5)
        self.assertGreater(res_b.edge_s10, res_d.edge_s10)

        # Forward/Reverse S@5 superiority
        self.assertGreaterEqual(res_b.forward_reverse_s5, 0.80)
        self.assertGreater(res_b.forward_reverse_s5, res_d.forward_reverse_s5)

    def test_margin_tie_rate_suppression(self) -> None:
        """System B must eliminate margin ties, while dense edge vectors (System D) exhibit >10% ties."""
        res_b = self.res_by_id[SYSTEM_B]
        res_d = self.res_by_id[SYSTEM_D]

        self.assertLessEqual(res_b.margin_tie_rate, 0.02)
        self.assertGreater(res_d.margin_tie_rate, 0.10)

    def test_zero_leaks_across_all_systems(self) -> None:
        """All systems must maintain zero leaks across adversarial and boundary cases."""
        for sid, r in self.res_by_id.items():
            self.assertEqual(r.leak_count, 0, f"Leaks detected in {sid}")

    def test_latency_within_budget(self) -> None:
        """All systems must satisfy p95 latency under 50ms."""
        for sid, r in self.res_by_id.items():
            self.assertLessEqual(r.p95_latency_ms, 50.0, f"High latency in {sid}")

    def test_entity_s1_preservation(self) -> None:
        """Systems A, B, and D must preserve Entity S@1 without co-mingled crowding collapse."""
        for sid in (SYSTEM_A, SYSTEM_B, SYSTEM_D):
            r = self.res_by_id[sid]
            self.assertGreaterEqual(r.entity_s1, 0.85, f"Entity S@1 degraded in {sid}")


if __name__ == "__main__":
    unittest.main()
