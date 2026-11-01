"""Adversarial and Edge-Case tests for nee ingest-resolve and active ingestion approvals."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.ingest_resolve_cmd import (
    execute_ingest_resolve,
    extract_package_deltas_and_digests,
)
from dual_pipeline.core import ROOT, write_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
)
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestIngestResolveAdversarial(unittest.TestCase):
    """Adversarial suite covering tamper detection, expiration, invalid states, and bypass rejections."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_ingest_resolve_adv_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        prov_res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = prov_res["isolated_db_path"]
        self.addCleanup(lambda: Path(self.isolated_db_path).unlink(missing_ok=True))

        bmo_fixture_src = ROOT / "tests" / "fixtures" / "bmo_fixture"
        if not (bmo_fixture_src / "intake_package.json").is_file():
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

    def test_adv_digest_mismatch_blocks_mutation(self) -> None:
        """Category: Invariant bypass attempt & Malformed input.
        If the staged package is altered after the approval request was created,
        ingest-resolve must fail closed with DIGEST_MISMATCH and not mutate the graph."""
        deltas, source_digest, delta_digest = extract_package_deltas_and_digests(self.pkg_path)
        req = build_active_ingestion_approval_request(
            run_id="run-tamper-001",
            directive_id="dir-tamper-001",
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

        # Tamper with the package by modifying a payload attribute
        pkg_data = json.loads(self.pkg_path.read_text(encoding="utf-8"))
        pkg_data["tampered_attribute"] = "forged_malicious_mutation"
        self.pkg_path.write_text(json.dumps(pkg_data), encoding="utf-8")

        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator",
            rationale="Attempting to approve tampered package",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )

        with self.assertRaises(ValueError) as ctx:
            execute_ingest_resolve(args)

        self.assertIn("DIGEST_MISMATCH", str(ctx.exception))
        self.assertIn("Graph publication rejected", str(ctx.exception))

    def test_adv_expired_request_fails_closed(self) -> None:
        """Category: Overflow & exhaustion.
        Approval requests past their expires_at deadline must fail closed and update status to EXPIRED."""
        deltas, source_digest, delta_digest = extract_package_deltas_and_digests(self.pkg_path)
        req = build_active_ingestion_approval_request(
            run_id="run-expired-001",
            directive_id="dir-expired-001",
            runtime_root=str(self.temp_dir),
            project="bmo",
            source_package_digest=source_digest,
            proposed_delta_digest=delta_digest,
            baseline_generation=0,
            table_deltas=deltas,
            package_path=str(self.pkg_path),
        )
        # Set expiration in the past
        past_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
        req["expires_at"] = past_time

        dispatched = dispatch_and_persist_approval_request(
            req,
            transport=None,
            ledger_path=self.ledger_path,
            storage_root=self.temp_dir,
        )
        req_id = dispatched["request_id"]

        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator",
            rationale="Approving expired request",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )

        with self.assertRaises(ValueError) as ctx:
            execute_ingest_resolve(args)

        self.assertIn("has expired", str(ctx.exception))

        # Request file status must now be EXPIRED
        req_file = self.temp_dir / "approval_requests" / f"{req_id}.json"
        updated_req = json.loads(req_file.read_text(encoding="utf-8"))
        self.assertEqual(updated_req["response_status"], "EXPIRED")

    def test_adv_missing_or_invalid_request_id_fails_closed(self) -> None:
        """Category: Boundary payload.
        Invalid or non-existent request ID must raise FileNotFoundError immediately."""
        args = argparse.Namespace(
            request_id="req-nonexistent-deadbeef-000",
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator",
            rationale="Approving non-existent request",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )

        with self.assertRaises(FileNotFoundError) as ctx:
            execute_ingest_resolve(args)

        self.assertIn("not found in candidates", str(ctx.exception))

    def test_adv_invalid_decision_choice_fails_closed(self) -> None:
        """Category: Boundary payload.
        Any decision value other than 'approve' or 'decline' must raise ValueError."""
        args = argparse.Namespace(
            request_id="req-foo",
            decision="force_merge",
            runtime_root=self.temp_dir,
            reviewer="operator",
            rationale="Invalid decision command",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )

        with self.assertRaises(ValueError) as ctx:
            execute_ingest_resolve(args)

        self.assertIn("Invalid decision 'force_merge'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
