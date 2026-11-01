"""Unit tests for Hardened GraphExtractionJudge (Output 3) with >=95 release gate."""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.llm_judge import GraphExtractionJudge
from dual_pipeline.transcript_qa import evaluate_judge_and_release


class TestHardenedGraphJudge(unittest.TestCase):
    def test_default_model_and_reasoning(self):
        judge = GraphExtractionJudge()
        self.assertIn(judge.model, ("gpt-5.6-luna", "gpt-5.6-luna"))
        self.assertIn(judge.reasoning_effort, ("high", "medium"))

    def test_verdict_threshold_logic(self):
        judge = GraphExtractionJudge(mock=True)

        # Mock evaluate return
        f1_text = "### [00:00:00]\n\n**Mitchell:** System architecture discussion."
        f3_valid = '{"graph_mutations": {"entity_upserts": [{"entity_id": "system:billing", "name": "Billing"}]}}'

        res_pass = judge.evaluate(f3_valid, f1_text, mock=True)
        self.assertEqual(res_pass["verdict"], "PASS")
        self.assertGreaterEqual(res_pass["composite_score"], 95)

    def test_fail_rejection_for_score_below_threshold(self):
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = (
            '{"meeting_archetype": "STRATEGIC_DISCOVERY", "composite_score": 85, '
            '"verdict": "PASS", "forensic_rationale": "Slightly imprecise predicate."}'
        )
        mock_choice.message = mock_message
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        f1_text = "Alex Rivera: Discussion on billing architecture."
        f3_text = '{"graph_mutations": {}}'

        eval_res = judge.evaluate(f3_text, f1_text, client=mock_client, mock=False)
        self.assertEqual(eval_res["composite_score"], 85)
        # Must be FAIL because score < 90 and PASS_ADVISORY is forbidden for Output 3
        self.assertEqual(eval_res["verdict"], "FAIL")

    def test_pass_for_score_96(self):
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = (
            '{"meeting_archetype": "STRATEGIC_DISCOVERY", "composite_score": 96, '
            '"verdict": "PASS", "forensic_rationale": "Atomic and canonical."}'
        )
        mock_choice.message = mock_message
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        f1_text = "Alex Rivera: Discussion on billing architecture."
        f3_text = '{"graph_mutations": {}}'

        eval_res = judge.evaluate(f3_text, f1_text, client=mock_client, mock=False)
        self.assertEqual(eval_res["composite_score"], 96)
        self.assertEqual(eval_res["verdict"], "PASS")

    def test_fail_for_score_75(self):
        judge = GraphExtractionJudge(mock=False)
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = (
            '{"meeting_archetype": "STRATEGIC_DISCOVERY", "composite_score": 75, '
            '"verdict": "FAIL", "forensic_rationale": "Tactical to-do list included."}'
        )
        mock_choice.message = mock_message
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp

        f1_text = "Alex Rivera: Discussion on billing architecture."
        f3_text = '{"graph_mutations": {}}'

        eval_res = judge.evaluate(f3_text, f1_text, client=mock_client, mock=False)
        self.assertEqual(eval_res["composite_score"], 75)
        self.assertEqual(eval_res["verdict"], "FAIL")

    def test_rule_c19_firewall_leak_rejection(self):
        judge = GraphExtractionJudge(mock=True)
        f1_text = "Alex Rivera: Discussion."
        f3_leaked = '{"graph_mutations": {"description": "Personal executive compensation band notes and INTERNAL_ADVISORY."}}'

        eval_res = judge.evaluate(f3_leaked, f1_text, mock=True)
        self.assertEqual(eval_res["verdict"], "FAIL")
        self.assertLessEqual(eval_res["composite_score"], 30)
        self.assertIn("RULE C-19 VIOLATION", eval_res["forensic_rationale"])

    def test_evaluate_judge_and_release_enforces_output3_gte95(self):
        # In mock mode, output 1 gets 95, output 2 gets 96, output 3 gets 96 -> all >=95 -> passes release
        f1 = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on system architecture.\n"
        f2 = (
            "# Executive Recap\n\n**Date:** 08/28/2026\n\n"
            "## Executive Summary\nJoint architecture review.\n\n"
            "## Action Items\n- Alex Rivera to deploy architecture by 09/15/2026."
        )
        f3 = (
            '{"schema_version": "5.7", "meeting_id": "m-1", "classification": "internal", '
            '"audiences": ["team"], "evidence_index": {}, "evidence_only_statement_ids": [], '
            '"graph_mutations": {"entity_upserts": [{"id": "person:mitchell_simmons", '
            '"name": "Alex Rivera", "type": "PERSON", "mutation": "UPSERT"}, '
            '{"id": "system:billing", "name": "Billing System", "type": "SYSTEM", "mutation": "UPSERT"}], '
            '"relationship_signals": [{"signal_id": "sig-1", "from_id": "person:mitchell_simmons", '
            '"to_id": "system:billing", "relationship": "OWNS", "evidence_ids": []}]}}'
        )

        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1,
            f2_text=f2,
            f3_text=f3,
            source_text=f1,
            mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            force_judge_on_qa_fail=True,
        )
        self.assertEqual(judge_rep["output_3_evaluation"]["composite_score"], 97)
        self.assertTrue(decision["output_3_llm_score_pass"])
        self.assertTrue(decision["output_3_deterministic_gates_pass"])
        self.assertTrue(decision["output_3_pass"])


if __name__ == "__main__":
    unittest.main()
