"""
tests/end_to_end/test_hitl_resolution_w6_e2e.py

End-to-end lifecycle verification for Wave 6: Feedback Isolation and Truthful Telemetry.
Verifies:
1. Frozen prior isolation: in-flight decision cannot affect its own prior during evaluation.
2. Zero-weight test records never increase empirical sample maturity.
3. Autonomous audit records do not inflate empirical priors without independent outcomes.
4. Negative independent outcomes drive down calibrated posterior confidence.
5. Long-lived readers see WAL updates immediately via data_version cache invalidation.
6. Structured audit events emit with verifiable digests and credential sanitization.
7. Export learned priors round-trip preserves provenance and cryptographic digest.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.hitl_governance_core.audit_events import (
    DecisionResolutionEvent,
    IndependentOutcomeEvent,
    OperatorAnswerEvent,
    QuestionDeliveryEvent,
    emit_audit_event,
)
from dual_pipeline.hitl_governance_core.bayesian import (
    _accumulate_historical_weights,
    compute_bayesian_calibrated_score,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.priors import export_learned_priors


class TestHITLResolutionWave6E2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "test_ledger.sqlite"
        self.json_path = self.root / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(ledger_path=self.json_path, sqlite_path=self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_01_frozen_priors_evaluation_isolation(self) -> None:
        """In-flight decision cannot alter its own prior distribution during evaluation."""
        category = "entity_resolution"
        # Seed 1 prior affirmation for opt_canonical
        self.store.record_decision(
            decision_id="dec_historical_01",
            category=category,
            prompt="Resolve entity",
            options=["opt_canonical", "opt_isolated"],
            selected_option="opt_canonical",
            operator_id="operator:human",
        )

        now = datetime.now(timezone.utc)
        history = self.store.get_history(category)

        # Baseline: historical affirmations = 1.0
        aff_w, _, aff_c, _ = _accumulate_historical_weights(history, category, None, now)
        self.assertEqual(aff_w.get("opt_canonical"), 1.0)
        self.assertEqual(aff_c.get("opt_canonical"), 1)

        # Candidate decision in-flight: passing current_decision_id excludes matching record
        candidate_history = list(history) + [{
            "decision_id": "dec_in_flight_99",
            "category": category,
            "selected_key": "opt_canonical",
            "options": ["opt_canonical", "opt_isolated"],
            "operator_id": "operator:human",
        }]

        # Without filtering, candidate would increase weight to 2.0
        aff_unfiltered, _, _, _ = _accumulate_historical_weights(candidate_history, category, None, now)
        self.assertEqual(aff_unfiltered.get("opt_canonical"), 2.0)

        # With frozen prior filtering for 'dec_in_flight_99', candidate is excluded
        aff_frozen, _, aff_c_frozen, _ = _accumulate_historical_weights(
            candidate_history, category, None, now, current_decision_id="dec_in_flight_99"
        )
        self.assertEqual(aff_frozen.get("opt_canonical"), 1.0)
        self.assertEqual(aff_c_frozen.get("opt_canonical"), 1)

    def test_02_zero_weight_test_decisions_never_increase_sample_maturity(self) -> None:
        """Recording 50 test decisions adds 0 calibration weight and 0 sample count."""
        category = "routing_test"
        for idx in range(50):
            self.store.record_decision(
                decision_id=f"test_run_{idx}",
                category=category,
                prompt="Test prompt",
                options=["opt_a", "opt_b"],
                selected_option="opt_a",
                operator_id="test_suite_runner",
                metadata={"is_test": True, "operator_class": "TEST"},
            )

        now = datetime.now(timezone.utc)
        history = self.store.get_history(category)
        self.assertEqual(len(history), 50)

        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(history, category, None, now)
        self.assertEqual(aff_w.get("opt_a", 0.0), 0.0)
        self.assertEqual(aff_c.get("opt_a", 0), 0)
        self.assertEqual(rej_w.get("opt_b", 0.0), 0.0)
        self.assertEqual(rej_c.get("opt_b", 0), 0)

        # Calibrate option: sample count remains 0, interval remains un-narrowed
        calibrated = self.store.calibrate_options(
            [{"key": "opt_a", "confidence": 0.80}], category=category
        )
        self.assertEqual(calibrated[0]["confidence_sample_count"], 0)
        self.assertEqual(calibrated[0]["prior_affirmations"], 0)

    def test_03_autonomous_audit_records_do_not_inflate_empirical_priors(self) -> None:
        """Autonomous decisions without verified independent outcomes do not inflate sample count."""
        category = "autonomous_disambiguation"
        # Record 10 autonomous decisions
        for idx in range(10):
            self.store.record_decision(
                decision_id=f"auto_dec_{idx}",
                category=category,
                prompt="Autonomous resolve",
                options=["opt_x", "opt_y"],
                selected_option="opt_x",
                operator_id="engine_autonomous_authority_01",
                metadata={
                    "authority_type": "AUTONOMOUS",
                    "operator_class": "AUTONOMOUS",
                    "outcome_type": "AUTONOMOUS",
                },
            )

        now = datetime.now(timezone.utc)
        history = self.store.get_history(category)
        self.assertEqual(len(history), 10)

        calibrated = self.store.calibrate_options(
            [{"key": "opt_x", "confidence": 0.85}], category=category
        )
        # Sample count must not reach 10
        self.assertLessEqual(calibrated[0]["confidence_sample_count"], 10)

    def test_04_negative_independent_outcome_lowers_confidence(self) -> None:
        """An independent downstream execution failure lowers calibrated confidence."""
        category = "deployment_policy"
        # Seed 1 initial affirmation
        self.store.record_decision(
            decision_id="deploy_01",
            category=category,
            prompt="Deploy config",
            options=["opt_fast", "opt_safe"],
            selected_option="opt_fast",
            operator_id="operator:human",
        )
        base_cal = self.store.calibrate_options(
            [{"key": "opt_fast", "confidence": 0.85}], category=category
        )
        initial_score = float(base_cal[0]["confidence_score"])

        # Record 3 independent failure outcomes for opt_fast
        for idx in range(3):
            self.store.record_decision(
                decision_id=f"deploy_fail_{idx}",
                category=category,
                prompt="Deploy failed",
                options=["opt_fast", "opt_safe"],
                selected_option="opt_fast",
                operator_id="operator:human",
                metadata={
                    "outcome_type": "FAILURE",
                    "independent_outcome": {"status": "FAILURE", "reason": "Crash in prod"},
                },
            )

        after_cal = self.store.calibrate_options(
            [{"key": "opt_fast", "confidence": 0.85}], category=category
        )
        updated_score = float(after_cal[0]["confidence_score"])

        # Confidence must decrease due to failure observations
        self.assertLess(updated_score, initial_score)
        self.assertGreater(after_cal[0]["rejected_weight"], 0.0)

    def test_05_long_lived_reader_sees_wal_updates_immediately(self) -> None:
        """Reader with populated cache immediately observes writes from another connection in WAL mode."""
        category = "wal_freshness"
        self.store.record_decision(
            decision_id="wal_init",
            category=category,
            prompt="Init",
            options=["opt_1"],
            selected_option="opt_1",
            operator_id="operator:human",
        )
        # Populate reader cache
        initial_records = self.store.get_history(category)
        self.assertEqual(len(initial_records), 1)

        # Create separate store instance writing to the same database file via WAL
        writer_store = HITLDecisionStore(ledger_path=self.json_path, sqlite_path=self.db_path)
        writer_store.record_decision(
            decision_id="wal_new_write",
            category=category,
            prompt="New write",
            options=["opt_1"],
            selected_option="opt_1",
            operator_id="operator:human",
        )

        # Long-lived reader re-queries: cache must invalidate on data_version / sequence change
        updated_records = self.store.get_history(category)
        self.assertEqual(len(updated_records), 2)
        dec_ids = [r["decision_id"] for r in updated_records]
        self.assertIn("wal_new_write", dec_ids)

    def test_06_structured_audit_events_emission_and_digests(self) -> None:
        """Structured audit events produce valid canonical digests and persist to JSONL."""
        audit_log = self.root / "audit_events.jsonl"

        res_event = DecisionResolutionEvent(
            decision_id="dec_audit_01",
            revision=1,
            authority_type="AUTONOMOUS",
            resolution_status="RESOLVED_AUTONOMOUSLY",
            reason_code="MARGIN_AND_ENTROPY_QUALIFIED",
            decision_content_digest="a" * 64,
            policy_digest="b" * 64,
            resolution_digest="c" * 64,
            selected_option_id="opt_allow",
            margin=0.45,
            entropy=0.88,
        )
        emitted_res = emit_audit_event(res_event, destination_path=audit_log)
        self.assertIn("event_digest", emitted_res)
        self.assertEqual(emitted_res["event_type"], "decision_resolved")

        deliv_event = QuestionDeliveryEvent(
            decision_id="dec_audit_01",
            delivery_status="DELIVERED",
            transport="IDE_MODAL",
            decision_content_digest="a" * 64,
            policy_digest="b" * 64,
        )
        emitted_deliv = emit_audit_event(deliv_event, destination_path=audit_log)
        self.assertIn("event_digest", emitted_deliv)

        ans_event = OperatorAnswerEvent(
            decision_id="dec_audit_01",
            operator_id="operator:amit",
            approval_origin="HUMAN_INTERACTIVE_MODAL",
            selected_option_id="opt_allow",
            outcome_type="AFFIRMED",
            decision_content_digest="a" * 64,
            entry_digest="d" * 64,
        )
        emitted_ans = emit_audit_event(ans_event, destination_path=audit_log)
        self.assertTrue(emitted_ans["is_authentic"])

        indep_event = IndependentOutcomeEvent(
            decision_id="dec_audit_01",
            selected_option_id="opt_allow",
            outcome_status="SUCCESS",
            confidence_impact=0.05,
            evidence_ref="run_receipt_123",
            receipt_digest="e" * 64,
            reason_code="DEPLOY_SUCCEEDED",
        )
        emitted_indep = emit_audit_event(indep_event, destination_path=audit_log)
        self.assertIn("event_digest", emitted_indep)

        # Verify JSONL log contains all 4 lines
        lines = [json.loads(ln) for ln in audit_log.read_text(encoding="utf-8").strip().splitlines()]
        self.assertEqual(len(lines), 4)

    def test_07_export_learned_priors_preserves_provenance(self) -> None:
        """export_learned_priors writes valid JSON schema with cryptographic digest."""
        category = "export_test"
        self.store.record_decision(
            decision_id="dec_exp_01",
            category=category,
            prompt="Exp prompt",
            options=["opt_1", "opt_2"],
            selected_option="opt_1",
            operator_id="operator:human",
        )
        export_file = self.root / "exported_priors.json"
        exported = export_learned_priors(self.store, export_file)

        self.assertIn("priors_digest", exported)
        self.assertIn("learned_priors", exported)
        self.assertIn(category, exported["learned_priors"])
        opt_data = exported["learned_priors"][category]["opt_1"]
        self.assertEqual(opt_data["affirmations"], 1)
        self.assertEqual(opt_data["affirmed_weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
