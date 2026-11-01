"""Wave 11 shadow ledger equivalence and non-execution tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest, read_json
from dual_pipeline.ingestion_planning import (
    compile_ingestion_plan_v2,
    load_ingestion_l1_ledger_policy,
    load_ingestion_l1_policy,
    persist_ingestion_shadow_checkpoint,
    run_ingestion_shadow_ledger,
    verify_ingestion_shadow_equivalence,
)

ROOT = Path(__file__).resolve().parents[1]


class L1IngestionShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        bundle = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/normalized-candidate-bundle.json")
        plan_v1 = read_json(ROOT / "contracts/dual_pipeline/fixtures/valid/ingestion-plan.json")
        v2_policy = load_ingestion_l1_policy(read_json(ROOT / "requirements/ingestion-l1-control-policy.json"))
        self.policy = load_ingestion_l1_ledger_policy(read_json(ROOT / "requirements/ingestion-l1-ledger-policy.json"))
        self.plan = compile_ingestion_plan_v2(
            bundle=bundle, plan_v1=plan_v1,
            directive={"directive_id": "directive-wave-11-fixture", "package_hashes": ["b" * 64]},
            active_pair={"generation_id": "generation-wave-11-fixture", "graph_sha256": "c" * 64, "embedding_backend": "flagembedding-bge-m3-dev", "model_id": "BAAI/bge-m3", "model_revision": "fixture-revision"},
            authorization_scope_digest="a" * 64, policy=v2_policy,
        )
        self.outputs = {step["step_id"]: digest({"v1_output": step["step_id"]}) for step in self.plan["steps"]}

    def test_interrupted_shadow_resume_preserves_order_and_equivalence(self) -> None:
        interrupted = run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.policy, stop_after=7)
        self.assertEqual(7, len(interrupted["receipts"]))
        self.assertEqual("OPEN", interrupted["checkpoint"]["terminal_state"])
        resumed = run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.policy, prior_receipts=interrupted["receipts"], checkpoint=interrupted["checkpoint"])
        self.assertEqual(20, len(resumed["receipts"]))
        self.assertEqual("COMPLETED", resumed["checkpoint"]["terminal_state"])
        self.assertEqual(0, resumed["provider_call_count"])
        self.assertEqual(0, resumed["graph_mutation_count"])
        result = verify_ingestion_shadow_equivalence(plan=self.plan, receipts=resumed["receipts"], baseline_output_digests=self.outputs)
        self.assertTrue(result["equivalent"])
        self.assertEqual(16, result["candidate_step_count"])
        self.assertEqual(4, result["lifecycle_step_count"])

    def test_checkpoint_persistence_is_atomic_and_run_scoped(self) -> None:
        shadow = run_ingestion_shadow_ledger(plan=self.plan, baseline_output_digests=self.outputs, policy=self.policy, stop_after=1)
        with tempfile.TemporaryDirectory() as directory:
            path = persist_ingestion_shadow_checkpoint(runtime_root=Path(directory), checkpoint=shadow["checkpoint"])
            self.assertTrue(path.is_file())
            self.assertIn(self.plan["run_id"], path.parts)
            with self.assertRaises(FileExistsError):
                persist_ingestion_shadow_checkpoint(runtime_root=Path(directory), checkpoint=shadow["checkpoint"])


if __name__ == "__main__":
    unittest.main()
