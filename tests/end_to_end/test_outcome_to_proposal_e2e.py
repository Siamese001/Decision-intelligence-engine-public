"""
tests/end_to_end/test_outcome_to_proposal_e2e.py

End-to-end integration test suite for Wave 3: Complete durable, scoped outcome and feedback integration.
Validates the complete lifecycle:
1. Native SQLite AnswerLedger feedback -> failure scanning -> proposal synthesis.
2. LivePipelineIntegrator capturing authentic success, failure, and abstention with denominators.
3. Durable cursor tracking across restarts without event loss or duplication.
4. CorrectionFeedbackIngestor persisting corrections, reconstructing state on restart.
5. Proposal source inspection resolving verified and unavailable receipts.
6. Zero foreground model calls or external network dependencies.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger_core.feedback import record_operator_feedback
from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import utc_now, write_json
from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.learning_engine_core.provenance import (
    inspect_proposal_sources,
    verify_proposal_provenance,
)
from dual_pipeline.learning_loop_core.scanner import (
    scan_answer_eval_receipts,
    synthesize_answer_learning_proposal,
)


class TestOutcomeToProposalE2E(unittest.TestCase):
    """E2E verification of outcome events, SQLite feedback, durable cursors, and proposal lineage."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_outcome_to_proposal_e2e_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "evals"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_db_path = self.ledger_dir / "answer_ledger.sqlite"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_answer(self, ledger: AnswerLedger, answer_id: str, pipeline: str = "copilot") -> AnswerRecord:
        record = AnswerRecord(
            answer_id=answer_id,
            pipeline=pipeline,
            session_id="session-test-01",
            intent_or_type="query_lookup",
            context_digest="d" * 64,
            retrieval_seeds=["seed1", "seed2"],
            context_snapshot=[{"text": "claim1"}],
            cited_assertion_ids=["assert-01"],
            rigor="standard",
            response_text="Test response text",
            model_id="test-model",
            timestamp=utc_now(),
        )
        return ledger.append_answer(record)

    def test_sqlite_feedback_to_proposal_lifecycle(self) -> None:
        """Verifies native SQLite AnswerLedger negative feedback is scanned and synthesized into a valid proposal."""
        ledger = AnswerLedger(self.ledger_db_path)
        self._create_sample_answer(ledger, "ans-001", pipeline="copilot")
        self._create_sample_answer(ledger, "ans-002", pipeline="copilot")

        record_operator_feedback(ledger, answer_id="ans-001", rating="not_useful", note="Low groundedness")
        record_operator_feedback(ledger, answer_id="ans-002", rating="wrong", note="Factually incorrect claim")

        cohorts = scan_answer_eval_receipts(
            eval_dir=self.eval_dir,
            ledger_path=self.ledger_db_path,
            min_occurrences=2,
        )
        self.assertEqual(len(cohorts), 1)
        cohort = cohorts[0]
        self.assertEqual(cohort.occurrence_count, 2)
        self.assertIn("ans-001", [it["question_id"] for it in cohort.items])
        self.assertIn("ans-002", [it["question_id"] for it in cohort.items])

        proposal = synthesize_answer_learning_proposal(cohort, self.proposals_dir)
        validated = validate_artifact(proposal)
        self.assertIsNotNone(validated)

        prov_ok, prov_msg = verify_proposal_provenance(proposal)
        self.assertTrue(prov_ok, f"Provenance verification failed: {prov_msg}")

    def test_live_pipeline_integrator_success_failure_abstention_and_restart(self) -> None:
        """Verifies capturing success/failure/abstention with authentic denominators and durable restart recovery."""
        events_path = self.ledger_dir / "live_events.jsonl"
        cursor_path = self.ledger_dir / "cursor.json"

        integrator = LivePipelineIntegrator(
            runtime_root=self.root,
            events_log_path=events_path,
            cursor_path=cursor_path,
        )

        ts_success = "2026-10-09T10:00:00+00:00"
        ts_abstain = "2026-10-09T10:05:00+00:00"
        evt_success = integrator.record_success(
            source_agent="RetrievalJudge",
            category="search_recall",
            denominator=10,
            created_at=ts_success,
        )
        evt_miss = integrator.record_retrieval_miss(
            query="Who approved Project Orion?",
            missing_requirements=["budget_approval", "sign_off"],
        )
        evt_abstain = integrator.record_abstention(
            source_agent="HitlArbiter",
            category="ambiguity_guard",
            reason="Confidence margin below 30%",
            denominator=5,
            created_at=ts_abstain,
        )

        self.assertEqual(evt_success.status, "PASS")
        self.assertEqual(evt_success.observed_outcome["denominator"], 10)
        self.assertEqual(evt_success.created_at, ts_success)

        self.assertEqual(evt_miss.status, "FAIL")

        self.assertEqual(evt_abstain.status, "NOOP")
        self.assertEqual(evt_abstain.observed_outcome["denominator"], 5)
        self.assertEqual(evt_abstain.observed_outcome["abstention_reason"], "Confidence margin below 30%")

        # First drain
        drained_first = integrator.drain_to_cohort_items()
        self.assertEqual(len(drained_first), 3)

        # Immediate second drain returns nothing
        drained_second = integrator.drain_to_cohort_items()
        self.assertEqual(len(drained_second), 0)

        # Simulate process restart by creating new instance pointing to same storage
        restarted = LivePipelineIntegrator(
            runtime_root=self.root,
            events_log_path=events_path,
            cursor_path=cursor_path,
        )
        self.assertEqual(len(restarted.drain_to_cohort_items()), 0)

        # Record new event after restart and drain
        restarted.record_adjudication_error("cand-A", "cand-B")
        drained_third = restarted.drain_to_cohort_items()
        self.assertEqual(len(drained_third), 1)
        self.assertEqual(drained_third[0]["outcome_type"], "adjudication_error")

    def test_operator_corrections_persistence_and_reconstruction(self) -> None:
        """Verifies operator corrections are durably persisted and state is reconstructed across restarts."""
        corrections_path = self.ledger_dir / "operator_corrections.jsonl"
        ingestor = CorrectionFeedbackIngestor(
            runtime_root=self.root,
            min_confirmations=2,
            log_path=corrections_path,
        )

        ingestor.record_speaker_correction(
            raw_speaker="Speaker 1",
            corrected_speaker="Alice Smith",
            operator_id="operator-42",
            session_id="session-01",
        )
        ingestor.record_speaker_correction(
            raw_speaker="Speaker 1",
            corrected_speaker="Alice Smith",
            operator_id="operator-88",
            session_id="session-02",
        )

        cohorts = ingestor.aggregate_cohorts()
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(cohorts[0].confirmations, 2)

        proposals, _ = ingestor.synthesize_proposals()
        self.assertEqual(len(proposals), 1)
        prop = proposals[0]

        validated = validate_artifact(prop)
        self.assertIsNotNone(validated)

        # Restart test
        restarted_ingestor = CorrectionFeedbackIngestor(
            runtime_root=self.root,
            min_confirmations=2,
            log_path=corrections_path,
        )
        reconstructed_cohorts = restarted_ingestor.aggregate_cohorts()
        self.assertEqual(len(reconstructed_cohorts), 1)
        self.assertEqual(reconstructed_cohorts[0].confirmations, 2)
        self.assertEqual(reconstructed_cohorts[0].target_identifier, "Alice Smith")

    def test_proposal_source_inspection_verified_and_unavailable(self) -> None:
        """Verifies inspect_proposal_sources explicitly resolves verified and unavailable sources."""
        receipt_file = self.eval_dir / "eval-receipt-001.json"
        receipt_data = {
            "receipt_id": "eval-receipt-001",
            "pipeline": "copilot",
            "metrics": {"context_recall": 0.50},
            "sealed_at": utc_now(),
        }
        write_json(receipt_file, receipt_data)

        # Create dummy cohort with one real receipt and one missing
        cohorts = scan_answer_eval_receipts(eval_dir=self.eval_dir, min_occurrences=1)
        self.assertTrue(len(cohorts) >= 1)
        cohort = cohorts[0]
        cohort.receipt_ids.append("eval-missing-receipt-999")

        proposal = synthesize_answer_learning_proposal(cohort, self.proposals_dir)
        report = inspect_proposal_sources(proposal, runtime_root=self.root)

        self.assertIn("eval-receipt-001.json", report["sources"])
        self.assertEqual(report["sources"]["eval-receipt-001.json"], "VERIFIED")
        self.assertIn("eval-missing-receipt-999", report["sources"])
        self.assertEqual(report["sources"]["eval-missing-receipt-999"], "UNAVAILABLE")
        self.assertTrue(report["has_unavailable"])
        self.assertFalse(report["verified"])


if __name__ == "__main__":
    unittest.main()
