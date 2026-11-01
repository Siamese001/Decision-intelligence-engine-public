"""
tests/test_batch_api_integration.py

Comprehensive tests for Wave 5: Asynchronous OpenAI Batch API integration.
Validates CLI parsing, JSONL request creation, pipeline queuing, 50% cost discounts,
and result collection / artifact materialization.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.batch_core.adapter import (
    BatchSubmissionAdapter,
    create_batch_request,
    format_batch_jsonl,
    load_pending_batch,
    parse_batch_jsonl,
    record_pending_batch,
    serialize_synthesis_prompts,
)
from dual_pipeline.batch_core.collector import (
    collect_and_materialize_batch,
)
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.cli.batch_cli import _backtest_collect
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cost_tracker import RunCostTracker


class TestBatchAPIIntegration(unittest.TestCase):
    """Unit and integration test suite for Batch API integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.meeting_dir = self.tmp_path / "output" / "project_nexus" / "10-01-2026" / "Batch_Meeting"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_parser_batch_queue_flag(self) -> None:
        """Verify --batch-queue flag parses on ingest commands."""
        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "project_nexus",
            "--batch-queue",
        ])
        self.assertTrue(args.batch_queue)

    def test_cli_parser_backtest_collect_command(self) -> None:
        """Verify backtest-collect CLI parser accepts arguments."""
        parser = build_parser()
        args = parser.parse_args([
            "backtest-collect",
            "--meeting-dir", str(self.meeting_dir),
            "--dry-run",
        ])
        self.assertEqual(args.command, "backtest-collect")
        self.assertEqual(args.meeting_dir, self.meeting_dir)
        self.assertTrue(args.dry_run)

    def test_create_batch_request_and_jsonl_formatting(self) -> None:
        """Verify batch request serialization and JSONL formatting."""
        req1 = create_batch_request(
            custom_id="req-01",
            body={"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "Hello"}]},
        )
        req2 = create_batch_request(
            custom_id="req-02",
            body={"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "World"}]},
        )
        jsonl = format_batch_jsonl([req1, req2])
        lines = [line for line in jsonl.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)

        parsed = parse_batch_jsonl(jsonl)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["custom_id"], "req-01")
        self.assertEqual(parsed[1]["custom_id"], "req-02")

    def test_serialize_synthesis_prompts_and_pending_batch(self) -> None:
        """Verify prompts are serialized to batch_requests.jsonl and recorded in ledger."""
        reqs = [create_batch_request("out1", {"model": "gpt-6.1-sol", "messages": []})]
        out_file = serialize_synthesis_prompts(self.meeting_dir, reqs)
        self.assertTrue(out_file.is_file())
        self.assertEqual(out_file.name, "batch_requests.jsonl")

        batch_info = {"batch_id": "batch_test_123", "status": "validating"}
        ledger_path = record_pending_batch(self.meeting_dir, batch_info)
        self.assertTrue(ledger_path.is_file())

        loaded = load_pending_batch(self.meeting_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get("latest_batch_id"), "batch_test_123")

    def test_batch_submission_adapter_simulated(self) -> None:
        """Verify simulated batch submission and status retrieval."""
        adapter = BatchSubmissionAdapter(offline=True)
        req_file = self.meeting_dir / "batch_requests.jsonl"
        req_file.write_text('{"custom_id": "test"}\n', encoding="utf-8")

        res = adapter.submit_batch(req_file)
        self.assertTrue(res["simulated"])
        self.assertTrue(res["batch_id"].startswith("batch_"))

        status = adapter.retrieve_batch_status(res["batch_id"])
        self.assertEqual(status["status"], "completed")

    def test_cost_tracker_batch_discount_50pct(self) -> None:
        """Verify RunCostTracker with is_batch=True computes exact 50% discount."""
        sync_tracker = RunCostTracker(max_cost_usd=100.0)
        sync_cost = sync_tracker.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            is_batch=False,
        )

        batch_tracker = RunCostTracker(max_cost_usd=100.0)
        batch_cost = batch_tracker.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            is_batch=True,
        )

        self.assertAlmostEqual(batch_cost, sync_cost * 0.5, places=5)
        self.assertGreater(sync_cost, 0.0)

    def test_pipeline_batch_queue_execution(self) -> None:
        """Verify pipeline execution with batch_queue=True stages requests and returns BATCH_QUEUED."""
        transcript_text = (
            "# Forensic Transcript\n\n"
            "**Date:** 10/01/2026\n"
            "**Meeting:** Batch Test Meeting\n\n"
            "**Alice**: [00:00:00] Enqueueing asynchronous batch for backtest.\n"
            "**Bob**: [00:00:15] Capturing 50% cost savings.\n"
        )
        input_file = self.tmp_path / "sample.md"
        input_file.write_text(transcript_text, encoding="utf-8")

        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        res = pipeline.run(
            input_path=input_file,
            metadata={
                "project": "project_nexus",
                "meeting_name": "Batch Test Meeting",
                "date": "10/01/2026",
                "time": "10:00 AM - 10:30 AM ET",
                "attendees": [
                    {"name": "Alice", "role": "Lead Architect"},
                    {"name": "Bob", "role": "Project Manager"},
                ],
                "attendee_roles_confirmed_by_operator": True,
                "hitl_role_resolutions": {
                    "Alice": "Lead Architect",
                    "Bob": "Project Manager",
                },
                "classification": "internal",
                "batch_queue": True,
                "enforce_worktree": False,
            },
            output_dir=str(self.meeting_dir),
        )

        self.assertEqual(res["status"], "BATCH_QUEUED")
        self.assertTrue((self.meeting_dir / "batch_requests.jsonl").is_file())
        self.assertTrue((self.meeting_dir / "pending_batches.json").is_file())

    def test_batch_result_collector_materialization(self) -> None:
        """Verify BatchResultCollector parses results, writes deliverables and generates receipt."""
        (self.meeting_dir / "metadata.json").write_text(json.dumps({
            "project": "project_nexus",
            "meeting_name": "Batch Meeting",
            "date": "10/01/2026",
            "model_name": "gpt-6.1-sol",
        }), encoding="utf-8")

        # Simulate batch results JSONL
        records = [
            {
                "custom_id": "meeting__output_1",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": "# Resolved Transcript\n\n**Alice**: Content"}}],
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
                    },
                },
            },
            {
                "custom_id": "meeting__output_2",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": "# Executive Recap\n\n## Objective\nSummary"}}],
                        "usage": {"prompt_tokens": 800, "completion_tokens": 400},
                    },
                },
            },
            {
                "custom_id": "meeting__output_3",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": '{"schema_version": "5.7", "meeting_id": "m1"}'}}],
                        "usage": {"prompt_tokens": 1200, "completion_tokens": 600},
                    },
                },
            },
        ]
        results_jsonl = format_batch_jsonl(records)

        # Test Dry Run
        dry_res = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
            results_jsonl=results_jsonl,
            dry_run=True,
        )
        self.assertEqual(dry_res["status"], "PASS")
        self.assertTrue(dry_res["dry_run"])
        self.assertAlmostEqual(dry_res["savings_percentage"], 50.0, places=1)
        self.assertFalse((self.meeting_dir / "batch_collection_receipt.json").exists())

        # Test Materialization
        res = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
            results_jsonl=results_jsonl,
            dry_run=False,
        )
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["deliverables_found"]["output_1"])
        self.assertTrue(res["deliverables_found"]["output_2"])
        self.assertTrue(res["deliverables_found"]["output_3"])
        self.assertTrue((self.meeting_dir / "batch_collection_receipt.json").is_file())

        # Test CLI handler dispatch
        args = argparse.Namespace(
            command="backtest-collect",
            meeting_dir=self.meeting_dir,
            results_file=None,
            dry_run=True,
        )
        (self.meeting_dir / "batch_results.jsonl").write_text(results_jsonl, encoding="utf-8")
        cli_res = _backtest_collect(args)
        self.assertEqual(cli_res["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
