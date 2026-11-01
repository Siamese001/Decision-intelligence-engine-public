"""Adversarial and fault injection tests for concurrent LLM provider calls (Wave 6)."""

from __future__ import annotations

import concurrent.futures
import time
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

from dual_pipeline.core import digest
from dual_pipeline.l2_policy import load_l2_config
from dual_pipeline.l2_providers_core.execution import execute_content_repair
from dual_pipeline.x1_quality import (
    QualityModelPolicy,
    QualityProviderRequest,
    QualityProviderResponse,
    QualityReviewStatus,
    QualityRole,
    QualityUsage,
    QualityVerdict,
    X1QualityConfig,
    X1QualityMode,
    load_x1_quality_config,
    run_concurrent_quality_reviews,
)


def _packet() -> dict[str, Any]:
    return {
        "packet_digest": "f" * 64,
        "evidence_items": [],
        "approved": [
            {
                "record": {
                    "record_id": "rec-adv-1",
                    "evidence_ids": ["ev-adv-1"],
                }
            }
        ],
        "candidate": [],
        "external_public": [],
    }


class FaultInjectingQualityProvider:
    provider_id = "fault-injector-provider"

    def __init__(
        self,
        factory: "FaultInjectingQualityFactory",
        model_policy: QualityModelPolicy,
    ) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        with self.factory.lock:
            self.factory.invocations.append((self.model_policy.model, request.role))

        delay = self.factory.delays.get(request.role, 0.0)
        if delay > 0:
            time.sleep(delay)

        if request.role in self.factory.faults:
            raise RuntimeError(f"Simulated fault for role {request.role.value}")

        output = {
            "verdict": "PASS",
            "defect_codes": [],
            "evidence_refs": [],
            "confidence": "high",
        }
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=self.model_policy.model,
            usage=QualityUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            latency_ms=float(delay * 1000),
        )


class FaultInjectingQualityFactory:
    def __init__(
        self,
        faults: set[QualityRole] | None = None,
        delays: dict[QualityRole, float] | None = None,
    ) -> None:
        import threading

        self.faults = faults or set()
        self.delays = delays or {}
        self.invocations: list[tuple[str, QualityRole]] = []
        self.lock = threading.Lock()

    def __call__(self, model_policy: QualityModelPolicy) -> FaultInjectingQualityProvider:
        return FaultInjectingQualityProvider(self, model_policy)


class TestProviderConcurrencyFaultAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.config: X1QualityConfig = replace(
            load_x1_quality_config(),
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        self.packet = _packet()
        self.answer = {
            "query_type": "cross_workstream_synthesis",
            "summary": "Adversarial testing answer summary.",
            "rationale": "Sufficiently grounded in facts.",
        }
        self.controls = {"status": "PASS", "checks": {"citations_resolve": True}}

    def test_fault_isolation_challenger_exception_does_not_abort_judge(self) -> None:
        """Challenger provider throws an unhandled exception while Judge succeeds."""
        factory = FaultInjectingQualityFactory(faults={QualityRole.CHALLENGER})
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=self.config,
            assurance_profile="enhanced",
            question="What is the architectural impact?",
            answer=self.answer,
            evidence_packet=self.packet,
            challenger_controls=self.controls,
            judge_controls=self.controls,
            provider_factory=factory,
            judge_prerequisite_passed=True,
        )
        self.assertEqual(QualityReviewStatus.FAIL, c_rev.status)
        self.assertEqual(QualityVerdict.ABSTAIN, c_rev.verdict)
        self.assertEqual("PROVIDER_UNAVAILABLE", c_rev.failure_code)
        self.assertEqual(QualityReviewStatus.PASS, j_rev.status)
        self.assertEqual(QualityVerdict.PASS, j_rev.verdict)
        self.assertIsNone(j_rev.failure_code)

    def test_fault_isolation_judge_exception_does_not_abort_challenger(self) -> None:
        """Judge provider throws an unhandled exception while Challenger succeeds."""
        factory = FaultInjectingQualityFactory(faults={QualityRole.JUDGE})
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=self.config,
            assurance_profile="enhanced",
            question="What is the architectural impact?",
            answer=self.answer,
            evidence_packet=self.packet,
            challenger_controls=self.controls,
            judge_controls=self.controls,
            provider_factory=factory,
            judge_prerequisite_passed=True,
        )
        self.assertEqual(QualityReviewStatus.PASS, c_rev.status)
        self.assertEqual(QualityVerdict.PASS, c_rev.verdict)
        self.assertEqual(QualityReviewStatus.FAIL, j_rev.status)
        self.assertEqual(QualityVerdict.ABSTAIN, j_rev.verdict)
        self.assertEqual("PROVIDER_UNAVAILABLE", j_rev.failure_code)

    def test_asymmetric_latency_does_not_deadlock(self) -> None:
        """One worker has high latency (50ms) while the other completes in 0ms."""
        factory = FaultInjectingQualityFactory(delays={QualityRole.CHALLENGER: 0.05})
        t0 = time.monotonic()
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=self.config,
            assurance_profile="enhanced",
            question="Analyze cross-system dependencies.",
            answer=self.answer,
            evidence_packet=self.packet,
            challenger_controls=self.controls,
            judge_controls=self.controls,
            provider_factory=factory,
            judge_prerequisite_passed=True,
        )
        elapsed = time.monotonic() - t0
        self.assertEqual(QualityReviewStatus.PASS, c_rev.status)
        self.assertEqual(QualityReviewStatus.PASS, j_rev.status)
        self.assertGreaterEqual(elapsed, 0.045)
        self.assertLess(elapsed, 0.25)

    def test_concurrent_reviews_under_thread_pressure(self) -> None:
        """10 concurrent threads invoking run_concurrent_quality_reviews simultaneously."""
        factory = FaultInjectingQualityFactory(delays={QualityRole.CHALLENGER: 0.01, QualityRole.JUDGE: 0.01})

        def _worker(idx: int) -> tuple[QualityReviewStatus, QualityReviewStatus]:
            c, j = run_concurrent_quality_reviews(
                config=self.config,
                assurance_profile="enhanced",
                question=f"Concurrent question {idx}",
                answer=self.answer,
                evidence_packet=self.packet,
                challenger_controls=self.controls,
                judge_controls=self.controls,
                provider_factory=factory,
                judge_prerequisite_passed=True,
            )
            return c.status, j.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_worker, i) for i in range(10)]
            results = [f.result() for f in futures]

        for c_status, j_status in results:
            self.assertEqual(QualityReviewStatus.PASS, c_status)
            self.assertEqual(QualityReviewStatus.PASS, j_status)
        self.assertEqual(20, len(factory.invocations))

    def test_content_repair_prefilter_adversarial_defect_codes_bypass_to_provider(self) -> None:
        """Non-deterministic or unrecognized defect codes must bypass the pre-filter."""
        config = load_l2_config()
        decision = MagicMock()
        decision.assurance_profile = "enhanced"
        decision.provider_egress_allowed = True
        decision.query_type = "cross_workstream_synthesis"
        decision.selected_model = config.enhanced.model
        decision.selected_model_profile = "enhanced"

        valid_answer = {
            "schema_version": "nee-governed-answer-1.0.0",
            "query_type": "cross_workstream_synthesis",
            "current_state": [],
            "patterns": [],
            "pain_points": [],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
            "source_evidence": [],
            "next_best_actions": [],
            "rationale": "Evidence text with 2026-10-01.",
            "counterevidence": [],
            "missing_information": [],
            "assumptions": [],
            "proposed_assertions": [],
            "what_would_change_the_conclusion": [],
            "automatic_graph_mutation_count": 0,
        }

        # Mixed defect codes: one deterministic, one semantic/hallucination defect
        result = execute_content_repair(
            decision=decision,
            config=config,
            question="What is the schedule?",
            plan={"query_type": "cross_workstream_synthesis"},
            evidence_packet=self.packet,
            prompt={"task": "Synthesize"},
            original_answer=valid_answer,
            defect_codes=["CLIENT_READINESS_GAP", "FACTUAL_CONTRADICTION"],
            provider_factory=None,
            prior_model_calls=1,
        )
        # Should not resolve deterministically; with no provider factory, should fail closed
        self.assertFalse(result.succeeded)
        self.assertNotEqual("DETERMINISTIC_HEALING_PREFILTER_RESOLVED", result.skip_reason)
        self.assertEqual("PROVIDER_UNAVAILABLE", result.provider_failure_code)

    def test_content_repair_prefilter_malformed_answer_fails_closed(self) -> None:
        """An answer missing required schema fields fails closed before healing."""
        config = load_l2_config()
        decision = MagicMock()
        decision.assurance_profile = "enhanced"
        decision.provider_egress_allowed = True
        decision.query_type = "cross_workstream_synthesis"

        malformed_answer = {
            "query_type": "cross_workstream_synthesis",
            "summary": "Missing schema_version and required array fields.",
        }

        result = execute_content_repair(
            decision=decision,
            config=config,
            question="What is the schedule?",
            plan={"query_type": "cross_workstream_synthesis"},
            evidence_packet=self.packet,
            prompt={"task": "Synthesize"},
            original_answer=malformed_answer,
            defect_codes=["CLIENT_READINESS_GAP"],
            provider_factory=None,
            prior_model_calls=1,
        )
        self.assertFalse(result.succeeded)
        self.assertEqual("INVALID_STRUCTURED_RESPONSE", result.provider_failure_code)


if __name__ == "__main__":
    unittest.main()
