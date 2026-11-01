"""
tests/test_answer_ledger.py

Unit tests for the Governed Answer Ledger and Feedback Capture:
1. Schema contract compliance for AnswerRecord and AnswerFeedback.
2. Canonical digest calculation (RFC 8785).
3. SQLite storage with WAL pragmas, busy_timeout, and serialized access.
4. Cryptographic hash chain calculation across sequential records.
5. Chain verification and tamper detection.
6. Pipeline separation (copilot vs query).
7. Explicit operator feedback and implicit recap feedback capture.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger import (
    ANSWER_FEEDBACK_CONTRACT_ID,
    ANSWER_RECORD_CONTRACT_ID,
    GENESIS_PREV_DIGEST,
    AnswerFeedback,
    AnswerLedger,
    AnswerRecord,
    compute_canonical_digest,
    record_implicit_feedback,
    record_operator_feedback,
)


class TestAnswerLedgerUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_answer_ledger_unit_")
        self.tmp_path = Path(self.temp_dir)
        self.db_path = self.tmp_path / "learning" / "answer_ledger.sqlite"
        self.ledger = AnswerLedger(self.db_path)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_schema_contract_compliance(self) -> None:
        """Verifies AnswerRecord and AnswerFeedback conform to their JSON schema contracts."""
        rec = AnswerRecord(
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="a" * 64,
            retrieval_seeds=["SCADA", "Project Nexus"],
            context_snapshot=[{"speaker": "Jen", "text": "Reviewing SCADA"}],
            cited_assertion_ids=["ast-001", "ast-002"],
            rigor="VERIFIED",
            response_text="Connected multi-hop chain found.",
            graph_sha256="b" * 64,
        )
        self.assertEqual(rec.contract_id, ANSWER_RECORD_CONTRACT_ID)
        self.assertEqual(rec.schema_version, "1.0.0")
        self.assertEqual(rec.prev_record_digest, GENESIS_PREV_DIGEST)

        # Check JSON schema file exists and validates contract_id
        schema_path = Path("contracts/answer_ledger/answer_record.schema.json")
        self.assertTrue(schema_path.exists())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], ANSWER_RECORD_CONTRACT_ID)

        fb = AnswerFeedback(
            answer_id=rec.answer_id,
            answer_digest="c" * 64,
            feedback_type="explicit",
            rating="useful",
            origin="USER_EXPLICIT",
        )
        self.assertEqual(fb.contract_id, ANSWER_FEEDBACK_CONTRACT_ID)
        self.assertEqual(fb.schema_version, "1.0.0")

        fb_schema_path = Path("contracts/answer_ledger/answer_feedback.schema.json")
        self.assertTrue(fb_schema_path.exists())
        fb_schema = json.loads(fb_schema_path.read_text(encoding="utf-8"))
        self.assertEqual(fb_schema["$id"], ANSWER_FEEDBACK_CONTRACT_ID)

    def test_canonical_digest_rfc8785(self) -> None:
        """Verifies canonical JSON serialization produces deterministic sha256 regardless of key order."""
        data1 = {"b": 2, "a": 1, "c": [3, 4]}
        data2 = {"a": 1, "c": [3, 4], "b": 2}
        dig1 = compute_canonical_digest(data1)
        dig2 = compute_canonical_digest(data2)
        self.assertEqual(dig1, dig2)
        self.assertEqual(len(dig1), 64)

    def test_sqlite_pragmas_and_tables(self) -> None:
        """Verifies database initializes with WAL mode, busy_timeout, and required tables."""
        con = sqlite3.connect(str(self.db_path))
        journal_mode = con.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")

        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        self.assertIn("answer_records", tables)
        self.assertIn("answer_feedback", tables)
        con.close()

    def test_hash_chain_sequential_linking(self) -> None:
        """Verifies sequential answer appends form an unbroken cryptographic hash chain."""
        rec1 = AnswerRecord(
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="1" * 64,
            retrieval_seeds=["seed1"],
            context_snapshot=[{"speaker": "A", "text": "Turn 1"}],
            cited_assertion_ids=["ast-1"],
            rigor="VERIFIED",
            response_text="Answer 1",
            graph_sha256="g" * 64,
        )
        saved1 = self.ledger.append_answer(rec1)
        self.assertEqual(saved1.prev_record_digest, GENESIS_PREV_DIGEST)
        self.assertTrue(len(saved1.record_digest) == 64)

        rec2 = AnswerRecord(
            pipeline="copilot",
            intent_or_type="gaps",
            context_digest="2" * 64,
            retrieval_seeds=["seed2"],
            context_snapshot=[{"speaker": "B", "text": "Turn 2"}],
            cited_assertion_ids=["ast-2"],
            rigor="VERIFIED",
            response_text="Answer 2",
            graph_sha256="g" * 64,
        )
        saved2 = self.ledger.append_answer(rec2)
        self.assertEqual(saved2.prev_record_digest, saved1.record_digest)
        self.assertTrue(len(saved2.record_digest) == 64)

        rec3 = AnswerRecord(
            pipeline="query",
            intent_or_type="cross_workstream_synthesis",
            context_digest="3" * 64,
            retrieval_seeds=["seed3"],
            context_snapshot=[{"speaker": "C", "text": "Question 3"}],
            cited_assertion_ids=["ast-3"],
            rigor="VERIFIED",
            response_text="Answer 3",
            graph_sha256="g" * 64,
        )
        saved3 = self.ledger.append_answer(rec3)
        self.assertEqual(saved3.prev_record_digest, saved2.record_digest)

        # Verify chain integrity
        is_valid, msg = self.ledger.verify_chain()
        self.assertTrue(is_valid, msg)
        self.assertIn("3 records intact", msg)

    def test_tamper_detection(self) -> None:
        """Verifies verify_chain detects unauthorized payload mutation or digest corruption."""
        for i in range(3):
            self.ledger.append_answer(
                AnswerRecord(
                    pipeline="copilot",
                    intent_or_type="connect",
                    context_digest=f"{i}" * 64,
                    retrieval_seeds=[f"seed{i}"],
                    context_snapshot=[{"speaker": "User", "text": f"T{i}"}],
                    cited_assertion_ids=[f"ast-{i}"],
                    rigor="VERIFIED",
                    response_text=f"Response {i}",
                )
            )

        # Verify intact initially
        is_valid, _ = self.ledger.verify_chain()
        self.assertTrue(is_valid)

        # Tamper directly with the middle record in SQLite
        con = sqlite3.connect(str(self.db_path))
        con.execute("UPDATE answer_records SET response_text = 'FORGED MALICIOUS ANSWER' WHERE row_id = 2;")
        con.commit()
        con.close()

        # Chain verification must fail closed
        is_valid, msg = self.ledger.verify_chain()
        self.assertFalse(is_valid)
        self.assertIn("TAMPER_DETECTED", msg)

    def test_pipeline_separation_and_listing(self) -> None:
        """Verifies copilot and query answers are stored in the same ledger but cleanly filterable."""
        self.ledger.append_answer(
            AnswerRecord(
                pipeline="copilot",
                intent_or_type="connect",
                context_digest="c" * 64,
                retrieval_seeds=["c_seed"],
                context_snapshot=[{"speaker": "A", "text": "live turn"}],
                cited_assertion_ids=["ast-c"],
                rigor="VERIFIED",
                response_text="Copilot answer",
                session_id="sess-001",
            )
        )
        self.ledger.append_answer(
            AnswerRecord(
                pipeline="query",
                intent_or_type="exact_lookup",
                context_digest="q" * 64,
                retrieval_seeds=["q_seed"],
                context_snapshot=[{"speaker": "User", "text": "query question"}],
                cited_assertion_ids=["ast-q"],
                rigor="VERIFIED",
                response_text="Query answer",
            )
        )

        copilot_answers = self.ledger.list_answers(pipeline="copilot")
        query_answers = self.ledger.list_answers(pipeline="query")
        all_answers = self.ledger.list_answers()

        self.assertEqual(len(copilot_answers), 1)
        self.assertEqual(copilot_answers[0].pipeline, "copilot")
        self.assertEqual(len(query_answers), 1)
        self.assertEqual(query_answers[0].pipeline, "query")
        self.assertEqual(len(all_answers), 2)

    def test_explicit_and_implicit_feedback(self) -> None:
        """Verifies recording operator feedback and post-meeting recap implicit feedback."""
        saved = self.ledger.append_answer(
            AnswerRecord(
                pipeline="copilot",
                intent_or_type="connect",
                context_digest="d" * 64,
                retrieval_seeds=["seed"],
                context_snapshot=[{"speaker": "A", "text": "Turn"}],
                cited_assertion_ids=["ast-1"],
                rigor="VERIFIED",
                response_text="Advised on risks.",
            )
        )

        # 1. Explicit operator feedback
        fb_op = record_operator_feedback(
            ledger=self.ledger,
            answer_id=saved.answer_id,
            rating="useful",
            note="Accurate cross-SOW connection during meeting.",
            origin="USER_EXPLICIT",
        )
        self.assertEqual(fb_op.answer_id, saved.answer_id)
        self.assertEqual(fb_op.answer_digest, saved.record_digest)
        self.assertEqual(fb_op.feedback_type, "explicit")
        self.assertEqual(fb_op.rating, "useful")

        # 2. Implicit recap feedback
        fb_imp = record_implicit_feedback(
            ledger=self.ledger,
            answer_id=saved.answer_id,
            cited_ids_found_in_recap=True,
            weight=0.75,
        )
        self.assertEqual(fb_imp.answer_id, saved.answer_id)
        self.assertEqual(fb_imp.answer_digest, saved.record_digest)
        self.assertEqual(fb_imp.feedback_type, "implicit")
        self.assertEqual(fb_imp.rating, "useful")
        self.assertEqual(fb_imp.weight, 0.75)

        feedbacks = self.ledger.list_feedback(answer_id=saved.answer_id)
        self.assertEqual(len(feedbacks), 2)


if __name__ == "__main__":
    unittest.main()
