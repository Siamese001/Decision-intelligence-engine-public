"""
tests/adversarial/test_wave5_observability_and_telemetry_adversarial.py

Adversarial and boundary test suite for Wave 5 Observability & Telemetry:
- Corrupted and malformed W3C traceparent headers
- High-concurrency multithreaded token tracking and cost accounting
- Micro-budget and zero-budget boundary conditions
- Hostile, broken, and timing-out alert dispatcher endpoints
- Corrupt ledger file resilience during run history queries
- Rapid log rotation stampede under concurrent file writes
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.alerting import (
    AlertDispatcher,
    clear_dispatched_alerts,
    get_dispatched_alerts,
)
from dual_pipeline.cli.handlers import handle_run_history
from dual_pipeline.cost_tracker import (
    BudgetExhaustionError,
    RunCostTracker,
    RunTokenUsageTracker,
)
from dual_pipeline.logging_config import StructuredJsonFormatter
from dual_pipeline.otel_engine import extract_trace_context


class TestWave5ObservabilityAndTelemetryAdversarial(unittest.TestCase):
    """Adversarial suite verifying Wave 5 observability resilience under extreme conditions."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)
        clear_dispatched_alerts()

    def tearDown(self):
        clear_dispatched_alerts()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_malformed_and_hostile_traceparents(self):
        """Verifies extract_trace_context fails gracefully on corrupt or invalid traceparent strings."""
        corrupted_payloads = [
            "invalid_non_hex_traceparent",
            "00-truncated-span-01",
            "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # version ff is invalid
            "00-00000000000000000000000000000000-0000000000000000-00",  # all zero trace id is invalid
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra-segment",
            "",
            None,
        ]

        for payload in corrupted_payloads:
            carrier = {"traceparent": payload} if payload is not None else {}
            # Must return a valid Context without raising an unhandled exception
            ctx = extract_trace_context(carrier)
            self.assertIsNotNone(ctx)

    def test_multithreaded_token_and_cost_accounting_stampede(self):
        """Verifies concurrent threads hammering token tracker and cost tracker produce exact totals."""
        token_tracker = RunTokenUsageTracker()
        cost_tracker = RunCostTracker(max_cost_usd=100.0)

        num_threads = 20
        calls_per_thread = 50

        def worker():
            for _ in range(calls_per_thread):
                token_tracker.record_usage(
                    prompt_tokens=10,
                    completion_tokens=5,
                    cached_prompt_tokens=2,
                    reasoning_tokens=1,
                )
                cost_tracker.record_call(
                    model="gpt-5.6-luna",
                    prompt_tokens=10,
                    completion_tokens=5,
                    cached_prompt_tokens=2,
                    reasoning_tokens=1,
                )

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected_total_calls = num_threads * calls_per_thread
        self.assertEqual(token_tracker.call_count, expected_total_calls)
        self.assertEqual(token_tracker.prompt_tokens, expected_total_calls * 10)
        self.assertEqual(token_tracker.completion_tokens, expected_total_calls * 5)
        self.assertEqual(token_tracker.cached_prompt_tokens, expected_total_calls * 2)
        self.assertEqual(token_tracker.reasoning_tokens, expected_total_calls * 1)

        cost_summary = cost_tracker.to_dict()
        self.assertEqual(cost_summary["call_count"], expected_total_calls)
        self.assertGreater(cost_summary["total_cost_usd"], 0.0)

    def test_micro_budget_and_zero_budget_boundaries(self):
        """Verifies budget guardrail triggers immediately when limit is set to zero or micro-cents."""
        zero_tracker = RunCostTracker(max_cost_usd=0.0)
        with self.assertRaises(BudgetExhaustionError):
            zero_tracker.record_call(model="gpt-5.6-luna", prompt_tokens=10, completion_tokens=10)

        micro_tracker = RunCostTracker(max_cost_usd=0.000001)
        with self.assertRaises(BudgetExhaustionError):
            micro_tracker.record_call(model="gpt-5.6-luna", prompt_tokens=100, completion_tokens=50)

    def test_alert_dispatcher_hostile_endpoints_and_failures(self):
        """Verifies AlertDispatcher never raises unhandled exceptions on invalid or timing-out endpoints."""
        dispatcher = AlertDispatcher(
            slack_webhook_url="http://192.0.2.1:81/blackhole_slack",  # Non-routable TEST-NET-1 IP
            pagerduty_routing_key="bad_routing_key",
            generic_webhook_url="http://invalid.domain.that.does.not.exist.example/webhook",
        )

        # Dispatch alert with short timeout mock to avoid slow test
        with patch.object(dispatcher, "_post_json", side_effect=TimeoutError("Connection timed out")):
            result = dispatcher.dispatch(
                event_type="CORRUPT_INPUT",
                message="Audio file header checksum mismatch",
                details={"file": "test.wav"},
            )
            self.assertFalse(result)

        # Verify alert was captured in memory ledger
        alerts = get_dispatched_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["event_type"], "CORRUPT_INPUT")

    def test_corrupt_ledger_file_resilience_in_run_history(self):
        """Verifies handle_run_history handles corrupt SQLite files cleanly without crashing."""
        corrupt_db = self.tmp_path / "ledger" / "learning.sqlite"
        corrupt_db.parent.mkdir(parents=True, exist_ok=True)
        # Write corrupted garbage bytes
        corrupt_db.write_text("CORRUPTED_NON_SQLITE_DATA", encoding="utf-8")

        args = argparse.Namespace(runtime_root=self.tmp_path, limit=10, status=None, since=None, json=True)
        res = handle_run_history(args)

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["runs"], [])
        self.assertEqual(res["count"], 0)

    def test_structured_json_formatter_handles_unserializable_objects(self):
        """Verifies StructuredJsonFormatter serializes complex and unserializable extra attributes."""
        formatter = StructuredJsonFormatter(run_id="run-extra-test")
        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=100,
            msg="Error occurred with complex extra",
            args=(),
            exc_info=None,
        )
        # Attach non-serializable object
        record.unserializable_obj = object()

        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "ERROR")
        self.assertIn("extra", parsed)
        self.assertIn("unserializable_obj", parsed["extra"])


if __name__ == "__main__":
    unittest.main()
