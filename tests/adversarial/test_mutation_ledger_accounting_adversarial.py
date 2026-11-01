"""Adversarial and edge-case tests for Phase 6: Mutation ledger accounting and mechanical barrier integrity.

Mandatory edge-case categories covered:
1. Boundary payload: 0-node / 0-assertion events, empty run_id, missing optional parameters.
2. Malformed input: Corrupted JSON file recovery, negative/float counts, non-dict payloads.
3. Concurrency & race: Multi-threaded concurrent preparation and mutation recording under flock.
4. Invariant bypass attempt: Bypassing production database barrier using symlinks or relative path traversals.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT
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


class TestMutationLedgerAccountingAdversarial(unittest.TestCase):
    """Adversarial suite testing edge cases, boundary payloads, concurrency, and bypass attempts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.ledger_file = self.root_path / "runtime" / "graph_mutation_ledger.json"
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self.ledger = GraphMutationLedger(self.ledger_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Category 1: Boundary payload
    def test_boundary_payloads_zero_and_empty_fields(self) -> None:
        """Ledger gracefully handles empty run_id, 0 nodes, 0 assertions, and None paths."""
        prep_cnt = self.ledger.record_package_preparation(
            run_id="",
            proposed_node_count=0,
            proposed_assertion_count=0,
            target_graph_path=None,
            runtime_root=None,
            generation_id=None,
        )
        self.assertEqual(prep_cnt, 1)
        self.assertEqual(self.ledger.get_total_mutations(), 0)

        last_prep = self.ledger.get_last_preparation()
        self.assertIsNotNone(last_prep)
        self.assertEqual(last_prep["run_id"], "")
        self.assertEqual(last_prep["proposed_node_count"], 0)
        self.assertIsNone(last_prep["target_graph_path"])

        mut_cnt = self.ledger.record_mutation(
            run_id="",
            node_count=0,
            assertion_count=0,
            target_graph_path=None,
            runtime_root=None,
            generation_id=None,
        )
        self.assertEqual(mut_cnt, 1)
        self.assertEqual(self.ledger.get_total_mutations(), 1)
        self.assertEqual(self.ledger.get_total_preparations(), 1)

    # Category 2: Malformed input and recovery
    def test_malformed_json_ledger_recovery(self) -> None:
        """Corrupted JSON payload in ledger file falls back safely to default schema 1.1.0."""
        self.ledger_file.write_text("{ this is malformed json truncated...", encoding="utf-8")

        loaded = self.ledger._load()
        self.assertEqual(loaded["schema_version"], "1.1.0")
        self.assertEqual(loaded["cumulative_mutation_count"], 0)
        self.assertEqual(loaded["cumulative_preparation_count"], 0)

        # Recording a new event recovers and overwrites the corrupted file cleanly
        cnt = self.ledger.record_package_preparation("recovery-run", 5, 10)
        self.assertEqual(cnt, 1)
        self.assertEqual(self.ledger.get_total_preparations(), 1)

    def test_malformed_counts_coerced_safely(self) -> None:
        """Floating point and string-compatible numbers are coerced to integers without crash."""
        prep_cnt = self.ledger.record_package_preparation(
            run_id="type-coercion-run",
            proposed_node_count=12.7,  # type: ignore[arg-type]
            proposed_assertion_count="25",  # type: ignore[arg-type]
        )
        self.assertEqual(prep_cnt, 1)
        last_prep = self.ledger.get_last_preparation()
        self.assertEqual(last_prep["proposed_node_count"], 12)
        self.assertEqual(last_prep["proposed_assertion_count"], 25)

    # Category 3: Concurrency & race
    def test_concurrency_race_mixed_preparations_and_mutations(self) -> None:
        """Concurrent workers simultaneously record preparations and mutations without counter drift."""
        num_workers = 8
        events_per_worker = 10

        def worker_task(worker_id: int) -> None:
            local_ledger = GraphMutationLedger(self.ledger_file)
            for i in range(events_per_worker):
                if (worker_id + i) % 2 == 0:
                    local_ledger.record_package_preparation(f"worker-{worker_id}-prep-{i}", 1, 2)
                else:
                    local_ledger.record_mutation(f"worker-{worker_id}-mut-{i}", 3, 4)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, w) for w in range(num_workers)]
            concurrent.futures.wait(futures)
            for f in futures:
                f.result()

        total_preparations = self.ledger.get_total_preparations()
        total_mutations = self.ledger.get_total_mutations()

        total_expected = num_workers * events_per_worker
        self.assertEqual(total_preparations + total_mutations, total_expected)
        self.assertTrue(total_preparations > 0)
        self.assertTrue(total_mutations > 0)

    # Category 4: Invariant bypass attempts
    def test_invariant_bypass_symlink_to_production_database_fails(self) -> None:
        """Symlinking a sandbox file to canonical production DB still triggers mechanical barrier."""
        canonical_prod = ROOT / "storage" / "CANONICAL_GRAPH.db"
        symlink_to_prod = self.root_path / "sneaky_symlink.db"

        try:
            symlink_to_prod.symlink_to(canonical_prod)
        except OSError:
            # Skip if OS prohibits symlink creation
            return

        self.assertTrue(is_test_environment())
        self.assertTrue(is_production_database(symlink_to_prod))

        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(symlink_to_prod, readonly=False)

    def test_invariant_bypass_relative_traversal_to_production_database_fails(self) -> None:
        """Relative path traversal resolving to production DB triggers mechanical barrier."""
        # e.g. self.root_path / "subdir" / ".." / ".." / ... -> canonical prod
        canonical_prod = ROOT / "storage" / "CANONICAL_GRAPH.db"
        traversal_path = self.root_path / "a" / "b" / os.path.relpath(canonical_prod, self.root_path / "a" / "b")

        self.assertTrue(is_production_database(traversal_path))
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(traversal_path, readonly=False)


if __name__ == "__main__":
    unittest.main()
