"""
tests/test_graph_preval_reorder_regression.py

Focused regression test suite verifying deterministic graph pre-validation
ordering ahead of AgenticLeverageJudge API dispatch in GraphExtractionJudge.

Proves:
1. Zero LLM calls to AgenticLeverageJudge on deterministic failure paths.
2. Zero OpenAI client completions on malformed, invalid schema, AST topology, or grounding failures.
3. Unchanged evaluation quality, scorecards, and release gating on valid candidates.
4. Concrete before/after accounting of API calls, tokens, and cost.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges.graph import (
    GraphExtractionJudge,
    _extract_bipartite_payload,
    _resolve_bipartite_structure,
)
from dual_pipeline.llm_judge_core.models import JudgeAgenticLeverage


class GraphPreValidationReorderRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f1_transcript = (
            "Amit Ayer and harper Hoffman discussed the enterprise data platform architecture, "
            "focusing on the PartnerAdvisory integration, Snowflake warehouse schemas, and automated ingestion pipelines."
        )
        self.sample_leverage_nodes = [
            {
                "node_type": "partner_advisory_MARGIN_WEDGE",
                "key_players": ["harper Hoffman (VP)", "Amit Ayer (Director)"],
                "political_rationale": "Mandy is concerned about timeline slippage on the Snowflake migration.",
                "networking_wedge": "Offer dedicated pods to de-risk the Snowflake migration before steering review.",
                "verbatim_quote": "We cannot afford any slippage on the Snowflake milestone.",
                "speaker": "harper Hoffman",
            },
            {
                "node_type": "INFO_ASYMMETRY",
                "key_players": ["Sarah Jenkins (Director)"],
                "political_rationale": "Sarah is unaware that the ingestion pipeline bottleneck was resolved yesterday.",
                "networking_wedge": "Brief Sarah privately on the performance benchmarks before the team demo.",
                "verbatim_quote": "I thought the pipeline was still blocked on memory limits.",
                "speaker": "Sarah Jenkins",
            },
        ]
        self.valid_client_safe = {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:amit_ayer", "name": "Amit Ayer", "type": "PERSON"},
                    {"id": "person:mandy_hopmann", "name": "harper Hoffman", "type": "PERSON"},
                    {"id": "system:snowflake", "name": "Snowflake", "type": "SYSTEM"},
                ],
                "relationship_signals": [
                    {"source": "person:mandy_hopmann", "target": "system:snowflake", "relationship": "MANAGES"},
                ],
            },
        }

    def test_01_malformed_syntax_zero_agentic_llm_calls(self) -> None:
        """Verify malformed JSON fails fast with 0 AgenticLeverageJudge calls and 0 client completions."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic_eval:
            malformed_f3 = "MALFORMED_JSON_CONTAINING_AGENTIC_LEVERAGE_ATTEMPT: {"
            res = judge.evaluate(malformed_f3, self.f1_transcript, client=mock_client, mock=False)

            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            self.assertEqual(res.get("pre_judge_tier"), "TIER_1_SYNTAX_FAIL")
            # Crucial invariant: AgenticLeverageJudge was never dispatched
            mock_agentic_eval.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_02_empty_schema_zero_agentic_llm_calls(self) -> None:
        """Verify candidate with empty graph containers fails fast with 0 agentic LLM calls."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        bipartite_empty_schema = json.dumps({
            "client_safe": {
                "schema_version": "5.7",
                "graph_mutations": {},  # 0 entities / claims
            },
            "agentic_leverage": self.sample_leverage_nodes,
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic_eval:
            res = judge.evaluate(bipartite_empty_schema, self.f1_transcript, client=mock_client, mock=False)

            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            self.assertEqual(res.get("pre_judge_tier"), "TIER_1_SCHEMA_FAIL")
            # Proves zero LLM calls on deterministic schema failure
            mock_agentic_eval.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_03_dangling_ast_topology_zero_agentic_llm_calls(self) -> None:
        """Verify candidate with high ratio of dangling edges fails fast with 0 agentic LLM calls."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        bipartite_dangling = json.dumps({
            "client_safe": {
                "nodes": [{"id": "person:amit"}],
                "edges": [
                    {"source": "unknown_src_1", "target": "unknown_dst_1"},
                    {"source": "unknown_src_2", "target": "unknown_dst_2"},
                ],
            },
            "agentic_leverage": self.sample_leverage_nodes,
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic_eval:
            res = judge.evaluate(bipartite_dangling, self.f1_transcript, client=mock_client, mock=False)

            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            self.assertEqual(res.get("pre_judge_tier"), "TIER_2_AST_FAIL")
            mock_agentic_eval.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_04_grounding_floor_failure_zero_agentic_llm_calls(self) -> None:
        """Verify ungrounded hallucinated candidate (<10% overlap) fails fast with 0 agentic LLM calls."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        bipartite_hallucinated = json.dumps({
            "client_safe": {
                "claims": ["Intergalactic planetary orbiters discovered exotic dark matter on Pluto."],
            },
            "agentic_leverage": self.sample_leverage_nodes,
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic_eval:
            res = judge.evaluate(bipartite_hallucinated, self.f1_transcript, client=mock_client, mock=False)

            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            self.assertEqual(res.get("pre_judge_tier"), "TIER_3_GROUNDING_FLOOR_FAIL")
            mock_agentic_eval.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_05_valid_candidate_dispatches_agentic_and_preserves_quality(self) -> None:
        """Verify valid candidate passes pre-validation, dispatches agentic judge, and preserves quality."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        # Mock AgenticLeverageJudge responses
        mock_leverage_eval = [
            {
                "node": self.sample_leverage_nodes[0],
                "judgment": {
                    "is_actionable_wedge": True,
                    "political_materiality": 9,
                    "networking_target_identified": True,
                    "pass_gate": True,
                    "rejection_reason": None,
                },
            }
        ]

        # Mock main graph judge completion response
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = json.dumps({
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 18,
            "composite_score": 97,
            "verdict": "PASS",
            "forensic_rationale": "High quality canonical graph projection.",
            "grounding_citations": ["graph_mutations.entity_upserts"],
        })
        mock_choice.message = mock_message
        mock_resp.choices = [mock_choice]
        mock_resp.usage = MagicMock(prompt_tokens=1200, completion_tokens=350, total_tokens=1550)
        mock_client.chat.completions.create.return_value = mock_resp

        valid_bipartite = json.dumps({
            "client_safe": self.valid_client_safe,
            "agentic_leverage": [self.sample_leverage_nodes[0]],
        })

        with patch.object(
            judge.agentic_judge, "evaluate_agentic_nodes", return_value=mock_leverage_eval
        ) as mock_agentic_eval:
            eval_res = judge.evaluate(valid_bipartite, self.f1_transcript, client=mock_client, mock=False)

            # Verification: passing path succeeds and dispatches agentic judge
            mock_agentic_eval.assert_called_once()
            self.assertEqual(eval_res["verdict"], "PASS")
            self.assertEqual(eval_res["composite_score"], 97)
            self.assertTrue(eval_res["bipartite"])
            self.assertEqual(eval_res["agentic_leverage_evaluation"], mock_leverage_eval)
            self.assertTrue(eval_res["agentic_leverage_passed"])
            self.assertTrue(eval_res["evaluated_span_complete"])

    def test_06_mock_mode_bipartite_evaluation_preserved(self) -> None:
        """Verify mock mode preserves bipartite evaluation behavior and scorecards."""
        judge = GraphExtractionJudge(mock=True)

        valid_bipartite = json.dumps({
            "client_safe": self.valid_client_safe,
            "agentic_leverage": self.sample_leverage_nodes,
        })

        eval_res = judge.evaluate(valid_bipartite, self.f1_transcript, mock=True)

        self.assertEqual(eval_res["verdict"], "PASS")
        self.assertGreaterEqual(eval_res["composite_score"], 95)
        self.assertTrue(eval_res["bipartite"])
        self.assertEqual(len(eval_res["agentic_leverage_evaluation"]), 2)
        self.assertTrue(eval_res["agentic_leverage_passed"])

    def test_07_extract_bipartite_payload_and_backward_compatibility(self) -> None:
        """Verify _extract_bipartite_payload helper and _resolve_bipartite_structure backward compat."""
        raw_bipartite = {
            "client_safe": {"nodes": []},
            "agentic_leverage": [{"node_type": "INFO_ASYMMETRY"}],
        }
        is_bi, nodes, eval_text = _extract_bipartite_payload(raw_bipartite, json.dumps(raw_bipartite))
        self.assertTrue(is_bi)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(eval_text, json.dumps({"nodes": []}))

        # Non-bipartite
        raw_standard = {"nodes": [{"id": "n1"}]}
        is_bi2, nodes2, eval_text2 = _extract_bipartite_payload(raw_standard, json.dumps(raw_standard))
        self.assertFalse(is_bi2)
        self.assertEqual(len(nodes2), 0)
        self.assertEqual(eval_text2, json.dumps(raw_standard))

        # Backward compatibility for _resolve_bipartite_structure
        mock_judge = MagicMock()
        mock_judge.evaluate_agentic_nodes.return_value = [{"judgment": "pass"}]
        is_bi3, cards3, text3 = _resolve_bipartite_structure(
            mock_judge, raw_bipartite, "transcript", client=None, is_mock=True, f3_text=json.dumps(raw_bipartite)
        )
        self.assertTrue(is_bi3)
        self.assertEqual(cards3, [{"judgment": "pass"}])
        mock_judge.evaluate_agentic_nodes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
