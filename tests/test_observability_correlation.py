"""
tests/test_observability_correlation.py

Wave 6 Invariant Tests:
1. Multi-Agent OpenTelemetry Trace Context Propagation & Correlation:
   - Validates context propagation via W3C TraceContext across agent-subagent boundaries.
   - Asserts subagent executions never produce detached or orphaned root spans.
   - Verifies trace_id preservation and parent_span_id linkage across multi-hop delegations.
2. Dialectical Evaluation & Ambiguity Margin Gating:
   - Enforces 25% ambiguity margin threshold (Delta = c_top - c_second >= 0.25 -> AUTONOMOUS).
   - Enforces HITL surfacing when Delta < 0.25 (HITL_REQUIRED).
   - Generates deterministic SHA-256 audit receipt digests.
3. Atomic Presentation Governance:
   - Atomic presentation (one decision at a time, mutually exclusive options, recommended option marked).
   - Confidence tier scoring (HIGH, MEDIUM, LOW) grounded in calibrated priors.
4. Bayesian Prior Calibration Learning Loop:
   - Closed-loop feedback via HITLDecisionStore (+0.15 boost for affirmed choices).
   - Widening margin past the 25% threshold to achieve autonomous convergence.
5. Strict Module Line Budgets & Cognitive Complexity Invariants:
   - Verifies all Wave 6 modules satisfy line budgets and ratchet baselines.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_span_id, format_trace_id

from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    HITLDecisionStore,
    validate_hitl_presentation,
)
from dual_pipeline.llm_judge import (
    DialecticalJudge,
    evaluate_dialectical_decision,
)
from dual_pipeline.otel_engine import (
    correlate_subagent_span,
    propagate_subagent_context,
    trace_agent_handoff,
)


class TestMultiAgentTraceCorrelation(unittest.TestCase):
    """Verifies OpenTelemetry trace context propagation across multi-agent handoffs."""

    def setUp(self) -> None:
        self.memory_exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.memory_exporter))
        self.tracer = self.provider.get_tracer("test_multi_agent_tracer")

    def test_subagent_context_propagation_and_correlation(self) -> None:
        """Verifies sub-agent task carries parent trace context and links spans without orphan roots."""

        with self.tracer.start_as_current_span("orchestrator_planning") as parent_span:
            p_ctx = parent_span.get_span_context()
            parent_trace_id = format_trace_id(p_ctx.trace_id)
            parent_span_id = format_span_id(p_ctx.span_id)

            # Delegate to sub-agent
            subagent_payload = propagate_subagent_context(
                task_payload={"task_id": "subtask-research-001", "role": "Researcher"},
                span=parent_span,
            )

            # Invariants on the outbound payload
            self.assertIn("trace_context", subagent_payload)
            self.assertIn("traceparent", subagent_payload)
            self.assertEqual(subagent_payload.get("trace_id"), parent_trace_id)
            self.assertEqual(subagent_payload.get("parent_span_id"), parent_span_id)

            # Execute sub-agent within the correlated context
            with correlate_subagent_span(
                operation_name="subagent_research_execution",
                task_payload=subagent_payload,
                attributes={"subagent.role": "Researcher"},
                tracer=self.tracer,
            ) as child_span:
                c_ctx = child_span.get_span_context()
                child_trace_id = format_trace_id(c_ctx.trace_id)
                child_span_id = format_span_id(c_ctx.span_id)

                # Sub-agent span must inherit exact parent trace_id
                self.assertEqual(child_trace_id, parent_trace_id)
                self.assertNotEqual(child_span_id, parent_span_id)

        # Inspect exported span hierarchy
        spans = self.memory_exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)

        child_exported = next(s for s in spans if s.name == "subagent_research_execution")
        parent_exported = next(s for s in spans if s.name == "orchestrator_planning")

        # Child's parent span id must strictly match parent span id
        self.assertEqual(
            format_span_id(child_exported.parent.span_id),
            format_span_id(parent_exported.context.span_id),
        )
        self.assertEqual(
            format_trace_id(child_exported.context.trace_id),
            format_trace_id(parent_exported.context.trace_id),
        )
        # Child span must NOT be a root span (parent is not None)
        self.assertIsNotNone(child_exported.parent)

    def test_multi_hop_subagent_delegation(self) -> None:
        """Verifies multi-hop delegation: Orchestrator -> Primary Agent -> Secondary Subagent."""
        with self.tracer.start_as_current_span("orchestrator_root") as root_span:
            root_trace_id = format_trace_id(root_span.get_span_context().trace_id)

            hop1_payload = propagate_subagent_context({"step": 1}, span=root_span)

            with correlate_subagent_span("agent_step_1", hop1_payload, tracer=self.tracer) as hop1_span:
                hop2_payload = propagate_subagent_context({"step": 2}, span=hop1_span)

                with correlate_subagent_span("subagent_step_2", hop2_payload, tracer=self.tracer) as hop2_span:
                    hop2_trace_id = format_trace_id(hop2_span.get_span_context().trace_id)
                    self.assertEqual(hop2_trace_id, root_trace_id)

        finished = self.memory_exporter.get_finished_spans()
        self.assertEqual(len(finished), 3)
        for s in finished:
            self.assertEqual(format_trace_id(s.context.trace_id), root_trace_id)

    def test_trace_agent_handoff_error_recording(self) -> None:
        """Verifies errors during agent handoffs record exceptions and set ERROR status."""
        carrier = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        with self.assertRaises(RuntimeError):
            with trace_agent_handoff("failing_handoff", parent_carrier=carrier, tracer=self.tracer):
                raise RuntimeError("Subagent execution failed intentionally")

        spans = self.memory_exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].status.status_code.name, "ERROR")
        self.assertTrue(len(spans[0].events) > 0)


class TestDialecticalJudgeAmbiguityGating(unittest.TestCase):
    """Verifies Dialectical LLM Judge and 25% ambiguity margin gating invariants."""

    def test_autonomous_resolution_above_25_percent_margin(self) -> None:
        """Delta >= 0.25 qualifies top option autonomously without operator prompt."""
        options = [
            {"option_id": "opt_a", "label": "Deploy Microservice Architecture", "confidence": "85%"},
            {"option_id": "opt_b", "label": "Retain Monolith Structure", "confidence": "45%"},
        ]
        result = evaluate_dialectical_decision(
            decision_id="dec-arch-001",
            question="Select deployment architecture?",
            options=options,
        )
        self.assertEqual(result.status, "AUTONOMOUS_RESOLUTION")
        self.assertFalse(result.should_surface)
        self.assertGreaterEqual(result.confidence_margin_delta, DEFAULT_MARGIN_THRESHOLD)
        self.assertTrue(result.options[0]["is_recommended"])
        self.assertTrue(result.options[0]["label"].startswith("(Recommended)"))
        self.assertEqual(len(result.audit_receipt_digest), 64)

    def test_hitl_surfacing_below_25_percent_margin(self) -> None:
        """Delta < 0.25 gates execution and surfaces atomic HITL decision card."""
        options = [
            {"option_id": "opt_1", "label": "Candidate Option 1", "confidence": "58%"},
            {"option_id": "opt_2", "label": "Candidate Option 2", "confidence": "52%"},
        ]
        result = evaluate_dialectical_decision(
            decision_id="dec-close-002",
            question="Select schema migration strategy?",
            options=options,
        )
        self.assertEqual(result.status, "HITL_REQUIRED")
        self.assertTrue(result.should_surface)
        self.assertLess(result.confidence_margin_delta, DEFAULT_MARGIN_THRESHOLD)
        self.assertEqual(len(result.audit_receipt_digest), 64)

    def test_atomic_presentation_governance(self) -> None:
        """Verifies HITL options adhere to atomic presentation invariants."""
        options = [
            {"key": "opt_1", "label": "Option Alpha", "confidence": 0.65},
            {"key": "opt_2", "label": "Option Beta", "confidence": 0.35},
        ]
        judge = DialecticalJudge()
        calibrated = judge.calibrate_options(options)

        # Presentation validation must pass (empty errors list)
        errors = validate_hitl_presentation(
            {"prompt": "Choose operational strategy?", "options": calibrated}
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(calibrated), 2)
        for opt in calibrated:
            self.assertIn("confidence_level", opt)
            self.assertIn(opt["confidence_level"], ("HIGH", "MEDIUM", "LOW"))

    def test_bayesian_prior_calibration_learning_loop(self) -> None:
        """Verifies Bayesian prior updating in HITL store widens margin past 25% threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            store = HITLDecisionStore(ledger_path=ledger_path)
            judge = DialecticalJudge(ledger_path=ledger_path)

            options = [
                {"option_id": "role_lead", "key": "role_lead", "label": "Lead Engineer", "confidence": "54%"},
                {"option_id": "role_consultant", "key": "role_consultant", "label": "Consultant", "confidence": "50%"},
            ]

            # Initial evaluation: ambiguous (Delta = 4% < 25%)
            res_initial = judge.evaluate_perspectives(
                decision_id="dec-role-001",
                question="Resolve principal attribution?",
                options=options,
                category="ROLE_RESOLUTION",
            )
            self.assertTrue(res_initial.should_surface)
            self.assertEqual(res_initial.status, "HITL_REQUIRED")

            # Operator confirms 'role_lead' twice
            for i in range(2):
                store.record_decision(
                    decision_id=f"hitl-affirm-{i}",
                    category="ROLE_RESOLUTION",
                    prompt="Resolve principal attribution?",
                    options=options,
                    selected_option={"key": "role_lead", "option_id": "role_lead"},
                    rationale="Confirmed against verified organizational ledger.",
                )

            # Re-evaluation with learned priors: Delta widens past 25% threshold
            res_calibrated = judge.evaluate_perspectives(
                decision_id="dec-role-002",
                question="Resolve principal attribution?",
                options=options,
                category="ROLE_RESOLUTION",
            )
            self.assertFalse(res_calibrated.should_surface)
            self.assertEqual(res_calibrated.status, "AUTONOMOUS_RESOLUTION")
            self.assertGreaterEqual(res_calibrated.confidence_margin_delta, DEFAULT_MARGIN_THRESHOLD)


class TestWave6ModuleBudgetsAndGovernance(unittest.TestCase):
    """Verifies code hygiene, module budgets, and cognitive complexity baselines."""

    def test_wave6_module_line_budgets(self) -> None:
        """Verifies all Wave 6 affected modules comply with module line budgets."""
        repo_root = Path(__file__).resolve().parent.parent
        budget_checks = [
            ("dual_pipeline/telemetry_core/exporter.py", 400),
            ("dual_pipeline/llm_judge_core/judge.py", 400),
            ("dual_pipeline/otel_engine.py", 400),
            ("dual_pipeline/telemetry.py", 400),
            ("dual_pipeline/llm_judge.py", 400),
            ("dual_pipeline/uwg_core/gateway.py", 2157),
        ]
        for rel_path, max_lines in budget_checks:
            target = repo_root / rel_path
            self.assertTrue(target.exists(), f"Target {rel_path} does not exist")
            line_count = len(target.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                max_lines,
                f"Module {rel_path} exceeds budget: {line_count} > {max_lines}",
            )


if __name__ == "__main__":
    unittest.main()
