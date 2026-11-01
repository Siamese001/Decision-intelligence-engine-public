"""Unit tests for concurrent LLM provider calls, early-exit heuristics, and deterministic healing."""
from __future__ import annotations

import time
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

from dual_pipeline.core import digest
from dual_pipeline.l1_review_contracts import L1ReviewVerdict
from dual_pipeline.l2_policy import load_l2_config
from dual_pipeline.l2_providers_core.execution import execute_content_repair
from dual_pipeline.query_core.quality_review_stage import resolve_initial_judge_review
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.x1_quality import (
    QualityModelPolicy,
    QualityProviderRequest,
    QualityProviderResponse,
    QualityReviewStatus,
    QualityRole,
    QualityUsage,
    QualityVerdict,
    X1QualityMode,
    load_x1_quality_config,
    run_concurrent_quality_reviews,
)


def _packet() -> dict[str, Any]:
    return {
        "packet_digest": "a" * 64,
        "approved": [
            {
                "record": {
                    "record_id": "record-1",
                    "evidence_ids": ["evidence-1"],
                }
            }
        ],
        "candidate": [],
        "external_public": [],
    }


class FakeDelayQualityProvider:
    provider_id = "google-delay-double"

    def __init__(self, factory: "FakeDelayQualityFactory", model_policy: QualityModelPolicy) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        if self.factory.sleep_sec > 0:
            time.sleep(self.factory.sleep_sec)
        self.factory.calls.append((self.model_policy.model, request.role))
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
            usage=QualityUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            latency_ms=10.0,
        )


class FakeDelayQualityFactory:
    def __init__(self, sleep_sec: float = 0.0) -> None:
        self.sleep_sec = sleep_sec
        self.calls: list[tuple[str, QualityRole]] = []

    def __call__(self, model_policy: QualityModelPolicy):
        return FakeDelayQualityProvider(self, model_policy)


class ConcurrentProviderCallsUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(
            load_x1_quality_config(),
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        self.answer = {
            "query_type": "cross_workstream_synthesis",
            "summary": "Synthesized recommendations.",
            "rationale": "Clear and grounded in evidence.",
        }
        self.packet = _packet()
        self.controls = {"status": "PASS", "checks": {"citations_resolve": True}}

    def test_concurrent_quality_reviews_execution(self) -> None:
        factory = FakeDelayQualityFactory(sleep_sec=0.0)
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=self.config,
            assurance_profile="enhanced",
            question="Analyze architecture options.",
            answer=self.answer,
            evidence_packet=self.packet,
            challenger_controls=self.controls,
            judge_controls=self.controls,
            provider_factory=factory,
            judge_prerequisite_passed=True,
        )
        self.assertEqual(QualityReviewStatus.PASS, c_rev.status)
        self.assertEqual(QualityReviewStatus.PASS, j_rev.status)
        self.assertEqual(QualityRole.CHALLENGER, c_rev.role)
        self.assertEqual(QualityRole.JUDGE, j_rev.role)
        self.assertEqual(2, len(factory.calls))
        roles = {role for _, role in factory.calls}
        self.assertEqual({QualityRole.CHALLENGER, QualityRole.JUDGE}, roles)

    def test_concurrent_reviews_timing_proves_parallelism(self) -> None:
        # Each call sleeps 0.04s. In parallel, total runtime is ~0.04s-0.07s, whereas serial would be >=0.08s
        factory = FakeDelayQualityFactory(sleep_sec=0.04)
        t0 = time.monotonic()
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=self.config,
            assurance_profile="enhanced",
            question="Analyze architecture options.",
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
        self.assertLess(elapsed, 0.075, f"Execution took {elapsed:.3f}s, indicating lack of parallelism")

    def test_early_exit_clean_l1_pass(self) -> None:
        factory = FakeDelayQualityFactory(sleep_sec=0.0)
        claim_map = {
            "claims": [
                {"claim_id": "c1", "status": "grounded"},
                {"claim_id": "c2", "status": "grounded"},
            ]
        }
        l1_review = {"verdict": L1ReviewVerdict.SUFFICIENT.value}
        judge_rev = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=self.config,
            assurance_profile="standard",
            question="Summarize decisions.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.controls,
            challenger_review_artifact=None,
            quality_provider_factory=factory,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=claim_map,
        )
        self.assertEqual(QualityReviewStatus.SKIPPED, judge_rev.status)
        self.assertEqual(QualityVerdict.NOT_RUN, judge_rev.verdict)
        self.assertEqual("EARLY_EXIT_CLEAN_L1_PASS", judge_rev.skip_reason)
        self.assertEqual(0, len(factory.calls))

    def test_early_exit_skipped_under_enforced_mode(self) -> None:
        factory = FakeDelayQualityFactory(sleep_sec=0.0)
        enforced_config = replace(self.config, mode=X1QualityMode.ENFORCED)
        claim_map = {"claims": [{"claim_id": "c1", "status": "grounded"}]}
        l1_review = {"verdict": L1ReviewVerdict.SUFFICIENT.value}
        judge_rev = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=enforced_config,
            assurance_profile="standard",
            question="Summarize decisions.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.controls,
            challenger_review_artifact=None,
            quality_provider_factory=factory,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=claim_map,
        )
        self.assertEqual(QualityReviewStatus.PASS, judge_rev.status)
        self.assertEqual(1, len(factory.calls))

    def test_early_exit_skipped_when_ungrounded(self) -> None:
        factory = FakeDelayQualityFactory(sleep_sec=0.0)
        claim_map = {
            "claims": [
                {"claim_id": "c1", "status": "grounded"},
                {"claim_id": "c2", "status": "ungrounded"},
            ]
        }
        l1_review = {"verdict": L1ReviewVerdict.SUFFICIENT.value}
        judge_rev = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=self.config,
            assurance_profile="standard",
            question="Summarize decisions.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.controls,
            challenger_review_artifact=None,
            quality_provider_factory=factory,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=claim_map,
        )
        self.assertEqual(QualityReviewStatus.PASS, judge_rev.status)
        self.assertEqual(1, len(factory.calls))

    def test_deterministic_healing_normalizes_whitespace_and_iso_dates(self) -> None:
        answer = {
            "summary": "Meeting on 2026-10-01 was completed.   \n\n\nNext steps defined.",
            "next_steps": ["Deliver prototype by 2026-11-15   "],
        }
        healed, changes = DeterministicHealer.heal_decision_answer(
            answer, defect_codes=["CLIENT_READINESS_GAP"]
        )
        self.assertIn("10/01/2026", healed["summary"])
        self.assertNotIn("2026-10-01", healed["summary"])
        self.assertNotIn("   ", healed["summary"])
        self.assertIn("11/15/2026", healed["next_steps"][0])
        self.assertTrue(len(changes) > 0)

    def test_execute_content_repair_deterministic_prefilter(self) -> None:
        config = load_l2_config()
        decision = MagicMock()
        decision.assurance_profile = "enhanced"
        decision.provider_egress_allowed = True
        decision.query_type = "cross_workstream_synthesis"
        decision.selected_model = config.enhanced.model
        decision.selected_model_profile = "enhanced"
        original_answer = {
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
            "rationale": "Evidence confirms readiness on 2026-10-01.   \n\n\nAction required.",
            "counterevidence": [],
            "missing_information": [],
            "assumptions": [],
            "proposed_assertions": [],
            "what_would_change_the_conclusion": [],
            "automatic_graph_mutation_count": 0,
        }
        result = execute_content_repair(
            decision=decision,
            config=config,
            question="What is the rollout schedule?",
            plan={"query_type": "cross_workstream_synthesis"},
            evidence_packet=self.packet,
            prompt={"task": "Synthesize"},
            original_answer=original_answer,
            defect_codes=["CLIENT_READINESS_GAP"],
            provider_factory=None,  # No provider factory injected!
            prior_model_calls=1,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(0, result.call_count)
        self.assertEqual("DETERMINISTIC_HEALING_PREFILTER_RESOLVED", result.skip_reason)
        assert result.answer is not None
        self.assertIn("10/01/2026", result.answer["rationale"])


if __name__ == "__main__":
    unittest.main()
