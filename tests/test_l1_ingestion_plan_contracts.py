"""Wave 10 proposal-only ingestion plan v2 and checkpoint boundaries."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, seal_artifact
from dual_pipeline.ingestion_planning import (
    IngestionPlanningError,
    build_ingestion_plan_checkpoint,
    build_ingestion_plan_step_receipt,
    compile_ingestion_plan_v2,
    load_ingestion_l1_policy,
    validate_checkpoint_binding,
    validate_ingestion_plan_v2,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


class L1IngestionPlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/normalized-candidate-bundle.json")
        self.v1 = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/ingestion-plan.json")
        self.policy = load_ingestion_l1_policy(read_json(ROOT / "requirements/ingestion-l1-control-policy.json"))
        self.directive = {"directive_id": "directive-wave-10-fixture", "package_hashes": ["b" * 64]}
        self.active = {"generation_id": "generation-wave-10-fixture", "graph_sha256": "c" * 64, "embedding_backend": "flagembedding-bge-m3-dev", "model_id": "BAAI/bge-m3", "model_revision": "fixture-revision"}

    def _plan(self):
        return compile_ingestion_plan_v2(bundle=self.bundle, plan_v1=self.v1, directive=self.directive, active_pair=self.active, authorization_scope_digest=SHA, policy=self.policy)

    def test_compiler_reproduces_candidate_and_lifecycle_obligations_without_execution(self) -> None:
        plan = self._plan()
        validate_artifact(plan)
        validate_ingestion_plan_v2(plan)
        self.assertTrue(plan["proposal_only"])
        self.assertEqual(20, len(plan["steps"]))
        self.assertEqual(0, sum(step["provider_call_count"] for step in plan["steps"]))
        self.assertEqual(0, sum(step["graph_mutation_count"] for step in plan["steps"]))
        self.assertEqual(["x1", "x2", "x3", "l4_terminal"], [step["step_kind"] for step in plan["steps"][-4:]])

    def test_policy_is_closed_default_off_and_provider_free(self) -> None:
        value = read_json(ROOT / "requirements/ingestion-l1-control-policy.json")
        value["mode"] = "shadow"
        with self.assertRaisesRegex(IngestionPlanningError, "off"):
            load_ingestion_l1_policy(value)
        value = read_json(ROOT / "requirements/ingestion-l1-control-policy.json")
        value["provider_calls_before_l2d"] = 1
        with self.assertRaisesRegex(IngestionPlanningError, "provider-free"):
            load_ingestion_l1_policy(value)

    def test_schema_rejects_authority_and_forward_dependency_mutations(self) -> None:
        plan = self._plan()
        authority = copy.deepcopy(plan)
        authority["steps"][0]["approval"] = "forbidden"
        authority = seal_artifact(authority)
        with self.assertRaises(Exception):
            validate_artifact(authority)
        drift = copy.deepcopy(plan)
        drift["steps"][0]["dependencies"] = [drift["steps"][1]["step_id"]]
        drift = seal_artifact(drift)
        with self.assertRaises(Exception):
            validate_ingestion_plan_v2(drift)

    def test_terminal_only_checkpoint_rejects_binding_drift(self) -> None:
        plan = self._plan()
        checkpoint = build_ingestion_plan_checkpoint(plan=plan, completed_step_receipt_digests=["d" * 64], terminal_state="BLOCKED")
        validate_artifact(checkpoint)
        validate_checkpoint_binding(checkpoint, plan)
        changed = copy.deepcopy(plan)
        changed["active_pair"]["graph_sha256"] = "e" * 64
        changed = seal_artifact(changed)
        with self.assertRaisesRegex(IngestionPlanningError, "L1_CHECKPOINT_BINDING_DRIFT"):
            validate_checkpoint_binding(checkpoint, changed)
        with self.assertRaisesRegex(IngestionPlanningError, "open or partial"):
            build_ingestion_plan_checkpoint(plan=plan, completed_step_receipt_digests=["d" * 64], terminal_state="OPEN")

    def test_step_receipt_is_plan_bound_and_non_executing(self) -> None:
        plan = self._plan()
        receipt = build_ingestion_plan_step_receipt(plan=plan, step=plan["steps"][0])
        validate_artifact(receipt)
        self.assertEqual("PLANNED", receipt["status"])
        self.assertIsNone(receipt["output_digest"])
        self.assertEqual(0, receipt["provider_call_count"])
        foreign = copy.deepcopy(plan["steps"][0])
        foreign["step_id"] = "foreign-step"
        with self.assertRaisesRegex(IngestionPlanningError, "does not belong"):
            build_ingestion_plan_step_receipt(plan=plan, step=foreign)


if __name__ == "__main__":
    unittest.main()
