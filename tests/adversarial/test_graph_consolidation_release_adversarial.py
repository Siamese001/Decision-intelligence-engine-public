"""
tests/adversarial/test_graph_consolidation_release_adversarial.py

Adversarial and boundary regression tests for Graph Consolidation Wave 4 actuator & release.
Validates:
1. Synthetic approval origin rejection (AGENTS.md invariant fail-closed).
2. Stale plan / graph hash drift fail-closed rejection.
3. Backend / generation drift protection.
4. Foreign key constraint enforcement (FKs ON, no write_control bypass).
5. Atomic rollback on actuation failure (zero partial corruption).
6. Idempotent zero-op safety (Defect D3 regression pin).
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.core import exact_file_copy, sha256_file, write_json
from dual_pipeline.graph_consolidation_core.apply import apply_consolidation_plan
from dual_pipeline.graph_consolidation_core.plan import build_consolidation_plan
from dual_pipeline.graph_consolidation_core.release import (
    prepare_consolidation_release,
    record_consolidation_release_events,
)
from dual_pipeline.release import ReleaseManager


class MockEmbeddingBackend:
    """Deterministic mock embedding backend for testing release isolation."""

    def __init__(self, backend_id: str = "flagembedding-bge-m3-dev") -> None:
        self.backend_id = backend_id
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
        self.model_manifest_digest = "5617a9f61b028005a4858fdac845db406aefb1815617a9f61b028005a4858fda"
        self.production_qualified = False



class TestGraphConsolidationReleaseAdversarial(unittest.TestCase):
    """Adversarial tests for actuator, foreign keys, approvals, and release gates."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / ".local" / "dual_pipeline"
        self.runtime_root.mkdir(parents=True)

        real_mgr = ReleaseManager(".local/dual_pipeline")
        real_active = real_mgr.active_pair()

        self.gen_id = real_active["generation_id"]
        self.gen_dir = self.runtime_root / "generations" / self.gen_id
        self.gen_dir.mkdir(parents=True)
        self.releases_dir = self.runtime_root / "releases"
        self.releases_dir.mkdir(parents=True)

        self.graph_path = self.gen_dir / "graph.sqlite"
        self.sidecar_path = self.gen_dir / "sidecar.sqlite"
        exact_file_copy(Path(real_mgr._resolve(real_active["graph_path"])), self.graph_path)
        exact_file_copy(Path(real_mgr._resolve(real_active["sidecar_path"])), self.sidecar_path)

        self.active_pair: Dict[str, Any] = dict(real_active)
        self.active_pair["graph_path"] = f"generations/{self.gen_id}/graph.sqlite"
        self.active_pair["sidecar_path"] = f"generations/{self.gen_id}/sidecar.sqlite"

        write_json(self.runtime_root / "active_pair.json", self.active_pair)
        write_json(self.gen_dir / "pair_manifest.json", self.active_pair)

        self.manager = ReleaseManager(self.runtime_root)
        self.backend = MockEmbeddingBackend()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthetic_approval_origin_rejected(self) -> None:
        """Synthetic IDE stop hook markers are fail-closed rejected via validate_approval_origin."""
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        synthetic_approvals = [
            "stop hook blocked termination due to reason: auto-approved",
            "automatically approved the artifact through review policy",
            "artifact_review_mode_turbo autoexecutionpolicy",
        ]
        for synth in synthetic_approvals:
            with self.assertRaises(PermissionError) as ctx:
                prepare_consolidation_release(
                    manager=self.manager,
                    backend=self.backend,
                    plan=plan,
                    run_id="run-test-01",
                    trace_id="trace-test-01",
                    approval_id=synth,
                )
            self.assertIn("synthetic", str(ctx.exception).lower())

    def test_stale_graph_hash_rejected(self) -> None:
        """Mutating the base graph after plan generation triggers stale hash detection."""
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        # Mutate base graph behind plan's back
        conn = sqlite3.connect(self.graph_path)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, created_at, updated_at) "
            "VALUES ('drift_node', 'Drift', 'org', '2026-07-08T00:00:00Z', '2026-07-08T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        with self.assertRaises(RuntimeError) as ctx:
            apply_consolidation_plan(
                self.graph_path,
                plan=plan,
                approval_id="human_operator_approved_directive_01",
                run_id="run-stale-01",
            )
        self.assertIn("stale", str(ctx.exception).lower())

    def test_backend_drift_rejected(self) -> None:
        """Release preparation fails closed if backend ID does not match active pair."""
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        drifted_backend = MockEmbeddingBackend(backend_id="flagembedding-bge-m3-internal")
        with self.assertRaises(RuntimeError) as ctx:
            prepare_consolidation_release(
                manager=self.manager,
                backend=drifted_backend,
                plan=plan,
                run_id="run-backend-drift",
                trace_id="trace-backend-drift",
                approval_id="human_operator_approved_directive_01",
            )
        self.assertIn("drifted", str(ctx.exception).lower())

    def test_zero_op_produces_idempotent_noop_receipt(self) -> None:
        """Zero-op plan emits IDEMPOTENT_NOOP without creating generation directory (Defect D3 pin)."""
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        zero_plan = dict(plan)
        zero_plan["operations"] = []
        zero_plan["proposed_operations"] = []
        zero_plan["summary"] = dict(plan["summary"])
        zero_plan["summary"]["total_operations_count"] = 0
        from dual_pipeline.core import digest
        zero_plan["plan_digest"] = digest({k: v for k, v in zero_plan.items() if k != "plan_digest"})

        receipt, receipt_path = prepare_consolidation_release(
            manager=self.manager,
            backend=self.backend,
            plan=zero_plan,
            run_id="run-noop-01",
            trace_id="trace-noop-01",
            approval_id="human_operator_approved_directive_01",
        )
        self.assertEqual(receipt["status"], "IDEMPOTENT_NOOP")
        self.assertTrue(receipt_path.exists())
        self.assertEqual(receipt["graph_write_receipt"]["operation_count"], 0)
        self.assertEqual(receipt["previous_pair"]["graph_sha256"], receipt["next_pair"]["graph_sha256"])

        # Telemetry records NOT_APPLICABLE for W0/E0 on zero-op runs
        telemetry = record_consolidation_release_events(
            runtime_root=self.runtime_root, receipt=receipt
        )
        self.assertEqual(telemetry["status"], "NOT_APPLICABLE")

    def test_atomic_rollback_on_fk_violation(self) -> None:
        """Actuator rolls back atomically and leaves graph unchanged if FK violation occurs."""
        conn = sqlite3.connect(self.graph_path)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, created_at, updated_at) "
            "VALUES ('surv_rollback', 'Survivor', 'org', '2026-07-08T00:00:00Z', '2026-07-08T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, created_at, updated_at) "
            "VALUES ('ret_rollback', 'Retired', 'org', '2026-07-08T00:00:00Z', '2026-07-08T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        self.active_pair["graph_sha256"] = sha256_file(self.graph_path)
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        # Inject an adversarial operation referencing non-existent node
        from dual_pipeline.core import digest
        bad_plan = dict(plan)
        bad_plan["operations"] = [
            {
                "operation_id": "op-adversarial-fk",
                "operation_type": "merge_node",
                "retired_node_id": "non_existent_node_id",
                "survivor_node_id": "surv_rollback",
            }
        ]
        bad_plan["plan_digest"] = digest({k: v for k, v in bad_plan.items() if k != "plan_digest"})
        before_hash = sha256_file(self.graph_path)

        with self.assertRaises(ValueError):
            apply_consolidation_plan(
                self.graph_path,
                plan=bad_plan,
                approval_id="human_operator_approved_directive_01",
                run_id="run-rollback-01",
            )
        # Base graph is uncorrupted
        self.assertEqual(sha256_file(self.graph_path), before_hash)



if __name__ == "__main__":
    unittest.main()
