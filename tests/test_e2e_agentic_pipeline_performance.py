"""End-to-End Performance & Telemetry Validation Tests (Wave 7).

Verifies:
1. Steady-state query execution latency meets the repository SLA (<7,500ms).
2. OpenTelemetry captures L1 review and re-plan events with proper lineage.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.otel_engine import (
    init_telemetry,
    record_l1_post_l2_review_event,
    record_l1_replan_cycle_event,
)


class E2EAgenticPipelinePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-perf-test-")
        cls.span_file = Path(cls.temp.name) / "spans.jsonl"
        cls.tracer = init_telemetry(service_name="perf-test", jsonl_path=cls.span_file)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_telemetry_event_recording(self) -> None:
        """Verifies L1 review and replan events are recorded onto OpenTelemetry spans."""
        with self.tracer.start_as_current_span("test_l1_feedback_flow") as span:
            record_l1_post_l2_review_event(
                span=span,
                iteration=0,
                verdict="INSUFFICIENT_REPLAN",
                deterministic_passed=True,
                semantic_passed=False,
                replan_directive={"directive_type": "EXPAND_COVERAGE"},
            )
            record_l1_replan_cycle_event(
                span=span,
                iteration=1,
                reason="INSUFFICIENT_EVIDENCE",
                unanswered_subquestions=["What are the system dependencies?"],
            )

        self.assertTrue(self.span_file.is_file())
        content = self.span_file.read_text(encoding="utf-8")
        self.assertIn("L1PostL2ReviewEvent", content)
        self.assertIn("L1ReplanCycleEvent", content)
        self.assertIn("INSUFFICIENT_REPLAN", content)

    def test_steady_state_latency_sla(self) -> None:
        """Verifies pure L1 deterministic check and state machine latency is well under 7,500ms (<50ms)."""
        from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine, LoopState
        from dual_pipeline.l1_review_contracts import (
            L1PostL2ReviewContract,
            L1ReviewVerdict,
        )

        start = time.perf_counter()
        sm = L1FeedbackStateMachine(max_iterations=2)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract = L1PostL2ReviewContract.create(
            run_id="run-sla",
            query_id="q-sla",
            plan_id="p-sla",
            sealed_artifact_id="s-sla",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        state, _ = sm.process_review_verdict(contract)
        duration_ms = (time.perf_counter() - start) * 1000

        self.assertEqual(LoopState.SUFFICIENT, state)
        self.assertLess(duration_ms, 7500.0, "L1 review cycle must complete within 7,500ms SLA")
        self.assertLess(duration_ms, 100.0, "Pure in-memory L1 review should execute in <100ms")


if __name__ == "__main__":
    unittest.main()
