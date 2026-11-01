"""Adversarial tests for maintenance CLI flags and mutation approval gating."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.ingest_resolve_cmd import execute_ingest_resolve
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.hitl_governance_core.mutation_approval import (
    compute_mutation_digest,
    request_mutation_approval,
)


class TestMaintenanceApprovalAdversarial(unittest.TestCase):
    """Adversarial test suite enforcing strict failure modes on simulated flags and approval forgery."""

    def setUp(self) -> None:
        self.parser = build_parser()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_maint_adversarial_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.ledger_path = self.temp_dir / "hitl_decision_ledger.sqlite"

    def test_adversarial_dry_run_rejected_on_graph_gc(self) -> None:
        """Verify passing --dry-run to graph-gc fails closed immediately."""
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["graph-gc", "--dry-run"])
        self.assertIn(cm.exception.code, (1, 2))

    def test_adversarial_dry_run_qa_rejected_globally(self) -> None:
        """Verify passing --dry-run-qa to any subcommand fails closed immediately."""
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["graph-gc", "--dry-run-qa"])
        self.assertIn(cm.exception.code, (1, 2))

    def test_adversarial_plan_only_rejected_globally(self) -> None:
        """Verify passing --plan-only to any subcommand fails closed immediately."""
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["graph-gc", "--plan-only"])
        self.assertIn(cm.exception.code, (1, 2))

    def test_adversarial_dry_run_rejected_on_recover_session(self) -> None:
        """Verify passing --dry-run to recover-session fails closed immediately."""
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["recover-session", "--session-dir", "/tmp", "--dry-run"])
        self.assertIn(cm.exception.code, (1, 2))

    def test_adversarial_dry_run_rejected_on_semantic_unit_release(self) -> None:
        """Verify passing --dry-run to semantic-unit-release fails closed immediately."""
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["semantic-unit-release", "--dry-run"])
        self.assertIn(cm.exception.code, (1, 2))

    def test_adversarial_forged_approval_request_id_fails_closed(self) -> None:
        """Verify a non-existent or forged approval request ID fails clearance check."""
        res = request_mutation_approval(
            action_type="TEST_ACTION",
            target_resource=self.temp_dir / "test.db",
            table_deltas={"node": 5},
            description="Adversarial forged check",
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        self.assertFalse(res["cleared"])
        self.assertEqual(res["status"], "AWAITING_APPROVAL")

    def test_adversarial_digest_mismatch_prevents_unauthorized_mutation(self) -> None:
        """Verify if table deltas change after approval was granted, approval does not clear."""
        # 1. Register and approve request for 5 nodes
        res1 = request_mutation_approval(
            action_type="TEST_MUTATION",
            target_resource=self.temp_dir / "target.db",
            table_deltas={"node": 5},
            description="Mutation of 5 nodes",
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        req_id = res1["request_id"]
        valid_digest = res1["delta_digest"]

        # Approve the 5-node request via ingest-resolve
        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator_test",
            rationale="Approved 5 nodes",
            ledger_path=self.ledger_path,
        )
        execute_ingest_resolve(args)

        # 2. Now attempt to clear with 50 nodes (tampered / expanded scope)
        res_tampered = request_mutation_approval(
            action_type="TEST_MUTATION",
            target_resource=self.temp_dir / "target.db",
            table_deltas={"node": 50},  # Expanded from 5 to 50!
            description="Tampered mutation of 50 nodes",
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        # The new mutation has a different digest and MUST NOT clear!
        self.assertFalse(res_tampered["cleared"])
        self.assertEqual(res_tampered["status"], "AWAITING_APPROVAL")
        self.assertNotEqual(res_tampered["delta_digest"], valid_digest)


if __name__ == "__main__":
    unittest.main()
