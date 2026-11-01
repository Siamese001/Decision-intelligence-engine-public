"""Adversarial checks for truthful ingestion timing and isolated trace correlation."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dual_pipeline.l0 import ingestion_timing as pipeline_timing
from dual_pipeline.execution_evidence import durable_execution_scope, write_immutable_evidence
from dual_pipeline.otel_engine import trace_llm_execution
from dual_pipeline.l0.ingestion_timing import TimedSpan, attempt_scope, correlation_attributes
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    PipelineSequencingViolationError,
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


from tests.document_ingestion_source_cases import SourceAdversarialCases
from tests.document_checkpoint_cases import CheckpointFoundationCases
from tests.document_publication_cases import PublicationCases
from tests.document_w3_bounded_cases import BoundedPatchCases


class Wave1AdversarialTests(SourceAdversarialCases, unittest.TestCase):
    """Exercise public lifecycle behavior with no providers or production exporters."""

    def setUp(self):
        self.directory = TemporaryDirectory(prefix="ingestion-timing-test-")
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(self.provider.shutdown)
        self.tracer = self.provider.get_tracer("ingestion-test")
        self._patch("dual_pipeline.otel_engine.get_tracer", return_value=self.tracer)
        self.metrics = self._patch("dual_pipeline.telemetry_core.metrics.record_stage_duration")
        self._patch(
            "dual_pipeline.pipeline_runtime_config.verify_zero_config_drift",
            return_value=(True, ""),
        )
        tokens = self._patch("dual_pipeline.cost_tracker.get_token_tracker")
        tokens.return_value.to_dict.return_value = {}
        costs = self._patch("dual_pipeline.cost_tracker.get_cost_tracker")
        costs.return_value.total_cost_usd = 0.0
        self.initial_context = correlation_attributes()
        self.initial_span = trace.get_current_span().get_span_context()
        self.addCleanup(self._assert_context_restored)

    def _patch(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _assert_context_restored(self):
        self.assertEqual(correlation_attributes(), self.initial_context)
        self.assertEqual(trace.get_current_span().get_span_context(), self.initial_span)

    def _tracker(self, request="request-a", digest="a" * 64, metadata=None):
        if metadata is None:
            metadata = {
                "request_id": request, "raw_source_sha256": digest,
                "source_id": "source-" + request, "generation_id": "generation-test",
            }
        return UnifiedAgenticPipelineTracker(
            "DocumentTest", "private-source.md", metadata,
            pinned_config_sha256="pinned", pinned_parameters_digest="pinned",
        )

    def _advance_before_l2(self, tracker):
        for stage in PIPELINE_STAGE_SEQUENCE[:4]:
            tracker.start_stage(stage)
            details = {"input_hash": "b" * 64} if stage == PipelineStage.U0_INGRESS else {}
            tracker.complete_stage(stage, details=details)
        tracker.start_stage(PipelineStage.L2_SYNTHESIS)

    def _finish_pipeline(self, tracker):
        tracker.complete_stage(PipelineStage.L2_SYNTHESIS)
        for stage in PIPELINE_STAGE_SEQUENCE[5:]:
            tracker.start_stage(stage)
            tracker.complete_stage(stage)
        return tracker.build_receipt(self.output)

    def _spans_named(self, name):
        return [span for span in self.exporter.get_finished_spans() if span.name == name]

    def test_wall_clock_adjustment_cannot_change_elapsed_duration(self):
        wall = iter((1_800_000_000_000_000_000, 1_799_999_990_000_000_000))
        ticks = iter((10_000_000_000, 11_250_000_000))
        clock = SimpleNamespace(time_ns=lambda: next(wall), perf_counter_ns=lambda: next(ticks))
        with patch.object(pipeline_timing, "time", clock):
            interval = TimedSpan("clock-adjustment", {"request_id": "clock"})
            record = interval.finish("PASS")
            repeated = interval.finish("FAILED")
        self.assertEqual(record, repeated)
        self.assertEqual(record["duration_ms"], 1250.0)
        self.assertEqual(record["duration_clock"], "perf_counter_ns")
        self.assertEqual(record["duration_accounting"], "inclusive")
        start = datetime.fromisoformat(record["started_at"])
        finish = datetime.fromisoformat(record["finished_at"])
        self.assertEqual((finish - start).total_seconds(), -10)
        self.assertEqual(start.tzinfo, timezone.utc)
        self.assertEqual(len(self._spans_named("clock-adjustment")), 1)

    def test_stage_receipt_has_actual_start_and_finish(self):
        tracker = self._tracker()
        wall = iter((1_800_000_000_000_000_000, 1_800_000_004_000_000_000))
        ticks = iter((100, 2_000_000_100))
        clock = SimpleNamespace(time_ns=lambda: next(wall), perf_counter_ns=lambda: next(ticks))
        with patch.object(pipeline_timing, "time", clock):
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.complete_stage(PipelineStage.U0_INGRESS, details={"input_hash": "c" * 64})
        record = tracker.build_receipt(self.output).stages["U0"]
        self.assertEqual(record["duration_ms"], 2000.0)
        start, end = (datetime.fromisoformat(record[key]) for key in ("started_at", "finished_at"))
        self.assertEqual((end - start).total_seconds(), 4)
        self.assertEqual(record["correlation"]["input_digest"], "c" * 64)
        self.metrics.assert_called_once_with(stage="U0", duration_seconds=2.0, status="PASS")

    def test_provider_is_child_of_attempt_stage_and_run_with_same_binding(self):
        tracker = self._tracker()
        with tracker.execution_scope():
            self._advance_before_l2(tracker)
            with attempt_scope(2) as attempt:
                with trace_llm_execution("provider-test", requested_model="test-model") as recorder:
                    recorder.record_mock("hermetic test")
                attempt.finish("PASS")
            snapshot = self._finish_pipeline(tracker)
            self.assertEqual(snapshot.execution_timing["measurement_state"], "RUNNING")
            self.assertIsNone(snapshot.execution_timing["finished_at"])
        receipt = tracker.build_receipt(self.output)
        run, stage, attempt, provider = (
            self._spans_named(name)[0]
            for name in ("pipeline.run", "stage.L2", "pipeline.attempt", "provider-test")
        )
        self.assertEqual(stage.parent.span_id, run.context.span_id)
        self.assertEqual(attempt.parent.span_id, stage.context.span_id)
        self.assertEqual(provider.parent.span_id, attempt.context.span_id)
        self.assertEqual({s.context.trace_id for s in (run, stage, attempt, provider)}, {run.context.trace_id})
        for span in (run, stage, attempt, provider):
            self.assertEqual(span.attributes["run_id"], tracker.timing.run_id)
            self.assertEqual(span.attributes["request_id"], "request-a")
            self.assertEqual(span.attributes["source_digest"], "a" * 64)
            self.assertEqual(span.attributes["generation_id"], "generation-test")
            self.assertEqual(span.attributes["input_digest"], "b" * 64)
        self.assertEqual(provider.attributes["attempt"], 2)
        self.assertEqual(receipt.terminal_outcome, "COMPLETE")
        self.assertEqual(len(receipt.attempts), 1)
        self.assertEqual(receipt.attempts[0]["status"], "PASS")
        self.assertEqual(receipt.execution_timing["duration_accounting"], "inclusive")
        self.assertGreaterEqual(receipt.execution_timing["duration_ms"], receipt.stages["L2"]["duration_ms"])

    def test_interleaved_async_runs_keep_source_and_attempt_isolated(self):
        async def worker(name, digest, ordinal, ready, other_ready):
            tracker = self._tracker(name, digest)
            with tracker.execution_scope():
                self._advance_before_l2(tracker)
                with attempt_scope(ordinal) as attempt:
                    ready.set()
                    await other_ready.wait()
                    await asyncio.sleep(0)
                    self.assertEqual(correlation_attributes()["request_id"], name)
                    self.assertEqual(correlation_attributes()["attempt"], ordinal)
                    with trace_llm_execution("provider-" + name) as recorder:
                        recorder.record_mock("hermetic concurrent test")
                    attempt.finish("PASS")
                self._finish_pipeline(tracker)
            return tracker.build_receipt(self.output)

        async def scenario():
            left, right = asyncio.Event(), asyncio.Event()
            return await asyncio.gather(
                worker("left", "c" * 64, 1, left, right),
                worker("right", "d" * 64, 3, right, left),
            )

        receipts = asyncio.run(scenario())
        self.assertNotEqual(receipts[0].correlation["run_id"], receipts[1].correlation["run_id"])
        for name, digest, ordinal in (("left", "c" * 64, 1), ("right", "d" * 64, 3)):
            span = self._spans_named("provider-" + name)[0]
            self.assertEqual(span.attributes["request_id"], name)
            self.assertEqual(span.attributes["source_digest"], digest)
            self.assertEqual(span.attributes["attempt"], ordinal)

    def test_failed_stage_and_attempt_close_without_advancing_or_content_leak(self):
        tracker = self._tracker()
        with self.assertRaisesRegex(RuntimeError, "private source detail"):
            with tracker.execution_scope():
                self._advance_before_l2(tracker)
                with attempt_scope(1):
                    raise RuntimeError("private source detail")
        receipt = tracker.build_receipt(self.output, terminal_outcome="FAILED")
        self.assertEqual(receipt.terminal_outcome, "FAILED")
        self.assertEqual(receipt.stages["L2"]["status"], "FAILED")
        self.assertEqual(receipt.stages["L2"]["details"], {"error_type": "RuntimeError"})
        self.assertNotIn(PipelineStage.L2_SYNTHESIS, tracker.completed_stages)
        self.assertIsNone(tracker.current_stage)
        self.assertEqual(receipt.attempts[0]["status"], "FAILED")
        self.assertIsNotNone(receipt.attempts[0]["finished_at"])
        self.assertNotIn("private source detail", str(receipt.to_dict()))
        spans = self._spans_named("stage.L2") + self._spans_named("pipeline.attempt") + self._spans_named("pipeline.run")
        for span in spans:
            self.assertEqual(span.status.status_code.name, "ERROR")
            self.assertNotIn("private source detail", str(dict(span.attributes)))
        self.assertEqual(len(spans), 3)

    def test_cancellation_closes_root_stage_and_attempt(self):
        tracker = self._tracker()
        with self.assertRaises(asyncio.CancelledError):
            with tracker.execution_scope():
                self._advance_before_l2(tracker)
                with attempt_scope(1):
                    raise asyncio.CancelledError()
        receipt = tracker.build_receipt(self.output, terminal_outcome="FAILED")
        self.assertEqual(receipt.stages["L2"]["details"]["error_type"], "CancelledError")
        self.assertEqual(receipt.execution_timing["status"], "FAILED")
        self.assertEqual(receipt.attempts[0]["status"], "FAILED")
        self.assertEqual(tracker.timing.stage_spans, {})

    def test_configuration_drift_keeps_elapsed_failed_stage(self):
        tracker = self._tracker()
        with self.assertRaises(PipelineSequencingViolationError):
            with tracker.execution_scope():
                tracker.start_stage(PipelineStage.U0_INGRESS)
                with patch("dual_pipeline.pipeline_runtime_config.verify_zero_config_drift", return_value=(False, "drift")):
                    tracker.complete_stage(PipelineStage.U0_INGRESS)
        receipt = tracker.build_receipt(self.output, terminal_outcome="FAILED")
        self.assertEqual(receipt.stages["U0"]["status"], "FAILED")
        self.assertIsNotNone(receipt.stages["U0"]["finished_at"])
        self.assertEqual(tracker.completed_stages, [])
        self.assertEqual(tracker.timing.stage_spans, {})

    def test_remediation_detaches_previous_stage_before_retry(self):
        tracker = self._tracker()
        with tracker.execution_scope():
            self._advance_before_l2(tracker)
            tracker.complete_stage(PipelineStage.L2_SYNTHESIS)
            tracker.start_stage(PipelineStage.X_EXIT_GATES)
            tracker.request_remediation(PipelineStage.X_EXIT_GATES, PipelineStage.L2_SYNTHESIS, "repair")
            self.assertNotIn("pipeline.stage", correlation_attributes())
            tracker.start_stage(PipelineStage.L2_SYNTHESIS)
            receipt = self._finish_pipeline(tracker)
        runs, l2s, exits = (self._spans_named(name) for name in ("pipeline.run", "stage.L2", "stage.X1-X3"))
        self.assertEqual(len(l2s), 2)
        self.assertEqual(len(exits), 2)
        for span in l2s + exits:
            self.assertEqual(span.parent.span_id, runs[0].context.span_id)
        self.assertEqual(exits[0].attributes["terminal_outcome"], "REMEDIATION_REQUIRED")
        self.assertEqual(receipt.remediation_cycles[0]["status"], "REMEDIATION_REQUIRED")
        self.assertIsNotNone(receipt.remediation_cycles[0]["finished_at"])

    def test_request_id_survives_new_run_and_ambient_source_is_not_inherited(self):
        metadata = {"directive_id": "same-request"}
        first, second = self._tracker(metadata=metadata), self._tracker(metadata=metadata)
        self.assertEqual(first.timing.request_id, second.timing.request_id)
        self.assertEqual(first.timing.request_id, "same-request")
        self.assertNotEqual(first.timing.run_id, second.timing.run_id)
        outer = TimedSpan("outer", {"source_digest": "outer-only", "generation_id": "outer-only", "attempt": 99})
        try:
            with second.execution_scope():
                self.assertNotIn("source_digest", correlation_attributes())
                self.assertNotIn("generation_id", correlation_attributes())
                self.assertNotIn("attempt", correlation_attributes())
                second.start_stage(PipelineStage.U0_INGRESS)
                second.complete_stage(PipelineStage.U0_INGRESS)
            self.assertEqual(correlation_attributes()["source_digest"], "outer-only")
        finally:
            outer.finish("PASS")

    def test_early_exit_closes_unfinished_work_and_does_not_claim_completion(self):
        tracker = self._tracker()
        with tracker.execution_scope():
            tracker.start_stage(PipelineStage.U0_INGRESS)
        receipt = tracker.build_receipt(self.output)
        self.assertEqual(receipt.terminal_outcome, "INCOMPLETE")
        self.assertEqual(receipt.stages["U0"]["status"], "INCOMPLETE")
        self.assertEqual(receipt.execution_timing["status"], "INCOMPLETE")
        self.assertEqual(tracker.timing.stage_spans, {})

    def test_failed_attempt_and_later_success_keep_distinct_immutable_evidence(self):
        metadata = {"directive_id": "retry-request", "raw_source_sha256": "e" * 64}
        first = self._tracker(metadata=metadata)
        with self.assertRaises(RuntimeError):
            with durable_execution_scope(first, self.output):
                self._advance_before_l2(first)
                with attempt_scope(1):
                    raise RuntimeError("failed attempt")
        first_dir = self.output / "executions" / first.timing.run_id
        first_path = first_dir / "pipeline_execution_receipt.json"
        original = first_path.read_bytes()
        failed = json.loads(original)
        attempt_path = first_dir / "attempt-1.json"
        attempt_bytes = attempt_path.read_bytes()
        attempt = json.loads(attempt_bytes)
        self.assertEqual((failed["terminal_outcome"], attempt["status"]), ("FAILED", "FAILED"))
        self.assertEqual(attempt["correlation"]["run_id"], first.timing.run_id)
        self.assertIsNotNone(attempt["finished_at"])
        with self.assertRaises(FileExistsError):
            write_immutable_evidence(attempt_path, {"status": "PASS"})
        self.assertEqual(attempt_path.read_bytes(), attempt_bytes)
        second = self._tracker(metadata=metadata)
        with durable_execution_scope(second, self.output):
            self._advance_before_l2(second)
            with attempt_scope(1) as interval:
                interval.finish("PASS")
            self._finish_pipeline(second)
        second_dir = self.output / "executions" / second.timing.run_id
        passed = json.loads((second_dir / "pipeline_execution_receipt.json").read_text())
        self.assertEqual(passed["terminal_outcome"], "COMPLETE")
        self.assertEqual(passed["execution_timing"]["status"], "COMPLETE")
        self.assertEqual(passed["correlation"]["request_id"], failed["correlation"]["request_id"])
        self.assertNotEqual(first_dir, second_dir)
        self.assertEqual(first_path.read_bytes(), original)
        self.assertEqual(attempt_path.read_bytes(), attempt_bytes)
        for payload in (failed, passed, attempt):
            observed = payload.pop("evidence_digest")
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            self.assertEqual(observed, hashlib.sha256(canonical.encode()).hexdigest())

    def test_durable_start_state_is_supplied_not_inferred_and_elapsed_is_measured(self):
        for supplied in ("cold", "warm", None):
            with self.subTest(start_state=supplied):
                metadata = {} if supplied is None else {"start_state": supplied}
                tracker = self._tracker(metadata=metadata)
                ticks = itertools.count(1_000_000_000, 25_000_000)
                with patch.object(pipeline_timing, "time", SimpleNamespace(
                    time_ns=time.time_ns, perf_counter_ns=lambda: next(ticks)
                )):
                    with durable_execution_scope(tracker, self.output):
                        self._advance_before_l2(tracker)
                        self._finish_pipeline(tracker)
                path = self.output / "executions" / tracker.timing.run_id / "pipeline_execution_receipt.json"
                receipt = json.loads(path.read_text())
                self.assertEqual(receipt["measurement_scope"]["start_state"], supplied or "unclassified")
                timing = receipt["execution_timing"]
                self.assertEqual(timing["duration_clock"], "perf_counter_ns")
                self.assertGreater(timing["duration_ms"], 0)
                self.assertIsNotNone(timing["finished_at"])
                self.assertNotIn("model_load_state", receipt["correlation"])

    def test_durable_early_exit_seals_incomplete_interval_before_persistence(self):
        tracker = self._tracker()
        with durable_execution_scope(tracker, self.output):
            tracker.start_stage(PipelineStage.U0_INGRESS)
        path = self.output / "executions" / tracker.timing.run_id / "pipeline_execution_receipt.json"
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["terminal_outcome"], "INCOMPLETE")
        self.assertEqual(receipt["execution_timing"]["status"], "INCOMPLETE")
        self.assertIsNotNone(receipt["execution_timing"]["finished_at"])
        self.assertIsNotNone(receipt["execution_timing"]["duration_ms"])

    def test_failure_after_intermediate_receipt_is_failed_in_durable_timing(self):
        tracker = self._tracker()
        with self.assertRaisesRegex(RuntimeError, "publication failed"):
            with durable_execution_scope(tracker, self.output):
                self._advance_before_l2(tracker)
                snapshot = self._finish_pipeline(tracker)
                self.assertEqual(snapshot.execution_timing["measurement_state"], "RUNNING")
                self.assertIsNone(snapshot.execution_timing["finished_at"])
                raise RuntimeError("publication failed after receipt construction")
        path = self.output / "executions" / tracker.timing.run_id / "pipeline_execution_receipt.json"
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["execution_timing"]["status"], "FAILED")
        self.assertEqual(self._spans_named("pipeline.run")[0].attributes["terminal_outcome"], "FAILED")


class Wave2AdversarialTests(CheckpointFoundationCases, PublicationCases, unittest.TestCase):
    """Versioned checkpoint and stop-before-repeat publication acceptance."""


class Wave3AdversarialTests(BoundedPatchCases, unittest.TestCase):
    """Bounded patch contract and unchanged-object adversarial acceptance."""


if __name__ == "__main__":
    unittest.main()
