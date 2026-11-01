"""End-to-end staged repair and release wiring using disposable SQLite fixtures."""
import json
from unittest.mock import patch

from dual_pipeline.core import read_json, sha256_file
from dual_pipeline.graph_consolidation_core.release import (
    activate_consolidation_release, prepare_consolidation_release,
)
from tests.redirect_repair_fixtures import RedirectFixture


class RedirectRepairE2ETests(RedirectFixture):
    def test_exact_overlapping_chains_preserve_every_other_record(self):
        original = self.rows(self.staged, "entity_redirect")
        receipt = self.apply()
        self.assertEqual(receipt["redirects_flattened"], 3)
        self.assertEqual(receipt["operation_count"], 3)
        after = self.rows(self.staged, "entity_redirect")
        for before, updated in zip(original, after):
            self.assertEqual(before[0], updated[0])
            self.assertEqual(before[2:], updated[2:])
            self.assertEqual(updated[1], "c")
        for table in ("node", "assertion", "entity_merge_event"):
            self.assertEqual(self.rows(self.graph, table), self.rows(self.staged, table))
        audits = self.rows(self.staged, "audit_event")
        self.assertEqual(len(audits), 3)
        self.assertTrue(all(row[11] == self.approval for row in audits))
        self.assertTrue(all(json.loads(row[6])["merge_event_id"] == "merge-original" for row in audits))
        self.assertEqual(sha256_file(self.graph), self.plan["base_graph_sha256"])
        self.assertEqual(sha256_file(self.sidecar), self.plan["base_sidecar_sha256"])
        self.assertEqual(sha256_file(self.pointer), self.plan["active_pair_sha256"])

    def prepare(self):
        with patch("dual_pipeline.graph_consolidation_core.release.build_sidecar",
                   side_effect=self.build_test_sidecar):
            return prepare_consolidation_release(manager=self.manager(), backend=self.backend(),
                plan=self.plan, run_id="fixture-release", approval_id=self.approval)

    def test_preparation_preserves_live_pair_and_binds_plan(self):
        with patch("dual_pipeline.graph_consolidation_core.release.build_sidecar",
                   side_effect=self.build_test_sidecar) as sidecar_builder:
            receipt, path = prepare_consolidation_release(
                manager=self.manager(), backend=self.backend(), plan=self.plan,
                run_id="fixture-release", approval_id=self.approval,
            )
        self.assertEqual(sidecar_builder.call_args.kwargs["prior_sidecar_path"], self.sidecar.resolve())
        self.assertEqual(receipt["status"], "PREPARED")
        self.assertEqual(receipt["redirect_repair_plan"], self.plan)
        self.assertEqual(receipt["graph_delta_summary"]["redirects_flattened"], 3)
        self.assertEqual(read_json(path), receipt)
        self.assertEqual(sha256_file(self.pointer), self.plan["active_pair_sha256"])
        self.assertEqual(sha256_file(self.graph), self.plan["base_graph_sha256"])

    def test_preparation_retry_preserves_prior_receipt(self):
        first, first_path = self.prepare()
        second, second_path = self.prepare()
        self.assertNotEqual(first_path, second_path)
        self.assertNotEqual(first["next_pair"]["generation_id"], second["next_pair"]["generation_id"])
        self.assertEqual(read_json(first_path), first)
        self.assertEqual(read_json(second_path), second)
        self.assertEqual(sha256_file(self.pointer), self.plan["active_pair_sha256"])

    def test_activation_requires_real_retrieval_gate(self):
        _, path = self.prepare()
        with self.assertRaises((ValueError, RuntimeError, PermissionError)):
            activate_consolidation_release(manager=self.manager(), receipt_path=path)
        self.assertEqual(sha256_file(self.pointer), self.plan["active_pair_sha256"])

    def test_activation_wiring_calls_gate_and_rejects_replay(self):
        receipt, path = self.prepare()
        with patch("dual_pipeline.graph_consolidation_core.redirect_activation.validate_activation_evaluation",
                   return_value={"required": True, "evaluation_digest": "TEST-ONLY"}) as gate:
            activation = activate_consolidation_release(manager=self.manager(), receipt_path=path)
        gate.assert_called_once_with(self.root.resolve(), receipt)
        self.assertEqual(activation["status"], "ACTIVE")
        self.assertEqual(read_json(self.pointer)["generation_id"], receipt["next_pair"]["generation_id"])
        with self.assertRaises(RuntimeError):
            activate_consolidation_release(manager=self.manager(), receipt_path=path)
