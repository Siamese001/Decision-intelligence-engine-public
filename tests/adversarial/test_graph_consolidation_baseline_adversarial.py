"""
tests/adversarial/test_graph_consolidation_baseline_adversarial.py

Adversarial and boundary test suite for Graph Consolidation Wave 1 baseline collection.
Exercises tamper detection, read-only boundary enforcement, missing manifests,
hash corruption, and fail-closed security invariants.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.collect_graph_consolidation_baseline import (
    canonical_json_digest,
    collect_baseline,
)


class TestGraphConsolidationBaselineAdversarial(unittest.TestCase):
    """Adversarial tests for baseline collector robustness and security boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_mock_graph(self) -> Path:
        db_path = self.root / "mock_graph.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, active INT, status TEXT)")
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, confidence_score REAL, primary_source_id TEXT, extraction_run_id TEXT, currentness_state TEXT, canonical_status TEXT, superseded_by_assertion_id TEXT, created_at TEXT)")
        conn.execute("CREATE TABLE edge (edge_id TEXT PRIMARY KEY, superseded_by TEXT)")
        conn.execute("CREATE TABLE legacy_edge_assertion_map (edge_id TEXT PRIMARY KEY, assertion_id TEXT)")
        conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_version_id TEXT, locator_value TEXT)")
        conn.execute("CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT)")
        conn.commit()
        conn.close()
        return db_path

    def test_read_only_boundary_prevents_graph_mutation(self) -> None:
        """Read-only SQLite URI fails closed with OperationalError on write attempts."""
        db_path = self._create_mock_graph()
        ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                ro_conn.execute("INSERT INTO node (node_id) VALUES ('malicious_node')")
            self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            ro_conn.close()

    def test_missing_runtime_root_fails_closed(self) -> None:
        """Non-existent runtime root directory fails closed with FileNotFoundError."""
        bad_root = self.root / "nonexistent_dir"
        with self.assertRaises(FileNotFoundError):
            collect_baseline(bad_root)

    def test_missing_graph_path_in_manifest_fails_closed(self) -> None:
        """active_pair.json lacking graph_path key fails closed with ValueError."""
        pair_file = self.root / "active_pair.json"
        pair_file.write_text(json.dumps({"generation_id": "gen-test"}), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            collect_baseline(self.root)
        self.assertIn("graph_path missing", str(ctx.exception))

    def test_hash_mismatch_fails_closed(self) -> None:
        """Disk SHA256 differing from declared graph_sha256 fails closed with RuntimeError."""
        db_path = self._create_mock_graph()
        pair_file = self.root / "active_pair.json"
        pair_file.write_text(
            json.dumps({
                "generation_id": "gen-test",
                "graph_path": str(db_path),
                "graph_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            }),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError) as ctx:
            collect_baseline(self.root)
        self.assertIn("mismatch", str(ctx.exception).lower())

    def test_receipt_digest_tamper_detection(self) -> None:
        """Any payload modification alters canonical digest, proving tampering."""
        base_data = {
            "schema_version": "1.0.0",
            "tier2_audit_events_count": 0,
            "exact_triple_dup_groups_count": 40,
        }
        orig_digest = canonical_json_digest(base_data)

        # Tampering with count changes digest
        tampered_data = dict(base_data)
        tampered_data["tier2_audit_events_count"] = 5
        tampered_digest = canonical_json_digest(tampered_data)
        self.assertNotEqual(orig_digest, tampered_digest)

        # Tampering with key order does NOT change canonical digest (RFC 8785 invariant)
        reordered_data = {
            "exact_triple_dup_groups_count": 40,
            "tier2_audit_events_count": 0,
            "schema_version": "1.0.0",
        }
        self.assertEqual(orig_digest, canonical_json_digest(reordered_data))


if __name__ == "__main__":
    unittest.main()
