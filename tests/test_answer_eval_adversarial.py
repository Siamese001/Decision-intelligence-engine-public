"""Adversarial tests for Answer Evaluation Harness (Wave 7)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger import AnswerLedger, AnswerRecord
from dual_pipeline.evals.answer_eval_core import (
    AnswerEvalHarness,
    evaluate_with_llm_judge,
    replay_candidate_parameters,
    validate_eval_receipt,
)


class TestAnswerEvalAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_answer_eval_adv_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def test_empty_ledger_handling(self) -> None:
        """Empty ledger produces valid schema-compliant receipt with zero records."""
        harness = AnswerEvalHarness(runtime_root=self.runtime_root)
        receipt = harness.evaluate(pipeline="both")

        self.assertEqual(receipt["contract_id"], "urn:nee:eval:answer-eval-receipt:1.0.0")
        self.assertEqual(receipt["ledger_range"]["record_count"], 0)
        self.assertTrue(receipt["ledger_range"]["chain_valid"])
        self.assertEqual(receipt["metrics"]["groundedness"], 0.0)
        self.assertEqual(receipt["metrics"]["operator_usefulness"]["total_feedback"], 0)

        # Must validate against JSON schema
        validate_eval_receipt(receipt)

    def test_corrupt_ledger_chain_detection(self) -> None:
        """Tampered ledger record is detected and flagged in chain_valid: False."""
        db_path = self.runtime_root / "learning" / "answer_ledger.sqlite"
        ledger = AnswerLedger(db_path)

        rec1 = AnswerRecord(
            answer_id="ans-001",
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="a" * 64,
            retrieval_seeds=["Nexus"],
            context_snapshot=[],
            cited_assertion_ids=["ast-001"],
            rigor="FAST",
            response_text="Advisory 1",
            timestamp="2026-10-05T12:00:00Z",
        )
        rec2 = AnswerRecord(
            answer_id="ans-002",
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="b" * 64,
            retrieval_seeds=["SCADA"],
            context_snapshot=[],
            cited_assertion_ids=["ast-002"],
            rigor="FAST",
            response_text="Advisory 2",
            timestamp="2026-10-05T12:01:00Z",
        )
        ledger.append_answer(rec1)
        ledger.append_answer(rec2)

        # Directly tamper with record 1 in sqlite
        con = sqlite3.connect(str(db_path))
        con.execute("UPDATE answer_records SET response_text = 'TAMPERED TEXT' WHERE answer_id = 'ans-001'")
        con.commit()
        con.close()

        harness = AnswerEvalHarness(runtime_root=self.runtime_root)
        receipt = harness.evaluate(pipeline="copilot")

        self.assertFalse(receipt["ledger_range"]["chain_valid"])
        self.assertEqual(receipt["ledger_range"]["record_count"], 2)
        validate_eval_receipt(receipt)

    def test_missing_graph_database_fallback(self) -> None:
        """Missing graph database falls back gracefully without unhandled exception."""
        active_pair = {
            "schema_version": "1.0.0",
            "graph_path": "non_existent_graph.sqlite",
            "graph_sha256": "abcdef123456",
            "generation_id": 999,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")

        db_path = self.runtime_root / "learning" / "answer_ledger.sqlite"
        ledger = AnswerLedger(db_path)
        ledger.append_answer(
            AnswerRecord(
                answer_id="ans-001",
                pipeline="query",
                intent_or_type="deep_synthesis",
                context_digest="c" * 64,
                retrieval_seeds=[],
                context_snapshot=[],
                cited_assertion_ids=["ast-001"],
                rigor="VERIFIED",
                response_text="Grounded answer [ast-001]",
                timestamp="2026-10-05T12:00:00Z",
            )
        )

        harness = AnswerEvalHarness(runtime_root=self.runtime_root)
        receipt = harness.evaluate(pipeline="query")

        self.assertEqual(receipt["ledger_range"]["record_count"], 1)
        # When graph database is missing, durability fails closed to 0.0
        self.assertEqual(receipt["metrics"]["citation_durability"], 0.0)
        validate_eval_receipt(receipt)

    def test_orphan_feedback_records(self) -> None:
        """Feedback referencing non-existent answer IDs does not corrupt eval or cause KeyError."""
        db_path = self.runtime_root / "learning" / "answer_ledger.sqlite"
        ledger = AnswerLedger(db_path)

        rec = AnswerRecord(
            answer_id="ans-valid",
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="d" * 64,
            retrieval_seeds=[],
            context_snapshot=[],
            cited_assertion_ids=[],
            rigor="FAST",
            response_text="Copilot output",
            timestamp="2026-10-05T12:00:00Z",
        )
        saved_rec = ledger.append_answer(rec)
        from dual_pipeline.answer_ledger_core.feedback import record_operator_feedback
        record_operator_feedback(ledger, saved_rec.answer_id, "useful", note="good answer")

        # Manually inject an orphan feedback record into sqlite
        con = sqlite3.connect(str(db_path))
        con.execute(
            """INSERT INTO answer_feedback (feedback_id, answer_id, answer_digest, feedback_type, rating, note, origin, weight, timestamp, contract_id, schema_version, signature)
               VALUES ('fb-orphan', 'ans-non-existent', '000000', 'operator_explicit', 'wrong', 'no answer', 'USER_EXPLICIT', 1.0, '2026-10-05T12:05:00Z', 'urn:nee:answer-ledger:answer-feedback:1.0.0', '1.0.0', 'sig')"""
        )
        con.commit()
        con.close()

        harness = AnswerEvalHarness(runtime_root=self.runtime_root)
        receipt = harness.evaluate(pipeline="copilot")

        self.assertEqual(receipt["metrics"]["operator_usefulness"]["useful_count"], 1)
        self.assertEqual(receipt["metrics"]["operator_usefulness"]["wrong_count"], 0)
        validate_eval_receipt(receipt)

    def test_llm_judge_timeout_and_failure_resilience(self) -> None:
        """LLM judge handles provider timeout and exceptions gracefully without crashing."""
        records = [
            {"response_text": None, "cited_assertion_ids": None},
            {"response_text": "Fail-closed: zero assertions found", "cited_assertion_ids": []},
            {"response_text": "Valid citation [ast-01] [ast-02]", "cited_assertion_ids": ["ast-01", "ast-02"]},
        ]
        judge_score = evaluate_with_llm_judge(records, provider="mock_provider", timeout_seconds=0.01)
        self.assertEqual(judge_score.evaluated_count, 3)
        self.assertGreater(judge_score.average_relevance_score, 0.0)
        self.assertTrue(judge_score.provider_bound)

    def test_malformed_candidate_params_in_replay(self) -> None:
        """Replay handles malformed parameter payloads without unhandled exceptions."""
        records = [
            {
                "pipeline": "copilot",
                "context_snapshot": [{"speaker": "A", "text": "Hello"}],
                "cited_assertion_ids": ["ast-1"],
                "response_text": "Text [ast-1]",
            }
        ]
        malformed_params = {
            "unknown_key": "some_value",
            "copilot.neighborhood_max_hops": 0,
        }
        res = replay_candidate_parameters(records, malformed_params)
        self.assertEqual(res.replayed_count, 1)
        self.assertIsNotNone(res.candidate_parameter_snapshot_digest)


if __name__ == "__main__":
    unittest.main()
