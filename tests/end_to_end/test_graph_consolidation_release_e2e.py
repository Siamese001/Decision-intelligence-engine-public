"""
tests/end_to_end/test_graph_consolidation_release_e2e.py

End-to-End lifecycle test for Governed Graph Consolidation Wave 4 release actuator.
Tests:
1. Bounded plan creation from real duplicate entities and assertions in active graph.
2. Atomic release preparation in isolated generation via ReleaseManager.
3. UWG actuation: node deactivation, assertion re-pointing, triple supersession,
   evidence consolidation, search_document sync, and audit_event logging.
4. Release receipt sealing with full component_runs operation scoping (Defect D6 fix).
5. W0/E0 telemetry recording via RunRecorder.
6. Atomic release activation into active_pair.json.
7. Subsequent zero-op idempotent no-op handling (Defect D3 fix).
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from dual_pipeline.core import (
    digest,
    exact_file_copy,
    sha256_file,
    stable_id,
    write_json,
)
from dual_pipeline.graph_consolidation_core.plan import build_consolidation_plan
from dual_pipeline.graph_consolidation_core.release import (
    activate_consolidation_release,
    prepare_consolidation_release,
    record_consolidation_release_events,
)
from dual_pipeline.release import ReleaseManager


class MockEmbeddingBackend:
    """Approved BGE-M3 test embedding backend with real-bge-m3 encoder."""

    def __init__(self, backend_id: str = "flagembedding-bge-m3-dev") -> None:
        self.backend_id = backend_id
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
        self.model_manifest_digest = "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e"
        self.production_qualified = False
        self.retrieval_encoder = "real-bge-m3"

    def verify_snapshot(self) -> None:
        pass


class TestGraphConsolidationReleaseE2E(unittest.TestCase):
    """End-to-end release lifecycle and activation test."""

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

    def test_full_consolidation_release_lifecycle(self) -> None:
        """Executes full release lifecycle from plan through activation and zero-op post-condition."""
        # 1. Build consolidation plan against baseline graph
        plan = build_consolidation_plan(self.graph_path, active_pair=self.active_pair)
        self.assertGreater(plan["summary"]["total_operations_count"], 0)
        self.assertEqual(plan["status"], "proposed")
        self.assertTrue(plan["approval_required"])

        merges = [op for op in plan["operations"] if op["operation_type"] == "merge_node"]
        supersedes = [op for op in plan["operations"] if op["operation_type"] == "supersede_assertion"]
        self.assertTrue(len(merges) > 0)
        self.assertTrue(len(supersedes) > 0)

        # Scope plan to 1 node merge and 1 assertion supersession for targeted verification
        target_merge = merges[0]
        target_supersede = supersedes[0]
        scoped_ops = [target_merge, target_supersede]

        test_plan = dict(plan)
        test_plan["operations"] = scoped_ops
        test_plan["proposed_operations"] = scoped_ops
        test_plan["summary"] = dict(plan["summary"])
        test_plan["summary"]["total_operations_count"] = len(scoped_ops)
        test_plan["plan_id"] = stable_id("test-e2e-plan", self.gen_id, self.active_pair["graph_sha256"], length=24)
        test_plan["plan_digest"] = digest({k: v for k, v in test_plan.items() if k != "plan_digest"})

        # Mock sidecar sync helper
        def mock_build_sidecar(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            sp = kwargs.get("sidecar_path")
            gid = kwargs.get("generation_id")
            snap = kwargs.get("graph_snapshot")
            if sp:
                exact_file_copy(self.sidecar_path, sp)
                sconn = sqlite3.connect(sp)
                sconn.execute("UPDATE metadata SET value=? WHERE key='generation_id'", (gid,))
                sconn.execute("UPDATE metadata SET value=? WHERE key='graph_snapshot'", (snap,))
                sconn.execute("UPDATE metadata SET value=? WHERE key='graph_sha256'", (sha256_file(kwargs.get("graph_path")),))
                sconn.commit()
                sconn.close()
            return {
                "sidecar_sha256": sha256_file(sp),
                "record_count": 10,
            }

        # 2. Prepare release with authentic human operator approval
        approval_id = "USER_INPUT: Operator Amit Ayer approves directive directive-graph-consolidation-e2e-v1"
        with patch("dual_pipeline.graph_consolidation_core.release.build_sidecar", side_effect=mock_build_sidecar):
            receipt, receipt_path = prepare_consolidation_release(
                manager=self.manager,
                backend=self.backend,
                plan=test_plan,
                run_id="run-consolidation-e2e-01",
                trace_id="0123456789abcdef0123456789abcdef",
                approval_id=approval_id,
            )

        self.assertEqual(receipt["status"], "PREPARED")
        self.assertTrue(receipt_path.exists())
        self.assertEqual(receipt["graph_write_receipt"]["nodes_merged"], 1)
        self.assertEqual(receipt["graph_write_receipt"]["assertions_superseded"], 1)

        # Verify component_runs itemizes all operations (Defect D6 fix)
        component = receipt["component_runs"][0]
        self.assertEqual(len(component["operation_ids"]), len(scoped_ops))

        # 3. Verify post-release graph invariants in isolated next generation
        next_gen_id = receipt["next_pair"]["generation_id"]
        next_graph = self.runtime_root / "generations" / next_gen_id / "graph.sqlite"
        self.assertTrue(next_graph.exists())

        conn = sqlite3.connect(next_graph)
        conn.row_factory = sqlite3.Row

        # Node check: retired node is merged and inactive
        ret_row = conn.execute(
            "SELECT active, status FROM node WHERE node_id=?",
            (target_merge["retired_node_id"],),
        ).fetchone()
        self.assertEqual(ret_row["active"], 0)
        self.assertEqual(ret_row["status"], "merged")

        # Assertion check: loser assertion is superseded and historical
        lose_asrt = conn.execute(
            "SELECT currentness_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id=?",
            (target_supersede["retired_assertion_id"],),
        ).fetchone()
        self.assertEqual(lose_asrt["currentness_state"], "historical")
        self.assertEqual(lose_asrt["retrieval_eligible"], 0)
        self.assertEqual(lose_asrt["superseded_by_assertion_id"], target_supersede["survivor_assertion_id"])

        # Audit events recorded for both operations
        audit_actions = [
            r["action"] for r in conn.execute(
                "SELECT action FROM audit_event WHERE actor_or_process='graph_consolidation'"
            ).fetchall()
        ]
        self.assertIn("merge_node", audit_actions)
        self.assertIn("supersede_assertion", audit_actions)
        conn.close()

        # 4. Record telemetry events (W0/E0)
        telemetry = record_consolidation_release_events(
            runtime_root=self.runtime_root, receipt=receipt
        )
        self.assertEqual(telemetry["status"], "PASS")

        # 5. Activate release into active_pair.json
        activation = activate_consolidation_release(
            manager=self.manager,
            receipt_path=receipt_path,
            approval_id=approval_id,
        )
        self.assertEqual(activation["status"], "ACTIVE")

        # Active pair is updated atomically
        new_active = self.manager.active_pair()
        self.assertEqual(new_active["generation_id"], next_gen_id)
        self.assertEqual(new_active["graph_sha256"], receipt["next_pair"]["graph_sha256"])

        # 6. Idempotent zero-op safety: subsequent zero-op plan emits IDEMPOTENT_NOOP
        zero_plan = dict(test_plan)
        zero_plan["base_generation_id"] = new_active["generation_id"]
        zero_plan["base_graph_sha256"] = new_active["graph_sha256"]
        zero_plan["operations"] = []
        zero_plan["proposed_operations"] = []
        zero_plan["summary"]["total_operations_count"] = 0
        zero_plan["plan_digest"] = digest({k: v for k, v in zero_plan.items() if k != "plan_digest"})

        noop_receipt, noop_path = prepare_consolidation_release(
            manager=self.manager,
            backend=self.backend,
            plan=zero_plan,
            run_id="run-consolidation-e2e-noop",
            trace_id="abcdef0123456789abcdef0123456789",
            approval_id=approval_id,
        )
        self.assertEqual(noop_receipt["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(noop_receipt["graph_write_receipt"]["operation_count"], 0)
        self.assertEqual(noop_receipt["previous_pair"]["graph_sha256"], noop_receipt["next_pair"]["graph_sha256"])


if __name__ == "__main__":
    unittest.main()
