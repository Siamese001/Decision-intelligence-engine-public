from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, sha256_file
from dual_pipeline.label_adjudication import (
    LabelAdjudicationError,
    build_graph_label_adjudication_packet,
)
from dual_pipeline.label_quality import build_legacy_label_migration_plan
from tools.validate_dual_pipeline_contracts import SchemaRegistry, validate_artifact

SEED_GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class GraphLabelAdjudicationTests(unittest.TestCase):
    @staticmethod
    def _runtime_with_active_pair(temporary: str) -> tuple[Path, Path, dict[str, object]]:
        runtime_root = Path(temporary) / "runtime"
        graph = runtime_root / "generations" / "generation-test-001" / "graph.sqlite"
        graph.parent.mkdir(parents=True)
        graph.write_bytes(SEED_GRAPH.read_bytes())
        pair: dict[str, object] = {
            "generation_id": "generation-test-001",
            "graph_path": graph.relative_to(runtime_root).as_posix(),
            "graph_sha256": sha256_file(graph),
            "graph_snapshot": "seed-graph-snapshot",
            "sidecar_sha256": "a" * 64,
            "embedding_backend": "test-bge-m3",
            "model_id": "test/model",
            "model_revision": "test-revision",
            "model_manifest_digest": "b" * 64,
            "production_qualified": False,
        }
        (runtime_root / "active_pair.json").write_text(
            json.dumps(pair, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return runtime_root, graph, pair

    def test_packet_is_exact_pair_bound_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root, graph, pair = self._runtime_with_active_pair(temporary)
            before = sha256_file(graph)
            first = build_graph_label_adjudication_packet(
                graph,
                runtime_root=runtime_root,
            )
            second = build_graph_label_adjudication_packet(
                graph,
                runtime_root=runtime_root,
            )

            self.assertEqual(first, second)
            self.assertEqual(before, sha256_file(graph))
            self.assertEqual("PENDING_HUMAN_REVIEW", first["status"])
            self.assertEqual(before, first["pair_binding"]["graph_sha256"])
            self.assertEqual(pair["generation_id"], first["pair_binding"]["generation_id"])
            self.assertEqual(0, first["summary"]["provider_call_count"])
            self.assertEqual(0, first["summary"]["graph_mutation_count"])
            self.assertGreater(first["summary"]["label_mapping_record_count"], 0)
            self.assertGreater(first["summary"]["lineage_record_count"], 0)
            self.assertGreater(first["summary"]["identity_record_count"], 0)
            self.assertEqual(
                first["summary"]["total_queue_count"],
                len(first["queues"]),
            )
            self.assertEqual(
                first["summary"]["label_mapping_record_count"],
                sum(
                    queue["record_count"]
                    for queue in first["queues"]
                    if queue["category"] == "label_mapping"
                ),
            )
            legacy_plan = build_legacy_label_migration_plan(graph)
            alias_count = sum(
                1
                for item in legacy_plan["items"]
                if item["decision"] == "hitl_required"
                and item["record_type"] == "canonical_alias"
            )
            self.assertEqual(
                legacy_plan["summary"]["hitl_required_count"] - alias_count,
                first["summary"]["label_mapping_record_count"],
            )
            self.assertTrue(
                all(
                    queue["dual_review_required"]
                    for queue in first["queues"]
                    if queue["review_tier"] == 2
                )
            )
            self.assertEqual(
                "DEFERRED_UNTIL_SUCCESSOR_PAIR",
                first["relevance_review"]["status"],
            )
            self.assertEqual(
                2,
                first["relevance_review"]["minimum_independent_reviewer_count"],
            )
            registry = SchemaRegistry(ROOT / "contracts" / "dual_pipeline" / "schemas")
            registry.load()
            validate_artifact(first, registry)

    def test_packet_rejects_graph_that_does_not_match_active_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root, graph, pair = self._runtime_with_active_pair(temporary)
            pair["graph_sha256"] = "0" * 64
            (runtime_root / "active_pair.json").write_text(
                json.dumps(pair, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(LabelAdjudicationError):
                build_graph_label_adjudication_packet(graph, runtime_root=runtime_root)


if __name__ == "__main__":
    unittest.main()
