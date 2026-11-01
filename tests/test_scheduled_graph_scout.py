"""Unit tests for Tier 2 Scheduled Graph Scout CLI and handler logic."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.handlers import _graph_scout
from dual_pipeline.graph_mutation_ledger import GraphMutationLedger


def _init_mock_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    cursor.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT,
            confidence_score REAL,
            valid_from TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus', 'project', 'Project Nexus', 'Nexus',
            'Platform', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    conn.commit()
    conn.close()


class TestScheduledGraphScout(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rt_dir = Path(self.temp_dir.name) / "runtime"
        self.rt_dir.mkdir(parents=True)
        self.out_dir = Path(self.temp_dir.name) / "output"
        self.out_dir.mkdir(parents=True)

        self.graph_file = self.rt_dir / "test_graph.sqlite"
        _init_mock_graph(self.graph_file)

        # active_pair.json
        with open(self.rt_dir / "active_pair.json", "w") as f:
            json.dump({"graph_path": "test_graph.sqlite"}, f)

        self.ledger = GraphMutationLedger(self.rt_dir / "graph_mutation_ledger.json")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scheduled_scout_exits_when_not_due(self) -> None:
        # Reset scan so 0 mutations are pending
        self.ledger.record_full_scan("scan-zero")

        args = argparse.Namespace(
            command="graph-scout",
            runtime_root=self.rt_dir,
            graph=self.graph_file,
            output_dir=self.out_dir,
            similarity_threshold=0.72,
            max_candidate_age_days=30,
            json=True,
            scheduled=True,
            cadence_mutations=10,
            cadence_days=7,
            max_proposal_backlog=20,
        )

        res = _graph_scout(args)
        self.assertEqual(res.get("status"), "SCHEDULED_NOT_DUE")

    def test_scheduled_scout_exits_when_queue_gated(self) -> None:
        # Create mock proposal dir with 5 proposals (and set max_proposal_backlog=5)
        prop_dir = self.rt_dir / "proposals"
        prop_dir.mkdir(parents=True)
        for i in range(5):
            with open(prop_dir / f"prop_{i}.json", "w") as f:
                json.dump({"status": "PROPOSED", "approval_required": True}, f)

        from unittest.mock import patch


        with patch("dual_pipeline.proposal_queue.DEFAULT_PROPOSAL_DIRS", [prop_dir]):
            args = argparse.Namespace(
                command="graph-scout",
                runtime_root=self.rt_dir,
                graph=self.graph_file,
                output_dir=self.out_dir,
                similarity_threshold=0.72,
                max_candidate_age_days=30,
                json=True,
                scheduled=True,
                cadence_mutations=10,
                cadence_days=7,
                max_proposal_backlog=5,
            )
            res = _graph_scout(args)
            self.assertEqual(res.get("status"), "QUEUE_GATED")

    def test_scheduled_scout_runs_when_due(self) -> None:
        # Record 10 mutations
        for i in range(10):
            self.ledger.record_mutation(f"mutation-{i}")

        args = argparse.Namespace(
            command="graph-scout",
            runtime_root=self.rt_dir,
            graph=self.graph_file,
            output_dir=self.out_dir,
            similarity_threshold=0.72,
            max_candidate_age_days=30,
            json=True,
            scheduled=True,
            cadence_mutations=10,
            cadence_days=7,
            max_proposal_backlog=20,
        )

        res = _graph_scout(args)
        self.assertEqual(res.get("status"), "SCHEDULED_COMPLETE")
        self.assertTrue(res.get("summary", {}).get("zero_mutation_verified"))

        # Verify ledger updated last_full_scan_timestamp and mutation count
        data = self.ledger._load()
        self.assertEqual(data.get("last_full_scan_mutation_count"), 10)
        self.assertIsNotNone(data.get("last_full_scan_timestamp"))


if __name__ == "__main__":
    unittest.main()
