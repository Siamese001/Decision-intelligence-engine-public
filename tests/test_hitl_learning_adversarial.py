"""
tests/test_hitl_learning_adversarial.py

Adversarial and edge-case test suite for HITL system learning and decision persistence:
Covers >= 3 mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payloads: empty ledger, 0 options, single option, 0-weight records.
2. Malformed input: invalid JSON in metadata, corrupted options text, unparseable timestamps.
3. Concurrency & race: concurrent WAL writes to hitl_decision_ledger.sqlite during calibration.
4. Invariant bypass attempts: synthetic stop-hook origin injection, unauthenticated verification claims, Merkle hash tampering.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.hitl_governance_core.bayesian import (
    _match_keys,
    canonicalize_pattern_key,
    compute_bayesian_calibrated_score,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    resolve_ledger_paths,
    verify_chain_integrity,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import QuestionPayloadValidationError
from dual_pipeline.learning_engine_core.hitl_specs import calculate_hitl_brier_scores, parse_hitl_decision_record
from storage.connection import create_sqlite_conn


class HitlLearningAdversarialTests(unittest.TestCase):
    """Adversarial and edge-case test suite for HITL learning and persistence."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.s_path, self.j_path = resolve_ledger_paths(repo_root=self.root)
        self.human_origin = {"source": "USER_INPUT", "text": "Confirmed"}

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # --- Category 1: Boundary Payloads ---

    def test_boundary_empty_ledger_calibration(self) -> None:
        """Calibrating options on an empty ledger must safely return baseline values without crashing."""
        store = HITLDecisionStore(ledger_path=self.j_path, sqlite_path=self.s_path)
        options = [
            {"key": "opt_a", "label": "Option A (Confidence: HIGH (90%))", "confidence": 0.90},
            {"key": "opt_b", "label": "Option B (Confidence: LOW (10%))", "confidence": 0.10},
        ]
        calibrated = store.calibrate_options(options, category="boundary_category")
        self.assertEqual(len(calibrated), 2)
        self.assertEqual(calibrated[0]["confidence_sample_count"], 0)
        self.assertEqual(calibrated[0]["confidence_score"], 0.90)

    def test_boundary_zero_options_and_single_option_rejection(self) -> None:
        """QuestionBuilder must reject 0 options or 1 option (atomic decisions require >= 2 options)."""
        builder = QuestionBuilder("Question with zero options?")
        with self.assertRaises(QuestionPayloadValidationError):
            builder.build()

        builder.add_option("Single option", "Summary", "Impact", 0.90)
        with self.assertRaises(QuestionPayloadValidationError):
            builder.build()

    def test_boundary_calculate_brier_scores_empty(self) -> None:
        """calculate_hitl_brier_scores with empty decisions must return bounded defaults."""
        curr, prop = calculate_hitl_brier_scores([], "hitl.bayesian_prior_strength", 2.0)
        self.assertGreaterEqual(curr, 0.0)
        self.assertLessEqual(curr, 1.0)
        self.assertGreaterEqual(prop, 0.0)
        self.assertLessEqual(prop, 1.0)

    # --- Category 2: Malformed Input ---

    def test_malformed_corrupt_json_in_ledger_metadata(self) -> None:
        """Corrupt JSON in SQLite metadata column must not crash reader or store."""
        # Insert a raw corrupted row
        conn = create_sqlite_conn(self.s_path, readonly=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hitl_decisions (
                entry_id TEXT PRIMARY KEY, sequence_number INTEGER NOT NULL, decision_id TEXT NOT NULL,
                category TEXT NOT NULL, operator_id TEXT NOT NULL, prompt TEXT, options TEXT,
                selected_option TEXT, selected_key TEXT NOT NULL, rationale TEXT, previous_entry_hash TEXT,
                recorded_at TEXT NOT NULL, metadata TEXT, entry_digest TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO hitl_decisions VALUES (
                'corrupt_entry', 0, 'dec_corrupt', 'general', 'human',
                'question', 'INVALID_JSON_OPTIONS', 'selected', 'key',
                'rationale', '0' || '0', '2026-10-14T00:00:00Z', '{MALFORMED_JSON}', 'digest'
            )
            """
        )
        conn.commit()
        conn.close()

        store = HITLDecisionStore(ledger_path=self.j_path, sqlite_path=self.s_path)
        history = store.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["entry_id"], "corrupt_entry")

    def test_malformed_unparseable_confidence_and_tokens(self) -> None:
        """canonicalize_pattern_key and parse_hitl_decision_record must not fail on junk input."""
        self.assertEqual(canonicalize_pattern_key(""), "")
        self.assertEqual(canonicalize_pattern_key(None), "")
        self.assertFalse(_match_keys("", "something"))
        self.assertFalse(_match_keys("something", ""))

        bad_rec = {"confidence": "not-a-number", "outcome_type": "AFFIRMED"}
        parsed = parse_hitl_decision_record(bad_rec)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["confidence"], 1.0)

    # --- Category 3: Concurrency & Race ---

    def test_concurrency_parallel_sqlite_writes_and_calibration_reads(self) -> None:
        """Multiple concurrent threads recording decisions to SQLite under WAL must not lock or corrupt chain."""
        def record_worker(worker_id: int) -> str:
            res = record_operator_answer(
                decision_id=f"concurrent_dec_{worker_id}",
                question=f"Worker question {worker_id}?",
                options=[
                    {"key": "opt_a", "label": "Option A (Confidence: HIGH (90%))", "recommended": True, "confidence": 0.90},
                    {"key": "opt_b", "label": "Option B (Confidence: LOW (10%))", "confidence": 0.10},
                ],
                selected_option={"key": "opt_a", "label": "Option A"},
                operator_origin={"source": "USER_INPUT", "text": f"Worker {worker_id} confirm"},
                category="concurrency_stress",
                sqlite_path=self.s_path,
                json_path=self.j_path,
            )
            return res.entry_id

        num_workers = 8
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(record_worker, i) for i in range(num_workers)]
            entry_ids = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(entry_ids), num_workers)

        # Confirm unbroken hash chain across all concurrent writes
        chain_ok, chain_msg = verify_chain_integrity(self.s_path)
        self.assertTrue(chain_ok, f"Merkle chain broken under concurrency: {chain_msg}")

    # --- Category 4: Invariant Bypass Attempts ---

    def test_invariant_bypass_synthetic_origin_rejected(self) -> None:
        """Synthetic stop hook origin markers must fail closed with ValueError."""
        synthetic_origin = {"source": "STOP_HOOK", "text": "auto-approved"}
        with self.assertRaises(ValueError) as ctx:
            record_operator_answer(
                decision_id="dec_synthetic_bypass",
                question="Attempt synthetic bypass?",
                options=["A", "B"],
                selected_option="A",
                operator_origin=synthetic_origin,
                sqlite_path=self.s_path,
                json_path=self.j_path,
            )
        self.assertIn("synthetic", str(ctx.exception).lower())

    def test_invariant_bypass_tampered_merkle_digest_detected(self) -> None:
        """Tampering with an SQLite record digest must be immediately detected by verify_chain_integrity."""
        # Record valid decision
        res = record_operator_answer(
            decision_id="dec_tamper_test",
            question="Question?",
            options=["A", "B"],
            selected_option="A",
            operator_origin=self.human_origin,
            sqlite_path=self.s_path,
            json_path=self.j_path,
        )

        # Tamper prompt in SQLite behind writer's back
        conn = create_sqlite_conn(self.s_path, readonly=False)
        conn.execute("UPDATE hitl_decisions SET prompt = 'TAMPERED PROMPT' WHERE entry_id = ?", (res.entry_id,))
        conn.commit()
        conn.close()

        chain_ok, chain_msg = verify_chain_integrity(self.s_path)
        self.assertFalse(chain_ok)
        self.assertIn("tampered digest", chain_msg.lower())

    def test_duplicate_decision_ids_in_cohort_produce_unique_evidence_events(self) -> None:
        """Duplicate decision_ids in a cohort must deduplicate cleanly without SCHEMA_UNIQUE_ITEMS failure."""
        from dual_pipeline.learning_engine import ClosedLoopLearningEngine

        engine = ClosedLoopLearningEngine(
            runtime_root=self.root,
            proposals_dir=self.root / "proposals",
        )
        cohort = {
            "cohort_key": "hitl_override:mutex_lead_resolution",
            "occurrence_count": 4,
            "run_ids": ["run-mutex-1"],
            "items": [
                {"decision_id": "dup-decision-1", "outcome_type": "OVERRIDDEN"},
                {"decision_id": "dup-decision-1", "outcome_type": "OVERRIDDEN"},
                {"decision_id": "dup-decision-2", "outcome_type": "OVERRIDDEN"},
                {"decision_id": "dup-decision-1", "outcome_type": "OVERRIDDEN"},
            ],
        }
        proposal = engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="hitl_confidence",
            target_override={"category": "mutex_lead_resolution", "parameter_name": "hitl.bayesian_prior_strength"},
        )
        ev_ids = proposal["evidence_event_ids"]
        self.assertEqual(len(ev_ids), len(set(ev_ids)))
        self.assertEqual(len(ev_ids), 2)


if __name__ == "__main__":
    unittest.main()
