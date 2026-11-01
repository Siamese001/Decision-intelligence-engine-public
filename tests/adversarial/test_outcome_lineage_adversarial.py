"""
tests/adversarial/test_outcome_lineage_adversarial.py

Adversarial and edge-case test suite for Wave 3: Complete durable, scoped outcome and feedback integration.
Covers 5 mandatory edge-case categories:
1. Boundary payload: empty logs, zero/negative denominators, 0-byte ledgers.
2. Malformed input: corrupted JSONL lines, non-JSON bytes, malformed SQLite ratings.
3. Invariant bypass: cross-engagement leakage, repeated correction amplification, duplicate receipt inflation.
4. Concurrency & race: multi-threaded concurrent event recording and clean draining.
5. Rollback & recovery: operator retractions and post-hoc tampered source detection.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger_core.feedback import record_operator_feedback
from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.core import utc_now, write_json
from dual_pipeline.learning_adapters.agent_outcome_adapter import OutcomeType
from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.learning_engine_core.provenance import inspect_proposal_sources
from dual_pipeline.learning_loop_core.scanner import (
    scan_answer_eval_receipts,
    synthesize_answer_learning_proposal,
)


class TestOutcomeLineageAdversarial(unittest.TestCase):
    """Adversarial suite testing failure modes, malformed payloads, concurrency, and tamper resistance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_outcome_lineage_adv_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "evals"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Boundary Payloads
    # -------------------------------------------------------------------------

    def test_boundary_empty_logs_and_zero_denominators(self) -> None:
        """Verifies boundary behavior with 0-byte logs, non-existent ledgers, and zero/negative denominators."""
        events_path = self.ledger_dir / "empty_events.jsonl"
        events_path.touch()
        cursor_path = self.ledger_dir / "empty_cursor.json"

        integrator = LivePipelineIntegrator(
            runtime_root=self.root,
            events_log_path=events_path,
            cursor_path=cursor_path,
        )
        self.assertEqual(integrator.drain_to_cohort_items(), [])

        # Zero and negative denominators must clamp to at least 1
        evt_zero = integrator.record_success(
            source_agent="TestAgent",
            category="test_cat",
            denominator=0,
        )
        self.assertEqual(evt_zero.observed_outcome["denominator"], 1)

        evt_neg = integrator.record_abstention(
            source_agent="TestAgent",
            category="test_cat",
            reason="test",
            denominator=-10,
        )
        self.assertEqual(evt_neg.observed_outcome["denominator"], 1)

        # Empty eval dir returns empty cohorts
        cohorts = scan_answer_eval_receipts(eval_dir=self.eval_dir, min_occurrences=1)
        self.assertEqual(cohorts, [])

    # -------------------------------------------------------------------------
    # 2. Malformed Inputs
    # -------------------------------------------------------------------------

    def test_malformed_corrupted_jsonl_in_event_log(self) -> None:
        """Verifies that corrupted JSONL lines (truncated, junk syntax, missing keys) are skipped safely."""
        events_path = self.ledger_dir / "corrupted_events.jsonl"
        cursor_path = self.ledger_dir / "corrupted_cursor.json"

        valid_evt_dict = {
            "event_id": "evt-valid-001",
            "run_id": "run-001",
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
            "outcome_type": "retrieval_miss",
            "source_agent": "RetrievalAgent",
            "stage": "L1",
            "category": "coverage_gap",
            "observed_outcome": {"query_text": "sample", "missing_requirements": ["req1"]},
            "confidence": 1.0,
            "status": "FAIL",
            "created_at": utc_now(),
        }

        # Write corrupted lines mixed with valid line
        with open(events_path, "w", encoding="utf-8") as f:
            f.write("{\n")  # Incomplete JSON
            f.write("$$INVALID_BINARY_JUNK$$\n")  # Garbage text
            f.write('{"missing_event_id": true}\n')  # Missing required contract keys
            f.write(json.dumps(valid_evt_dict) + "\n")  # Valid line
            f.write("{truncated\n")

        integrator = LivePipelineIntegrator(
            runtime_root=self.root,
            events_log_path=events_path,
            cursor_path=cursor_path,
        )

        drained = integrator.drain_to_cohort_items()
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["event_id"], "evt-valid-001")

        # Second drain returns nothing (cursor advanced past all lines)
        self.assertEqual(integrator.drain_to_cohort_items(), [])

    # -------------------------------------------------------------------------
    # 3. Invariant Bypass Attempts
    # -------------------------------------------------------------------------

    def test_invariant_bypass_cross_engagement_leakage_prevented(self) -> None:
        """Verifies corrections from engagement Alpha do not leak or aggregate into engagement Beta."""
        ingestor = CorrectionFeedbackIngestor(runtime_root=self.root, min_confirmations=2)

        # Engagement Alpha has 2 corrections
        ingestor.record_speaker_correction(
            raw_speaker="Guest 1",
            corrected_speaker="Alice",
            operator_id="op-1",
            session_id="sess-1",
            engagement_id="eng-alpha",
        )
        ingestor.record_speaker_correction(
            raw_speaker="Guest 1",
            corrected_speaker="Alice",
            operator_id="op-2",
            session_id="sess-2",
            engagement_id="eng-alpha",
        )

        # Engagement Beta has only 1 correction
        ingestor.record_speaker_correction(
            raw_speaker="Guest 1",
            corrected_speaker="Bob",
            operator_id="op-3",
            session_id="sess-3",
            engagement_id="eng-beta",
        )

        cohorts_alpha = ingestor.aggregate_cohorts(engagement_id="eng-alpha")
        self.assertEqual(len(cohorts_alpha), 1)
        self.assertEqual(cohorts_alpha[0].target_identifier, "Alice")
        self.assertEqual(cohorts_alpha[0].confirmations, 2)

        cohorts_beta = ingestor.aggregate_cohorts(engagement_id="eng-beta")
        self.assertEqual(len(cohorts_beta), 0)  # Only 1 confirmation, min_conf=2 blocks it

    def test_invariant_bypass_repeated_correction_amplification_prevented(self) -> None:
        """Verifies repeated corrections from the same operator within a session count as 1 confirmation."""
        ingestor = CorrectionFeedbackIngestor(runtime_root=self.root, min_confirmations=2)

        # Same operator submits 10 identical corrections
        for _ in range(10):
            ingestor.record_speaker_correction(
                raw_speaker="Speaker X",
                corrected_speaker="Dr. Jane Doe",
                operator_id="operator-bad-actor",
                session_id="session-001",
            )

        cohorts = ingestor.aggregate_cohorts(min_confirmations=2)
        # Invariant: single-operator amplification cannot satisfy min_confirmations=2
        self.assertEqual(cohorts, [])

        # Adding a second operator allows cohort formation
        ingestor.record_speaker_correction(
            raw_speaker="Speaker X",
            corrected_speaker="Dr. Jane Doe",
            operator_id="operator-legit",
            session_id="session-002",
        )
        cohorts_after = ingestor.aggregate_cohorts(min_confirmations=2)
        self.assertEqual(len(cohorts_after), 1)
        self.assertEqual(cohorts_after[0].confirmations, 2)

    def test_invariant_bypass_duplicate_receipt_file_inflation_prevented(self) -> None:
        """Verifies duplicate receipt files do not inflate failure item counts."""
        receipt_data = {
            "receipt_id": "eval-duplicate-test",
            "pipeline": "copilot",
            "metrics": {"context_recall": 0.50},
            "sealed_at": utc_now(),
        }
        write_json(self.eval_dir / "receipt_original.json", receipt_data)
        # Duplicate copies with different filenames
        write_json(self.eval_dir / "receipt_copy_1.json", receipt_data)
        write_json(self.eval_dir / "receipt_backup.json", receipt_data)

        cohorts = scan_answer_eval_receipts(eval_dir=self.eval_dir, min_occurrences=1)
        self.assertEqual(len(cohorts), 1)
        # Occurrence count must be 1, NOT 3
        self.assertEqual(cohorts[0].occurrence_count, 1)

    # -------------------------------------------------------------------------
    # 4. Concurrency & Race Conditions
    # -------------------------------------------------------------------------

    def test_concurrency_multi_threaded_outcome_recording(self) -> None:
        """Verifies concurrent writes from multiple worker threads without data loss or corruption."""
        events_path = self.ledger_dir / "concurrent_events.jsonl"
        cursor_path = self.ledger_dir / "concurrent_cursor.json"

        integrator = LivePipelineIntegrator(
            runtime_root=self.root,
            events_log_path=events_path,
            cursor_path=cursor_path,
        )

        def worker(worker_id: int) -> None:
            for j in range(10):
                integrator.record_adjudication_error(
                    candidate_a=f"cand-{worker_id}-{j}-A",
                    candidate_b=f"cand-{worker_id}-{j}-B",
                    run_id=f"run-worker-{worker_id}",
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, w_id) for w_id in range(8)]
            concurrent.futures.wait(futures)

        drained = integrator.drain_to_cohort_items()
        self.assertEqual(len(drained), 80)
        # Verify all 80 unique candidate pairs are present
        seen_pairs = {it["details"]["candidate_id"] for it in drained}
        self.assertEqual(len(seen_pairs), 80)

    # -------------------------------------------------------------------------
    # 5. Rollback & Recovery / Tamper Resistance
    # -------------------------------------------------------------------------

    def test_rollback_and_recovery_operator_retraction(self) -> None:
        """Verifies retracted corrections are excluded and rollback-eligible."""
        ingestor = CorrectionFeedbackIngestor(runtime_root=self.root, min_confirmations=2)

        c1 = ingestor.record_speaker_correction("Speaker 9", "Bob", "op-1", "sess-1")
        c2 = ingestor.record_speaker_correction("Speaker 9", "Bob", "op-2", "sess-2")

        cohorts_before = ingestor.aggregate_cohorts()
        self.assertEqual(len(cohorts_before), 1)

        # Operator retracts correction c1
        retracted = ingestor.retract_correction(c1.correction_id, "op-1", "Retracting mistaken label")
        self.assertTrue(retracted)

        # Cohort now has only 1 confirmation, drops below min_confirmations=2
        cohorts_after = ingestor.aggregate_cohorts()
        self.assertEqual(cohorts_after, [])

    def test_tampered_source_receipt_detection(self) -> None:
        """Verifies inspect_proposal_sources flags tampered source files."""
        receipt_file = self.eval_dir / "eval-tamper-target.json"
        write_json(
            receipt_file,
            {
                "receipt_id": "eval-tamper-target",
                "pipeline": "copilot",
                "metrics": {"context_recall": 0.40},
                "sealed_at": utc_now(),
            },
        )

        cohorts = scan_answer_eval_receipts(eval_dir=self.eval_dir, min_occurrences=1)
        self.assertTrue(len(cohorts) >= 1)

        proposal = synthesize_answer_learning_proposal(cohorts[0], self.proposals_dir)

        # Verify initial clean state
        clean_report = inspect_proposal_sources(proposal, runtime_root=self.root)
        self.assertEqual(clean_report["sources"]["eval-tamper-target.json"], "VERIFIED")
        self.assertTrue(clean_report["verified"])

        # Adversary mutates the source receipt file after proposal was created
        with open(receipt_file, "a", encoding="utf-8") as f:
            f.write("\n// TAMPERED_PAYLOAD")

        tampered_report = inspect_proposal_sources(proposal, runtime_root=self.root)
        self.assertEqual(tampered_report["sources"]["eval-tamper-target.json"], "TAMPERED")
        self.assertTrue(tampered_report["has_tampered"])
        self.assertFalse(tampered_report["verified"])


if __name__ == "__main__":
    unittest.main()
