"""Adversarial and boundary test suite for L2 Responses prefix stabilization and repair cascades."""
from __future__ import annotations

import unittest

from dual_pipeline.l2_policy import CallPurpose
from dual_pipeline.l2_providers_core.payload import (
    l2_system_instruction,
    l2_user_payload,
)
from dual_pipeline.l2_providers_core.types import L2ProviderRequest


class TestPromptCacheL2PrefixAdversarial(unittest.TestCase):
    def test_adv_unsupported_purpose_fallback_preserves_prefix(self):
        """Boundary Test: Passing None or boundary CallPurpose values returns the invariant base prefix."""
        base = l2_system_instruction(CallPurpose.PRIMARY)

        # None purpose boundary
        res_none = l2_system_instruction(None)
        self.assertEqual(res_none, base)

    def test_adv_payload_mutation_isolation(self):
        """Mutation Resistance Test: Modifying returned user payload dictionary does not leak state across calls."""
        req1 = L2ProviderRequest(
            purpose=CallPurpose.SCHEMA_REPAIR,
            question="Question 1",
            plan={},
            prompt={},
            evidence_packet={},
            validation_errors=("Error 1",),
            original_response="{}",
        )
        p1 = l2_user_payload(req1)
        p1["purpose_instruction"] = "CORRUPTED INSTRUCTION"
        p1["validation_errors"].append("INJECTED_ERROR")

        req2 = L2ProviderRequest(
            purpose=CallPurpose.SCHEMA_REPAIR,
            question="Question 2",
            plan={},
            prompt={},
            evidence_packet={},
            validation_errors=("Error 2",),
            original_response="{}",
        )
        p2 = l2_user_payload(req2)

        self.assertNotIn("CORRUPTED", p2["purpose_instruction"])
        self.assertEqual(p2["validation_errors"], ["Error 2"])

    def test_adv_multi_stage_repair_cascade_stability(self):
        """Cascade Test: Simulating multiple sequential repair passes maintains identical system instruction."""
        purposes = [
            CallPurpose.PRIMARY,
            CallPurpose.SCHEMA_REPAIR,
            CallPurpose.CONTENT_REPAIR,
            CallPurpose.QUALITY_ESCALATION,
            CallPurpose.CONTENT_REPAIR,
        ]

        instructions = [l2_system_instruction(p) for p in purposes]
        # Invariant: Every single step in the repair cascade has the identical system instruction
        self.assertEqual(len(set(instructions)), 1)
        self.assertIn("Preserve approved, candidate, and external-public evidence lanes", instructions[0])


if __name__ == "__main__":
    unittest.main()
