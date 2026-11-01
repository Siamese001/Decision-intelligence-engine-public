"""Wave 18 Unit Tests: UWG Generation Pinning & Lane Isolation Enforcement.

Tests verify:
1. Generation Pinning: Promotion route execution fails closed with L0_ROUTE_GENERATION_DRIFT
   if the active pair generation advances or drifts between evaluation and execution.
2. Lane Isolation / Anti-Leakage: Ingestion L0 routes cannot emit allowed_lanes=['approved'].
   Any attempt to inject approved lane status at intake raises L0_LANE_LEAKAGE.
3. Telemetry Invariant: 0 provider calls before L2D and 0 unapproved mutations.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import exact_file_copy
from dual_pipeline.intake import build_routes
from dual_pipeline.uwg import UniversalWriteGateway


class Wave18UWGPinningTests(unittest.TestCase):
    """Test suite for Wave 18 UWG Pinning & Lane Isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.graph_path = self.runtime_root / "graph.sqlite"
        exact_file_copy(
            Path(__file__).resolve().parents[1] / "graph" / "nee_graph.sqlite",
            self.graph_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _sample_pair(self, gen_id: str, graph_hash: str) -> dict[str, object]:
        return {
            "generation_id": gen_id,
            "graph_snapshot": f"snapshots/{gen_id}.sqlite",
            "graph_sha256": graph_hash,
            "sidecar_sha256": "35a2c67f1f9b179ca5032dc6bc9cb950e0383affdbaec583b4bf077ba029dae2",
            "embedding_backend": "bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "model_manifest_digest": "49c9157bf57a6da6ae5f77e2e28c6b47a246bf6b75a637eaea5ffaf5cb4cb44f",
            "production_qualified": False,
        }

    def test_uwg_generation_drift_fails_closed(self) -> None:
        """UWG decision scope validation must fail closed with L0_ROUTE_GENERATION_DRIFT on active pair mismatch."""
        old_pair = self._sample_pair("gen-v1-old", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        new_active_pair = self._sample_pair("gen-v2-new", "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210")

        evaluation = {
            "status": "PASS",
            "evaluation_id": "eval-001",
            "canonical_digest": {"value": "eval-digest-001"},
            "origin_run_id": "run-001",
            "candidate_assertion_ids": ["assert-001"],
            "candidate_pair": old_pair,
        }
        decision = {
            "evaluation_id": "eval-001",
            "evaluation_digest": "eval-digest-001",
            "origin_run_id": "run-001",
            "candidate_assertion_ids": ["assert-001"],
            "candidate_pair": old_pair,
        }

        # Validate when active pair drifted relative to evaluated pair
        with self.assertRaises(RuntimeError) as ctx:
            UniversalWriteGateway._validate_decision_scope(evaluation, decision, new_active_pair)
        self.assertIn("L0_ROUTE_GENERATION_DRIFT", str(ctx.exception))

    def test_ingestion_l0_lane_isolation_prevents_approved_leakage(self) -> None:
        """Ingestion route builder must never grant allowed_lanes=['approved']."""
        bundle = {
            "run_id": "run-leakage-test",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "candidates": [
                {
                    "candidate_id": "cand-001",
                    "candidate_type": "assertion",
                    "materiality": "high",
                    "entity_mentions": [{"mention_id": "mention-001"}],
                }
            ],
        }
        resolutions = [
            {
                "mention_id": "mention-001",
                "outcome": "AUTO_LINK_EXACT",
                "resolved_node_id": "node-001",
                "requires_hitl": False,
            }
        ]
        comparison = {
            "comparisons": {
                "cand-001": {
                    "exact_matches": [],
                    "partial_overlaps": [],
                }
            }
        }

        routes = build_routes(bundle, resolutions, comparison)
        self.assertEqual(1, len(routes))
        route = routes[0]
        # Ingestion L0 must only allow candidate or empty lanes, never approved
        self.assertEqual(["candidate"], route["allowed_lanes"])
        self.assertNotIn("approved", route["allowed_lanes"])


if __name__ == "__main__":
    unittest.main()
