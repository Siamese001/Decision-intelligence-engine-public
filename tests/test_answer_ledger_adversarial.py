"""
tests/test_answer_ledger_adversarial.py

Adversarial and edge-case tests for the Governed Answer Ledger:
1. Boundary & malformed payloads: Empty seeds, null fields, non-ASCII/unicode symbols.
2. Concurrency & race conditions: Multi-threaded parallel appends verifying zero lock starvation and unbroken hash chain.
3. Synthetic feedback rejection: Anti-synthetic stop hook defense blocks forged approvals via validate_approval_origin.
4. Rule C-19 Career Firewall: Quarantining of personal career leverage patterns in stored context snapshots.
5. Invariant bypass prevention: Feedback binding rejected for non-existent answer records.
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger import (
    AnswerLedger,
    AnswerRecord,
    record_operator_feedback,
)


class TestAnswerLedgerAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_answer_ledger_adv_")
        self.tmp_path = Path(self.temp_dir)
        self.db_path = self.tmp_path / "learning" / "answer_ledger.sqlite"
        self.ledger = AnswerLedger(self.db_path)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_boundary_and_malformed_payloads(self) -> None:
        """Verifies AnswerRecord handles edge-case boundary inputs gracefully."""
        # Null / empty optional fields
        rec_empty = AnswerRecord(
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="",
            retrieval_seeds=[],
            context_snapshot=[],
            cited_assertion_ids=[],
            rigor="FAST",
            response_text="Empty seeds edge case.",
            graph_sha256=None,
            generation=None,
            parameter_snapshot_digest=None,
            latency_ms=None,
            model_id=None,
            session_id=None,
        )
        saved = self.ledger.append_answer(rec_empty)
        self.assertTrue(len(saved.context_digest) == 64)
        self.assertTrue(len(saved.record_digest) == 64)

        # Unicode, emojis, escaped quotes, multiline formatting
        unicode_text = "Advisory: 🚀 Test with non-ASCII chars: 日本語, العربية, and \n newlines with \"quotes\"."
        rec_unicode = AnswerRecord(
            pipeline="query",
            intent_or_type="relationship_explanation",
            context_digest="f" * 64,
            retrieval_seeds=["unicode_seed"],
            context_snapshot=[{"speaker": "Tester", "text": unicode_text}],
            cited_assertion_ids=["ast-unicode"],
            rigor="VERIFIED",
            response_text=unicode_text,
        )
        saved_u = self.ledger.append_answer(rec_unicode)
        fetched_u = self.ledger.get_answer(saved_u.answer_id)
        self.assertIsNotNone(fetched_u)
        self.assertEqual(fetched_u.response_text, unicode_text)

    def test_multi_threaded_concurrency_and_race(self) -> None:
        """Verifies 16 concurrent worker threads can append answer records without database lockup or chain corruption."""
        num_threads = 16
        records_per_thread = 5
        total_records = num_threads * records_per_thread

        def _worker(thread_idx: int) -> list[str]:
            ids = []
            for j in range(records_per_thread):
                rec = AnswerRecord(
                    pipeline="copilot" if (thread_idx + j) % 2 == 0 else "query",
                    intent_or_type=f"intent_{thread_idx}_{j}",
                    context_digest=f"digest_{thread_idx}_{j}".ljust(64, "0")[:64],
                    retrieval_seeds=[f"seed_{thread_idx}_{j}"],
                    context_snapshot=[{"speaker": f"Spk{thread_idx}", "text": f"Turn {j}"}],
                    cited_assertion_ids=[f"ast_{thread_idx}_{j}"],
                    rigor="VERIFIED",
                    response_text=f"Response from thread {thread_idx} turn {j}",
                )
                saved = self.ledger.append_answer(rec)
                ids.append(saved.answer_id)
            return ids

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker, i) for i in range(num_threads)]
            all_ids = []
            for f in concurrent.futures.as_completed(futures):
                all_ids.extend(f.result())

        self.assertEqual(len(all_ids), total_records)
        all_stored = self.ledger.list_answers(limit=total_records + 10)
        self.assertEqual(len(all_stored), total_records)

        # Hash chain must verify cleanly across all concurrently appended records
        is_valid, msg = self.ledger.verify_chain()
        self.assertTrue(is_valid, f"Chain validation failed after concurrent writes: {msg}")
        self.assertIn(f"{total_records} records intact", msg)

    def test_synthetic_feedback_rejection(self) -> None:
        """Verifies synthetic stop hooks and non-human origin markers are rejected by validate_approval_origin."""
        saved = self.ledger.append_answer(
            AnswerRecord(
                pipeline="copilot",
                intent_or_type="connect",
                context_digest="1" * 64,
                retrieval_seeds=["s1"],
                context_snapshot=[{"speaker": "A", "text": "turn"}],
                cited_assertion_ids=["ast-1"],
                rigor="VERIFIED",
                response_text="Advice.",
            )
        )

        # 1. Synthetic marker
        with self.assertRaises(ValueError) as ctx:
            record_operator_feedback(
                ledger=self.ledger,
                answer_id=saved.answer_id,
                rating="useful",
                origin="AUTO_APPROVED_BY_STOP_HOOK",
            )
        self.assertIn("SYNTHETIC_FEEDBACK_REJECTED", str(ctx.exception))

        # 2. Unauthorized synthetic source
        with self.assertRaises(ValueError) as ctx:
            record_operator_feedback(
                ledger=self.ledger,
                answer_id=saved.answer_id,
                rating="useful",
                origin="autonomous_agent_daemon",
            )
        self.assertIn("SYNTHETIC_FEEDBACK_REJECTED", str(ctx.exception))

    def test_c19_career_firewall_quarantine(self) -> None:
        """Verifies Rule C-19 career leverage content is sanitized from stored context snapshots."""
        poisoned_turns = [
            {"speaker": "Candidate", "text": "Targeting a executive compensation band and executive VP title."},
            {"speaker": "Lead", "text": "Valid discussion on Project Nexus timeline."},
        ]

        rec = AnswerRecord(
            pipeline="copilot",
            intent_or_type="connect",
            context_digest="c" * 64,
            retrieval_seeds=["seed"],
            context_snapshot=poisoned_turns,
            cited_assertion_ids=["ast-1"],
            rigor="VERIFIED",
            response_text="Grounded advice.",
        )
        saved = self.ledger.append_answer(rec)
        fetched = self.ledger.get_answer(saved.answer_id)
        self.assertIsNotNone(fetched)

        # Check turn 0 is quarantined
        turn0 = fetched.context_snapshot[0]
        self.assertNotIn("$1.2M", turn0["text"])
        self.assertIn("RULE C-19", turn0["text"])

        # Check turn 1 is preserved
        turn1 = fetched.context_snapshot[1]
        self.assertEqual(turn1["text"], "Valid discussion on Project Nexus timeline.")

    def test_feedback_binding_rejected_on_missing_answer(self) -> None:
        """Verifies feedback cannot be recorded for non-existent answer records."""
        with self.assertRaises(KeyError) as ctx:
            record_operator_feedback(
                ledger=self.ledger,
                answer_id="ans-nonexistent-12345",
                rating="useful",
                origin="USER_EXPLICIT",
            )
        self.assertIn("ANSWER_NOT_FOUND", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
