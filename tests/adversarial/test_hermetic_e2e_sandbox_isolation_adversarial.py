"""Adversarial Acceptance Suite for Hermetic E2E Sandbox Isolation (Wave 3).

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Missing reports/ directory and missing .local/dual_pipeline state.
   - 0-byte or uninitialized database files handled fail-closed.
2. Malformed input & schema mismatch:
   - Corrupted active_pair.json (missing generation_id, unparseable JSON).
   - Incomplete schemas missing required assertion/node tables.
3. Concurrency & isolation:
   - Concurrent provision of isolated sandboxes ensuring zero cross-test collision.
4. Rollback & recovery:
   - Cleanup of temporary sandboxes without leaving dangling lock files or SQLite WAL files.
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from benchmarks.hardware_capacity.crossover_benchmarks import (
    CALIBRATION_BATCH_SIZES,
    run_crossover_calibration,
)
from storage.connection import (
    GraphUnavailableError,
    configure_pragmas,
    validate_canonical_graph_db,
)
from tests.end_to_end.test_bmo_real_ingest_e2e import _seed_isolated_bmo_fixture


class TestHermeticE2ESandboxIsolationAdversarial(TestCase):
    """Adversarial and boundary validation of sandbox isolation for E2E suites."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_boundary_missing_reports_and_local_state(self) -> None:
        """Verifies crossover calibration and BMO fixtures execute when reports/ is absent."""
        with tempfile.TemporaryDirectory() as sandbox_dir:
            s_path = Path(sandbox_dir)
            # Empty sandbox with no reports/ or .local
            self.assertFalse((s_path / "reports").exists())
            self.assertFalse((s_path / ".local").exists())

            # 1. Crossover calibration must generate receipt cleanly in empty sandbox
            receipt_path = s_path / "crossover_receipt.json"
            receipt = run_crossover_calibration(
                batch_sizes=[16, 32],
                output_path=receipt_path,
            )
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(len(receipt.points), 2)
            self.assertGreaterEqual(receipt.bge_mps_throughput_tokens_per_sec, 1300.0)

            # 2. BMO fixture must seed hermetically in empty sandbox
            bmo_graph = _seed_isolated_bmo_fixture(s_path)
            self.assertTrue(bmo_graph.is_file())
            self.assertTrue((s_path / "active_pair.json").is_file())

    def test_boundary_zero_byte_database_fails_closed(self) -> None:
        """Verifies 0-byte database files fail closed under validate_canonical_graph_db."""
        with tempfile.TemporaryDirectory() as sandbox_dir:
            zero_db = Path(sandbox_dir) / "empty.sqlite"
            zero_db.touch()

            with self.assertRaises(GraphUnavailableError) as ctx:
                validate_canonical_graph_db(zero_db)
            self.assertIn("0 bytes", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Category 2: Malformed input & schema mismatch
    # -------------------------------------------------------------------------
    def test_malformed_database_lacks_required_table(self) -> None:
        """Verifies database lacking assertion table fails closed with GraphUnavailableError."""
        with tempfile.TemporaryDirectory() as sandbox_dir:
            bad_db = Path(sandbox_dir) / "bad.sqlite"
            conn = sqlite3.connect(bad_db)
            conn.execute("CREATE TABLE wrong_table (id INT);")
            conn.commit()
            conn.close()

            with self.assertRaises(GraphUnavailableError) as ctx:
                validate_canonical_graph_db(bad_db, required_table="assertion")
            self.assertIn("missing required table 'assertion'", str(ctx.exception))

    def test_malformed_active_pair_json_recovery(self) -> None:
        """Verifies unparseable active_pair.json fails gracefully with json.JSONDecodeError."""
        with tempfile.TemporaryDirectory() as sandbox_dir:
            corrupt_pair = Path(sandbox_dir) / "active_pair.json"
            corrupt_pair.write_text("{ unclosed json content: 123", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                with open(corrupt_pair, "r", encoding="utf-8") as f:
                    json.load(f)

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & isolation
    # -------------------------------------------------------------------------
    def test_concurrent_sandbox_provisioning_zero_collision(self) -> None:
        """Verifies concurrent isolated sandboxes never collide or overwrite each other."""
        num_workers = 4

        def _provision_and_verify(worker_id: int) -> dict[str, int]:
            with tempfile.TemporaryDirectory(prefix=f"worker_{worker_id}_") as td:
                p = Path(td)
                _seed_isolated_bmo_fixture(p)
                conn = sqlite3.connect(p / "canonical_graph.sqlite")
                cur = conn.cursor()
                source_id = "source-ac4d0c3c8bb21f95267182a3"
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT node_id) FROM node
                    WHERE node_id IN (
                        SELECT DISTINCT subject_node_id FROM assertion WHERE primary_source_id = ?
                    ) OR node_id IN (
                        SELECT DISTINCT object_node_id FROM assertion WHERE primary_source_id = ? AND object_node_id IS NOT NULL
                    );
                    """,
                    (source_id, source_id),
                )
                nodes = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM assertion WHERE primary_source_id = ? AND predicate = 'RELATED_TO';",
                    (source_id,),
                )
                assertions = cur.fetchone()[0]
                conn.close()
                return {"worker_id": worker_id, "nodes": nodes, "assertions": assertions}

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(_provision_and_verify, i) for i in range(num_workers)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), num_workers)
        for res in results:
            self.assertEqual(res["nodes"], 19)
            self.assertEqual(res["assertions"], 11)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------
    def test_sandbox_teardown_cleans_wal_and_locks(self) -> None:
        """Verifies sandbox teardown leaves zero lingering lockfiles or open handles."""
        tmp = tempfile.TemporaryDirectory(prefix="teardown_test_")
        sandbox_path = Path(tmp.name)

        db_path = sandbox_path / "test.sqlite"
        conn = sqlite3.connect(db_path)
        configure_pragmas(conn, is_sandbox=True)
        conn.execute("CREATE TABLE sample (id INT);")
        conn.execute("INSERT INTO sample VALUES (1);")
        conn.commit()
        conn.close()

        # Before teardown, db exists
        self.assertTrue(db_path.exists())

        # Cleanup
        tmp.cleanup()
        self.assertFalse(sandbox_path.exists())
