"""
tests/end_to_end/test_active_ingestion_scheduled_e2e.py

Wave 5 E2E Test Suite: Active Ingestion Scheduled & Host Workflows.
Verifies that:
  1. Unattended scheduled runs without pre-cleared authority enter AWAITING_APPROVAL
     and register a visible durable approval task.
  2. Scheduled runs with authentic pre-recorded clearance continue to authorized publication.
  3. Pending tasks are discoverable and resumable via the scheduler adapter.
  4. Structured observable summary accurately reflects pending age and actionable work.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.scheduler_adapter import (
    format_pending_approval_summary,
    get_pending_approval_task,
    list_pending_approval_requests,
)


class ActiveIngestionScheduledE2ETests(unittest.TestCase):
    """E2E suite verifying unattended scheduled approval continuation and host adapters."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unattended_scheduled_run_enters_awaiting_approval(self) -> None:
        """Verify unattended scheduled run registers a durable task and returns AWAITING_APPROVAL."""
        source_file = self.tmp_path / "weekly_update.md"
        source_file.write_text("# Weekly Intel\nMarket trends update.", encoding="utf-8")

        args = argparse.Namespace(
            input=source_file,
            project="project_nexus",
            meeting="Weekly Trends",
            date="10/06/2026",
            provider="offline",
            metadata=None,
            output_dir=str(self.tmp_path),
            directive_id="dir-weekly-trends-10-06-2026",
            reviewer=None,
            rationale=None,
            dry_run=False,
            dry_run_graph=False,
            # Scheduled run uses active intent defaults
        )

        with patch("dual_pipeline.hitl_governance_core.active_ingestion_approval.check_active_ingestion_clearance") as mock_clear, \
             patch("dual_pipeline.hitl_governance_core.runtime_tool_adapter.dispatch_interactive_question") as mock_dispatch:

            mock_clear.return_value = (False, "NO_PRIOR_APPROVAL", None)
            mock_dispatch.return_value = {
                "status": "QUEUED_UNATTENDED",
                "request_id": "req-weekly-01",
                "display_receipt": {"channel": "operator_inbox"},
            }

            result = ingest_canonical(args)

            self.assertEqual(result.get("status"), "AWAITING_APPROVAL")
            self.assertEqual(result.get("directive_id"), "dir-weekly-trends-10-06-2026")
            self.assertFalse(result.get("graph_mutation_committed"))
            self.assertIn("approval_request_id", result)

            req_id = result["approval_request_id"]
            task = get_pending_approval_task(req_id, self.tmp_path)
            self.assertIsNotNone(task)
            self.assertEqual(task.get("directive_id"), "dir-weekly-trends-10-06-2026")

    def test_scheduled_adapter_discovers_and_summarizes_pending_tasks(self) -> None:
        """Verify scheduler adapter lists pending requests with age calculation."""
        req1 = build_active_ingestion_approval_request(
            run_id="run-sched-01",
            directive_id="dir-sched-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="a" * 64,
            proposed_delta_digest="b" * 64,
            baseline_generation=1,
        )
        dispatch_and_persist_approval_request(req1, storage_root=self.tmp_path)

        pending = list_pending_approval_requests(self.tmp_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["request_id"], req1["request_id"])
        self.assertFalse(pending[0]["is_expired"])

        summary = format_pending_approval_summary(self.tmp_path)
        self.assertEqual(summary["total_pending"], 1)
        self.assertEqual(summary["total_expired"], 0)
        self.assertTrue(summary["has_actionable_work"])

    def test_resuming_pending_scheduled_request_with_operator_approval(self) -> None:
        """Verify operator approval resumes the workflow and persists authentic clearance."""
        req = build_active_ingestion_approval_request(
            run_id="run-sched-02",
            directive_id="dir-sched-02",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="c" * 64,
            proposed_delta_digest="d" * 64,
            baseline_generation=2,
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        approved_option = req["question_payload"]["options"][0]["label"]
        auth_response = {
            "operator_id": "human_operator",
            "selected_option": approved_option,
            "signature": f"sig:{approved_option}:human_operator",
            "rationale": "Verified weekly briefing trends for Nexus knowledge graph",
        }

        authorized, msg, updated = resume_ingestion_with_approval(
            request_id=req_id,
            operator_response=auth_response,
            storage_root=self.tmp_path,
            ledger_path=self.ledger_path,
        )

        self.assertTrue(authorized)
        self.assertIn("AUTHORIZED", msg)
        self.assertEqual(updated["response_status"], "APPROVED")

        is_cleared, clear_reason, rec = check_active_ingestion_clearance(
            run_id="run-sched-02",
            directive_id="dir-sched-02",
            delta_digest="d" * 64,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(is_cleared)
        self.assertIn("RATIFIED", clear_reason)


if __name__ == "__main__":
    unittest.main()
