"""
tests/adversarial/test_bipartite_preval_adversarial.py

Adversarial edge-case tests for bipartite graph pre-validation fast-fail.
Covers at least 3 mandatory categories from e2e-and-edge-case-coverage.md:
1. Boundary payload (empty strings, zero-length structures, null candidate blocks)
2. Malformed input (truncated bipartite JSON, corrupt types, unparseable structures)
3. Invariant bypass attempt (attempting to bypass graph validation via agentic packaging alone)
4. Overflow & exhaustion (large candidate payload with 50 leverage nodes on invalid graph)
5. Exact production regression (empty graph_mutations with active leverage nodes)
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge


class BipartitePreValidationAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f1_transcript = (
            "PartnerAdvisory steering committee review on cloud migration, data models, and executive delivery roadmap."
        )

    def test_01_boundary_payload_empty_and_null_bipartite_structures(self) -> None:
        """Category 1: Boundary payload - null client_safe, empty string, and whitespace candidates."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        # 1a: Empty string
        res1 = judge.evaluate("", self.f1_transcript, client=mock_client, mock=False)
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertFalse(res1.get("passed_pre_check", True))
        mock_client.chat.completions.create.assert_not_called()

        # 1b: Whitespace only
        res2 = judge.evaluate("   \n\t  ", self.f1_transcript, client=mock_client, mock=False)
        self.assertEqual(res2["verdict"], "FAIL")
        self.assertFalse(res2.get("passed_pre_check", True))
        mock_client.chat.completions.create.assert_not_called()

        # 1c: Empty dict
        res3 = judge.evaluate("{}", self.f1_transcript, client=mock_client, mock=False)
        self.assertEqual(res3["verdict"], "FAIL")
        self.assertFalse(res3.get("passed_pre_check", True))
        mock_client.chat.completions.create.assert_not_called()

    def test_02_malformed_input_truncated_bipartite_json(self) -> None:
        """Category 2: Malformed input - truncated JSON with dangling quotes and braces."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        truncated_f3 = '{"client_safe": {"graph_mutations": {"entity_upserts": [{"id": "sys:cloud'
        res = judge.evaluate(truncated_f3, self.f1_transcript, client=mock_client, mock=False)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertFalse(res.get("passed_pre_check", True))
        self.assertEqual(res.get("pre_judge_tier"), "TIER_1_SYNTAX_FAIL")
        mock_client.chat.completions.create.assert_not_called()

    def test_03_invariant_bypass_attempt_packaging_leverage_without_graph(self) -> None:
        """Category 3: Invariant bypass attempt - packing leverage nodes without valid graph entities."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        # Attempt to pass judge by providing rich agentic_leverage but fake/empty client_safe
        bypass_payload = json.dumps({
            "client_safe": {
                "irrelevant_meta": "bypass_attempt",
            },
            "agentic_leverage": [
                {
                    "node_type": "CAREER_VISIBILITY",
                    "key_players": ["Drew Oakley (Managing Director)"],
                    "political_rationale": "High materiality career alignment wedge.",
                    "networking_wedge": "Position directly with Drew Oakley before committee.",
                    "speaker": "Drew Oakley",
                }
            ],
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic:
            res = judge.evaluate(bypass_payload, self.f1_transcript, client=mock_client, mock=False)
            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            # Must fail at schema stage, averting any leverage evaluation
            mock_agentic.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_04_overflow_and_exhaustion_50_leverage_nodes_averted(self) -> None:
        """Category 4: Overflow & exhaustion - 50 leverage nodes on invalid graph averted in 0 calls."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        # 50 leverage nodes would cost 50 paid LLM calls if evaluated eagerly
        many_leverage_nodes = [
            {
                "node_type": "INFO_ASYMMETRY",
                "key_players": [f"Executive {i} (VP)"],
                "political_rationale": f"Asymmetric intelligence chunk {i} regarding budget slippage.",
                "networking_wedge": f"Actionable outreach strategy {i}.",
                "speaker": f"Executive {i}",
            }
            for i in range(50)
        ]

        exhaustion_payload = json.dumps({
            "client_safe": {
                "graph_mutations": {},  # Invalid empty graph
            },
            "agentic_leverage": many_leverage_nodes,
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic:
            res = judge.evaluate(exhaustion_payload, self.f1_transcript, client=mock_client, mock=False)
            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            # Proves all 50 potential LLM calls averted
            mock_agentic.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()

    def test_05_exact_production_regression_empty_mutations_with_valid_transcript(self) -> None:
        """Category 5: Exact production regression - empty mutations payload fails fast with 0 LLM calls."""
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()

        substantive_transcript = (
            "During the strategic discovery session, the engineering leadership discussed "
            "implementing hybrid BM25 and vector search retrieval architectures for enterprise compliance."
        )
        regression_payload = json.dumps({
            "client_safe": {
                "schema_version": "5.7",
                "graph_mutations": {
                    "entity_upserts": [],
                    "relationship_signals": [],
                },
            },
            "agentic_leverage": [
                {
                    "node_type": "partner_advisory_MARGIN_WEDGE",
                    "key_players": ["Sarah Jenkins (Director)"],
                    "political_rationale": "Bandwidth pinch on regulatory compliance.",
                    "networking_wedge": "Deploy specialized pod.",
                    "speaker": "Sarah Jenkins",
                }
            ],
        })

        with patch.object(judge.agentic_judge, "evaluate_agentic_nodes") as mock_agentic:
            res = judge.evaluate(regression_payload, substantive_transcript, client=mock_client, mock=False)
            self.assertEqual(res["verdict"], "FAIL")
            self.assertFalse(res.get("passed_pre_check", True))
            mock_agentic.assert_not_called()
            mock_client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
