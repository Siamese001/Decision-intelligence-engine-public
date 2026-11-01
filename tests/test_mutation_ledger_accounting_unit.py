"""Unit tests for Phase 6: Shared-state and mutation accounting in GraphMutationLedger."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_mutation_ledger import (
    DEFAULT_LEDGER_PATH,
    GraphMutationLedger,
    ProductionPurityViolationError,
)
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    is_production_database,
    is_test_environment,
)


class TestMutationLedgerAccountingUnit(unittest.TestCase):
    """Verifies truthful separation between package preparation, committed mutations, and release activations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.ledger_file = self.root_path / "runtime" / "graph_mutation_ledger.json"
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self.ledger = GraphMutationLedger(self.ledger_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_1_1_0_defaults_and_backward_compatibility(self) -> None:
        """Loading a legacy 1.0.0 ledger preserves historical mutations and populates 1.1.0 fields."""
        legacy_data = {
            "schema_version": "1.0.0",
            "cumulative_mutation_count": 42,
            "last_mutation_timestamp": "2026-10-01T12:00:00Z",
            "last_full_scan_timestamp": None,
            "last_full_scan_mutation_count": 0,
            "mutation_history": [
                {
                    "run_id": "legacy-run-1",
                    "timestamp": "2026-10-01T12:00:00Z",
                    "node_count": 5,
                    "assertion_count": 10,
                }
            ],
        }
        self.ledger_file.write_text(json.dumps(legacy_data, indent=2), encoding="utf-8")

        loaded = self.ledger._load()
        self.assertEqual(loaded["schema_version"], "1.1.0")
        self.assertEqual(loaded["cumulative_mutation_count"], 42)
        self.assertEqual(loaded["cumulative_preparation_count"], 0)
        self.assertEqual(len(loaded["mutation_history"]), 1)
        self.assertEqual(loaded["preparation_history"], [])
        self.assertEqual(loaded["activation_history"], [])
        self.assertTrue(loaded["physical_runtime_path"].startswith("/"))

    def test_package_preparation_does_not_increment_committed_mutations(self) -> None:
        """Package-only scout runs record preparation success without incrementing committed mutation counter."""
        self.assertEqual(self.ledger.get_total_mutations(), 0)
        self.assertEqual(self.ledger.get_total_preparations(), 0)

        mock_graph = self.root_path / "graph.sqlite"
        prep_count = self.ledger.record_package_preparation(
            run_id="scout-prep-001",
            proposed_node_count=15,
            proposed_assertion_count=30,
            target_graph_path=mock_graph,
            runtime_root=self.root_path,
        )

        self.assertEqual(prep_count, 1)
        self.assertEqual(self.ledger.get_total_preparations(), 1)
        self.assertEqual(self.ledger.get_total_mutations(), 0)

        last_prep = self.ledger.get_last_preparation()
        self.assertIsNotNone(last_prep)
        self.assertEqual(last_prep["run_id"], "scout-prep-001")
        self.assertEqual(last_prep["event_type"], "PREPARED_PACKAGE")
        self.assertEqual(last_prep["proposed_node_count"], 15)
        self.assertEqual(last_prep["proposed_assertion_count"], 30)
        self.assertEqual(last_prep["target_graph_path"], str(mock_graph.resolve()))

    def test_committed_mutation_and_activation_accounting(self) -> None:
        """Authoritative mutations increment cumulative_mutation_count with resolved paths."""
        mock_graph = self.root_path / "canon_graph.sqlite"
        mut_count = self.ledger.record_mutation(
            run_id="publish-001",
            node_count=8,
            assertion_count=16,
            target_graph_path=mock_graph,
            runtime_root=self.root_path,
            generation_id="gen-001",
        )

        self.assertEqual(mut_count, 1)
        self.assertEqual(self.ledger.get_total_mutations(), 1)
        self.assertEqual(self.ledger.get_total_preparations(), 0)

        last_mut = self.ledger.get_last_mutation()
        self.assertIsNotNone(last_mut)
        self.assertEqual(last_mut["event_type"], "COMMITTED_MUTATION")
        self.assertEqual(last_mut["node_count"], 8)
        self.assertEqual(last_mut["generation_id"], "gen-001")

        act_count = self.ledger.record_release_activation(
            run_id="publish-001",
            generation_id="gen-001",
            graph_sha256="abc123def456",
            target_graph_path=mock_graph,
            runtime_root=self.root_path,
        )
        self.assertEqual(act_count, 1)
        self.assertEqual(self.ledger.get_total_activations(), 1)
        last_act = self.ledger.get_last_activation()
        self.assertIsNotNone(last_act)
        self.assertEqual(last_act["event_type"], "ACTIVATED_RELEASE")
        self.assertEqual(last_act["graph_sha256"], "abc123def456")

    def test_detect_shared_runtime_resolves_symlinks_and_prefixes(self) -> None:
        """Physical runtime location detection resolves symlinks and different paths."""
        real_dir = self.root_path / "canonical_runtime"
        real_dir.mkdir()
        ledger_file = real_dir / "graph_mutation_ledger.json"
        ledger = GraphMutationLedger(ledger_file)

        symlink_dir = self.root_path / "worktree_alias"
        symlink_dir.symlink_to(real_dir)

        # Same physical directory through symlink
        self.assertTrue(ledger.detect_shared_runtime(symlink_dir))
        self.assertTrue(ledger.detect_shared_runtime(symlink_dir / "graph_mutation_ledger.json"))

        # Different directory
        different_dir = self.root_path / "other_runtime"
        different_dir.mkdir()
        self.assertFalse(ledger.detect_shared_runtime(different_dir))

    def test_mechanical_barrier_blocks_test_runner_from_mutating_production_ledger(self) -> None:
        """Test runners are barred from writing to DEFAULT_LEDGER_PATH without explicit bypass."""
        prod_ledger = GraphMutationLedger(DEFAULT_LEDGER_PATH)
        self.assertTrue(is_test_environment())
        self.assertTrue(prod_ledger._is_production_ledger())

        with self.assertRaises(ProductionPurityViolationError):
            prod_ledger.record_mutation(run_id="illegal-test-run", node_count=1)

    def test_mechanical_barrier_blocks_test_runner_from_mutating_production_database(self) -> None:
        """GovernedStorageContext prevents test runners from opening write connections to production DBs."""
        from dual_pipeline.core import ROOT
        canonical_prod = ROOT / "storage" / "CANONICAL_GRAPH.db"

        self.assertTrue(is_test_environment())
        self.assertTrue(is_production_database(canonical_prod))

        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(canonical_prod, readonly=False)

        with self.assertRaises(ProductionPurityViolationError):
            with GovernedStorageContext.write_context(canonical_prod):
                pass

    def test_isolated_sandbox_database_writes_are_permitted(self) -> None:
        """Isolated test databases in temporary folders connect and write without error."""
        sandbox_db = self.root_path / "eval_sandbox_123" / "isolated.db"
        sandbox_db.parent.mkdir(parents=True, exist_ok=True)
        self.assertFalse(is_production_database(sandbox_db))

        with GovernedStorageContext.write_context(sandbox_db) as conn:
            conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT);")
            conn.execute("INSERT INTO test_table (val) VALUES ('allowed');")

        with GovernedStorageContext.read_context(sandbox_db) as conn:
            row = conn.execute("SELECT val FROM test_table WHERE id=1;").fetchone()
            self.assertEqual(row[0], "allowed")


if __name__ == "__main__":
    unittest.main()
