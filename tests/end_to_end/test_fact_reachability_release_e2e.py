import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.completion import IngestionCompletionGuard
from dual_pipeline.core import read_json, sha256_file, write_json
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.embedding_space_contract import (
    assert_query_vector_contract,
    validate_embedding_space_contract,
)
from dual_pipeline.index_core.shadow_eval import build_index_b_sidecar
from dual_pipeline.index_core.sidecar import validate_sidecar
from dual_pipeline.index_core.v0_audit import verify_v0_fact_reachability

ROOT = Path(__file__).resolve().parent.parent.parent


class TestFactReachabilityReleaseE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

        # Baseline graph and generation
        self.active_pair = read_json(ROOT / ".local" / "dual_pipeline" / "active_pair.json")
        self.gen_dir = ROOT / ".local" / "dual_pipeline" / "generations" / "generation-graph-consolidation-d488dbeff02817be07106935301f"
        self.graph_path = self.gen_dir / "graph.sqlite"
        self.sidecar_path = self.gen_dir / "sidecar.sqlite"
        write_json(self.work_dir / "active_pair.json", self.active_pair)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_v0_reachability_and_release_lifecycle(self) -> None:
        """Happy path E2E: Index B build, V0 audit pass, receipt generation, and no-op completion."""
        # 1. Build Index B sidecar (units only)
        index_b_dir = self.work_dir / "index_b"
        backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
            model_revision=self.active_pair["model_revision"],
        )
        build_meta = build_index_b_sidecar(
            graph_path=self.graph_path,
            base_sidecar_path=self.sidecar_path,
            output_dir=index_b_dir,
            generation_id="gen-e2e-w5",
            backend=backend,
        )
        index_b_path = index_b_dir / "sidecar.sqlite"
        self.assertTrue(index_b_path.is_file())
        self.assertEqual(build_meta["reused_vectors"], 194)

        # 2. Validate sidecar with full extended invariants (set equality, norms, unit policy)
        validation = validate_sidecar(
            index_b_path,
            expected_generation_id="gen-e2e-w5",
            enforce_unit_policy=True,
        )
        self.assertEqual(validation["status"], "PASS")

        # 3. Run V0 Fact Reachability audit
        v0_receipt = verify_v0_fact_reachability(
            graph_path=self.graph_path,
            sidecar_path=index_b_path,
            generation_id="gen-e2e-w5",
            directive_id="directive-e2e-w5-reachability",
            runtime_root=self.work_dir,
        )
        self.assertEqual(v0_receipt["status"], "PASS")
        self.assertEqual(v0_receipt["unreachable_count"], 0)
        self.assertIn("receipt_digest", v0_receipt)

        # 4. Verify V0 receipt file was persisted
        persisted_v0 = self.work_dir / "v0_receipts" / f"{v0_receipt['receipt_id']}.json"
        self.assertTrue(persisted_v0.is_file())
        saved = read_json(persisted_v0)
        self.assertEqual(saved["receipt_digest"], v0_receipt["receipt_digest"])

        # 5. Verify IngestionCompletionGuard complete_noop records V0
        guard = IngestionCompletionGuard(
            runtime_root=self.work_dir,
            seed_graph=self.graph_path,
        )
        pkg = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "architecture-diagram.json"
        )
        mock_evidence = [
            {
                "run_id": "run-intake-noop-001",
                "package_file_sha256": sha256_file(pkg),
                "graph_sha256_before": self.active_pair["graph_sha256"],
                "graph_sha256_after": self.active_pair["graph_sha256"],
                "graph_operation_count": 0,
                "event_chain_digest": "0" * 64,
            }
        ]
        with (
            mock.patch.object(guard.manager, "validate_pair", return_value={"status": "PASS"}),
            mock.patch.object(guard, "_validate_release_request_state", return_value=mock_evidence),
        ):
            open_res = guard.open(
                directive_id="directive-test-v0-noop-v1",
                scope="Test V0 audit in completion guard",
                packages=[pkg],
            )
            self.assertEqual(open_res["status"], "OPEN")

            # Classify and record human decision
            class_res = guard.classify("directive-test-v0-noop-v1")
            classification = class_res["classification"]
            guard.record_human_decision(
                "directive-test-v0-noop-v1",
                decision="APPROVE",
                classification_digest=classification["classification_digest"],
                reviewer="test-operator",
                rationale="Reviewed no-op classification",
            )
            guard.validate_release_request(
                "directive-test-v0-noop-v1",
                run_ids=["run-intake-noop-001"],
            )

            # Complete no-op
            noop_res = guard.complete_noop(
                "directive-test-v0-noop-v1",
                run_ids=["run-intake-noop-001"],
            )
            self.assertEqual(noop_res["status"], "PASS")
            comp = noop_res["completion"]
            self.assertIn("V0", comp["not_applicable_stages"])
            self.assertEqual(comp["v0_fact_reachability"]["status"], "NOT_APPLICABLE")

        # 6. Verify real BGE-M3 query vector compatibility
        encoded = backend.encode("What is the operational architecture?")
        valid_query = assert_query_vector_contract(encoded["dense"])
        self.assertEqual(valid_query.shape, (1024,))

        validate_embedding_space_contract(
            backend=backend,
            sidecar_metadata=validation["metadata"],
            query_vector=valid_query,
        )


if __name__ == "__main__":
    unittest.main()
