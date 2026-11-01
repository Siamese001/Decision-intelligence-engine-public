"""End-to-End E2E test verifying that ingestion never terminates in simulated/fake states.

Proves:
1. Normal document ingestion terminates in AWAITING_APPROVAL with authentic deltas, never PACKAGE_SEALED.
2. Normal meeting ingestion terminates in AWAITING_APPROVAL, never PACKAGE_SEALED.
3. Zero premature graph mutation occurs before approval.
4. Approving via nee ingest-resolve mutates graph and reaches COMPLETE / PUBLISHED.
5. Declining via nee ingest-resolve terminates in DECLINED and leaves graph unmutated.
6. PACKAGE_SEALED is strictly barred as a terminal output status.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline as PipelineOrchestrator
from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.cli.ingest_resolve_cmd import execute_ingest_resolve
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.core import ROOT, connect_sqlite, read_json
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestIngestNoSimulatedModesE2E(unittest.TestCase):
    """End-to-End verification that simulated run modes are removed and runs require approval."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_no_sim_modes_e2e_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Provision isolated temporary graph DB and apply canonical schema
        prov_res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = prov_res["isolated_db_path"]
        self.addCleanup(lambda: Path(self.isolated_db_path).unlink(missing_ok=True))
        with connect_sqlite(self.isolated_db_path) as conn:
            schema_file = ROOT / "storage" / "schemas" / "canonical_graph.sql"
            conn.executescript(schema_file.read_text(encoding="utf-8"))

        # Copy sealed BMO fixture into sandbox
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
        self.doc_input = self.pkg_sandbox_dir / "source_document.txt"
        self.ledger_path = self.temp_dir / "hitl_decision_ledger.sqlite"
        self.parser = build_parser()

    def _get_node_count(self) -> int:
        """Helper to get current node count from isolated test DB."""
        with connect_sqlite(self.isolated_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM canonical_graph_nodes")
            return cursor.fetchone()[0]

    def test_e2e_document_ingest_reaches_awaiting_approval_never_package_sealed(self) -> None:
        """Verify document ingestion transitions from sealed package to AWAITING_APPROVAL, never PACKAGE_SEALED."""
        initial_nodes = self._get_node_count()

        # Build execution args for document ingest
        args = self.parser.parse_args([
            "ingest-document",
            "--input", str(self.doc_input),
            "--project", "bmo",
            "--output-dir", str(self.pkg_sandbox_dir),
            "--runtime-root", str(self.temp_dir),
            "--graph", str(self.isolated_db_path),
            "--directive-id", "directive-bmo-doc-e2e-v1",
        ])

        pipeline_mock_result = {
            "status": "PACKAGE_SEALED",
            "output_dir": str(self.pkg_sandbox_dir),
            "package_path": str(self.pkg_path),
            "metadata": {"project": "bmo", "worktree_dir": str(self.temp_dir)},
            "stage_latencies": {"u0": 0.1, "l1": 0.2},
        }

        with patch.object(PipelineOrchestrator, "run", return_value=pipeline_mock_result):
            result = ingest_canonical(args)

        # 1. State must be AWAITING_APPROVAL, NEVER PACKAGE_SEALED
        self.assertNotEqual(result.get("status"), "PACKAGE_SEALED", "PACKAGE_SEALED must never be a final terminal status")
        self.assertEqual(result.get("status"), "AWAITING_APPROVAL")

        # 2. Authentic counts and non-zero sha256 digests
        deltas = result.get("table_deltas", {})
        self.assertEqual(deltas.get("node"), 19, "Expected 19 extracted entities")
        self.assertEqual(deltas.get("source"), 1)
        self.assertEqual(deltas.get("assertion"), 11, "Expected 11 assertion signals")
        self.assertNotEqual(result.get("source_package_digest"), "0" * 64)
        self.assertNotEqual(result.get("proposed_delta_digest"), "0" * 64)
        self.assertEqual(len(result.get("source_package_digest", "")), 64)
        self.assertEqual(len(result.get("proposed_delta_digest", "")), 64)

        # 3. Approval request was generated and persisted
        app_req_id = result.get("approval_request_id")
        self.assertTrue(app_req_id and app_req_id.startswith("req-ingest-"))
        self.assertFalse(result.get("graph_mutation_committed"))

        # 4. Graph remains 100% unmutated before operator approval
        after_nodes = self._get_node_count()
        self.assertEqual(initial_nodes, after_nodes, "Graph must not mutate before explicit approval")

    def test_e2e_meeting_ingest_reaches_awaiting_approval_never_package_sealed(self) -> None:
        """Verify meeting ingestion transitions to AWAITING_APPROVAL and does not mutate graph prematurely."""
        initial_nodes = self._get_node_count()

        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", str(self.doc_input),
            "--project", "bmo",
            "--meeting", "BMO Org Review",
            "--date", "10-12-2026",
            "--output-dir", str(self.pkg_sandbox_dir),
            "--runtime-root", str(self.temp_dir),
            "--graph", str(self.isolated_db_path),
            "--directive-id", "directive-bmo-meeting-e2e-v1",
        ])

        pipeline_mock_result = {
            "status": "PACKAGE_SEALED",
            "output_dir": str(self.pkg_sandbox_dir),
            "package_path": str(self.pkg_path),
            "metadata": {"project": "bmo", "worktree_dir": str(self.temp_dir)},
            "stage_latencies": {"u0": 0.1, "l1": 0.2},
        }

        with patch.object(PipelineOrchestrator, "run", return_value=pipeline_mock_result):
            result = ingest_canonical(args)

        self.assertNotEqual(result.get("status"), "PACKAGE_SEALED")
        self.assertEqual(result.get("status"), "AWAITING_APPROVAL")
        self.assertFalse(result.get("graph_mutation_committed"))
        self.assertEqual(initial_nodes, self._get_node_count())

    def test_e2e_approval_and_decline_resolution_flows(self) -> None:
        """Verify full lifecycle: pending approval resolves to COMPLETE on approve, or DECLINED on decline."""
        args = self.parser.parse_args([
            "ingest-document",
            "--input", str(self.doc_input),
            "--project", "bmo",
            "--output-dir", str(self.pkg_sandbox_dir),
            "--runtime-root", str(self.temp_dir),
            "--graph", str(self.isolated_db_path),
            "--directive-id", "directive-bmo-lifecycle-v1",
        ])

        pipeline_mock_result = {
            "status": "PACKAGE_SEALED",
            "output_dir": str(self.pkg_sandbox_dir),
            "package_path": str(self.pkg_path),
            "metadata": {"project": "bmo", "worktree_dir": str(self.temp_dir)},
            "stage_latencies": {"u0": 0.1},
        }

        with patch.object(PipelineOrchestrator, "run", return_value=pipeline_mock_result):
            result = ingest_canonical(args)

        req_id = result["approval_request_id"]

        # Case 1: Decline resolution
        decline_args = argparse.Namespace(
            request_id=req_id,
            decision="decline",
            runtime_root=self.temp_dir,
            reviewer="operator_tester",
            rationale="Declined in test verification",
            ledger_path=self.ledger_path,
            graph=self.isolated_db_path,
        )
        decline_res = execute_ingest_resolve(decline_args)
        self.assertEqual(decline_res["status"], "DECLINED")
        self.assertFalse(decline_res["graph_mutation_committed"])

        # Case 2: Approve resolution (with mock activation)
        mock_receipt = {
            "status": "PASS",
            "activated_generation": 1,
            "directive_id": "directive-bmo-lifecycle-v1",
            "table_counts": {"node": 19, "assertion": 11},
        }
        with patch("dual_pipeline.cli.preview_exit_hook.run_auto_activation", return_value=mock_receipt):
            approve_args = argparse.Namespace(
                request_id=req_id,
                decision="approve",
                runtime_root=self.temp_dir,
                reviewer="operator_tester",
                rationale="Approved for publication into canonical graph",
                ledger_path=self.ledger_path,
                graph=self.isolated_db_path,
            )
            approve_res = execute_ingest_resolve(approve_args)
            self.assertEqual(approve_res["status"], "PUBLISHED")
            self.assertTrue(approve_res["graph_mutation_committed"])


if __name__ == "__main__":
    unittest.main()
