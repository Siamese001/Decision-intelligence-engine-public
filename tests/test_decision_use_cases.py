#!/usr/bin/env python3
"""Provider-free regression tests for W15 decision-use-case readiness."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.decision_use_cases import evaluate_use_case, validate_use_case
from dual_pipeline.strategic_evidence import build_evidence_packet
from dual_pipeline.temporal_observations import build_observation
from tests.decision_intelligence_support import authorization, monitor_for

ROOT = Path(__file__).resolve().parents[1]


def _observation(dimension: str, **overrides):
    values = {
        "scope_id": "nea-ai-pdlc",
        "dimension": dimension,
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": [f"assertion-{dimension}"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _packet(**overrides):
    values = {
        "claim_id": "claim-ai-pdlc-pov",
        "evidence_role": "supporting",
        "content": {"finding": "AI-PDLC opportunity"},
        "source_assertion_ids": ["assertion-claim-1"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "expires_at": "2026-09-08T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "review_state": "REVIEWED",
        "reviewed_by": "Amit Ayer",
        "reviewed_at": "2026-08-09T00:00:00Z",
        "falsification_tests": [{"test_id": "test-1", "question": "Does it replicate?"}],
    }
    values.update(overrides)
    return build_evidence_packet(**values)


def _use_case(**overrides):
    values = {
        "use_case_id": "ai-pdlc-pov",
        "question": "What should PartnerAdvisory's POV be around AI-PDLC for NEA?",
        "scope_id": "nea-ai-pdlc",
        "audience": "internal_consulting",
        "required_dimensions": ["delivery", "readiness"],
        "claim_ids": ["claim-ai-pdlc-pov"],
        "candidate_answer_sections": ["current_status", "evidence", "gaps", "candidate_pov"],
    }
    values.update(overrides)
    return values


def _evaluate(**overrides):
    values = {
        "use_case": _use_case(),
        "as_of": "2026-08-10T00:00:00Z",
        "known_at": "2026-08-10T00:00:00Z",
        "authorization_context": authorization(),
        "reference_monitor": monitor_for(
            [
                "assertion-delivery",
                "assertion-delivery-2",
                "assertion-readiness",
                "assertion-capacity",
                "assertion-adoption",
                "assertion-value",
                "assertion-claim-1",
                "assertion-claim-2",
            ]
        ),
        "observations": [_observation("delivery"), _observation("readiness")],
        "evidence_packets": [
            _packet(),
            _packet(
                evidence_id="counter-1",
                evidence_role="counterevidence",
                source_assertion_ids=["assertion-claim-2"],
            ),
        ],
    }
    values.update(overrides)
    return evaluate_use_case(**values)


class DecisionUseCaseTests(unittest.TestCase):
    def test_ai_pdlc_pov_is_candidate_only_when_evidence_is_ready(self) -> None:
        result = _evaluate()
        self.assertEqual("CANDIDATE_ANSWER_READY", result["state"])
        self.assertEqual("CANDIDATE_ONLY", result["answer_posture"])
        self.assertIn("candidate_pov", result["candidate_answer_sections"])

    def test_support_only_metrics_question_exposes_gap(self) -> None:
        use_case = _use_case(
            use_case_id="ai-productivity-metrics",
            question="Which AI productivity metrics are candidates?",
            scope_id="nea-ai-productivity",
            required_dimensions=["capacity", "adoption", "value"],
            claim_ids=["claim-ai-productivity-metrics"],
            candidate_answer_sections=["candidate_metrics", "gaps"],
        )
        observations = [
            _observation("capacity", scope_id="nea-ai-productivity"),
            _observation("adoption", scope_id="nea-ai-productivity"),
            _observation("value", scope_id="nea-ai-productivity"),
        ]
        result = _evaluate(
            use_case=use_case,
            observations=observations,
            evidence_packets=[_packet(claim_id="claim-ai-productivity-metrics")],
        )
        self.assertEqual("CANDIDATE_ANSWER_WITH_GAPS", result["state"])
        self.assertIn("counterevidence_not_found", result["decision_context"]["gaps"])

    def test_missing_required_status_blocks_the_answer(self) -> None:
        result = _evaluate(observations=[_observation("delivery")])
        self.assertEqual("BLOCKED_STALE_OR_UNKNOWN", result["state"])
        self.assertEqual("NO_ANSWER", result["answer_posture"])

    def test_conflict_requires_hitl(self) -> None:
        result = _evaluate(
            observations=[
                _observation("delivery", observation_id="delivery-left"),
                _observation(
                    "delivery",
                    observation_id="delivery-right",
                    value={"status": "at_risk"},
                    source_assertion_ids=["assertion-delivery-2"],
                ),
                _observation("readiness"),
            ]
        )
        self.assertEqual("BLOCKED_HITL_REQUIRED", result["state"])
        self.assertEqual("NO_ANSWER", result["answer_posture"])

    def test_unreviewed_evidence_requires_hitl(self) -> None:
        result = _evaluate(
            evidence_packets=[
                _packet(review_state="UNREVIEWED", reviewed_by=None, reviewed_at=None)
            ]
        )
        self.assertEqual("BLOCKED_HITL_REQUIRED", result["state"])

    def test_undeclared_answer_section_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_use_case(
                _use_case(candidate_answer_sections=["candidate_pov", "candidate_pov"])
            )

    def test_priority_portfolio_contains_the_three_question_contracts(self) -> None:
        portfolio_path = ROOT / "governance/decision_intelligence/W15_USE_CASE_PORTFOLIO.json"
        portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
        use_cases = portfolio["use_cases"]
        self.assertEqual(
            {"ai-pdlc-pov", "ai-productivity-metrics", "agentic-readiness"},
            {item["use_case_id"] for item in use_cases},
        )
        for item in use_cases:
            validate_use_case(item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
