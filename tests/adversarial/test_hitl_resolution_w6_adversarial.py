"""
tests/adversarial/test_hitl_resolution_w6_adversarial.py

Adversarial edge-case and attack matrix tests for Wave 6:
1. Audit flooding attack: Replaying 100 identical autonomous records adds 0 mass (Overflow & Exhaustion).
2. Malformed input resilience: Corrupt, non-dict, None, and truncated JSON records in history (Malformed Input).
3. Invariant bypass prevention: Synthetic personas masquerading as human rejected (Invariant Bypass).
4. Secret & credential pre-egress sanitization in audit telemetry (Credential Isolation).
5. Concurrency & race: Multi-threaded SQLite WAL writes and cache consistency (Concurrency & Race).
6. Non-monotonic confidence recovery: Repeated failures depress score, verified human recovery restores it (Rollback & Recovery).
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.hitl_governance_core.audit_events import (
    DecisionResolutionEvent,
    OperatorAnswerEvent,
    emit_audit_event,
    sanitize_audit_payload,
    validate_operator_authenticity,
)
from dual_pipeline.hitl_governance_core.bayesian import (
    _accumulate_historical_weights,
    compute_bayesian_calibrated_score,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.priors import export_learned_priors


class TestHITLResolutionWave6Adversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "test_ledger.sqlite"
        self.json_path = self.root / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(ledger_path=self.json_path, sqlite_path=self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_adv_01_audit_flooding_attack_adds_zero_calibration_mass(self) -> None:
        """Adversarial audit flood: 100 duplicate autonomous records add 0 mass and 0 sample count."""
        category = "flooding_defense"
        # Seed 1 genuine human decision
        self.store.record_decision(
            decision_id="human_seed_01",
            category=category,
            prompt="Genuine human decision",
            options=["opt_legit", "opt_fraud"],
            selected_option="opt_legit",
            operator_id="operator:alice",
        )

        # Flood 100 duplicate autonomous records attempting to inflate opt_fraud
        for _ in range(100):
            self.store.record_decision(
                decision_id="flood_attack_01",
                category=category,
                prompt="Autonomous flood attempt",
                options=["opt_legit", "opt_fraud"],
                selected_option="opt_fraud",
                operator_id="engine_autonomous_authority_01",
                metadata={
                    "authority_type": "AUTONOMOUS",
                    "operator_class": "AUTONOMOUS",
                    "idempotency_key": "auto_idemp_flood_attack_01",
                },
            )

        now = datetime.now(timezone.utc)
        history = self.store.get_history(category)

        # Accumulator must deduplicate the 100 replays to at most 1, and since unverified, mass is 0
        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(history, category, None, now)
        self.assertEqual(aff_w.get("opt_legit", 0.0), 1.0)
        self.assertEqual(aff_c.get("opt_legit", 0), 1)
        self.assertEqual(aff_w.get("opt_fraud", 0.0), 0.0)
        self.assertEqual(aff_c.get("opt_fraud", 0), 0)

    def test_adv_02_secret_and_credential_sanitization_in_audit_events(self) -> None:
        """Telemetry scrubbing: API keys, AWS secrets, bearer tokens, and career leverage are scrubbed."""
        tainted_payload = {
            "decision_id": "dec_tainted_01",
            "api_key": "sk_test_redacted_placeholder",
            "aws_token": "AKIA_REDACTED_EXAMPLE",
            "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload",
            "career_notes": "Aiming for executive compensation band and rapid personal advancement",
            "nested": {
                "password": "SuperSecretPassword123!",
                "message": "Connected with sk_test_redacted_placeholder",
            },
        }

        sanitized = sanitize_audit_payload(tainted_payload)

        # Verify no raw secrets survive in sanitized output
        sanitized_json = json.dumps(sanitized)
        self.assertNotIn("sk-proj-", sanitized_json)
        self.assertNotIn("AKIA_REDACTED_EXAMPLE", sanitized_json)
        self.assertNotIn("SuperSecretPassword123!", sanitized_json)
        self.assertNotIn("executive compensation band", sanitized_json)
        self.assertNotIn("personal advancement", sanitized_json)

        self.assertEqual(sanitized["api_key"], "[SCRUBBED_CREDENTIAL]")
        self.assertEqual(sanitized["nested"]["password"], "[SCRUBBED_CREDENTIAL]")

    def test_adv_03_synthetic_operator_id_rejected_from_human_classification(self) -> None:
        """Anti-fabrication: Automated bots or mock operators cannot masquerade as HUMAN."""
        forged_operators = [
            "auto",
            "autonomous_engine",
            "mock_agent",
            "synthetic_reviewer",
            "engine_autonomous_authority_01",
            "auto_pipeline_bot",
        ]
        for op in forged_operators:
            is_valid, reason = validate_operator_authenticity(op, "HUMAN")
            self.assertFalse(is_valid, f"Expected forgery rejection for '{op}'")
            self.assertIn("SYNTHETIC_HUMAN_FORGERY", reason)

        # Legitimate humans must pass
        valid_humans = ["Amit Ayer", "operator:reviewer_01", "Dr. Jane Doe"]
        for op in valid_humans:
            is_valid, reason = validate_operator_authenticity(op, "HUMAN")
            self.assertTrue(is_valid, f"Expected acceptance for '{op}'")

    def test_adv_04_malformed_and_corrupt_history_handled_gracefully(self) -> None:
        """Malformed input resilience: Null, non-dict, and corrupt records in history do not crash."""
        corrupt_history = [
            None,
            "corrupt string record",
            12345,
            [],
            {"missing_category": True},
            {"category": "test_cat"},  # missing options and selected_key
            {"category": "test_cat", "selected_key": None, "options": None},
            {"category": "test_cat", "selected_key": "opt_valid", "options": ["opt_valid"], "operator_id": "human"},
        ]

        now = datetime.now(timezone.utc)
        # Should not raise exception
        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(corrupt_history, "test_cat", None, now)
        self.assertEqual(aff_w.get("opt_valid"), 1.0)
        self.assertEqual(aff_c.get("opt_valid"), 1)

    def test_adv_05_concurrent_wal_writes_and_cache_freshness(self) -> None:
        """Concurrency stress: Concurrent threads writing to SQLite WAL are immediately visible to reader."""
        category = "concurrent_wal"
        worker_count = 8
        writes_per_worker = 5

        def writer_task(worker_id: int) -> None:
            local_store = HITLDecisionStore(ledger_path=self.json_path, sqlite_path=self.db_path)
            for i in range(writes_per_worker):
                local_store.record_decision(
                    decision_id=f"conc_w{worker_id}_d{i}",
                    category=category,
                    prompt="Concurrent write",
                    options=["opt_conc"],
                    selected_option="opt_conc",
                    operator_id="operator:human",
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(writer_task, wid) for wid in range(worker_count)]
            concurrent.futures.wait(futures)

        # Long-lived reader re-reads: all committed writes must be present
        all_records = self.store.get_history(category)
        expected_total = worker_count * writes_per_worker
        self.assertEqual(len(all_records), expected_total)

    def test_adv_06_non_monotonic_repeated_failures_depress_confidence(self) -> None:
        """Rollback & Recovery: Repeated failures depress confidence, affirmative action restores it."""
        category = "resilience_policy"
        # Seed positive baseline
        self.store.record_decision(
            decision_id="res_01",
            category=category,
            prompt="Initial test",
            options=["opt_candidate", "opt_fallback"],
            selected_option="opt_candidate",
            operator_id="operator:human",
        )
        base = self.store.calibrate_options(
            [{"key": "opt_candidate", "confidence": 0.80}], category=category
        )
        initial_score = float(base[0]["confidence_score"])

        # Inject 5 independent failures
        for idx in range(5):
            self.store.record_decision(
                decision_id=f"fail_event_{idx}",
                category=category,
                prompt="Failure event",
                options=["opt_candidate", "opt_fallback"],
                selected_option="opt_candidate",
                operator_id="operator:human",
                metadata={"outcome_type": "FAILURE"},
            )

        depressed = self.store.calibrate_options(
            [{"key": "opt_candidate", "confidence": 0.80}], category=category
        )
        depressed_score = float(depressed[0]["confidence_score"])
        # Confidence must have dropped significantly
        self.assertLess(depressed_score, initial_score)
        self.assertLess(depressed_score, 0.45)

        # Affirmative recovery: human operator investigates, fixes, and re-affirms
        for idx in range(10):
            self.store.record_decision(
                decision_id=f"recovery_event_{idx}",
                category=category,
                prompt="Recovery affirm",
                options=["opt_candidate", "opt_fallback"],
                selected_option="opt_candidate",
                operator_id="operator:human",
                metadata={"outcome_type": "AFFIRMED"},
            )

        recovered = self.store.calibrate_options(
            [{"key": "opt_candidate", "confidence": 0.80}], category=category
        )
        recovered_score = float(recovered[0]["confidence_score"])
        self.assertGreater(recovered_score, depressed_score)
        self.assertGreaterEqual(recovered_score, 0.70)


if __name__ == "__main__":
    unittest.main()
