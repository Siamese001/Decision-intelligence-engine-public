#!/usr/bin/env python3
"""Provider-free post-retrieval graph-fit acceptance tests."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import digest, read_json
from dual_pipeline.query_eligibility import assess_query_graph_fit

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid"


def _reseal(value: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(value)
    value.pop("canonical_digest", None)
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(value),
    }
    validate_artifact(value)
    return value


def _inputs(*, with_records: bool = True, with_paths: bool = True):
    run_id = "run-query-graph-fit-test"
    destination = read_json(VALID / "query-destination-decision.json")
    destination["run_id"] = run_id
    destination = _reseal(destination)
    plan = read_json(VALID / "query-plan-v2.json")
    plan["run_id"] = run_id
    plan = _reseal(plan)
    connected = read_json(VALID / "connected-evidence-map.json")
    connected["run_id"] = run_id
    if not with_records:
        connected["selected_record_ids_by_lane"] = {
            "approved": [], "candidate": [], "external_public": []
        }
    if not with_paths:
        connected["paths"] = []
        connected["metrics"]["path_count"] = 0
    connected = _reseal(connected)
    active = {
        "graph_snapshot": connected["graph_snapshot"],
        "graph_sha256": connected["graph_sha256"],
        "generation_id": "generation-query-graph-fit-test",
        "embedding_backend": "flagembedding-bge-m3",
        "model_id": "BAAI/bge-m3",
        "model_revision": "revision-test",
        "model_manifest_digest": "6" * 64,
        "production_qualified": False,
    }
    packet = {
        "graph_snapshot": active["graph_snapshot"],
        "packet_digest": "7" * 64,
    }
    return destination, plan, connected, packet, active


class QueryGraphFitTests(unittest.TestCase):
    def test_connected_authorized_evidence_allows_nee_l2(self) -> None:
        inputs = _inputs()
        result = assess_query_graph_fit(
            destination_decision=inputs[0], plan=inputs[1],
            connected_evidence_map=inputs[2], evidence_packet=inputs[3],
            active_pair=inputs[4],
        )
        validate_artifact(result)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["l2_allowed"])
        self.assertEqual(result["automatic_provider_call_count"], 0)

    def test_missing_records_revokes_nee_eligibility(self) -> None:
        inputs = _inputs(with_records=False, with_paths=False)
        result = assess_query_graph_fit(
            destination_decision=inputs[0], plan=inputs[1],
            connected_evidence_map=inputs[2], evidence_packet=inputs[3],
            active_pair=inputs[4],
        )
        validate_artifact(result)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["l2_allowed"])
        self.assertIn("NO_AUTHORIZED_GRAPH_RECORDS", result["reason_codes"])

    def test_reverse_or_disconnected_discovery_cannot_establish_graph_fit(self) -> None:
        inputs = _inputs(with_records=True, with_paths=False)
        result = assess_query_graph_fit(
            destination_decision=inputs[0], plan=inputs[1],
            connected_evidence_map=inputs[2], evidence_packet=inputs[3],
            active_pair=inputs[4],
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("NO_CONNECTED_GRAPH_EVIDENCE", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
