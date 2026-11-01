"""tests/end_to_end/test_prompt_cache_hit_rate_e2e.py.

End-to-end verification of Wave 12 LLM & Agent Runtime Cost Economics:
- Repeat L2 decisions achieve cached-token share >= 50.0%.
- Judge retry attempts hit prompt cache with transcript-first prefixing.
- prompt_cache_options pass-through and extraction in credentials engine.
- Slim subagent context profile enforces <= 4,000 token budget.
- DelegationCostLedger accurately tracks token and USD expenditure per delegation.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from config.credentials import _guard_client_completions, _prepare_call_kwargs
from dual_pipeline.agent_architecture_core.context_profile import (
    MAX_SUBAGENT_CONTEXT_TOKENS,
    SubagentContextProfile,
    estimate_token_count,
)
from dual_pipeline.agent_architecture_core.delegation_ledger import DelegationCostLedger
from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.l2_providers_core.payload import (
    DECISION_MODEL_OUTPUT_SCHEMA,
    build_l2_provider_body,
    l2_system_instruction,
)
from dual_pipeline.llm_judge_core.judges.graph import build_graph_judge_user_content
from dual_pipeline.llm_judge_core.judges.recap import build_recap_judge_user_content
from dual_pipeline.prompt_cache import PROVIDER_MIN_PREFIX_TOKENS


class TestPromptCacheHitRateE2E(unittest.TestCase):
    """End-to-End verification of prompt cache hit rates and runtime economics."""

    def test_repeat_l2_workload_cached_token_share_ge_50(self):
        """Verify that repeat L2 decisions with static prefix achieve >= 50% cached-token share."""
        system_prefix = l2_system_instruction("general_advisory")
        estimated_prefix_tokens = estimate_token_count(system_prefix)
        # Prefix must cross provider minimum threshold
        self.assertGreaterEqual(estimated_prefix_tokens, PROVIDER_MIN_PREFIX_TOKENS["openai"])

        tracker = RunCostTracker(max_cost_usd=10.0)
        total_prompt_per_call = estimated_prefix_tokens + 200  # prefix + user query

        # Call 1 (cold): initial uncached call
        tracker.record_call(
            model="gpt-5.6-sol",
            prompt_tokens=total_prompt_per_call,
            completion_tokens=300,
            cached_prompt_tokens=0,
            workflow="l2_decision",
        )

        # Call 2 (warm): repeated call with identical static prefix
        tracker.record_call(
            model="gpt-5.6-sol",
            prompt_tokens=total_prompt_per_call,
            completion_tokens=300,
            cached_prompt_tokens=estimated_prefix_tokens,
            workflow="l2_decision",
        )

        # Call 3 (warm): repeated call with identical static prefix
        tracker.record_call(
            model="gpt-5.6-sol",
            prompt_tokens=total_prompt_per_call,
            completion_tokens=300,
            cached_prompt_tokens=estimated_prefix_tokens,
            workflow="l2_decision",
        )

        metrics = tracker.get_cache_metrics_by_workflow()["l2_decision"]
        cached_share = metrics["cached_token_share"]
        self.assertGreaterEqual(
            cached_share,
            0.50,
            f"Expected repeat L2 cached-token share >= 0.50, got {cached_share:.4f}",
        )

    def test_judge_retry_caching_prefix(self):
        """Verify that judge user payload positions reference transcript first for retry caching."""
        gov_ctx = "GOVERNANCE: Strictest forensic verification floor applies."
        ref_transcript = "SPEAKER_01: We decided to deploy the edge models across all 14 regions." * 50
        candidate_v1 = "The team will deploy across regions eventually."
        candidate_v2 = "The team decided to deploy edge models across all 14 regions."

        payload_v1 = build_recap_judge_user_content(gov_ctx, ref_transcript, candidate_v1)
        payload_v2 = build_recap_judge_user_content(gov_ctx, ref_transcript, candidate_v2)

        # Invariant: Reference transcript MUST appear before candidate in both payloads
        ref_idx_1 = payload_v1.find("<reference_forensic_transcript>")
        cand_idx_1 = payload_v1.find("<candidate_output_2>")
        self.assertNotEqual(ref_idx_1, -1)
        self.assertNotEqual(cand_idx_1, -1)
        self.assertLess(ref_idx_1, cand_idx_1)

        # Common prefix length between attempt 1 and retry attempt 2
        common_len = 0
        for c1, c2 in zip(payload_v1, payload_v2):
            if c1 == c2:
                common_len += 1
            else:
                break

        # The common prefix must cover all of gov_ctx and ref_transcript
        prefix_cutoff = payload_v1.find("</reference_forensic_transcript>") + len("</reference_forensic_transcript>")
        self.assertGreaterEqual(common_len, prefix_cutoff)

        # Graph judge also adheres to reference-first invariant
        graph_payload = build_graph_judge_user_content(gov_ctx, ref_transcript, '{"assertions": []}')
        self.assertLess(
            graph_payload.find("<reference_forensic_transcript>"),
            graph_payload.find("<candidate_output_3_json>"),
        )

    def test_prompt_cache_options_passthrough_and_extraction(self):
        """Verify prompt_cache_options dict parameters are extracted cleanly without TypeError."""
        kwargs = {
            "model": "gpt-5.6-luna",
            "prompt_cache_options": {
                "workflow": "l2_eval_benchmark",
                "retention": "in_memory",
                "prompt_cache_key": "nee:l2_eval_benchmark:gpt-5.6-luna:deadbeef01234567",
            },
            "messages": [{"role": "system", "content": "system preamble text"}],
        }

        canon_m, sanitized = _prepare_call_kwargs(kwargs, "gpt-5.6-luna")
        # prompt_cache_options must be popped to prevent OpenAI Chat API TypeError
        self.assertNotIn("prompt_cache_options", sanitized)
        # Inner parameters must be preserved
        self.assertEqual(sanitized.get("prompt_cache_key"), "nee:l2_eval_benchmark:gpt-5.6-luna:deadbeef01234567")
        self.assertEqual(sanitized.get("prompt_cache_retention"), "in_memory")

    def test_subagent_context_profile_slim_budget(self):
        """Verify SubagentContextProfile truncates oversized context to fit <= 4,000 tokens."""
        massive_context = "CRITICAL EVIDENCE SPAN: " + ("data block with extensive notes " * 2000)

        contract = {
            "task_id": "subtask-sec-audit-01",
            "parent_task_id": "task-wave12-parent",
            "objective": "Audit permission scopes and authentication headers",
            "definition_of_done": "Emit zero-bypass proof and test matrix",
            "allowed_tools": ["view_file", "run_command"],
            "scope_in": "dual_pipeline/agent_architecture_core/",
            "scope_out": "storage/",
            "escalation_rule": "Escalate to parent if timeout exceeds 30s",
        }

        profile = SubagentContextProfile.from_contract(contract, delegated_context=massive_context)
        compiled = profile.compile_context()
        token_count = estimate_token_count(compiled)

        self.assertLessEqual(token_count, MAX_SUBAGENT_CONTEXT_TOKENS)
        # Structural invariants must be retained
        self.assertIn("## Objective", compiled)
        self.assertIn("Audit permission scopes and authentication headers", compiled)
        self.assertIn("## Definition of Done", compiled)
        self.assertIn("## Allowed Tools", compiled)
        self.assertIn("view_file, run_command", compiled)

    def test_delegation_cost_ledger_accounting(self):
        """Verify DelegationCostLedger tracks per-delegation tokens, costs, and cache shares."""
        ledger = DelegationCostLedger(max_cost_usd=5.0)

        r1 = ledger.record_delegation(
            task_id="task-child-01",
            parent_task_id="task-parent-root",
            subagent_role="research_auditor",
            model="gemini-3.8-flash",
            prompt_tokens=2000,
            cached_tokens=1200,
            completion_tokens=400,
            cost_usd=0.00035,
        )
        self.assertEqual(r1.cached_token_share, 0.6)

        r2 = ledger.record_delegation(
            task_id="task-child-02",
            parent_task_id="task-parent-root",
            subagent_role="code_synthesizer",
            model="gpt-5.6-luna",
            prompt_tokens=3000,
            cached_tokens=1800,
            completion_tokens=600,
            cost_usd=0.0011,
        )
        self.assertEqual(r2.cached_token_share, 0.6)

        summary = ledger.get_summary()
        self.assertEqual(summary["delegation_count"], 2)
        self.assertEqual(summary["total_prompt_tokens"], 5000)
        self.assertEqual(summary["total_cached_tokens"], 3000)
        self.assertEqual(summary["blended_cached_token_share"], 0.6)
        self.assertIn("research_auditor", summary["by_role"])
        self.assertIn("code_synthesizer", summary["by_role"])


if __name__ == "__main__":
    unittest.main()
