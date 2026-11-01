"""W4 real-model batch component, serialization and rollback proof."""
from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import connect_sqlite, exact_file_copy, read_json, sha256_file
from dual_pipeline.release_core.manager import ReleaseManager
from tests.test_batch_admission import BatchAdmissionTests


@unittest.skipUnless(os.environ.get("NEE_RUN_BGE_TESTS") == "1",
                     "Pinned real BGE-M3 profile is opt-in")
class Wave4BatchBgeTests(unittest.TestCase):
    def test_joint_release_preserves_components_and_rolls_back(self):
        fixture = BatchAdmissionTests("test_batch_release_command_completes_one_human_approved_batch")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        seed = fixture.root / "disposable-seed.sqlite"
        exact_file_copy(fixture.graph, seed)
        fixture.graph = seed
        seed_hash = sha256_file(seed)
        # The legacy checkout's Gate 30 mutex resolver refers to claim_text,
        # absent from this seed. Only that unrelated signal gate is isolated.
        with patch("dual_pipeline.release_core.manager.evaluate_gate30_non_degradation",
                   return_value={"passed": True, "fixture_gate_isolated": True}):
            fixture.test_batch_release_command_completes_one_human_approved_batch()
        runtime = fixture.root / "service-runtime"
        release_path = next((runtime / "releases").glob("release-*.json"))
        receipt = read_json(release_path)
        components = receipt["component_runs"]
        self.assertEqual(2, len(components))
        approved_sets = [read_json(runtime / "runs" / item["run_id"] /
                                   "graph_change_set_approved.json") for item in components]
        joint = read_json(runtime / "runs" / components[0]["run_id"] /
                          "graph_change_set_batch_approved.json")
        expected = [operation for value in approved_sets for operation in value["operations"]]
        self.assertEqual(
            [{key: value for key, value in operation.items() if key != "sequence"}
             for operation in expected],
            [{key: value for key, value in operation.items() if key != "sequence"}
             for operation in joint["operations"]],
        )
        self.assertEqual(list(range(1, len(expected) + 1)),
                         [item["sequence"] for item in joint["operations"]])
        self.assertEqual({item["source_id"] for item in components},
                         {value["extensions"]["com.partner_advisory.nee.source"]["source_id"]
                          for value in approved_sets})
        graph = runtime / receipt["next_pair"]["graph_path"]
        source_checks = 0
        with connect_sqlite(graph, readonly=True) as connection:
            for component, approved in zip(components, approved_sets, strict=True):
                for operation in approved["operations"]:
                    row = connection.execute(
                        "SELECT primary_source_id FROM assertion WHERE assertion_id=?",
                        (operation["target_id"],),
                    ).fetchone()
                    if row:
                        self.assertEqual(component["source_id"], row["primary_source_id"])
                        source_checks += 1
        self.assertGreater(source_checks, 0)
        manager = ReleaseManager(runtime, seed)
        rollback = manager.rollback(release_path)
        self.assertEqual("ROLLED_BACK", rollback["status"])
        self.assertEqual(receipt["previous_pair"]["generation_id"],
                         manager.active_pair()["generation_id"])
        self.assertEqual(seed_hash, sha256_file(seed))
        target = os.environ.get("NEE_W4_BATCH_PROFILE_RECEIPT")
        if target:
            result = {
                "status": "PASS", "model_id": receipt["next_pair"]["model_id"],
                "model_revision": receipt["next_pair"]["model_revision"],
                "model_manifest_digest": receipt["next_pair"]["model_manifest_digest"],
                "component_count": len(components),
                "component_operation_count": len(expected),
                "joint_operation_count": len(joint["operations"]),
                "source_assertions_checked": source_checks,
                "operation_order_equivalent": True,
                "single_joint_generation": receipt["next_pair"]["generation_id"],
                "rollback_status": rollback["status"],
                "active_generation_restored": True,
                "seed_graph_unchanged": True,
                "gate30_fixture_isolated": True,
            }
            result["receipt_digest"] = hashlib.sha256(json.dumps(
                result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            with Path(target).open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
