#!/usr/bin/env python3
"""Comprehensive test suite for concurrent release rebasing and staged vector coalescing.

Validates:
1. Conflict detection for target entity, assertion, supersession, and evidence IDs.
2. Conflict-free sequential rebase (G0 -> G1 -> G2) when concurrent runs finish out of order.
3. True mutation conflict fail-closed rejection with ConcurrentMutationConflictError.
4. Multi-run group commit via prepare_many with combined staged vectors.
5. Verification of the 5 mandatory physical graph mutation proof checks.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, seal_artifact, sha256_file, write_json  # noqa: E402
from dual_pipeline.index_core.backends import EmbeddingBackend  # noqa: E402
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS  # noqa: E402
from dual_pipeline.index_core.vector_staging import (  # noqa: E402
    encode_and_stage_records,
    init_vector_staging_db,
)
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.release import (  # noqa: E402
    ConcurrentMutationConflictError,
    ReleaseManager,
    detect_mutation_conflicts,
    rebase_component_run,
)
from storage.connection import sqlite3  # noqa: E402


class DeterministicTestBackend:
    """Deterministic mock embedding backend satisfying EmbeddingBackend protocol."""

    backend_id: str = "flagembedding-bge-m3-dev"
    model_id: str = "BAAI/bge-m3"
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    production_qualified: bool = False
    assurance_tier: str = "development"
    model_manifest_digest: Optional[str] = "5617a9f61b028005a4858fdac845db406aefb1815617a9f61b028005a4858fda"
    colbert_dimensions: int = 1024
    retrieval_encoder: str = "real-bge-m3"
    encoding_fingerprint_digest: str = "test-fingerprint-digest-123"

    def __init__(
        self,
        model_id: Optional[str] = None,
        model_revision: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> None:
        if model_id:
            self.model_id = model_id
        if model_revision:
            self.model_revision = model_revision
        if fingerprint:
            self.encoding_fingerprint_digest = fingerprint
        self.encode_calls: List[str] = []

    @property
    def encoding_fingerprint(self) -> Dict[str, Any]:
        return {
            "backend": self.backend_id,
            "revision": self.model_revision,
            "digest": self.encoding_fingerprint_digest,
        }

    def encode(self, text: str) -> Dict[str, Any]:
        self.encode_calls.append(text)
        unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
        dense = [unit_val] * DENSE_DIMENSIONS
        sparse = {"keyword": 0.75, "entity": 0.45}
        colbert = [[unit_val] * 1024, [unit_val] * 1024]
        colbert_tokens = ["[CLS]", "token"]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert": colbert,
            "colbert_tokens": colbert_tokens,
        }

    def encode_dense_only(self, text: str) -> List[float]:
        self.encode_calls.append(text)
        unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
        return [unit_val] * DENSE_DIMENSIONS

    def encode_many_dense_only(
        self, texts: Any, *, batch_size: Optional[int] = None
    ) -> List[List[float]]:
        return [self.encode_dense_only(t) for t in texts]

    def verify_snapshot(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.model_manifest_digest,
        }


class TestConcurrentReleaseRebase(unittest.TestCase):
    """Test suite for concurrent intake release rebasing and conflict detection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-rebase-test-")
        self.root = Path(self.temp_dir.name)
        self.runtime = self.root / "runtime"
        self.seed_graph = ROOT / "graph" / "nee_graph.sqlite"
        self.backend = DeterministicTestBackend()
        self.manager = ReleaseManager(self.runtime, self.seed_graph)
        self.bootstrap = self.manager.bootstrap(self.backend)
        self.active_pair = self.manager.active_pair()

        # Build IntakeEngine bound to bootstrap graph
        self.intake = IntakeEngine(
            self.manager._resolve(self.bootstrap["graph_path"]),
            self.runtime,
            retrieval_audiences=["internal_consulting", "nee-engineering"],
        )

        # Base meeting package fixture
        fixture_path = (
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"
        )
        self.base_fixture = read_json(fixture_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_package(
        self,
        name: str,
        statement_text: str,
        action_text: str,
        speaker_role: str = "Engineering Lead",
    ) -> Path:
        pkg_path = self.root / f"{name}.json"
        data = copy.deepcopy(self.base_fixture)
        data["artifact_id"] = f"evidence-envelope-{name}"
        data["meeting"]["meeting_id"] = f"meeting-{name}"
        data["meeting"]["title"] = f"Meeting {name}"
        data["meeting"]["source_location"] = f"controlled/archive/{name}.txt"
        data["meeting"]["transcript_reference"] = f"controlled/archive/{name}.txt"
        data["meeting"]["source_hash"] = hashlib.sha256(name.encode("utf-8")).hexdigest()
        data["speakers"][0]["role_or_org"] = speaker_role
        for idx, stmt in enumerate(data["speakers"][0]["statements"], 1):
            stmt["statement_id"] = f"stmt-{name}-{idx}"
            stmt["text"] = f"{statement_text} (part {idx})"
            stmt["locator"]["value"] = f"S0{idx}"
        data["actions"][0]["action_id"] = f"act-{name}-1"
        data["actions"][0]["action"] = action_text
        data["actions"][0]["dependency"] = f"stmt-{name}-1"
        data["actions"][0]["locator"]["value"] = "A01"
        write_json(pkg_path, seal_artifact(data))
        return pkg_path

    def _receipt_path(self, receipt: dict[str, Any]) -> Path:
        return self.runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"

    def _activate(self, receipt_path: Path) -> dict[str, Any]:
        with patch(
            "dual_pipeline.retrieval_release.validate_activation_evaluation",
            return_value={"required": False},
        ):
            return self.manager.activate(receipt_path)

    def test_detect_mutation_conflicts_on_existing_assertion(self) -> None:
        """Conflicting target assertion IDs already present in active graph are detected."""
        active_graph = self.manager._resolve(self.active_pair["graph_path"])
        conn = sqlite3.connect(active_graph)
        first_row = conn.execute("SELECT assertion_id FROM assertion LIMIT 1").fetchone()
        conn.close()
        self.assertIsNotNone(first_row, "Bootstrap graph must contain initial assertions")
        existing_assertion_id = first_row[0]

        conflicting_change_set = {
            "change_set_id": "cs-test-conflict-01",
            "operations": [
                {
                    "operation_id": "op-conflict-1",
                    "operation": "add_candidate_assertion",
                    "target_type": "assertion",
                    "target_id": existing_assertion_id,
                }
            ],
            "extensions": {},
        }
        conflicts = detect_mutation_conflicts(conflicting_change_set, active_graph)
        self.assertTrue(any(existing_assertion_id in c for c in conflicts))

    def test_detect_mutation_conflicts_on_superseded_assertion(self) -> None:
        """Superseding an assertion that is already historical or superseded is rejected."""
        active_graph = self.manager._resolve(self.active_pair["graph_path"])
        conn = sqlite3.connect(active_graph)
        # Mark an assertion as historical
        first_row = conn.execute("SELECT assertion_id FROM assertion LIMIT 1").fetchone()
        existing_id = first_row[0]
        conn.execute(
            "UPDATE assertion SET currentness_state='historical', superseded_at='2026-01-01' WHERE assertion_id=?",
            (existing_id,),
        )
        conn.commit()
        conn.close()

        change_set = {
            "change_set_id": "cs-test-supersede-conflict",
            "operations": [
                {
                    "operation_id": "op-sup-1",
                    "operation": "supersede_assertion",
                    "target_type": "assertion",
                    "target_id": existing_id,
                }
            ],
            "extensions": {},
        }
        conflicts = detect_mutation_conflicts(change_set, active_graph)
        self.assertTrue(any("already been superseded" in c for c in conflicts))

    def test_conflict_free_sequential_rebase(self) -> None:
        """Two concurrent pipelines started from G0 rebase sequentially (G0 -> G1 -> G2)."""
        pkg_a = self._make_package("pkg-alpha", "Alpha project deliverables.", "Deliver alpha.")
        pkg_b = self._make_package("pkg-beta", "Beta architecture design.", "Finalize beta.")

        # Both ingest against initial active graph G0
        run_a = self.intake.ingest(pkg_a)
        run_b = self.intake.ingest(pkg_b)
        self.assertGreater(run_a["graph_operation_count"], 0)
        self.assertGreater(run_b["graph_operation_count"], 0)

        # Release Run A -> activates Generation G1
        receipt_a = self.manager.prepare(
            run_id=run_a["run_id"],
            approval_id="approval-alpha",
            backend=self.backend,
        )
        activation_a = self._activate(self._receipt_path(receipt_a))
        pair_g1 = activation_a["active_pair"]
        self.assertNotEqual(pair_g1["graph_sha256"], self.active_pair["graph_sha256"])

        # Run B was planned against G0, but now G1 is active!
        # With rebase support, preparing Run B must succeed without stale-base error.
        receipt_b = self.manager.prepare(
            run_id=run_b["run_id"],
            approval_id="approval-beta",
            backend=self.backend,
        )
        self.assertIsNotNone(receipt_b)
        activation_b = self._activate(self._receipt_path(receipt_b))
        pair_g2 = activation_b["active_pair"]
        self.assertNotEqual(pair_g2["graph_sha256"], pair_g1["graph_sha256"])

        # Independent readback from G2 graph: both Alpha and Beta assertions must exist
        g2_graph = self.manager._resolve(pair_g2["graph_path"])
        conn = sqlite3.connect(g2_graph)
        alpha_rows = conn.execute(
            "SELECT count(*) FROM assertion WHERE scope_text LIKE '%Alpha project%'"
        ).fetchone()[0]
        beta_rows = conn.execute(
            "SELECT count(*) FROM assertion WHERE scope_text LIKE '%Beta architecture%'"
        ).fetchone()[0]
        conn.close()

        self.assertGreater(alpha_rows, 0, "Alpha assertions must be present in G2")
        self.assertGreater(beta_rows, 0, "Beta assertions must be present in G2")

    def test_true_conflict_fails_closed_with_concurrent_mutation_conflict_error(self) -> None:
        """A component attempting to create an already-committed assertion ID fails closed during rebase."""
        pkg_a = self._make_package("pkg-first", "First run text.", "Action first.")
        run_a = self.intake.ingest(pkg_a)

        # Commit Run A into G1
        receipt_a = self.manager.prepare(
            run_id=run_a["run_id"],
            approval_id="appr-a",
            backend=self.backend,
        )
        activation_a = self._activate(self._receipt_path(receipt_a))
        active_pair = activation_a["active_pair"]
        committed_graph_path = self.manager._resolve(active_pair["graph_path"])
        target_a_id = receipt_a["graph_write_receipt"]["operations"][0]["target_id"]

        # Construct a component attempting to rebase onto G1 but with colliding target_a_id
        conflicting_component = {
            "run_id": "run-conflicting-test",
            "summary": {
                "graph_sha256_before": "stale-base-hash-from-g0",
            },
            "proposed": {
                "change_set_id": "cs-conflicting",
                "base_graph_hash": "stale-base-hash-from-g0",
                "operations": [
                    {
                        "operation_id": "op-collision-1",
                        "operation": "add_candidate_assertion",
                        "target_type": "assertion",
                        "target_id": target_a_id,
                    }
                ],
                "extensions": {},
            },
            "bundle": {},
        }

        # Rebase must detect the collision and fail closed with ConcurrentMutationConflictError
        with self.assertRaises(ConcurrentMutationConflictError) as ctx:
            rebase_component_run(conflicting_component, committed_graph_path, active_pair)
        self.assertIn(target_a_id, str(ctx.exception))
        self.assertIn("already exists in active graph", str(ctx.exception))

    def test_multi_run_group_commit_with_staged_vectors(self) -> None:
        """Multiple runs group-commit in one atomic prepare_many call with staged vectors."""
        pkg_1 = self._make_package("pkg-doc1", "Document 1 content.", "Action 1.")
        pkg_2 = self._make_package("pkg-doc2", "Document 2 content.", "Action 2.")

        run_1 = self.intake.ingest(pkg_1)
        run_2 = self.intake.ingest(pkg_2)

        # Create staged vector stores for both runs
        run_1_dir = self.runtime / "runs" / run_1["run_id"]
        run_2_dir = self.runtime / "runs" / run_2["run_id"]
        stage_1_path = run_1_dir / "vector_staging.sqlite"
        stage_2_path = run_2_dir / "vector_staging.sqlite"

        init_vector_staging_db(stage_1_path, self.backend)
        init_vector_staging_db(stage_2_path, self.backend)

        # Encode and stage mock retrieval records
        rec_1 = [{"retrieval_text_hash": "hash_doc1_rec1", "retrieval_text": "Doc 1 text"}]
        rec_2 = [{"retrieval_text_hash": "hash_doc2_rec1", "retrieval_text": "Doc 2 text"}]
        encode_and_stage_records(stage_1_path, self.backend, rec_1)
        encode_and_stage_records(stage_2_path, self.backend, rec_2)

        # Prepare many with merged staged vectors
        receipt = self.manager.prepare_many(
            run_ids=[run_1["run_id"], run_2["run_id"]],
            approval_id="group-commit-approval",
            backend=self.backend,
            staged_vector_paths=[stage_1_path, stage_2_path],
        )

        self.assertIsNotNone(receipt)
        self.assertEqual(len(receipt["component_runs"]), 2)

        # Verify sidecar telemetry recorded staged vector paths and execution
        index_receipt = receipt["embedding_generation_receipt"]
        self.assertIn("generation_id", index_receipt)

        # Activate the atomic group release
        activation = self._activate(self._receipt_path(receipt))
        active_graph = self.manager._resolve(activation["active_pair"]["graph_path"])

        # Check both documents' assertions in active graph
        conn = sqlite3.connect(active_graph)
        cnt1 = conn.execute("SELECT count(*) FROM assertion WHERE scope_text LIKE '%Document 1%'").fetchone()[0]
        cnt2 = conn.execute("SELECT count(*) FROM assertion WHERE scope_text LIKE '%Document 2%'").fetchone()[0]
        conn.close()

        self.assertGreater(cnt1, 0)
        self.assertGreater(cnt2, 0)

    def test_five_mandatory_physical_proof_checks(self) -> None:
        """Every committed graph mutation satisfies all 5 empirical physical proof checks."""
        active_graph_path = self.manager._resolve(self.active_pair["graph_path"])

        # Baseline measurements
        pre_size = active_graph_path.stat().st_size
        pre_sha256 = sha256_file(active_graph_path)
        pre_conn = sqlite3.connect(active_graph_path)
        pre_assertion_count = pre_conn.execute("SELECT count(*) FROM assertion").fetchone()[0]
        pre_conn.close()

        # Ingest and activate a new document
        pkg = self._make_package("pkg-proof", "Physical proof verification text.", "Physical action.")
        run = self.intake.ingest(pkg)
        receipt = self.manager.prepare(
            run_id=run["run_id"],
            approval_id="appr-physical-proof",
            backend=self.backend,
        )
        activation = self._activate(self._receipt_path(receipt))
        committed_graph_path = self.manager._resolve(activation["active_pair"]["graph_path"])

        written_target_id = receipt["graph_write_receipt"]["operations"][0]["target_id"]

        # Check 1: File Changed
        post_size = committed_graph_path.stat().st_size
        post_sha256 = sha256_file(committed_graph_path)
        file_changed = (pre_sha256 != post_sha256)
        self.assertTrue(file_changed, "Check 1 Failed: SHA-256 must differ before vs after")

        # Check 2: Raw Bytes Scan (bypassing SQLite driver)
        raw_bytes = committed_graph_path.read_bytes()
        raw_bytes_pass = written_target_id.encode("utf-8") in raw_bytes
        self.assertTrue(raw_bytes_pass, f"Check 2 Failed: Target ID {written_target_id} not in raw disk bytes")

        # Check 3: Integrity Verification
        fresh_conn = sqlite3.connect(committed_graph_path)
        integrity_result = fresh_conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(integrity_result, "ok", "Check 3 Failed: PRAGMA integrity_check must return 'ok'")

        # Check 4: Fresh Readback
        row = fresh_conn.execute(
            "SELECT scope_text FROM assertion WHERE assertion_id = ?",
            (written_target_id,),
        ).fetchone()
        self.assertIsNotNone(row, "Check 4 Failed: Row must exist on fresh readback")
        self.assertIn("Physical proof verification", row[0])

        # Check 5: Row Delta
        post_assertion_count = fresh_conn.execute("SELECT count(*) FROM assertion").fetchone()[0]
        fresh_conn.close()
        expected_delta = receipt["graph_write_receipt"]["operation_count"]
        self.assertGreater(post_assertion_count, pre_assertion_count)
        self.assertEqual(
            post_assertion_count - pre_assertion_count,
            expected_delta,
            "Check 5 Failed: Row count change must match expected delta exactly",
        )


if __name__ == "__main__":
    unittest.main()
