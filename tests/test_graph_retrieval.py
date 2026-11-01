#!/usr/bin/env python3
"""Focused acceptance tests for governed BM25 and multi-hop graph retrieval."""
from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import connect_sqlite, read_json, sha256_file  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    HybridRetriever,
)
from dual_pipeline.query import QueryEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from dual_pipeline.retrieval_release import _evaluate_case  # noqa: E402
from dual_pipeline.semantic_cache import configure_semantic_cache  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class GovernedGraphRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-graph-retrieval-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = ROOT / "graph" / "nee_graph.sqlite"
        cls.graph_hash = sha256_file(cls.graph)
        cls.backend = BgeM3TestBackend()
        cls.manager = ReleaseManager(cls.runtime, cls.graph)
        cls.pair = cls.manager.bootstrap(cls.backend)
        configure_semantic_cache(cls.runtime, "off")
        cls.engine = QueryEngine(
            cls.runtime,
            cls.graph,
            cls.backend,
            semantic_cache_mode="off",
        )
        cls.connected_result = cls.engine.ask(
            (
                "How do Project Beacon, the AI Hub, partner dependencies, "
                "risks, and opportunities connect across workstreams?"
            ),
            query_type="cross_workstream_synthesis",
            output_dir=Path(cls.temp.name) / "connected-output",
            audience="internal_consulting",
            provider_egress_allowed=False,
        )
        cls.connected_run = (
            cls.runtime / "runs" / cls.connected_result["run_id"]
        )
        cls.exact_result = cls.engine.ask(
            "Which exact governed PDLC records are available?",
            query_type="exact_lookup",
            output_dir=Path(cls.temp.name) / "exact-output",
            audience="internal_consulting",
            provider_egress_allowed=False,
        )
        cls.exact_run = cls.runtime / "runs" / cls.exact_result["run_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_bm25_is_an_independent_executed_retrieval_signal(self) -> None:
        exact = read_json(self.connected_run / "exact_retrieval.json")
        retrieval = read_json(self.connected_run / "hybrid_retrieval.json")
        connected_map = read_json(
            self.connected_run / "connected_evidence_map.json"
        )
        self.assertTrue(exact["bm25_executed"])
        self.assertTrue(retrieval["bm25_executed"])
        self.assertGreater(len(exact["bm25_anchors"]), 0)
        self.assertGreater(connected_map["metrics"]["bm25_seed_count"], 0)
        self.assertTrue(
            any(
                result["bm25_rank"] is not None
                for lane in retrieval["lanes"].values()
                for result in lane["results"]
            )
        )

    def test_connected_map_reaches_real_two_and_three_hop_context(self) -> None:
        connected_map = read_json(
            self.connected_run / "connected_evidence_map.json"
        )
        validate_artifact(connected_map)
        self.assertEqual(3, connected_map["requested_hops"])
        self.assertEqual(3, connected_map["max_hop_reached"])
        self.assertGreater(
            connected_map["metrics"]["expanded_record_count"],
            0,
        )
        self.assertTrue(
            any(path["hop_count"] == 2 for path in connected_map["paths"])
        )
        self.assertTrue(
            any(path["hop_count"] == 3 for path in connected_map["paths"])
        )
        self.assertGreater(
            connected_map["metrics"]["relationship_family_count"],
            1,
        )
        self.assertGreater(
            connected_map["metrics"]["branch_suppression_count"],
            0,
        )
        bridge_counts = Counter(
            path["steps"][-1]["from_node_id"]
            for path in connected_map["paths"]
            if path["hop_count"] >= 2
        )
        self.assertLessEqual(
            max(bridge_counts.values(), default=0),
            connected_map["retrieval_policy"][
                "maximum_records_per_bridge_node"
            ],
        )

    def test_every_selected_fact_has_one_decision_safe_path(self) -> None:
        connected_map = read_json(
            self.connected_run / "connected_evidence_map.json"
        )
        selected = {
            record_id
            for lane in connected_map["selected_record_ids_by_lane"].values()
            for record_id in lane
        }
        targets = [path["target_record_id"] for path in connected_map["paths"]]
        self.assertEqual(selected, set(targets))
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(
            0,
            connected_map["metrics"]["missing_evidence_count"],
        )
        selected_assertions = {
            path["target_assertion_id"] for path in connected_map["paths"]
        }
        connection = connect_sqlite(self.graph, readonly=True)
        try:
            for assertion_id in sorted(selected_assertions):
                row = connection.execute(
                    """SELECT a.predicate,a.legacy_predicate,
                              subject.node_type AS subject_type,
                              object.node_type AS object_type
                         FROM assertion a
                         LEFT JOIN node subject
                           ON subject.node_id=a.subject_node_id
                         LEFT JOIN node object
                           ON object.node_id=a.object_node_id
                        WHERE a.assertion_id=?""",
                    (assertion_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertNotEqual("ROUTES_TO", row["predicate"])
                self.assertNotEqual("ROUTES_TO", row["legacy_predicate"])
                self.assertNotEqual("card", row["subject_type"])
                self.assertNotEqual("card", row["object_type"])
        finally:
            connection.close()
        for path in connected_map["paths"]:
            for step in path["steps"]:
                self.assertNotEqual("ROUTES_TO", step["relationship_family"])
                self.assertNotEqual("card", step["from_node_type"])
                self.assertNotEqual("card", step["to_node_type"])

    def test_connected_retrieval_is_read_only_and_surfaces_opportunity_context(
        self,
    ) -> None:
        connected_map = read_json(
            self.connected_run / "connected_evidence_map.json"
        )
        self.assertEqual("COMPLETED", self.connected_result["status"])
        self.assertEqual(
            "X3E_ALLOW_EVIDENCE_ONLY",
            self.connected_result["x3_outcome"],
        )
        self.assertGreater(
            connected_map["metrics"]["opportunity_context_count"],
            0,
        )
        self.assertEqual(0, connected_map["automatic_graph_mutation_count"])
        self.assertEqual(self.graph_hash, sha256_file(self.graph))
        self.assertEqual(
            self.connected_result["graph_sha256_before"],
            self.connected_result["graph_sha256_after"],
        )

    def test_exact_lookup_does_not_expand_the_graph(self) -> None:
        connected_map = read_json(
            self.exact_run / "connected_evidence_map.json"
        )
        validate_artifact(connected_map)
        self.assertTrue(self.exact_result["bm25_executed"])
        self.assertFalse(self.exact_result["graph_traversal_executed"])
        self.assertEqual(0, connected_map["requested_hops"])
        self.assertEqual(0, connected_map["max_hop_reached"])
        self.assertEqual(
            0,
            connected_map["metrics"]["expanded_record_count"],
        )

    def test_release_evaluation_measures_bm25_as_a_separate_component(
        self,
    ) -> None:
        record_ids, diagnostics, components, _latency_ms = _evaluate_case(
            HybridRetriever(
                self.manager._resolve(self.pair["sidecar_path"]),
                self.backend,
            ),
            {
                "query": "AI-enabled PDLC supporting evidence",
                "lane": "approved",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "retrieval_mode": "strategic",
                "top_k": 10,
            },
            self.manager._resolve(self.pair["graph_path"]),
        )
        self.assertTrue(record_ids)
        self.assertTrue(components["bm25"])
        self.assertTrue(any(item["bm25_rank"] is not None for item in diagnostics))


if __name__ == "__main__":
    unittest.main(verbosity=2)
