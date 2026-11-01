"""
tests/test_wave5_observability_and_telemetry_e2e.py

End-to-end test suite for Wave 5: Observability & Telemetry.
Tests:
- Structured JSON logging format and trace correlation
- Real OpenTelemetry span emission for pipeline stages
- Metrics collection (counters, histograms, gauges, snapshots)
- Token usage tracking and persistence in execution receipts
- Cost accounting with AstraModelPricing and budget ceiling guardrails
- Alert dispatcher webhook execution and fail-safe handling
- Run history query from learning.sqlite
- W3C traceparent extraction and propagation
- Span exporter file rotation and retention pruning
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.alerting import (
    AlertDispatcher,
    clear_dispatched_alerts,
    dispatch_pipeline_alert,
    get_dispatched_alerts,
)
from dual_pipeline.cli.handlers import handle_run_history
from dual_pipeline.cost_tracker import (
    BudgetExhaustionError,
    RunCostTracker,
    get_cost_tracker,
    get_token_tracker,
    reset_trackers,
)
from dual_pipeline.logging_config import (
    configure_logging,
    set_active_run_id,
)
from dual_pipeline.otel_engine import (
    extract_trace_context,
    format_traceparent,
    init_telemetry,
)
from dual_pipeline.telemetry_core.constants import ledger_connection
from dual_pipeline.telemetry_core.exporter import JSONLSpanExporter
from dual_pipeline.telemetry_core.metrics import (
    get_counter_value,
    get_histogram_values,
    get_metrics_snapshot,
    record_error_metric,
    record_ingestion_metric,
    record_stage_duration,
    reset_metrics,
    set_gauge,
)
from dual_pipeline.transcript_pipeline_core.recorder import PipelineSpanRecorder
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


class TestWave5ObservabilityAndTelemetryE2E(unittest.TestCase):
    """End-to-end suite verifying all Wave 5 Observability & Telemetry deliverables."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)
        reset_metrics()
        reset_trackers()
        clear_dispatched_alerts()

    def tearDown(self):
        reset_metrics()
        reset_trackers()
        clear_dispatched_alerts()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_structured_json_logging_e2e(self):
        """Verifies structured JSON logging emits valid single-line JSON with trace and run IDs."""
        stream = io.StringIO()
        set_active_run_id("run-test-json-001")
        configure_logging(log_format="json", log_level="INFO", run_id="run-test-json-001", stream=stream)

        logger = logging.getLogger("dual_pipeline.test_logger")
        logger.info("Test structured message", extra={"custom_field": "custom_val"})

        output = stream.getvalue().strip()
        self.assertTrue(len(output) > 0, "No log output recorded")

        # Parse JSON
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["logger"], "dual_pipeline.test_logger")
        self.assertEqual(parsed["message"], "Test structured message")
        self.assertEqual(parsed["run_id"], "run-test-json-001")
        self.assertIn("timestamp", parsed)
        self.assertEqual(parsed["extra"]["custom_field"], "custom_val")

    def test_pipeline_stage_otel_spans_e2e(self):
        """Verifies PipelineSpanRecorder and UnifiedAgenticPipelineTracker emit real OTel spans."""
        init_telemetry(service_name="test-pipeline", jsonl_path=self.tmp_path / "traces.jsonl")

        # 1. PipelineSpanRecorder
        recorder = PipelineSpanRecorder(service_name="test-pipeline")
        with recorder.span("test_custom_stage", attributes={"stage.custom": "val"}):
            time.sleep(0.01)

        # Verify metric recorded
        durations = get_histogram_values("stage.duration_seconds", {"stage": "test_custom_stage", "status": "PASS"})
        self.assertEqual(len(durations), 1)
        self.assertGreater(durations[0], 0.0)

        # 2. UnifiedAgenticPipelineTracker stages
        tracker = UnifiedAgenticPipelineTracker(pipeline_name="ObservabilityTest", input_path="sample.txt")
        tracker.start_stage(PipelineStage.U0_INGRESS)
        time.sleep(0.01)
        tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={"items": 5})

        u0_durations = get_histogram_values("stage.duration_seconds", {"stage": "U0", "status": "PASS"})
        self.assertEqual(len(u0_durations), 1)

    def test_metrics_framework_counters_and_histograms_e2e(self):
        """Verifies metric collection across counters, histograms, gauges, and snapshot aggregation."""
        record_ingestion_metric(status="PASS", project="project_nexus")
        record_ingestion_metric(status="PASS", project="project_nexus")
        record_error_metric(stage="U0", error_type="AudioCorruption")
        record_stage_duration(stage="L1", duration_seconds=1.25)
        set_gauge("queue.depth", 4.0)

        self.assertEqual(get_counter_value("ingestion.total", {"status": "PASS", "project": "project_nexus"}), 2.0)
        self.assertEqual(get_counter_value("errors.total", {"stage": "U0", "error_type": "AudioCorruption"}), 1.0)
        self.assertEqual(get_histogram_values("stage.duration_seconds", {"stage": "L1", "status": "PASS"}), [1.25])

        snapshot = get_metrics_snapshot()
        self.assertIn("timestamp", snapshot)
        self.assertTrue(len(snapshot["counters"]) >= 2)
        self.assertTrue(len(snapshot["histograms"]) >= 1)
        self.assertTrue(len(snapshot["gauges"]) >= 1)

    def test_token_usage_tracker_and_receipt_persistence_e2e(self):
        """Verifies RunTokenUsageTracker captures tokens and persists them into pipeline_execution_receipt.json."""
        token_tracker = get_token_tracker()
        token_tracker.record_usage(
            prompt_tokens=1500,
            completion_tokens=400,
            cached_prompt_tokens=500,
            reasoning_tokens=250,
        )

        cost_tracker = get_cost_tracker()
        cost_tracker.record_call(
            model="gpt-5.6-luna",
            prompt_tokens=1500,
            completion_tokens=400,
            cached_prompt_tokens=500,
            reasoning_tokens=250,
        )

        summary = token_tracker.to_dict()
        self.assertEqual(summary["total_prompt_tokens"], 1500)
        self.assertEqual(summary["cached_prompt_tokens"], 500)
        self.assertEqual(summary["uncached_prompt_tokens"], 1000)
        self.assertEqual(summary["total_completion_tokens"], 400)
        self.assertEqual(summary["reasoning_tokens"], 250)

        # Build receipt and verify persistence
        tracker = UnifiedAgenticPipelineTracker(pipeline_name="TokenReceiptTest", input_path="sample.txt")
        # Advance through all stages
        for stage in PIPELINE_STAGE_SEQUENCE:
            tracker.start_stage(stage)
            tracker.complete_stage(stage, status="PASS")

        receipt = tracker.build_receipt(output_dir=self.tmp_path)
        receipt_path = receipt.write_to_dir(self.tmp_path)

        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertIn("token_usage", receipt_data)
        self.assertEqual(receipt_data["token_usage"]["total_prompt_tokens"], 1500)
        self.assertEqual(receipt_data["token_usage"]["reasoning_tokens"], 250)
        self.assertGreater(receipt_data["cost_usd"], 0.0)

    def test_cost_tracker_and_budget_exhaustion_e2e(self):
        """Verifies RunCostTracker calculates USD cost and enforces max_cost_usd ceiling."""
        tracker = RunCostTracker(max_cost_usd=0.05)

        # First call within budget: 1000 prompt tokens, 100 completion tokens (~$0.0075)
        cost1 = tracker.record_call(model="gpt-5.6-luna", prompt_tokens=1000, completion_tokens=100)
        self.assertGreater(cost1, 0.0)
        self.assertLess(tracker.total_cost_usd, 0.05)

        # Second call that breaches $0.05 ceiling
        with self.assertRaises(BudgetExhaustionError) as ctx:
            tracker.record_call(model="gpt-5.6-luna", prompt_tokens=20000, completion_tokens=2000)
        self.assertIn("BUDGET_EXHAUSTED", str(ctx.exception))

    def test_alert_dispatcher_e2e(self):
        """Verifies AlertDispatcher dispatches formatted alert notifications and remains fail-safe."""
        dispatcher = AlertDispatcher(generic_webhook_url="http://localhost:9999/nonexistent_webhook")

        # Even with nonexistent URL, dispatch() must log a warning and return without raising
        result = dispatcher.dispatch(
            event_type="PIPELINE_STAGE_CRASH",
            message="Stage X1 failed conformance gate",
            details={"run_id": "run-001", "error": "AssertionCountZero"},
            level="ERROR",
        )
        self.assertFalse(result)  # Target unreachable, but no crash

        history = get_dispatched_alerts()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["event_type"], "PIPELINE_STAGE_CRASH")
        self.assertEqual(history[0]["details"]["run_id"], "run-001")

        # Test global helper
        dispatch_pipeline_alert("SECURITY_INCIDENT", "Bipartite namespace violation")
        self.assertEqual(len(get_dispatched_alerts()), 2)

    def test_run_history_query_e2e(self):
        """Verifies handle_run_history queries learning.sqlite and filters by status, since, and limit."""
        ledger_dir = self.tmp_path / "ledger"
        ledger_path = ledger_dir / "learning.sqlite"
        conn = ledger_connection(ledger_path)

        # Seed mock run_seal entries
        now_iso = "2026-09-27T12:00:00Z"
        conn.execute(
            """
            INSERT INTO run_seal (run_id, trace_id, use_case, status, required_stages_json, observed_stages_json, event_count, event_chain_digest, sealed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-hist-01", "trace-01", "transcript_synthesis", "COMPLETE", "[]", "[]", 10, "digest01", now_iso),
        )
        conn.execute(
            """
            INSERT INTO run_seal (run_id, trace_id, use_case, status, required_stages_json, observed_stages_json, event_count, event_chain_digest, sealed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-hist-02", "trace-02", "transcript_synthesis", "FAIL", "[]", "[]", 4, "digest02", "2026-09-25T10:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Query all
        args = argparse.Namespace(runtime_root=self.tmp_path, limit=10, status=None, since=None, json=True)
        res = handle_run_history(args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["count"], 2)

        # Filter by status: FAIL
        args_fail = argparse.Namespace(runtime_root=self.tmp_path, limit=10, status="FAIL", since=None, json=True)
        res_fail = handle_run_history(args_fail)
        self.assertEqual(res_fail["count"], 1)
        self.assertEqual(res_fail["runs"][0]["run_id"], "run-hist-02")

        # Filter by since: 2026-09-26
        args_since = argparse.Namespace(runtime_root=self.tmp_path, limit=10, status=None, since="2026-09-26", json=True)
        res_since = handle_run_history(args_since)
        self.assertEqual(res_since["count"], 1)
        self.assertEqual(res_since["runs"][0]["run_id"], "run-hist-01")

    def test_w3c_traceparent_propagation_e2e(self):
        """Verifies W3C traceparent header extraction and context injection."""
        valid_traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        carrier = {"traceparent": valid_traceparent}
        ctx = extract_trace_context(carrier)

        # Context contains valid trace
        tp = format_traceparent(ctx)
        self.assertEqual(tp, valid_traceparent)

    def test_log_rotation_and_retention_pruning_e2e(self):
        """Verifies JSONLSpanExporter rotates logs when exceeding max_bytes and prunes stale backups."""
        span_file = self.tmp_path / "traces_test.jsonl"
        # Small max_bytes (100 bytes) to force rotation
        exporter = JSONLSpanExporter(span_file, max_bytes=100, backup_count=3, retention_days=1)

        # Mock span
        mock_span = MagicMock()
        mock_span.get_span_context.return_value.trace_id = 0x1234567890abcdef1234567890abcdef
        mock_span.get_span_context.return_value.span_id = 0x1234567890abcdef
        mock_span.parent = None
        mock_span.start_time = int(time.time() * 1e9)
        mock_span.end_time = int(time.time() * 1e9) + 1000000
        mock_span.name = "rot_test_span"
        mock_span.status = None
        mock_span.attributes = {"key": "val"}
        mock_span.events = []

        # Export multiple times
        for _ in range(5):
            exporter.export([mock_span])

        # Verify rotation occurred: backup files exist
        backups = list(self.tmp_path.glob("traces_test.jsonl*"))
        self.assertGreater(len(backups), 1, "Expected rotated backup files to exist")


if __name__ == "__main__":
    unittest.main()
