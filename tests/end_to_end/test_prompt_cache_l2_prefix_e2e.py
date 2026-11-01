"""End-to-end integration tests for L2 Responses prefix stabilization and prompt cache preservation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.ingestion_reasoning_core.advisory import (
    ingestion_system_instruction,
)
from dual_pipeline.l2_policy import CallPurpose, L2Config, ModelPolicy
from dual_pipeline.l2_providers_core.payload import (
    build_l2_provider_body,
    l2_system_instruction,
    l2_user_payload,
)
from dual_pipeline.l2_providers_core.types import L2ProviderRequest


class TestPromptCacheL2PrefixE2E(unittest.TestCase):
    def test_e2e_l2_system_instruction_invariance_across_purposes(self):
        """Verify l2_system_instruction is 100% identical across all CallPurposes to maximize prompt cache hits."""
        p_init = l2_system_instruction(CallPurpose.PRIMARY)
        p_schema = l2_system_instruction(CallPurpose.SCHEMA_REPAIR)
        p_qual = l2_system_instruction(CallPurpose.QUALITY_ESCALATION)
        p_content = l2_system_instruction(CallPurpose.CONTENT_REPAIR)

        self.assertEqual(p_init, p_schema)
        self.assertEqual(p_init, p_qual)
        self.assertEqual(p_init, p_content)

    def test_e2e_ingestion_system_instruction_invariance(self):
        """Verify ingestion_system_instruction is identical across primary and repair calls."""
        p_normal = ingestion_system_instruction("")
        p_repair = ingestion_system_instruction("SCHEMA_REPAIR")

        self.assertEqual(p_normal, p_repair)

    def test_e2e_l2_user_payload_purpose_instructions(self):
        """Verify purpose-specific repair instructions are cleanly situated in user payload."""
        req_schema = L2ProviderRequest(
            purpose=CallPurpose.SCHEMA_REPAIR,
            question="What is the strategic objective?",
            plan={},
            prompt={},
            evidence_packet={},
            validation_errors=("Missing record_id",),
            original_response='{"answer": "incomplete"}',
        )
        payload_schema = l2_user_payload(req_schema)
        self.assertIn("purpose_instruction", payload_schema)
        self.assertIn("Repair only the listed", payload_schema["purpose_instruction"])

        req_content = L2ProviderRequest(
            purpose=CallPurpose.CONTENT_REPAIR,
            question="What is the strategic objective?",
            plan={},
            prompt={},
            evidence_packet={},
            quality_deficiencies=("Ungrounded claim in section 2",),
            original_response='{"answer": "needs revision"}',
        )
        payload_content = l2_user_payload(req_content)
        self.assertIn("purpose_instruction", payload_content)
        self.assertIn("Revise only the supplied answer", payload_content["purpose_instruction"])

    def test_e2e_build_l2_provider_body_cache_key_identical(self):
        """Verify derived cache key remains identical between primary call and quality escalation."""
        config = MagicMock(spec=L2Config)
        config.prompt_cache_enabled = True
        config.output = MagicMock(maximum_output_tokens=2048)

        model_policy = MagicMock(spec=ModelPolicy)
        model_policy.model = "gpt-5.6-luna"
        model_policy.reasoning_effort = "medium"

        evidence_packet = {
            "approved": [],
            "candidate": [],
            "external_public": [],
        }

        req1 = L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question="Summarize decisions.",
            plan={"query_type": "strategic", "prompt_blueprint": "v1"},
            prompt={"template_version": "v1"},
            evidence_packet=evidence_packet,
        )

        req2 = L2ProviderRequest(
            purpose=CallPurpose.QUALITY_ESCALATION,
            question="Summarize decisions.",
            plan={"query_type": "strategic", "prompt_blueprint": "v1"},
            prompt={"template_version": "v1"},
            evidence_packet=evidence_packet,
            quality_deficiencies=("Citation missing",),
        )

        body1 = build_l2_provider_body(config=config, model_policy=model_policy, request=req1)
        body2 = build_l2_provider_body(config=config, model_policy=model_policy, request=req2)

        self.assertIn("prompt_cache_key", body1)
        self.assertIn("prompt_cache_key", body2)
        # Invariant: Cache key is identical across purposes!
        self.assertEqual(body1["prompt_cache_key"], body2["prompt_cache_key"])


if __name__ == "__main__":
    unittest.main()
