"""End-to-end tests for sealed generation immutability and active pair integrity."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from dual_pipeline.canonical_pipeline.publish_helper import (
    _handle_publication_activation,
    _re_seal_active_pair_if_needed,
)
from dual_pipeline.core import connect_sqlite
from dual_pipeline.runtime_root import _compute_sha256, resolve_active_pair
from dual_pipeline.storage.governed_context import (
    ProductionPurityViolationError,
    is_sealed_generation_database,
)


def _init_dummy_graph(graph_file: Path) -> None:
    """Creates a valid dummy graph database with minimal tables."""
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(graph_file)) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                properties_json TEXT,
                active INTEGER DEFAULT 1
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                currentness_state TEXT,
                status TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, properties_json, active) "
            "VALUES ('node-1', 'Alice Smith', 'PERSON', '{}', 1);"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, status) "
            "VALUES ('asrt-1', 'node-1', 'HAS_ROLE', 'Engineering Lead', 'current', 'active');"
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")


class SealedGenerationImmutabilityE2ETest(unittest.TestCase):
    """Verifies that sealed generation databases remain immutable and active pair hashes stay synchronized."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="sealed_gen_e2e_"))
        self.rt_root = self.tmp_dir / ".local" / "dual_pipeline"
        self.gen_dir = self.rt_root / "generations" / "generation-e2e-001"
        self.gen_dir.mkdir(parents=True, exist_ok=True)
        self.graph_file = self.gen_dir / "graph.sqlite"
        _init_dummy_graph(self.graph_file)
        self.sha_initial = _compute_sha256(self.graph_file)

        self.manifest_data = {
            "$schema": "graph-manifest:1.0.0",
            "generation_id": "generation-e2e-001",
            "graph_path": f"generations/generation-e2e-001/{self.graph_file.name}",
            "graph_sha256": self.sha_initial,
            "manifest_created_at": "2026-10-16T12:00:00Z",
        }
        (self.gen_dir / "pair_manifest.json").write_text(
            json.dumps(self.manifest_data, indent=2), encoding="utf-8"
        )
        (self.rt_root / "active_pair.json").write_text(
            json.dumps(self.manifest_data, indent=2), encoding="utf-8"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_e2e_sealed_generation_is_classified_as_sealed(self) -> None:
        """The sealed generation database is recognized as immutable and sealed."""
        self.assertTrue(is_sealed_generation_database(self.graph_file))

    def test_e2e_sealed_generation_rejects_write_connection(self) -> None:
        """Attempting to open a sealed generation database for write fails closed."""
        with self.assertRaises(ProductionPurityViolationError):
            connect_sqlite(self.graph_file, readonly=False)

    def test_e2e_sealed_generation_allows_readonly_query(self) -> None:
        """Read-only connections to the sealed generation database succeed cleanly."""
        with connect_sqlite(self.graph_file, readonly=True) as conn:
            row = conn.execute("SELECT canonical_name FROM node WHERE node_id = 'node-1'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["canonical_name"], "Alice Smith")

    def test_e2e_post_activation_role_resolutions_do_not_mutate_sealed_generation(self) -> None:
        """Post-activation HITL role resolutions skip mutating sealed generations, preserving SHA-256."""
        resolutions = {
            "Alice Smith": {"resolved_role": "Engineering Lead"}
        }
        meta = {
            "run_id": "run-e2e-001",
            "hitl_role_resolutions": resolutions,
            "graph_path": str(self.graph_file),
            "runtime_root": str(self.rt_root),
        }
        auth_info = {
            "reviewer": "human_operator",
            "request_id": "req-001",
        }
        pub_res = {
            "generation_id": "generation-e2e-001",
            "status": "SUCCESS",
        }

        # Handle activation
        _handle_publication_activation(
            pub_res=pub_res,
            written={},
            auth_info=auth_info,
            meta=meta,
            out_p=self.rt_root,
        )

        # On-disk hash must remain identical to initial hash
        sha_after = _compute_sha256(self.graph_file)
        self.assertEqual(sha_after, self.sha_initial)

        # Active pair integrity check must pass with 0 errors
        pair_data, pair_path = resolve_active_pair(self.rt_root, enforce_integrity=True)
        self.assertEqual(pair_data["graph_sha256"], self.sha_initial)
        self.assertEqual(pair_data["graph_sha256"], sha_after)
        self.assertEqual(pair_path.resolve(), (self.rt_root / "active_pair.json").resolve())

    def test_e2e_re_seal_active_pair_syncs_drifted_hash(self) -> None:
        """Candidate graph modifications re-seal active_pair.json and pair_manifest.json to match on disk."""
        candidate_file = self.tmp_dir / "candidate_graph.sqlite"
        _init_dummy_graph(candidate_file)
        cand_sha_initial = _compute_sha256(candidate_file)

        cand_manifest = {
            "$schema": "graph-manifest:1.0.0",
            "generation_id": "candidate-gen",
            "graph_path": str(candidate_file),
            "graph_sha256": cand_sha_initial,
        }
        (self.rt_root / "active_pair.json").write_text(
            json.dumps(cand_manifest, indent=2), encoding="utf-8"
        )

        # Mutate candidate file outside generations
        with sqlite3.connect(str(candidate_file)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, node_type, properties_json, active) "
                "VALUES ('node-2', 'Bob Builder', 'PERSON', '{}', 1);"
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        cand_sha_updated = _compute_sha256(candidate_file)
        self.assertNotEqual(cand_sha_updated, cand_sha_initial)

        # Re-seal
        _re_seal_active_pair_if_needed(self.rt_root, candidate_file)

        # Verify active pair is synchronized
        active_data = json.loads((self.rt_root / "active_pair.json").read_text(encoding="utf-8"))
        self.assertEqual(active_data["graph_sha256"], cand_sha_updated)

        # resolve_active_pair passes
        data, _ = resolve_active_pair(self.rt_root, enforce_integrity=True)
        self.assertEqual(data["graph_sha256"], cand_sha_updated)


if __name__ == "__main__":
    unittest.main()
