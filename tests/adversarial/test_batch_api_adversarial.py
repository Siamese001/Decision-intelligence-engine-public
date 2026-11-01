"""
tests/adversarial/test_batch_api_adversarial.py

Adversarial and edge-case test suite for Wave 5: Asynchronous OpenAI Batch API integration.
Validates boundary payloads, malformed JSONL, batch failure cascades, corrupted ledgers,
and zero-token edge cases across submission and collection.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.batch_core.adapter import (
    BatchSubmissionAdapter,
    create_batch_request,
    format_batch_jsonl,
    load_pending_batch,
    parse_batch_jsonl,
)
from dual_pipeline.batch_core.collector import (
    collect_and_materialize_batch,
)


class TestBatchAPIAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case test suite for Batch API integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.meeting_dir = self.tmp_path / "output" / "project_nexus" / "10-01-2026" / "Adversarial_Meeting"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_malformed_jsonl_recovery(self) -> None:
        """Verify parser safely ignores malformed lines, binary garbage, and partial json."""
        bad_jsonl = (
            '{"custom_id": "good_1", "method": "POST"}\n'
            '{"incomplete": \n'
            'NOT_JSON_AT_ALL\n'
            '\x00\x01\x02\n'
            '{"custom_id": "good_2", "method": "POST"}\n'
        )
        parsed = parse_batch_jsonl(bad_jsonl)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["custom_id"], "good_1")
        self.assertEqual(parsed[1]["custom_id"], "good_2")

    def test_adversarial_create_batch_request_validation(self) -> None:
        """Verify create_batch_request rejects empty custom_id or non-dict body."""
        with self.assertRaises(ValueError):
            create_batch_request(custom_id="", body={})
        with self.assertRaises(ValueError):
            create_batch_request(custom_id="valid", body="not-a-dict")  # type: ignore

    def test_adversarial_corrupted_pending_batches_ledger(self) -> None:
        """Verify load_pending_batch handles corrupted or empty ledger files gracefully."""
        ledger = self.meeting_dir / "pending_batches.json"
        ledger.write_text("{corrupted-json...", encoding="utf-8")
        loaded = load_pending_batch(self.meeting_dir)
        self.assertIsNone(loaded)

    def test_adversarial_nonexistent_meeting_dir(self) -> None:
        """Verify collect raises FileNotFoundError when target directory is absent."""
        fake_dir = self.tmp_path / "does_not_exist"
        with self.assertRaises(FileNotFoundError):
            collect_and_materialize_batch(meeting_dir=fake_dir)

    def test_adversarial_batch_failed_or_in_progress(self) -> None:
        """Verify collector detects non-completed batch state and halts safely."""
        (self.meeting_dir / "pending_batches.json").write_text(json.dumps({
            "latest_batch_id": "batch_failed_123",
        }), encoding="utf-8")

        mock_adapter = MagicMock(spec=BatchSubmissionAdapter)
        mock_adapter.retrieve_batch_status.return_value = {
            "batch_id": "batch_failed_123",
            "status": "failed",
        }

        res = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
            adapter=mock_adapter,
        )
        self.assertEqual(res["status"], "BATCH_IN_PROGRESS")
        self.assertEqual(res["batch_status"], "failed")

    def test_adversarial_upstream_http_errors_in_batch_records(self) -> None:
        """Verify responses with HTTP 429/500 are handled without crashing."""
        error_records = [
            {
                "custom_id": "meeting__output_1",
                "response": {
                    "status_code": 429,
                    "body": {"error": {"message": "Rate limit exceeded"}},
                },
            },
            {
                "custom_id": "meeting__output_2",
                "response": {
                    "status_code": 500,
                    "body": {"error": {"message": "Internal server error"}},
                },
            },
            {
                "custom_id": "meeting__output_3",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": '{"schema_version": "5.7"}'}}],
                        "usage": {"prompt_tokens": 500, "completion_tokens": 100},
                    },
                },
            },
        ]
        results_jsonl = format_batch_jsonl(error_records)
        res = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
            results_jsonl=results_jsonl,
            dry_run=True,
        )
        self.assertEqual(res["status"], "PASS")
        self.assertFalse(res["deliverables_found"]["output_1"])
        self.assertFalse(res["deliverables_found"]["output_2"])
        self.assertTrue(res["deliverables_found"]["output_3"])

    def test_adversarial_empty_records_payload(self) -> None:
        """Verify collector raises ValueError when results JSONL has zero valid records."""
        with self.assertRaises(ValueError):
            collect_and_materialize_batch(
                meeting_dir=self.meeting_dir,
                results_jsonl="\n\n   \n",
            )

    def test_adversarial_zero_and_missing_token_usage(self) -> None:
        """Verify cost calculation handles zero or missing usage dictionaries gracefully."""
        zero_records = [
            {
                "custom_id": "meeting__output_1",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": "text"}}],
                        # usage is omitted entirely
                    },
                },
            }
        ]
        res = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
            results_jsonl=format_batch_jsonl(zero_records),
            dry_run=True,
        )
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["token_usage"]["prompt_tokens"], 0)
        self.assertEqual(res["cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
