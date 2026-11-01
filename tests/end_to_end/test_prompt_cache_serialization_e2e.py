"""End-to-end integration tests for prompt serialization determinism and prefix isolation."""
from __future__ import annotations

import unittest

from dual_pipeline.batch_core.adapter import format_batch_jsonl
from dual_pipeline.l2_healer_service import HealingScope, L2HealingRequest, UnifiedL2HealerService
from dual_pipeline.token_optimization.profiler import TokenProfiler
from dual_pipeline.token_optimization.schemas import PromptComponents


class TestPromptCacheSerializationE2E(unittest.TestCase):
    def test_e2e_compact_json_key_determinism(self):
        """Verify dicts with different key insertion orders produce identical serialized bytes."""
        d1 = {"zebra": 1, "alpha": 2, "beta": 3, "nested": {"z": 10, "a": 20}}
        d2 = {"beta": 3, "alpha": 2, "zebra": 1, "nested": {"a": 20, "z": 10}}

        s1 = TokenProfiler.compact_json(d1)
        s2 = TokenProfiler.compact_json(d2)

        self.assertEqual(s1, s2)
        self.assertEqual(s1, '{"alpha":2,"beta":3,"nested":{"a":20,"z":10},"zebra":1}')

    def test_e2e_prompt_components_prefix_invariance(self):
        """Verify build_messages isolates dynamic system suffix into user payload."""
        components = PromptComponents(
            static_system_prefix="System Core Invariant Instructions",
            dynamic_system_suffix="Execution Run ID: run-12345\nTimestamp: 2026-10-03",
            user_payload="Process transcript body.",
        )

        messages = components.build_messages()

        self.assertEqual(len(messages), 2)
        # Invariant 1: System message contains ONLY static prefix
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "System Core Invariant Instructions")
        self.assertNotIn("run-12345", messages[0]["content"])

        # Invariant 2: Dynamic system suffix relocated cleanly to user payload
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Execution Run ID: run-12345", messages[1]["content"])
        self.assertIn("Process transcript body.", messages[1]["content"])

    def test_e2e_batch_adapter_jsonl_determinism(self):
        """Verify format_batch_jsonl serializes requests deterministically."""
        req1 = {"url": "/v1/chat/completions", "method": "POST", "body": {"b": 2, "a": 1}}
        req2 = {"method": "POST", "url": "/v1/chat/completions", "body": {"a": 1, "b": 2}}

        l1 = format_batch_jsonl([req1])
        l2 = format_batch_jsonl([req2])

        self.assertEqual(l1, l2)

    def test_e2e_healer_prompt_sorting(self):
        """Verify UnifiedL2HealerService builds user prompt with sorted failure reasons and artifacts."""
        service = UnifiedL2HealerService(mock_mode=True)
        req1 = L2HealingRequest(
            scope=HealingScope.PIPELINE_B_PROJECTION,
            failure_reasons={"zebra_error", "alpha_error", "beta_error"},
            allowed_entity_ids={"person:alice", "person:bob"},
            allowed_evidence_ids={"ev-1", "ev-2"},
            candidate_artifact={"z": 1, "a": 2},
        )
        req2 = L2HealingRequest(
            scope=HealingScope.PIPELINE_B_PROJECTION,
            failure_reasons={"alpha_error", "beta_error", "zebra_error"},
            allowed_entity_ids={"person:bob", "person:alice"},
            allowed_evidence_ids={"ev-2", "ev-1"},
            candidate_artifact={"a": 2, "z": 1},
        )

        prompt1 = service._build_user_prompt(req1)
        prompt2 = service._build_user_prompt(req2)

        self.assertEqual(prompt1, prompt2)
        # Check alphabetical sorting
        self.assertIn('"alpha_error",\n  "beta_error",\n  "zebra_error"', prompt1)


if __name__ == "__main__":
    unittest.main()
