"""dual_pipeline.rollout_controls_core.cross_pipeline_evaluator

Cross-pipeline evaluator verifying integrated behavior across ingestion, documents,
query/decision, and live delivery paths under failure modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Sequence

from dual_pipeline.core import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrossPipelineEvaluationReport:
    """Comprehensive evaluation report across all 4 entrypoint workflows."""
    all_workflows_passed: bool
    workflow_results: Dict[str, Dict[str, Any]]
    aggregate_latency_ms: float
    aggregate_cost_usd: float
    accuracy_ratio: float
    is_rollout_eligible: bool
    reasons: Sequence[str] = field(default_factory=tuple)
    evaluated_at: str = field(default_factory=utc_now)


class CrossPipelineEvaluator:
    """Evaluates cross-entrypoint workflows and resilience under adversarial conditions."""

    def __init__(
        self,
        max_latency_ms: float = 5000.0,
        max_cost_usd: float = 0.10,
        min_accuracy_ratio: float = 0.95,
    ) -> None:
        self.max_latency_ms = max_latency_ms
        self.max_cost_usd = max_cost_usd
        self.min_accuracy_ratio = min_accuracy_ratio

    def evaluate_workflows(
        self,
        workflow_test_records: Sequence[Dict[str, Any]],
    ) -> CrossPipelineEvaluationReport:
        """Evaluates simulated or observed workflow execution records."""
        if not workflow_test_records:
            return CrossPipelineEvaluationReport(
                all_workflows_passed=False,
                workflow_results={},
                aggregate_latency_ms=0.0,
                aggregate_cost_usd=0.0,
                accuracy_ratio=0.0,
                is_rollout_eligible=False,
                reasons=("No workflow records provided for evaluation.",),
            )

        results: Dict[str, Dict[str, Any]] = {}
        total_tests = 0
        passed_tests = 0
        total_latency = 0.0
        total_cost = 0.0
        reasons: List[str] = []

        for rec in workflow_test_records:
            wf_id = rec.get("workflow_id", "WF_UNKNOWN")
            scenarios = rec.get("scenarios", {})
            wf_passed = True

            for sc_name, sc_data in scenarios.items():
                total_tests += 1
                success = sc_data.get("success", False)
                if success:
                    passed_tests += 1
                else:
                    wf_passed = False
                    err = sc_data.get("error", "Failed scenario")
                    reasons.append(f"{wf_id} scenario '{sc_name}' failed: {err}")

            total_latency += float(rec.get("latency_ms", 100.0))
            total_cost += float(rec.get("cost_usd", 0.005))
            results[wf_id] = {
                "passed": wf_passed,
                "scenarios": scenarios,
                "latency_ms": rec.get("latency_ms", 100.0),
                "cost_usd": rec.get("cost_usd", 0.005),
            }

        accuracy = (passed_tests / total_tests) if total_tests > 0 else 0.0
        all_passed = (passed_tests == total_tests) and (total_tests > 0)

        # Budget checks
        if total_latency > self.max_latency_ms:
            all_passed = False
            reasons.append(f"Latency exceeded: {total_latency:.1f}ms > {self.max_latency_ms:.1f}ms")

        if total_cost > self.max_cost_usd:
            all_passed = False
            reasons.append(f"Cost exceeded: ${total_cost:.4f} > ${self.max_cost_usd:.4f}")

        if accuracy < self.min_accuracy_ratio:
            all_passed = False
            reasons.append(f"Accuracy below floor: {accuracy:.1%} < {self.min_accuracy_ratio:.1%}")

        return CrossPipelineEvaluationReport(
            all_workflows_passed=all_passed,
            workflow_results=results,
            aggregate_latency_ms=round(total_latency, 2),
            aggregate_cost_usd=round(total_cost, 4),
            accuracy_ratio=round(accuracy, 4),
            is_rollout_eligible=all_passed,
            reasons=tuple(reasons),
        )
