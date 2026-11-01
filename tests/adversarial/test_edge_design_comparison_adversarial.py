"""Adversarial and stress test suite for Wave 5 edge design evaluation."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from dual_pipeline.retrieval_eval_core.edge_design_evaluator import (
    SYSTEM_A,
    SYSTEM_B,
    SYSTEM_C,
    SYSTEM_D,
    EdgeDesignEvaluator,
)

ROOT = Path(__file__).resolve().parents[2]


class TestEdgeDesignComparisonAdversarial(unittest.TestCase):
    """Adversarial validation of crowding, leak boundaries, and error resilience."""

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

    def test_malformed_and_injection_queries(self) -> None:
        """Verify evaluator handles pathological, empty, injection, and overflow queries safely."""
        pathological_queries = [
            "",
            "   \t\n  ",
            "??? !!! ...",
            "SELECT * FROM assertion WHERE predicate = 'REPORTS_TO'; DROP TABLE node;--",
            "<script>alert('xss')</script> Who reports to Drew Oakley?",
            "Who reports to (.*)+ [a-z]* {1,100} \\d+ $ ^ | Bob Jones?",
            "Who leads " + ("Nexus " * 500) + "initiative?",
        ]
        for sid in (SYSTEM_A, SYSTEM_B, SYSTEM_C, SYSTEM_D):
            for q in pathological_queries:
                fake_case = {
                    "case_id": self.evaluator.case_ids[0],
                    "query": q,
                    "lane": "approved",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                }
                res, lat, tie = self.evaluator.retrieve_system(sid, fake_case)
                self.assertIsInstance(res, list)
                self.assertIsInstance(lat, float)
                self.assertIsInstance(tie, float)

    def test_crowding_penalty_adversarial_verification(self) -> None:
        """Verify that co-mingling edge vectors with entity facets collapses Entity S@1 by >=30 pp."""
        # 1. Un-crowded Entity S@1 (traversal / partitioned systems)
        ent_cases = [
            c for c in self.evaluator.qrels.get("control_cases", [])
            if c.get("target_kind") == "entity"
        ]
        self.assertGreater(len(ent_cases), 100)

        # Baseline uncrowded facet vectors
        fac_indices = self.evaluator.fac_indices
        fac_V = self.evaluator.vectors[fac_indices]
        fac_rids = [self.evaluator.records[i]["record_id"] for i in fac_indices]

        uncrowded_hits = 0
        for c in ent_cases:
            q_idx = self.evaluator.case_id_to_idx[c["case_id"]]
            qv = self.evaluator.query_vecs[q_idx]
            sims = fac_V @ qv
            top1 = fac_rids[np.argmax(sims)]
            if top1 in c["expected_record_ids"]:
                uncrowded_hits += 1
        uncrowded_s1 = uncrowded_hits / len(ent_cases)
        self.assertGreaterEqual(uncrowded_s1, 0.85)

        # 2. Co-mingled pool (facets + edge vectors in the same partition)
        all_rids = [r["record_id"] for r in self.evaluator.records]
        all_V = self.evaluator.vectors

        comingled_hits = 0
        for c in ent_cases:
            q_idx = self.evaluator.case_id_to_idx[c["case_id"]]
            qv = self.evaluator.query_vecs[q_idx]
            sims = all_V @ qv
            top1 = all_rids[np.argmax(sims)]
            if top1 in c["expected_record_ids"]:
                comingled_hits += 1
        comingled_s1 = comingled_hits / len(ent_cases)

        crowding_drop = uncrowded_s1 - comingled_s1
        # Empirically proves the crowding collapse (>30 percentage point drop)
        self.assertGreaterEqual(
            crowding_drop,
            0.30,
            f"Expected severe crowding collapse (>=0.30 drop), got {crowding_drop:.3f}",
        )

    def test_adversarial_leak_boundaries(self) -> None:
        """Verify that forbidden, unapproved, or restricted edges never leak into System B results."""
        adv_cases = self.evaluator.qrels.get("adversarial_cases", [])
        self.assertGreaterEqual(len(adv_cases), 10)

        for c in adv_cases:
            res, _, _ = self.evaluator.retrieve_system(SYSTEM_B, c)
            forbidden = set(c.get("forbidden_record_ids", []))
            leaked = [r for r in res if r in forbidden]
            self.assertEqual(
                leaked,
                [],
                f"Adversarial leak detected in case {c['case_id']}: {leaked}",
            )

    def test_provider_free_invariant(self) -> None:
        """Verify that evaluation executes with zero external socket or provider calls."""
        with patch("socket.socket") as mock_socket:
            mock_socket.side_effect = RuntimeError("Illegal provider call detected!")
            c = self.evaluator.qrels["edge_cases"][0]
            res, _, _ = self.evaluator.retrieve_system(SYSTEM_B, c)
            self.assertIsInstance(res, list)
            self.assertTrue(len(res) > 0)


if __name__ == "__main__":
    unittest.main()
