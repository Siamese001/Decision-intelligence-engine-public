"""Focused acceptance for bounded runtime challenger and judge controls."""
from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from dual_pipeline.core import digest
from dual_pipeline.x1_quality import (
    QualityProviderRequest,
    QualityProviderResponse,
    QualityReviewStatus,
    QualityRole,
    QualityUsage,
    QualityVerdict,
    X1QualityMode,
    build_quality_provider_body,
    load_x1_quality_config,
    run_quality_review,
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


class FakeQualityProvider:
    provider_id = "google-test-double"

    def __init__(self, factory: "FakeQualityFactory", model_policy) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        self.factory.requests.append((self.model_policy, request))
        output = self.factory.outputs.pop(0)
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=self.model_policy.model,
            usage=QualityUsage(input_tokens=24, output_tokens=12, total_tokens=36),
            latency_ms=7.5,
        )


class FakeQualityFactory:
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = list(outputs)
        self.requests: list[tuple[Any, QualityProviderRequest]] = []

    def __call__(self, model_policy):
        return FakeQualityProvider(self, model_policy)


class X1QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_x1_quality_config()
        self.answer = {"query_type": "cross_workstream_synthesis", "rationale": "Clear."}
        self.packet = _packet()
        self.x1 = {"status": "PASS", "checks": {"citations_resolve": True}}

    def test_default_policy_is_off_and_pins_the_cost_quality_split(self) -> None:
        self.assertEqual(X1QualityMode.OFF, self.config.mode)
        self.assertFalse(self.config.provider_egress_allowed)
        self.assertEqual("gemini-3.8-flash", self.config.challenger.model)
        self.assertEqual("medium", self.config.challenger.reasoning_effort)
        self.assertEqual("gemini-3.8-flash", self.config.judge.model)
        self.assertEqual("high", self.config.judge.reasoning_effort)
        self.assertEqual(
            {"standard", "enhanced"},
            self.config.eligible_assurance_profiles,
        )
        self.assertEqual(
            {"enhanced"},
            self.config.content_repair.eligible_assurance_profiles,
        )

    def test_off_mode_never_initializes_a_provider(self) -> None:
        factory = FakeQualityFactory([])
        review = run_quality_review(
            role=QualityRole.CHALLENGER,
            config=self.config,
            assurance_profile="standard",
            question="Explain this architecture in client language.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.x1,
            provider_factory=factory,
        )
        self.assertEqual(QualityReviewStatus.SKIPPED, review.status)
        self.assertEqual(QualityVerdict.NOT_RUN, review.verdict)
        self.assertEqual("QUALITY_MODE_OFF", review.skip_reason)
        self.assertEqual([], factory.requests)

    def test_challenger_is_advisory_and_evidence_bound_when_enabled(self) -> None:
        config = replace(
            self.config,
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        factory = FakeQualityFactory(
            [
                {
                    "verdict": "REPAIRABLE_FAIL",
                    "defect_codes": ["CLIENT_JARGON"],
                    "evidence_refs": ["record-1"],
                    "confidence": "high",
                }
            ]
        )
        review = run_quality_review(
            role=QualityRole.CHALLENGER,
            config=config,
            assurance_profile="standard",
            question="Explain this architecture in client language.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.x1,
            provider_factory=factory,
        )
        self.assertEqual(QualityReviewStatus.PASS, review.status)
        self.assertEqual(QualityVerdict.REPAIRABLE_FAIL, review.verdict)
        self.assertEqual(("CLIENT_JARGON",), review.defect_codes)
        self.assertEqual(("record-1",), review.evidence_refs)
        self.assertEqual("gemini-3.8-flash", factory.requests[0][0].model)
        self.assertEqual("medium", factory.requests[0][0].reasoning_effort)
        self.assertEqual(digest(self.answer), review.input_bindings["answer_digest"])
        self.assertEqual(self.packet["packet_digest"], review.input_bindings["evidence_packet_digest"])

    def test_unknown_evidence_reference_fails_closed(self) -> None:
        config = replace(
            self.config,
            mode=X1QualityMode.ENFORCED,
            provider_egress_allowed=True,
        )
        factory = FakeQualityFactory(
            [
                {
                    "verdict": "ESCALATE_HITL",
                    "defect_codes": ["EVIDENCE_GROUNDING_GAP"],
                    "evidence_refs": ["outside-sealed-packet"],
                    "confidence": "medium",
                }
            ]
        )
        review = run_quality_review(
            role=QualityRole.JUDGE,
            config=config,
            assurance_profile="enhanced",
            question="Give the executive architecture recommendation.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.x1,
            provider_factory=factory,
        )
        self.assertEqual(QualityReviewStatus.FAIL, review.status)
        self.assertEqual(QualityVerdict.ABSTAIN, review.verdict)
        self.assertEqual("EVIDENCE_BINDING_INVALID", review.failure_code)

    def test_request_body_has_no_tools_and_respects_the_bounded_schema(self) -> None:
        config = replace(self.config, mode=X1QualityMode.SHADOW, provider_egress_allowed=True)
        request = QualityProviderRequest(
            role=QualityRole.JUDGE,
            question="Explain the future state.",
            answer=self.answer,
            evidence_packet=self.packet,
            deterministic_controls=self.x1,
            challenger_review=None,
            input_bindings={"answer_digest": digest(self.answer)},
        )
        body = build_quality_provider_body(
            config=config,
            model_policy=config.judge,
            request=request,
        )
        self.assertNotIn("tools", body)
        self.assertEqual("application/json", body["generationConfig"]["responseMimeType"])
        self.assertEqual("HIGH", body["generationConfig"]["thinkingConfig"]["thinkingLevel"])
        self.assertEqual("gemini-3.8-flash", config.judge.model)


if __name__ == "__main__":
    unittest.main()
