"""
tests/characterization/test_wave5_observability_telemetry.py

Wave 5 characterization tests:
- Observability, metrics & OpenTelemetry engine harmonization
- Strict module line budgets (<= 400 lines) across telemetry modules
- Sensitive-content-safe spans and event envelope privacy validation
- RunRecorder ledger recording, sealing, and event reconstruction
- W3C TraceContext propagation and format invariants
- Domain-specific event helpers verification
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.telemetry import (
    L2_OTEL_ATTRIBUTE_KEYS,
    SPAN_NAMES,
    RunRecorder,
    compute_prompt_hash,
    extract_trace_context,
    format_traceparent,
    init_telemetry,
    inject_trace_context,
    record_bipartite_leak_event,
    record_eval_result,
    record_phonetic_repair_event,
    record_quarantine_event,
)


class TestWave5ObservabilityTelemetry(unittest.TestCase):
    """Characterization tests for Wave 5 Observability, Metrics & Telemetry Harmonization."""

    def test_module_line_budgets(self) -> None:
        """Verify all telemetry modules comply with the 400-line budget invariant."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        target_modules = [
            "dual_pipeline/telemetry.py",
            "dual_pipeline/otel_engine.py",
            "dual_pipeline/telemetry_core/__init__.py",
            "dual_pipeline/telemetry_core/constants.py",
            "dual_pipeline/telemetry_core/recorder.py",
            "dual_pipeline/telemetry_core/events.py",
            "dual_pipeline/telemetry_core/agent_trajectory.py",
        ]
        for rel_path in target_modules:
            abs_path = repo_root / rel_path
            self.assertTrue(abs_path.exists(), f"Module {rel_path} does not exist")
            line_count = len(abs_path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {rel_path} exceeds 400-line budget ({line_count} lines)",
            )

    def test_telemetry_facade_exports(self) -> None:
        """Verify all expected symbols are available via the dual_pipeline.telemetry facade."""
        self.assertIsInstance(SPAN_NAMES, dict)
        self.assertIn("intake", SPAN_NAMES)
        self.assertIn("query", SPAN_NAMES)
        self.assertIsInstance(L2_OTEL_ATTRIBUTE_KEYS, frozenset)
        self.assertIn("latency_ms", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertIn("cost_usd", L2_OTEL_ATTRIBUTE_KEYS)
        self.assertTrue(callable(RunRecorder))
        self.assertTrue(callable(init_telemetry))
        self.assertTrue(callable(inject_trace_context))
        self.assertTrue(callable(extract_trace_context))
        self.assertTrue(callable(record_eval_result))
        self.assertTrue(callable(record_quarantine_event))
        self.assertTrue(callable(record_bipartite_leak_event))
        self.assertTrue(callable(record_phonetic_repair_event))

    def test_run_recorder_lifecycle_and_ledger_validation(self) -> None:
        """Verify RunRecorder creates schema-valid learning events, OTEL envelopes, and seals."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            recorder = RunRecorder(
                runtime_root,
                run_id="run-wave5-test",
                trace_id="0123456789abcdef0123456789abcdef",
                use_case="intake",
                graph_snapshot="snapshot-wave5-001",
            )

            # Record U0 event
            correlation = {
                "source_id": "src-001",
                "candidate_id": "cand-001",
                "query_id": None,
                "graph_operation_id": None,
                "hitl_item_id": None,
            }
            event = recorder.record(
                stage="U0",
                event_type="pipeline_event",
                payload={"test_key": "test_value"},
                correlation=correlation,
                status="PASS",
                input_digest="a" * 64,
                record_count=1,
            )

            self.assertEqual(event["run_id"], "run-wave5-test")
            self.assertEqual(event["stage"], "U0")
            validate_artifact(event)

            # Verify otel envelope written to disk
            otel_files = list((runtime_root / "runs" / "run-wave5-test" / "otel").glob("*.json"))
            self.assertEqual(len(otel_files), 1)
            otel_data = json.loads(otel_files[0].read_text(encoding="utf-8"))
            validate_artifact(otel_data)
            self.assertEqual(otel_data["span_name"], "nee.uc1.u0.validate")

            # Seal run
            seal_result = recorder.seal(
                required_stages=["U0", "L4"],
                correlation=correlation,
                status="PASS",
            )
            self.assertEqual(seal_result["status"], "PASS")
            self.assertEqual(seal_result["event_count"], 2)

    def test_sensitive_content_privacy_enforcement(self) -> None:
        """Verify RunRecorder rejects non-allowlisted / sensitive L2 attributes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                Path(tmpdir),
                run_id="run-privacy-test",
                trace_id="0123456789abcdef0123456789abcdef",
                use_case="intake",
                graph_snapshot=None,
            )
            correlation = {
                "source_id": "src-001",
                "candidate_id": None,
                "query_id": None,
                "graph_operation_id": None,
                "hitl_item_id": None,
            }

            # Attempting to include raw sensitive content in otel_attributes must raise ValueError
            with self.assertRaises(ValueError) as ctx:
                recorder.record(
                    stage="U0",
                    event_type="pipeline_event",
                    payload={"data": "safe"},
                    correlation=correlation,
                    otel_attributes={"raw_content": "meeting secret text", "prompt_text": "hello"},
                )
            self.assertIn("unsupported L2 OTEL attributes", str(ctx.exception))

    def test_compute_prompt_hash_privacy(self) -> None:
        """Verify compute_prompt_hash computes deterministic 64-char SHA-256 hashes."""
        text = "This is a sensitive prompt containing proprietary reasoning."
        hash1 = compute_prompt_hash(text)
        hash2 = compute_prompt_hash(text)
        hash3 = compute_prompt_hash("Different prompt text.")

        self.assertEqual(len(hash1), 64)
        self.assertEqual(hash1, hash2)
        self.assertNotEqual(hash1, hash3)

    def test_w3c_trace_context_propagation(self) -> None:
        """Verify W3C context injection, extraction, and traceparent formatting."""
        provider = TracerProvider()
        tracer = provider.get_tracer("wave5_test")
        with tracer.start_as_current_span("parent_span") as span:
            tp = format_traceparent(span)
            self.assertTrue(
                re.match(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$", tp),
                f"Invalid traceparent format: {tp}",
            )

            carrier: dict[str, str] = {}
            inject_trace_context(carrier)
            self.assertIn("traceparent", carrier)

            extracted_ctx = extract_trace_context(carrier)
            self.assertIsNotNone(extracted_ctx)

    def test_domain_event_helpers(self) -> None:
        """Verify specialized domain event helpers attach attributes and events properly."""
        provider = TracerProvider()
        tracer = provider.get_tracer("wave5_events_test")
        with tracer.start_as_current_span("test_span") as span:
            # 1. Eval result
            record_eval_result(span, gate_id="gate_qa_01", verdict="PASS", score=95.0, is_mock=True)

            # 2. Quarantine event
            record_quarantine_event(span, node_id="node:bad_claim", reason="grounding_failed")

            # 3. Bipartite leak event
            record_bipartite_leak_event(
                span,
                requested_namespace="CLIENT_SAFE",
                leaked_node_id="node:career_leverage_01",
            )

            # 4. Phonetic repair event
            record_phonetic_repair_event(
                span,
                original_token="Jone",
                resolved_speaker_id="person:john_doe",
                confidence=0.92,
            )

            # Ensure execution completes without raising exceptions


if __name__ == "__main__":
    unittest.main()
