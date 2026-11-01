from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.query import (
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
)
from dual_pipeline.query_router import load_query_routing_policy, route_query

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "fixtures" / "decision_routing_wave1_baseline.json"


class DecisionRoutingWave1BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def test_wave10_shadow_router_matches_all_target_destinations(self) -> None:
        for case in self.baseline["cases"]:
            with self.subTest(case_id=case["case_id"]):
                result = route_query(case["question"])
                self.assertEqual(case["target_destination"], result.destination.value)
        self.assertFalse(load_query_routing_policy().external_classifier_allowed)

    def test_unrelated_record_cannot_cover_requirement(self) -> None:
        plan = {
            "run_id": "run-routing-baseline",
            "query_type": "cross_workstream_synthesis",
            "allowed_lanes": ["approved"],
            "materiality": "high",
            "primary_entities": ["Project Beacon"],
            "subquestions": ["A2A protocol", "PostgreSQL write-back"],
            "canonical_digest": {"value": "2" * 64},
        }
        manifest = compile_query_coverage_manifest(
            question="1. A2A protocol\n2. PostgreSQL write-back",
            validated_request_digest="1" * 64,
            plan=plan,
            active={
                "graph_snapshot": "graph-snapshot-routing-baseline",
                "graph_sha256": "3" * 64,
            },
            authorization_scope_digest="4" * 64,
        )
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet={
                "graph_snapshot": "graph-snapshot-routing-baseline",
                "packet_digest": "5" * 64,
                "approved": [{"record_id": "unrelated-record"}],
                "candidate": [],
                "external_public": [],
            },
            authorization_scope_digest="4" * 64,
            graph_sha256="3" * 64,
            view_authorized=True,
        )
        self.assertTrue(report["requirement_outcomes"])
        self.assertTrue(
            all(row["outcome"] == "explicit_gap" for row in report["requirement_outcomes"])
        )
        self.assertTrue(
            all(not row["record_ids"] for row in report["requirement_outcomes"])
        )


if __name__ == "__main__":
    unittest.main()
