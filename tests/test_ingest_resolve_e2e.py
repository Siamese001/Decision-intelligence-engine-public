"""End-to-End E2E test for nee ingest-resolve and authentic operator approval path."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from dual_pipeline.cli.ingest_resolve_cmd import (
    execute_ingest_resolve,
    extract_package_deltas_and_digests,
)
from dual_pipeline.core import ROOT, read_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    dispatch_and_persist_approval_request,
)
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestIngestResolveE2E(unittest.TestCase):
    """E2E suite verifying the single authentic approval path and ledger-backed mutation gate."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_ingest_resolve_e2e_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Provision isolated temporary graph DB
        prov_res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = prov_res["isolated_db_path"]
        self.addCleanup(lambda: Path(self.isolated_db_path).unlink(missing_ok=True))

        # Copy sealed BMO fixture into sandbox
        bmo_fixture_src = ROOT / "tests" / "fixtures" / "bmo_fixture"
        if not (bmo_fixture_src / "intake_package.json").is_file():
            # Fallback to worktree path if running directly in linked worktree
            bmo_fixture_src = (
                ROOT.parent.parent
                / ".worktrees"
                / "ingest_chart_bmo_james_beebe_org"
                / "output"
                / "bmo"
                / "10-12-2026"
                / "James_Beebe_Business_Management_Chad"
            )

        self.pkg_sandbox_dir = self.temp_dir / "bmo_pkg"
        shutil.copytree(bmo_fixture_src, self.pkg_sandbox_dir)
        self.pkg_path = self.pkg_sandbox_dir / "intake_package.json"
        self.ledger_path = self.temp_dir / "hitl_decision_ledger.sqlite"

    def test_e2e_real_counts_and_digests_extracted_from_sealed_package(self) -> None:
        """Verify extract_package_deltas_and_digests produces authentic counts (19 entities, 11 assertions) and non-zero sha256 digests."""
        deltas, source_digest, delta_digest = extract_package_deltas_and_digests(self.pkg_path)

        self.assertEqual(deltas["node"], 19, "Expected exactly 19 entities extracted from BMO org chart")
        self.assertEqual(deltas["source"], 1)
        self.assertEqual(deltas["assertion"], 11, "Expected 11 assertion signals from BMO org chart")
        self.assertNotEqual(source_digest, "0" * 64, "Source digest must not be placeholder zero hash")
        self.assertNotEqual(delta_digest, "0" * 64, "Delta digest must not be placeholder zero hash")
        self.assertEqual(len(source_digest), 64)
        self.assertEqual(len(delta_digest), 64)

    def test_e2e_approval_request_lifecycle_and_decline(self) -> None:
        """Verify building, dispatching, and declining an approval request leaves the graph unmutated."""
        deltas, source_digest, delta_digest = extract_package_deltas_and_digests(self.pkg_path)
        req = build_active_ingestion_approval_request(
            run_id="run-bmo-e2e-decline",
            directive_id="directive-bmo-e2e-decline-v1",
            runtime_root=str(self.temp_dir),
            project="bmo",
            source_package_digest=source_digest,
            proposed_delta_digest=delta_digest,
            baseline_generation=0,
            table_deltas=deltas,
            package_path=str(self.pkg_path),
        )

        dispatched = dispatch_and_persist_approval_request(
            req,
            transport=None,
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        req_id = dispatched["request_id"]
        self.assertIn(dispatched["response_status"], ("AWAITING", "DISPATCHED"))

        # Resolve with decline
        args = argparse.Namespace(
            request_id=req_id,
            decision="decline",
            runtime_root=self.temp_dir,
            reviewer="operator_test",
            rationale="Operator declined mutation during review",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )
        res = execute_ingest_resolve(args)

        self.assertEqual(res["status"], "DECLINED")
        self.assertFalse(res["graph_mutation_committed"])
        self.assertIn("Operator declined publication", res["reason"])

        # Check ledger record
        is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
            run_id="run-bmo-e2e-decline",
            directive_id="directive-bmo-e2e-decline-v1",
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)
        self.assertIn("DECLINED", clear_reason)

    def test_e2e_approval_request_lifecycle_and_approve(self) -> None:
        """Verify approving an approval request records AFFIRMED in the ledger and triggers activation."""
        deltas, source_digest, delta_digest = extract_package_deltas_and_digests(self.pkg_path)
        req = build_active_ingestion_approval_request(
            run_id="run-bmo-e2e-approve",
            directive_id="directive-bmo-e2e-approve-v1",
            runtime_root=str(self.temp_dir),
            project="bmo",
            source_package_digest=source_digest,
            proposed_delta_digest=delta_digest,
            baseline_generation=0,
            table_deltas=deltas,
            package_path=str(self.pkg_path),
        )

        dispatched = dispatch_and_persist_approval_request(
            req,
            transport=None,
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        req_id = dispatched["request_id"]

        mock_receipt = {
            "status": "PASS",
            "activated_generation": 1,
            "directive_id": "directive-bmo-e2e-approve-v1",
            "table_counts": {"node": 19, "assertion": 11},
        }

        with patch("dual_pipeline.cli.preview_exit_hook.run_auto_activation", return_value=mock_receipt) as mock_activate:
            args = argparse.Namespace(
                request_id=req_id,
                decision="approve",
                runtime_root=self.temp_dir,
                reviewer="authentic_operator",
                rationale="Approved 19 entities and 11 signals for BMO org structure",
                ledger_path=self.ledger_path,
                graph=self.isolated_db_path,
            )
            res = execute_ingest_resolve(args)

            self.assertEqual(res["status"], "PUBLISHED")
            self.assertTrue(res["graph_mutation_committed"])
            self.assertEqual(res["table_deltas"]["node"], 19)
            self.assertEqual(res["table_deltas"]["assertion"], 11)
            mock_activate.assert_called_once()

        # Check ledger record is cleared and affirmative
        is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
            run_id="run-bmo-e2e-approve",
            directive_id="directive-bmo-e2e-approve-v1",
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(is_cleared)
        self.assertIsNotNone(clear_rec)
        self.assertEqual(clear_rec["operator_id"], "authentic_operator")


if __name__ == "__main__":
    unittest.main()
