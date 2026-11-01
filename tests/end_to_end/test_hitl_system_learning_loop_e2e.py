"""
tests/end_to_end/test_hitl_system_learning_loop_e2e.py

End-to-End lifecycle test suite for Wave 3 closed-loop HITL learning:
1. Multi-turn prompt generation with initial priors.
2. Authentic human operator answer recording and enriched telemetry persistence.
3. Subsequent prompt evaluation in the same category verifying dynamic Bayesian posterior shifting.
4. Dynamic label text updating with calibrated percentage and tier.
5. Ingestion and Brier score evaluation via ClosedLoopLearningEngine.
6. Export and programmatic retrieval of learned priors.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    resolve_ledger_paths,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.priors import export_learned_priors, load_learned_priors
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.hitl_specs import calculate_hitl_brier_scores
from storage.connection import create_sqlite_conn


class HitlSystemLearningLoopE2ETests(unittest.TestCase):
    """End-to-end integration and lifecycle verification of closed-loop HITL learning."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.s_path, self.j_path = resolve_ledger_paths(repo_root=self.root)
        self.store = HITLDecisionStore(ledger_path=self.j_path, sqlite_path=self.s_path)
        self.human_origin = {"source": "USER_INPUT", "text": "Operator explicit choice"}

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_closed_loop_learning_lifecycle(self) -> None:
        # Step 1: Initial Prompt Generation (Turn 1)
        # Category: role_clarification
        initial_builder = QuestionBuilder(
            "Please confirm Alice's title:",
            metadata={"category": "role_clarification"},
        )
        initial_builder.add_option(
            label="Assign verified professional title Project Lead",
            plain_english_summary="Assign verified title",
            impact_statement="Ensures roster title consistency and maintains professional standards.",
            confidence_score=0.90,
            recommended=True,
        )
        initial_builder.add_option(
            label="Assign approximate title Consultant",
            plain_english_summary="Assign approximate title",
            impact_statement="Allows approximate qualifier but increases ambiguity risk.",
            confidence_score=0.60,
            recommended=False,
        )
        payload_1 = initial_builder.build()
        self.assertEqual(len(payload_1.options), 2)
        # Initial score is unadjusted
        self.assertEqual(payload_1.options[0]["confidence_score"], 0.90)

        # Step 2: Human Operator Overrides Initial Recommendation (Turn 2)
        res_override = record_operator_answer(
            decision_id="dec_e2e_001",
            question="Please confirm Alice's title:",
            options=payload_1.options,
            selected_option=payload_1.options[1],  # Overriding option 0 with option 1
            operator_origin=self.human_origin,
            category="role_clarification",
            rationale="Operator verified Alice is actually Consultant",
            sqlite_path=self.s_path,
            json_path=self.j_path,
        )
        self.assertEqual(res_override.outcome_type, "OVERRIDDEN")
        self.assertAlmostEqual(res_override.margin_at_decision, 0.30, places=2)

        # Confirm persisted row in SQLite
        conn = create_sqlite_conn(self.s_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM hitl_decisions WHERE entry_id = ?", (res_override.entry_id,))
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        self.assertEqual(meta.get("outcome_type"), "OVERRIDDEN")

        # Step 3: Subsequent Prompt in Same Category Shifts Posterior (Turn 3)
        # A new meeting asks about another person with the same role options
        subsequent_builder = QuestionBuilder(
            "Please confirm Bob's title:",
            metadata={"category": "role_clarification"},
        )
        subsequent_builder.add_option(
            label="Assign verified professional title Project Lead",
            plain_english_summary="Assign verified title",
            impact_statement="Ensures roster title consistency and maintains professional standards.",
            confidence_score=0.90,
            recommended=True,
        )
        subsequent_builder.add_option(
            label="Assign approximate title Consultant",
            plain_english_summary="Assign approximate title",
            impact_statement="Allows approximate qualifier but increases ambiguity risk.",
            confidence_score=0.60,
            recommended=False,
        )
        # Point HITL store to this test ledger
        store_subsequent = HITLDecisionStore(ledger_path=self.j_path, sqlite_path=self.s_path)
        calibrated_options = store_subsequent.calibrate_options(
            [
                {"key": "project_lead", "label": "Assign verified professional title Project Lead", "confidence": 0.90, "recommended": True},
                {"key": "consultant", "label": "Assign approximate title Consultant", "confidence": 0.60, "recommended": False},
            ],
            category="role_clarification",
        )

        lead_opt = calibrated_options[0]
        consultant_opt = calibrated_options[1]

        # Project Lead received rejection weight from the override
        self.assertGreaterEqual(lead_opt["confidence_sample_count"], 1)
        self.assertEqual(lead_opt["prior_rejections"], 1)
        # Posterior confidence for Project Lead decreased below original 0.90
        self.assertLess(float(lead_opt["confidence_score"]), 0.90)

        # Consultant received affirmation weight from the operator selection
        self.assertGreaterEqual(consultant_opt["confidence_sample_count"], 1)
        self.assertEqual(consultant_opt["prior_affirmations"], 1)
        # Posterior confidence for Consultant increased above original 0.60
        self.assertGreater(float(consultant_opt["confidence_score"]), 0.60)

        # Step 4: Closed-Loop Learning Engine Ingestion and Brier Score Evaluation (Turn 4)
        engine = ClosedLoopLearningEngine(runtime_root=self.root)
        ingested_decisions = engine.ingest_hitl_decisions(self.j_path)
        self.assertEqual(len(ingested_decisions), 1)
        self.assertEqual(ingested_decisions[0]["outcome_type"], "OVERRIDDEN")

        # Simulate Brier score calibration
        brier_curr, brier_prop = calculate_hitl_brier_scores(
            ingested_decisions,
            parameter_name="hitl.human_feedback_weight",
            proposed_value=1.5,
        )
        self.assertLess(brier_curr, 1.0)
        self.assertLess(brier_prop, brier_curr)

        # Step 5: Export and Load Learned Priors (Turn 5)
        priors_file = self.root / "ledger" / "hitl_prior_calibration.json"
        exported = export_learned_priors(store_subsequent, output_path=priors_file)
        self.assertTrue(priors_file.exists())
        self.assertIn("role_clarification", exported.get("learned_priors", {}))

        loaded = load_learned_priors(priors_file)
        self.assertEqual(loaded.get("total_records_analyzed"), 1)
        self.assertIn("role_clarification", loaded.get("learned_priors", {}))


if __name__ == "__main__":
    unittest.main()
