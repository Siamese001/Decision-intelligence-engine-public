"""
tests/end_to_end/test_l2_extraction_economizer_e2e.py

End-to-End tests verifying prompt prefix cache hit rate >= 90%, dynamic reasoning
token budgeting, and schema validation integrity across Stage 02 and Stage 03.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner
from dual_pipeline.transcript_stages_core.graph_executor import L2GraphExtractorExecutor
from dual_pipeline.transcript_stages_core.recap_executor import L2ExecutiveRecapExecutor


class L2ExtractionEconomizerE2ETests(unittest.TestCase):
    """E2E suite verifying prompt prefix cache alignment, reasoning economizer, and schema compliance."""

    def test_01_stage03_prompt_prefix_cache_hit_rate_exceeds_90_percent(self) -> None:
        """Verify that Stage 03 static prefixes across separate calls share >= 90% cache alignment."""
        captured_prefixes = []

        def mock_synthesize(sys_prefix: str, user_content: str) -> str:
            captured_prefixes.append(sys_prefix)
            return json.dumps({
                "nodes": [{"id": "person:alice", "type": "person", "name": "Alice"}],
                "edges": [],
                "evidence_index": {},
                "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            })

        mock_provider = MagicMock()
        mock_provider.synthesize_graph.side_effect = mock_synthesize

        executor = L2GraphExtractorExecutor(mock_provider)

        # Call 1: Meeting A
        meta_a = {"project": "internal_consulting", "archetype": "INTERNAL_SYNC", "meeting_name": "Sync_A"}
        pa_ctx_a = {"metadata": meta_a}
        executor.execute(
            forensic_transcript="**Alice**: We finalized the token budget design.",
            pa_context=pa_ctx_a,
        )

        # Call 2: Meeting B (different transcript, same archetype)
        meta_b = {"project": "internal_consulting", "archetype": "INTERNAL_SYNC", "meeting_name": "Sync_B"}
        pa_ctx_b = {"metadata": meta_b}
        executor.execute(
            forensic_transcript="**Bob**: The caching tier reduces latency by 80%.",
            pa_context=pa_ctx_b,
        )

        self.assertEqual(len(captured_prefixes), 2)
        prefix_a, prefix_b = captured_prefixes[0], captured_prefixes[1]

        # Calculate cache hit rate between the two system prompt prefixes
        hit_rate = PromptPrefixAligner.compute_cache_hit_rate(prefix_a, prefix_b)
        self.assertGreaterEqual(
            hit_rate, 0.90,
            f"Prompt prefix cache hit rate must be >= 90%, got {hit_rate * 100:.1f}%",
        )

    def test_02_dynamic_reasoning_token_budgeting_applied_to_stage03(self) -> None:
        """Verify dynamic reasoning budget ceilings scale with document complexity."""
        mock_provider = MagicMock()
        mock_provider.synthesize_graph.return_value = json.dumps({
            "nodes": [], "edges": [], "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        })

        executor = L2GraphExtractorExecutor(mock_provider)

        # 1. Compact transcript -> Low reasoning budget
        meta_small = {"project": "internal_consulting", "meeting_name": "Quick_Update"}
        executor.execute("Speaker 1: Status update.", pa_context={"metadata": meta_small})

        self.assertIn("reasoning_budget", meta_small)
        self.assertEqual(meta_small["reasoning_budget"]["budget_tokens"], 2048)
        self.assertEqual(meta_small["reasoning_budget"]["reasoning_effort"], "low")
        mock_provider.set_reasoning_budget.assert_called_with(2048)

        # 2. Large complex transcript -> Standard/Deep reasoning budget
        meta_large = {"project": "internal_consulting", "meeting_name": "Deep_Architecture"}
        large_transcript = ("**Architect 1**: System partitioning discussion.\n" * 50) + ("word " * 2500)
        executor.execute(large_transcript, pa_context={"metadata": meta_large})

        self.assertEqual(meta_large["reasoning_budget"]["budget_tokens"], 4096)
        self.assertEqual(meta_large["reasoning_budget"]["reasoning_effort"], "medium")
        mock_provider.set_reasoning_budget.assert_called_with(4096)

    def test_03_stage02_recap_executor_applies_prefix_alignment_and_budgeting(self) -> None:
        """Verify Stage 02 Executive Recap executor applies prefix alignment and reasoning budgeting."""
        mock_provider = MagicMock()
        mock_provider.synthesize_recap.return_value = (
            "# Executive Recap\n## Meeting Objective\nAlign on tokenomics.\n"
            "## Key Discussion Points & Findings\nDiscussion details.\n"
            "## Strategic Decisions & Trade-Offs\nDecision details.\n"
            "## Systemic Risks & Vulnerabilities\nRisk details.\n"
            "## Action & Governance Ledger\nAction details.\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\nDetails."
        )

        executor = L2ExecutiveRecapExecutor(mock_provider)
        meta = {"project": "internal_consulting", "meeting_name": "Recap_Test"}
        recap_out = executor.execute(
            forensic_transcript="**Speaker 1**: Recap discussion text.",
            pa_context={"metadata": meta},
        )

        self.assertIn("reasoning_budget", meta)
        self.assertEqual(meta["reasoning_budget"]["budget_tokens"], 2048)
        mock_provider.set_reasoning_budget.assert_called_with(2048)
        self.assertIn("Executive Recap", recap_out)

    def test_04_output_extraction_schema_maintains_100_percent_pass_rate(self) -> None:
        """Verify that prefix alignment preserves full output extraction schema validation."""
        valid_graph_payload = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev_1": {
                    "statement_id": "ev_1",
                    "exact_text": "Amit Ayer discussed the PartnerAdvisory architecture.",
                    "speaker": "Amit Ayer",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"entity_id": "person:amit_ayer", "entity_type": "person", "evidence_ids": ["ev_1"]}
                ],
                "relationship_signals": [
                    {"source_id": "person:amit_ayer", "target_id": "org:partner_advisory", "relation_type": "MEMBER_OF", "evidence_ids": ["ev_1"]}
                ],
            },
        }

        mock_provider = MagicMock()
        mock_provider.synthesize_graph.return_value = json.dumps(valid_graph_payload)

        executor = L2GraphExtractorExecutor(mock_provider)
        meta = {"project": "internal_consulting", "meeting_name": "Schema_Test", "attendees": ["Amit Ayer"]}
        result_json_str = executor.execute(
            forensic_transcript="**Amit Ayer**: Validated canonical extraction.",
            pa_context={"metadata": meta},
        )

        parsed = json.loads(result_json_str)
        self.assertIn("graph_mutations", parsed)
        self.assertIn("evidence_index", parsed)
        self.assertEqual(parsed.get("schema_version"), "5.7")


if __name__ == "__main__":
    unittest.main()
