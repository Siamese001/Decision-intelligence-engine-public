"""Unit tests for GraphMutationLedger and ProposalQueue."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
from dual_pipeline.proposal_queue import ProposalQueue


class TestGraphMutationLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_file = Path(self.temp_dir.name) / "ledger.json"
        self.ledger = GraphMutationLedger(self.ledger_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_record_mutation_increments_count(self) -> None:
        self.assertEqual(self.ledger.get_total_mutations(), 0)
        c1 = self.ledger.record_mutation("run-001", node_count=5, assertion_count=12)
        self.assertEqual(c1, 1)
        self.assertEqual(self.ledger.get_total_mutations(), 1)

        c2 = self.ledger.record_mutation("run-002", node_count=3, assertion_count=8)
        self.assertEqual(c2, 2)
        self.assertEqual(self.ledger.get_total_mutations(), 2)

    def test_should_trigger_cadence_every_10_mutations(self) -> None:
        # Initial scan
        self.ledger.record_full_scan("initial-scan")
        self.assertFalse(self.ledger.should_trigger_scheduled_scout(cadence_mutations=10))

        # Add 9 mutations: should NOT trigger
        for i in range(9):
            self.ledger.record_mutation(f"run-{i}")
        self.assertFalse(self.ledger.should_trigger_scheduled_scout(cadence_mutations=10))

        # 10th mutation: should trigger!
        self.ledger.record_mutation("run-9")
        self.assertTrue(self.ledger.should_trigger_scheduled_scout(cadence_mutations=10))

        # Record scan: reset
        self.ledger.record_full_scan("scan-after-10")
        self.assertFalse(self.ledger.should_trigger_scheduled_scout(cadence_mutations=10))

    def test_should_trigger_by_elapsed_days(self) -> None:
        self.ledger.record_full_scan("old-scan")
        data = self.ledger._load()
        # Set last_full_scan_timestamp to 8 days ago
        eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
        data["last_full_scan_timestamp"] = eight_days_ago.isoformat()
        self.ledger._save(data)

        self.assertTrue(self.ledger.should_trigger_scheduled_scout(cadence_mutations=10, cadence_days=7))


class TestProposalQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.proposals_dir = Path(self.temp_dir.name) / "proposals"
        self.proposals_dir.mkdir(parents=True)
        self.queue = ProposalQueue(proposal_dirs=[self.proposals_dir])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_proposal_queue_counting_and_gating(self) -> None:
        self.assertEqual(self.queue.get_open_proposals_count(), 0)
        self.assertFalse(self.queue.is_queue_gated(max_backlog=5))

        # Create 4 open proposals
        for i in range(4):
            prop = {
                "schema_version": "1.0.0",
                "artifact_id": f"prop-{i}",
                "status": "PROPOSED",
                "approval_required": True,
            }
            with open(self.proposals_dir / f"prop_{i}.json", "w") as f:
                json.dump(prop, f)

        self.assertEqual(self.queue.get_open_proposals_count(), 4)
        self.assertFalse(self.queue.is_queue_gated(max_backlog=5))

        # Add 5th proposal: triggers gating
        prop = {
            "schema_version": "1.0.0",
            "artifact_id": "prop-4",
            "status": "PROPOSED",
            "approval_required": True,
        }
        with open(self.proposals_dir / "prop_4.json", "w") as f:
            json.dump(prop, f)

        self.assertEqual(self.queue.get_open_proposals_count(), 5)
        self.assertTrue(self.queue.is_queue_gated(max_backlog=5))


if __name__ == "__main__":
    unittest.main()
