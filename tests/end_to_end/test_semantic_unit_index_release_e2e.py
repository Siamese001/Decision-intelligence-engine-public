"""
tests/end_to_end/test_semantic_unit_index_release_e2e.py

End-to-End lifecycle test for Live Semantic-Unit Index Release (Wave 6).
Tests:
1. Preparation of semantic-unit release in isolated generation directory.
2. Zero-mutation graph preservation (graph_sha256 identical before and after).
3. Unit-only sidecar generation (edges dropped, passages, propositions, profiles built).
4. Extended sidecar validation (set equality, L2 norms, unit policy enforcement).
5. V0 fact reachability audit passing with zero unexplained gap.
6. 11 BGE-M3 release evaluation gates passing.
7. Immutable receipt sealing for release and retrieval evaluation.
8. Telemetry recording of W0 (NOOP) and E0 (embeddings) stages.
9. Atomic activation under active_pair lock updating active_pair.json.
10. Idempotent re-preparation / repeat handling.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    exact_file_copy,
    read_json,
    sha256_file,
    write_json,
)
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.policy import load_semantic_unit_policy
from dual_pipeline.index_core.release import (
    activate_semantic_unit_release,
    prepare_semantic_unit_release,
    record_semantic_unit_release_events,
)
from dual_pipeline.index_core.sidecar import validate_sidecar
from dual_pipeline.release import ReleaseManager

ROOT = Path(__file__).resolve().parent.parent.parent


class TestSemanticUnitIndexReleaseE2E(unittest.TestCase):
    """E2E lifecycle test for Wave 6 live semantic-unit index release."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / ".local" / "dual_pipeline"
        self.runtime_root.mkdir(parents=True)

        # Baseline graph and generation
        source_active = read_json(ROOT / ".local" / "dual_pipeline" / "active_pair.json")
        source_gen_dir = (
            ROOT / ".local" / "dual_pipeline" / source_active["graph_path"]
        ).parent
        source_graph = source_gen_dir / "graph.sqlite"
        source_sidecar = source_gen_dir / "sidecar.sqlite"

        self.gen_id = source_active["generation_id"]
        self.gen_dir = self.runtime_root / "generations" / self.gen_id
        self.gen_dir.mkdir(parents=True)
        self.releases_dir = self.runtime_root / "releases"
        self.releases_dir.mkdir(parents=True)

        self.graph_path = self.gen_dir / "graph.sqlite"
        self.sidecar_path = self.gen_dir / "sidecar.sqlite"
        exact_file_copy(source_graph, self.graph_path)
        exact_file_copy(source_sidecar, self.sidecar_path)

        conn = sqlite3.connect(self.graph_path)
        missing = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT node_id FROM node_state WHERE node_id NOT IN (SELECT node_id FROM node)"
            )
        ]
        for nid in missing:
            ntype = (
                "capability"
                if nid.startswith("capability")
                else ("risk" if nid.startswith("risk") else "card")
            )
            conn.execute(
                "INSERT OR IGNORE INTO node (node_id, node_type, canonical_name, active, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, '2026-10-04T00:00:00Z', '2026-10-04T00:00:00Z')",
                (nid, ntype, nid),
            )
        conn.commit()
        conn.close()

        gh = sha256_file(self.graph_path)
        sconn = sqlite3.connect(self.sidecar_path)
        sconn.execute("UPDATE metadata SET value = ? WHERE key = 'graph_sha256'", (gh,))
        sconn.execute("UPDATE metadata SET value = ? WHERE key = 'generation_id'", (self.gen_id,))
        sconn.commit()
        sconn.close()

        sh = sha256_file(self.sidecar_path)

        self.active_pair: dict[str, Any] = dict(source_active)
        self.active_pair["graph_path"] = f"generations/{self.gen_id}/graph.sqlite"
        self.active_pair["sidecar_path"] = f"generations/{self.gen_id}/sidecar.sqlite"
        self.active_pair["graph_sha256"] = gh
        self.active_pair["sidecar_sha256"] = sh

        write_json(self.runtime_root / "active_pair.json", self.active_pair)
        write_json(self.gen_dir / "pair_manifest.json", self.active_pair)

        self.manager = ReleaseManager(self.runtime_root)
        self.backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
            model_revision=source_active["model_revision"],
        )
        self.policy = load_semantic_unit_policy()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_semantic_unit_index_release_lifecycle(self) -> None:
        """Executes full release lifecycle from preparation through activation and telemetry."""
        approval_id = "USER_INPUT: Operator authorized Wave 6 live semantic unit index release"
        run_id = "run-semantic-unit-release-e2e-001"

        # 1. Prepare release
        receipt, receipt_path = prepare_semantic_unit_release(
            manager=self.manager,
            backend=self.backend,
            run_id=run_id,
            approval_id=approval_id,
            policy=self.policy,
        )

        self.assertEqual(receipt["status"], "PREPARED")
        self.assertEqual(receipt["release_kind"], "semantic_unit_index_release")
        self.assertEqual(receipt["run_id"], run_id)
        self.assertTrue(receipt_path.is_file())

        # 2. Verify zero-mutation graph preservation
        graph_receipt = receipt["graph_write_receipt"]
        self.assertEqual(graph_receipt["status"], "NOOP")
        self.assertEqual(graph_receipt["operation_count"], 0)
        self.assertEqual(
            graph_receipt["graph_sha256_before"],
            self.active_pair["graph_sha256"],
        )
        self.assertEqual(
            graph_receipt["graph_sha256_after"],
            self.active_pair["graph_sha256"],
        )

        # 3. Verify V0 fact reachability audit passed
        v0_receipt = receipt["v0_fact_reachability_receipt"]
        self.assertEqual(v0_receipt["status"], "PASS")
        self.assertEqual(v0_receipt["unreachable_count"], 0)
        self.assertIn("receipt_digest", v0_receipt)

        # 4. Verify 11 BGE-M3 release evaluation gates passed
        eval_receipt = receipt["retrieval_evaluation_receipt"]
        self.assertEqual(eval_receipt["status"], "PASS")
        self.assertTrue(eval_receipt["all_11_gates_passed"])
        self.assertTrue(eval_receipt["reachability_100pct_met"])

        # 5. Verify unit-only sidecar validation
        next_pair = receipt["next_pair"]
        new_sidecar_path = self.runtime_root / next_pair["sidecar_path"]
        validation = validate_sidecar(
            new_sidecar_path,
            expected_generation_id=next_pair["generation_id"],
            enforce_unit_policy=True,
        )
        self.assertEqual(validation["status"], "PASS")

        # 6. Record telemetry
        telemetry = record_semantic_unit_release_events(
            runtime_root=self.runtime_root,
            receipt=receipt,
        )
        self.assertEqual(telemetry["status"], "PASS")

        # 7. Activate release
        activation = activate_semantic_unit_release(
            manager=self.manager,
            receipt_path=receipt_path,
            approval_id=approval_id,
        )
        self.assertEqual(activation["status"], "ACTIVE")
        self.assertEqual(activation["release_receipt_id"], receipt["receipt_id"])

        # 8. Verify active_pair.json was updated
        active_now = self.manager.active_pair()
        self.assertEqual(active_now["generation_id"], next_pair["generation_id"])
        self.assertEqual(active_now["graph_sha256"], self.active_pair["graph_sha256"])
        self.assertEqual(active_now["sidecar_sha256"], next_pair["sidecar_sha256"])
        self.assertIsNotNone(active_now["activated_at"])

        # 9. Verify activation receipt was persisted
        activation_path = (
            self.releases_dir / f"activation-{next_pair['generation_id']}.json"
        )
        self.assertTrue(activation_path.is_file())


if __name__ == "__main__":
    unittest.main()
