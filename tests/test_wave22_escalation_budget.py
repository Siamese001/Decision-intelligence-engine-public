#!/usr/bin/env python3
"""Wave 22 unit tests: Bounded Multi-Turn Escalation Budget Expansion (1 -> 2 Calls)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.core import (
    digest,
    read_json,
)
from dual_pipeline.l2 import (
    CallPurpose,
    L2Disposition,
    L2ProviderError,
    ProviderFailureCode,
    QueryAssuranceProfile,
    _CallBudget,
    _load_model_policy,
    decide_l2_execution,
    execute_conditional_l2,
    load_l2_config,
)
from dual_pipeline.query import assemble_prompt
from tests.l2_fakes import (
    FakeProviderFactory,
    schema_valid_quality_failure,
)

ROOT = Path(__file__).resolve().parent.parent


class Wave22EscalationBudgetTests(unittest.TestCase):
    def test_model_policy_escalation_cap_validation(self) -> None:
        # Standard profile allows only 0
        standard_policy_val = {
            "provider": "openai",
            "model_family": "gpt-5.6",
            "model": "gpt-5.6-luna",
            "assurance_profile": "standard",
            "reasoning_effort": "xhigh",
            "maximum_primary_calls_per_run": 1,
            "maximum_repair_calls_per_run": 0,
            "maximum_escalation_calls_per_run": 0,
            "pricing_usd_per_million_tokens": {
                "input": 2.5,
                "cached_input": 1.25,
                "output": 10.0,
            },
        }
        pol_std = _load_model_policy(
            standard_policy_val,
            expected_profile=QueryAssuranceProfile.STANDARD,
            label="test_standard",
        )
        self.assertEqual(pol_std.maximum_escalation_calls_per_run, 0)

        # Enhanced profile allows 1 or 2
        enhanced_policy_val = dict(standard_policy_val)
        enhanced_policy_val["assurance_profile"] = "enhanced"
        enhanced_policy_val["reasoning_effort"] = "max"
        enhanced_policy_val["maximum_repair_calls_per_run"] = 1
        enhanced_policy_val["maximum_escalation_calls_per_run"] = 2
        pol_enh = _load_model_policy(
            enhanced_policy_val,
            expected_profile=QueryAssuranceProfile.ENHANCED,
            label="test_enhanced",
        )
        self.assertEqual(pol_enh.maximum_escalation_calls_per_run, 2)

        # Disallow cap > 2
        invalid_val = dict(enhanced_policy_val)
        invalid_val["maximum_escalation_calls_per_run"] = 3
        with self.assertRaises(ValueError):
            _load_model_policy(
                invalid_val,
                expected_profile=QueryAssuranceProfile.ENHANCED,
                label="test_invalid",
            )

    def test_call_budget_consumption_and_cap_enforcement(self) -> None:
        decision = SimpleNamespace(
            maximum_primary_calls=1,
            maximum_repair_calls=1,
            maximum_escalation_calls=2,
        )
        budget = _CallBudget(decision=decision)  # type: ignore[arg-type]

        # First primary call
        self.assertEqual(budget.consume(CallPurpose.PRIMARY), 1)
        self.assertEqual(budget.primary, 1)

        # First escalation call
        self.assertEqual(budget.consume(CallPurpose.QUALITY_ESCALATION), 2)
        self.assertEqual(budget.escalation, 1)

        # Second escalation call
        self.assertEqual(budget.consume(CallPurpose.QUALITY_ESCALATION), 3)
        self.assertEqual(budget.escalation, 2)

        # Third escalation call must raise CALL_CAP_EXHAUSTED
        with self.assertRaises(L2ProviderError) as cm:
            budget.consume(CallPurpose.QUALITY_ESCALATION)
        self.assertEqual(cm.exception.code, ProviderFailureCode.CALL_CAP_EXHAUSTED)


class Wave22MultiTurnEscalationExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        run_dir = (
            ROOT
            / ".local"
            / "main"
            / "runtime-bge-m3-sherpa-strategic-20260729"
            / "runs"
            / "run-query-0690c033a9f378aa5f62b01cca"
        )
        if not run_dir.exists():
            self.skipTest(f"Run fixture directory not found: {run_dir}")
        self.req = read_json(run_dir / "validated_request.json")
        self.plan = read_json(run_dir / "query_plan.json")
        self.route = read_json(run_dir / "route_decision.json")
        self.packet = read_json(run_dir / "evidence_packet.json")

        # Load policy with enhanced maximum_escalation_calls_per_run: 2
        raw_policy = read_json(ROOT / "requirements" / "l2-policy.json")
        raw_policy["enhanced"]["maximum_escalation_calls_per_run"] = 2
        self.temp_policy = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(raw_policy, self.temp_policy)
        self.temp_policy.close()
        self.config = load_l2_config(Path(self.temp_policy.name))

        # Set enhanced assurance profile and rebuild plan digest
        self.plan["assurance_profile"] = "enhanced"
        self.plan["canonical_digest"]["value"] = digest(
            {k: v for k, v in self.plan.items() if k != "canonical_digest"}
        )

        # Re-assemble prompt matching enhanced assurance
        self.prompt = assemble_prompt(
            self.req["normalized_question"],
            self.plan,
            self.route,
            self.packet,
        )

        # Make deterministic execution decision
        self.decision = decide_l2_execution(
            validated_request=self.req,
            plan=self.plan,
            route=self.route,
            evidence_packet=self.packet,
            config=self.config,
            prompt=self.prompt,
        )

    def tearDown(self) -> None:
        if os.path.exists(self.temp_policy.name):
            os.unlink(self.temp_policy.name)

    def test_multi_turn_escalation_success_on_second_turn(self) -> None:
        # Primary call fails semantic quality.
        # Turn 1 escalation fails semantic quality.
        # Turn 2 escalation succeeds.
        factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [schema_valid_quality_failure],
                CallPurpose.QUALITY_ESCALATION: [schema_valid_quality_failure],
            }
        )
        result = execute_conditional_l2(
            decision=self.decision,
            config=self.config,
            validated_request=self.req,
            route=self.route,
            question=self.req["normalized_question"],
            plan=self.plan,
            evidence_packet=self.packet,
            prompt=self.prompt,
            provider_factory=factory,
        )
        self.assertEqual(L2Disposition.ESCALATED, result.disposition)
        self.assertEqual(1, result.primary_call_count)
        self.assertEqual(0, result.schema_repair_call_count)
        self.assertEqual(2, result.quality_escalation_call_count)
        self.assertEqual(3, result.model_call_count)
        self.assertEqual(
            ["PRIMARY", "QUALITY_ESCALATION", "QUALITY_ESCALATION"],
            factory.call_purposes,
        )

    def test_multi_turn_escalation_exhaustion_blocks(self) -> None:
        # Primary call fails semantic quality.
        # Both turn 1 and turn 2 escalations fail semantic quality.
        # Must fail closed without attempting a 3rd escalation call.
        factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [schema_valid_quality_failure],
                CallPurpose.QUALITY_ESCALATION: [
                    schema_valid_quality_failure,
                    schema_valid_quality_failure,
                ],
            }
        )
        result = execute_conditional_l2(
            decision=self.decision,
            config=self.config,
            validated_request=self.req,
            route=self.route,
            question=self.req["normalized_question"],
            plan=self.plan,
            evidence_packet=self.packet,
            prompt=self.prompt,
            provider_factory=factory,
        )
        self.assertEqual(L2Disposition.FAILED, result.disposition)
        self.assertEqual(
            ProviderFailureCode.SEMANTIC_QUALITY_GATE_FAILED,
            result.provider_failure_code,
        )
        self.assertEqual(1, result.primary_call_count)
        self.assertEqual(0, result.schema_repair_call_count)
        self.assertEqual(2, result.quality_escalation_call_count)
        self.assertEqual(3, result.model_call_count)
        self.assertEqual(
            ["PRIMARY", "QUALITY_ESCALATION", "QUALITY_ESCALATION"],
            factory.call_purposes,
        )


if __name__ == "__main__":
    unittest.main()
