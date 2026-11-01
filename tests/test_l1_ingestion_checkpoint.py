"""Wave 11 checkpoint drift and enforced-mode admission tests."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from dual_pipeline.core import digest, read_json, seal_artifact
from dual_pipeline.ingestion_planning import (
    IngestionPlanningError,
    compile_ingestion_plan_v2,
    load_ingestion_l1_ledger_policy,
    load_ingestion_l1_policy,
    resolve_ingestion_ledger_mode,
    run_ingestion_shadow_ledger,
    schedule_ingestion_enforced_ledger,
    verify_ingestion_shadow_equivalence,
)

ROOT = Path(__file__).resolve().parents[1]


class L1IngestionCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        bundle = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/normalized-candidate-bundle.json")
        plan_v1 = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/ingestion-plan.json")
        policy = load_ingestion_l1_policy(read_json(ROOT / "requirements/ingestion-l1-control-policy.json"))
        self.ledger_policy = load_ingestion_l1_ledger_policy(read_json(ROOT / "requirements/ingestion-l1-ledger-policy.json"))
        self.plan = compile_ingestion_plan_v2(bundle=bundle, plan_v1=plan_v1, directive={"directive_id": "directive-wave-11-drift", "package_hashes": ["b" * 64]}, active_pair={"generation_id": "generation-wave-11-drift", "graph_sha256": "c" * 64, "embedding_backend": "flagembedding-bge-m3-dev", "model_id": "BAAI/bge-m3", "model_revision": "fixture-revision"}, authorization_scope_digest="a" * 64, policy=policy)
        self.outputs = {step["step_id"]: digest({"v1_output": step["step_id"]}) for step in self.plan["steps"]}

    def test_resume_fails_closed_on_active_pair_and_chain_drift(self) -> None:
        interrupted = run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.ledger_policy, stop_after=3)
        changed_plan = copy.deepcopy(self.plan)
        changed_plan["active_pair"]["graph_sha256"] = "d" * 64
        changed_plan = seal_artifact(changed_plan)
        with self.assertRaisesRegex(IngestionPlanningError, "L1_CHECKPOINT_BINDING_DRIFT"):
            run_ingestion_shadow_ledger(plan=changed_plan, baseline_output_digests={step["step_id"]: digest({"v1_output": step["step_id"]}) for step in changed_plan["steps"]}, policy=self.ledger_policy, prior_receipts=interrupted["receipts"], checkpoint=interrupted["checkpoint"])
        changed_receipts = copy.deepcopy(interrupted["receipts"])
        changed_receipts[0]["output_digest"] = "e" * 64
        changed_receipts[0] = seal_artifact(changed_receipts[0])
        with self.assertRaisesRegex(IngestionPlanningError, "L1_SHADOW_CHECKPOINT_CHAIN_INVALID"):
            run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.ledger_policy, prior_receipts=changed_receipts, checkpoint=interrupted["checkpoint"])

    def test_enforced_requires_equivalence_and_does_not_change_default(self) -> None:
        self.assertEqual("off", resolve_ingestion_ledger_mode(self.ledger_policy, None))
        with self.assertRaisesRegex(IngestionPlanningError, "L1_ENFORCED_MODE_REQUIRES_SHADOW_EQUIVALENCE"):
            resolve_ingestion_ledger_mode(self.ledger_policy, "enforced")
        self.assertEqual("enforced", resolve_ingestion_ledger_mode(self.ledger_policy, "enforced", shadow_equivalent=True))
        shadow = run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.ledger_policy)
        equivalence = verify_ingestion_shadow_equivalence(plan=self.plan, receipts=shadow["receipts"], baseline_output_digests=self.outputs)
        self.assertEqual(tuple(step["step_id"] for step in self.plan["steps"]), schedule_ingestion_enforced_ledger(plan=self.plan, policy=self.ledger_policy, shadow_equivalence=equivalence))


if __name__ == "__main__":
    unittest.main()
