"""
tests/test_backtest_promote.py

Unit and integration tests for Wave 4: Backtest Promotion CLI (nee backtest-promote).
Verifies dry-run preview and authoritative promotion via Universal Write Gateway without provider calls.
Compatible with pytest and unittest.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.promote import _backtest_promote


class TestBacktestPromote(unittest.TestCase):
    """Test suite for 'nee backtest-promote' CLI command."""

    def setUp(self) -> None:
        os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
        os.environ["NEE_ENFORCE_WORKTREE"] = "0"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Setup meeting directory
        self.meeting_dir = self.tmp_path / "output" / "project_nexus" / "10-01-2026" / "Promote_Session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write sample Output 03
        self.output_3_data = {
            "schema_version": "5.7",
            "meeting_id": "meeting-promote-session",
            "classification": "internal",
            "entity_upserts": [
                {"id": "person:alice", "entity_id": "person:alice", "name": "Alice", "entity_type": "person"},
                {"id": "org:partner_advisory", "entity_id": "org:partner_advisory", "name": "PartnerAdvisory", "entity_type": "org"},
            ],
            "action_updates": [
                {"action_id": "action:promote-wave4", "description": "Promote Wave 4", "status": "planned"},
            ],
            "relationship_signals": [
                {"signal_id": "rel-01", "from_id": "person:alice", "to_id": "org:partner_advisory", "relationship": "AFFILIATED_WITH"},
            ],
        }
        self.out3_file = self.meeting_dir / "03_Promote_Session_10-01-2026_canonical_signal_projection.json"
        self.out3_file.write_text(json.dumps(self.output_3_data), encoding="utf-8")

        # Write sample Output 01 and 02
        self.out1_file = self.meeting_dir / "01_Promote_Session_10-01-2026_hitl_resolved_transcript.md"
        self.out1_file.write_text(
            "# Transcript\n\n"
            "[00:00:00] Alice: Welcome everyone to the promote session. Today we are walking through "
            "the architectural deliverables, verifying the backtest pipeline results, and preparing "
            "our promotion receipts for the knowledge graph release. All team members must review "
            "the action items and confirm their workstream alignment before we proceed with the final "
            "governed graph release lifecycle.",
            encoding="utf-8",
        )
        self.out2_file = self.meeting_dir / "02_Promote_Session_10-01-2026_executive_recap.md"
        self.out2_file.write_text("# Executive Recap\n\n## Objective\nPromote backtest\n", encoding="utf-8")

        # Write metadata and governed receipt files
        self.meta = {
            "project": "project_nexus",
            "meeting_name": "Promote Session",
            "date": "10/01/2026",
            "attendees": ["Alice"],
        }
        (self.meeting_dir / "metadata.json").write_text(json.dumps(self.meta), encoding="utf-8")
        (self.meeting_dir / "qa_gates_report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
        (self.meeting_dir / "llm_judge_evaluation.json").write_text(json.dumps({"overall_verdict": "PASS", "composite_score": 90}), encoding="utf-8")
        (self.meeting_dir / "final_release_decision.json").write_text(json.dumps({"status": "PASS", "authoritative_write_authorized": True}), encoding="utf-8")

        # Setup mock graph
        self.db_path = self.tmp_path / "target_graph.sqlite"
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, description TEXT)")
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, claim_text TEXT, predicate TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        os.environ.pop("NEE_ENFORCE_WORKTREE", None)
        os.environ.pop("NEE_ALLOW_TEST_FIXTURES", None)
        self.temp_dir.cleanup()

    def test_cli_parser_backtest_promote_arguments(self) -> None:
        """Verify backtest-promote parser accepts required flags and rejects removed --reviewer/--rationale/--dry-run."""
        parser = build_parser()
        args = parser.parse_args([
            "backtest-promote",
            "--meeting-dir", str(self.meeting_dir),
            "--directive-id", "directive-test-01",
        ])
        self.assertEqual(args.command, "backtest-promote")
        self.assertEqual(args.meeting_dir, self.meeting_dir)
        self.assertEqual(args.directive_id, "directive-test-01")
        with self.assertRaises(SystemExit) as exc_info:
            parser.parse_args([
                "backtest-promote",
                "--meeting-dir", str(self.meeting_dir),
                "--reviewer", "Alice Operator",
            ])
        self.assertEqual(exc_info.exception.code, 1)

    @patch("dual_pipeline.hitl_governance_core.mutation_approval.request_mutation_approval")
    def test_backtest_promote_awaiting_approval_preview(self, mock_approval: MagicMock) -> None:
        """Verify backtest-promote computes preview and enters AWAITING_APPROVAL when not yet cleared."""
        mock_approval.return_value = {
            "cleared": False,
            "status": "AWAITING_APPROVAL",
            "request_id": "req-promote-01",
            "delta_digest": "abc123",
        }
        args = argparse.Namespace(
            command="backtest-promote",
            meeting_dir=self.meeting_dir,
            graph=self.db_path,
            runtime_root=self.tmp_path / "runtime",
            directive_id=None,
            embedding_backend="bge-m3-dev",
        )

        res = _backtest_promote(args)
        self.assertEqual(res["status"], "AWAITING_APPROVAL")
        self.assertFalse(res["graph_mutation_committed"])
        self.assertIn("preview", res)
        self.assertEqual(res["preview"]["counts"]["nodes_added"], 2)
        self.assertEqual(res["preview"]["counts"]["assertions_new"], 1)

        # Confirm preview files written
        self.assertTrue((self.meeting_dir / "graph_delta_preview.json").is_file())
        self.assertTrue((self.meeting_dir / "graph_delta_preview.md").is_file())

        # No promotion receipt when awaiting approval
        self.assertFalse((self.meeting_dir / "backtest_promotion_receipt.json").exists())

    def test_backtest_promote_requires_meeting_dir(self) -> None:
        """Verify error raised when meeting-dir is missing or nonexistent."""
        args = argparse.Namespace(
            command="backtest-promote",
            meeting_dir=None,
        )
        with self.assertRaises(ValueError):
            _backtest_promote(args)

        args.meeting_dir = self.tmp_path / "nonexistent_dir"
        with self.assertRaises(FileNotFoundError):
            _backtest_promote(args)

    def test_backtest_promote_requires_output_03(self) -> None:
        """Verify error raised when meeting directory has no Output 03."""
        empty_dir = self.tmp_path / "empty_meeting"
        empty_dir.mkdir(parents=True)
        args = argparse.Namespace(
            command="backtest-promote",
            meeting_dir=empty_dir,
        )
        with self.assertRaises(FileNotFoundError):
            _backtest_promote(args)

    @patch("dual_pipeline.hitl_governance_core.mutation_approval.request_mutation_approval")
    @patch("dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph")
    def test_backtest_promote_authoritative_execution(self, mock_publish: MagicMock, mock_approval: MagicMock) -> None:
        """Verify authoritative promotion delegates to release_runner and writes receipt."""
        mock_approval.return_value = {
            "cleared": True,
            "status": "APPROVED",
            "request_id": "req-promote-02",
            "clearance_record": {
                "decision_id": "req-promote-02",
                "operator_id": "Alice Operator",
                "reviewer": "Alice Operator",
                "rationale": "Validating promotion",
            },
        }
        mock_publish.return_value = {
            "status": "COMPLETE",
            "receipt_path": str(self.meeting_dir / "release_receipt.json"),
            "graph_mutated": True,
        }

        args = argparse.Namespace(
            command="backtest-promote",
            meeting_dir=self.meeting_dir,
            dry_run=False,
            reviewer="Alice Operator",
            rationale="Validating promotion",
            directive_id="directive-test-promote-01",
            graph=self.db_path,
            runtime_root=self.tmp_path / "runtime",
            embedding_backend="bge-m3-dev",
        )

        res = _backtest_promote(args)
        self.assertEqual(res["status"], "COMPLETE")
        self.assertTrue(res["promoted"])

        receipt_file = self.meeting_dir / "backtest_promotion_receipt.json"
        self.assertTrue(receipt_file.is_file())
        receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["status"], "PROMOTED")
        self.assertEqual(receipt_data["reviewer"], "Alice Operator")
        self.assertEqual(receipt_data["directive_id"], "directive-test-promote-01")


if __name__ == "__main__":
    unittest.main()
