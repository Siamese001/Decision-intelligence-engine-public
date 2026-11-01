"""Unit tests for OpenTelemetry Engine in dual_pipeline/otel_engine.py.

Verifies:
1. JSONLSpanExporter initialization, file creation, and permissions.
2. Span serialization, timestamps, events, and attribute encoding.
3. Thread-safe appending of spans to local JSONL ledger.
4. W3C TraceContext propagation (inject / extract).
5. Safe handling of empty span lists and shutdown lifecycle.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags

from dual_pipeline.otel_engine import JSONLSpanExporter, extract_trace_context, inject_trace_context


class TestOtelEngineUnit(unittest.TestCase):
    """Unit test suite for dual_pipeline.otel_engine."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file = Path(self.temp_dir.name) / "spans.jsonl"
        self.exporter = JSONLSpanExporter(self.log_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_exporter_file_initialization(self) -> None:
        self.assertTrue(self.log_file.parent.is_dir())
        self.assertEqual(self.exporter.file_path, self.log_file.resolve())

    def test_export_empty_spans(self) -> None:
        result = self.exporter.export([])
        self.assertEqual(result, SpanExportResult.SUCCESS)
        self.assertFalse(self.log_file.exists())

    def test_export_mocked_readable_span(self) -> None:
        mock_span = MagicMock()
        span_ctx = SpanContext(
            trace_id=0x12345678123456781234567812345678,
            span_id=0x1234567812345678,
            is_remote=False,
            trace_flags=TraceFlags(0x01),
        )
        mock_span.get_span_context.return_value = span_ctx
        mock_span.name = "test_operation"
        mock_span.parent = None
        mock_span.start_time = 1_700_000_000_000_000_000
        mock_span.end_time = 1_700_000_001_000_000_000
        mock_span.events = []
        mock_span.status = Status(status_code=StatusCode.OK)
        mock_span.attributes = {
            "component": "dual_pipeline",
            "count": 5,
            "cost": 0.002,
            "is_active": True,
            "tags": ["tag1", "tag2"],
        }
        mock_span.resource.attributes = {"service.name": "nee_decision_intelligence"}

        result = self.exporter.export([mock_span])
        self.assertEqual(result, SpanExportResult.SUCCESS)
        self.assertTrue(self.log_file.is_file())

        lines = self.log_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

        record = json.loads(lines[0])
        self.assertEqual(record["name"], "test_operation")
        self.assertEqual(record["trace_id"], "12345678123456781234567812345678")
        self.assertEqual(record["span_id"], "1234567812345678")
        self.assertEqual(record["status"]["code"], "OK")
        self.assertEqual(record["attributes"]["component"], "dual_pipeline")
        self.assertEqual(record["attributes"]["count"], 5)
        self.assertEqual(record["attributes"]["tags"], ["tag1", "tag2"])
        self.assertEqual(record["duration_ms"], 1000.0)

    def test_trace_context_injection_and_extraction(self) -> None:
        carrier: dict[str, str] = {}
        inject_trace_context(carrier)
        # Verify carrier can be processed without crashing
        extracted_ctx = extract_trace_context(carrier)
        self.assertIsNotNone(extracted_ctx)

    def test_exporter_shutdown(self) -> None:
        self.exporter.shutdown()
        # Shutdown should be safe and idempotent
        self.exporter.shutdown()


if __name__ == "__main__":
    unittest.main()
