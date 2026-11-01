"""
tests/adversarial/test_hitl_resolution_w4_adversarial.py

Class B Adversarial and Boundary Test Suite for Wave 4:
Unify clearance and close legacy authority bypasses.

Covers mandatory edge-case categories:
1. Malformed input: empty/whitespace decision IDs, truncated JSON, duplicate IDs in scope.
2. Invariant bypass attempts: prefix IDs, SQL wildcards, status text in markdown, cross-wave leakage, wrong-wave attestations.
3. Corrupt authoritative storage: Merkle chain tampering fails closed without JSON fallback, tampered signatures.
4. Exact regression & human-only protection: boolean verified claims, engine signatures in mutation lanes.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    _evaluate_clearance_record,
    check_active_ingestion_clearance,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.eligibility import (
    evaluate_decision_eligibility,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.models import (
    AuthorityType,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (
    load_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.storage import (
    AutonomousResolutionStore,
)
from dual_pipeline.hitl_governance_core.decision_clearance import (
    EXIT_CODE_NO_DECISIONS,
    EXIT_CODE_OPERATIONAL_ERROR,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
    require_cleared,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core import (
    ClearanceVerificationRequest,
    NoDecisionAttestation,
    PreWaveDecision,
    verify_decision_clearance,
)
from dual_pipeline.hitl_governance_core.pre_wave_interceptor import (
    evaluate_pre_wave_gate,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
)


class HitlResolutionW4AdversarialTests(unittest.TestCase):
    """Adversarial and stress tests for Wave 4 authority verification and bypass resistance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.sqlite_path = self.temp_path / "hitl_decision_ledger.sqlite"
        self.json_path = self.temp_path / "hitl_decision_ledger.json"
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.policy_snapshot = load_policy_snapshot()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Category 1: Malformed Input
    def test_adversarial_malformed_empty_decision_id(self) -> None:
        """Empty or whitespace-only decision ID fails closed with INVALID_INPUT."""
        for bad_id in ("", "   ", "\t\n"):
            req = ClearanceVerificationRequest(decision_id=bad_id)
            res = verify_decision_clearance(req, sqlite_path=self.sqlite_path)
            self.assertFalse(res.cleared)
            self.assertEqual(res.status_code, "INVALID_INPUT")

    def test_adversarial_corrupt_and_truncated_decisions_file_fails_closed(self) -> None:
        """Corrupt or non-JSON decisions file fails closed with exit code 1."""
        corrupt_file = self.temp_path / "corrupt_decisions.json"
        corrupt_file.write_text('{"unclosed": "brace"', encoding="utf-8")

        res = evaluate_pre_wave_gate(
            wave=4,
            decisions_file=corrupt_file,
            ledger_path=self.sqlite_path,
        )
        self.assertFalse(res.clearance_granted)
        self.assertEqual(res.exit_code, EXIT_CODE_OPERATIONAL_ERROR)
        self.assertTrue(any("Error parsing decisions file" in d for d in res.diagnostics))

    def test_adversarial_duplicate_decision_ids_in_scope_fails_closed(self) -> None:
        """Duplicate decision IDs within the target wave fail closed with exit code 1."""
        d1 = PreWaveDecision(
            decision_id="DEC-W4-DUP", wave=4, status="OPEN",
            question="Duplicate choice 1?", options=[{"label": "Opt A"}]
        )
        d2 = PreWaveDecision(
            decision_id="DEC-W4-DUP", wave=4, status="OPEN",
            question="Duplicate choice 2?", options=[{"label": "Opt B"}]
        )

        res = evaluate_pre_wave_gate(
            wave=4,
            extra_decisions=[d1, d2],
            ledger_path=self.sqlite_path,
        )
        self.assertFalse(res.clearance_granted)
        self.assertEqual(res.exit_code, EXIT_CODE_OPERATIONAL_ERROR)
        self.assertTrue(any("DUPLICATE_DECISION_ID" in d for d in res.diagnostics))

    # Category 2: Invariant Bypass Attempts
    def test_adversarial_prefix_id_matching_strictly_rejected(self) -> None:
        """Legitimate record for DEC-W4-001 does NOT clear prefix or extended IDs."""
        record_operator_answer(
            decision_id="DEC-W4-001",
            question="Base question?",
            options=[{"label": "Opt A", "confidence_score": 0.8}],
            selected_option={"label": "Opt A", "confidence_score": 0.8},
            operator_origin="Approval verified from authentic human operator origin.",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        extended_ids = [
            "DEC-W4-001_subtask",
            "DEC-W4-001-other",
            "DEC-W4-001_extra",
            "PREFIX_DEC-W4-001",
        ]
        for hostile_id in extended_ids:
            req = ClearanceVerificationRequest(decision_id=hostile_id)
            res = verify_decision_clearance(req, sqlite_path=self.sqlite_path)
            self.assertFalse(res.cleared, f"Hostile prefix ID '{hostile_id}' must not be cleared.")
            self.assertEqual(res.status_code, "UNRATIFIED")

    def test_adversarial_sql_wildcard_injection_rejected(self) -> None:
        """SQL wildcards (%) and (_) in decision_id do not match arbitrary records."""
        record_operator_answer(
            decision_id="DEC-W4-TARGET",
            question="Target question?",
            options=[{"label": "Opt A", "confidence_score": 0.8}],
            selected_option={"label": "Opt A", "confidence_score": 0.8},
            operator_origin="Approval verified from authentic human operator origin.",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        wildcard_ids = ["DEC-W4-%", "DEC-W4-_", "%", "_"]
        for wildcard_id in wildcard_ids:
            req = ClearanceVerificationRequest(decision_id=wildcard_id)
            res = verify_decision_clearance(req, sqlite_path=self.sqlite_path)
            self.assertFalse(res.cleared, f"SQL wildcard '{wildcard_id}' must not grant clearance.")

    def test_adversarial_status_text_alone_in_plan_fails_closed(self) -> None:
        """Markdown plan marking status: RATIFIED without store record fails closed."""
        plan_content = """# Hostile Plan
### Decision DEC-W4-FORGED: Forged ratification claim
- **Wave**: 4
- **Status**: RATIFIED
- **Question**: Is this cleared?
- **Option A**: Yes
- **Option B**: No
"""
        plan_path = self.temp_path / "WAVE_PLAN_FORGED.md"
        plan_path.write_text(plan_content, encoding="utf-8")

        res = evaluate_pre_wave_gate(
            wave=4,
            plan_path=plan_path,
            ledger_path=self.sqlite_path,
        )
        self.assertFalse(res.clearance_granted)
        self.assertEqual(res.exit_code, EXIT_CODE_OPERATOR_INPUT_REQUIRED)
        self.assertEqual(len(res.open_decisions), 1)

    def test_adversarial_cross_wave_authority_leakage_rejected(self) -> None:
        """A decision approved for Wave 1 cannot clear the same decision evaluated for Wave 2."""
        record_operator_answer(
            decision_id="DEC-W-CROSS",
            question="Cross wave question?",
            options=[{"label": "Opt A", "confidence_score": 0.8}],
            selected_option={"label": "Opt A", "confidence_score": 0.8},
            operator_origin="Approval verified from authentic human operator origin.",
            metadata={"wave": 1},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        req_wave2 = ClearanceVerificationRequest(decision_id="DEC-W-CROSS", wave=2)
        res = verify_decision_clearance(req_wave2, sqlite_path=self.sqlite_path)
        self.assertFalse(res.cleared)
        self.assertEqual(res.status_code, "REJECTED_SCOPE_MISMATCH")

    def test_adversarial_wrong_wave_no_decision_attestation_fails(self) -> None:
        """An attestation certifying Wave 3 does not clear an empty Wave 4."""
        plan_content = """# Misattested Plan
### No Decisions In Scope (Wave 3)
Attested zero decisions for Wave 3 only.

## Wave 4: Execution
### Milestones & Deliverables
- [MODIFY] code.py
### Acceptance Criteria
- Pass
### Runtime Receipt & Completion Gate
- .venv/bin/python test
"""
        plan_path = self.temp_path / "WAVE_PLAN_MISATTEST.md"
        plan_path.write_text(plan_content, encoding="utf-8")

        res = evaluate_pre_wave_gate(
            wave=4,
            plan_path=plan_path,
            ledger_path=self.sqlite_path,
        )
        self.assertFalse(res.clearance_granted)
        self.assertEqual(res.exit_code, EXIT_CODE_NO_DECISIONS)

    # Category 3: Corrupt Authoritative Storage & Rollback
    def test_adversarial_corrupt_sqlite_never_falls_back_to_json(self) -> None:
        """Corrupt Merkle chain in SQLite fails closed and never falls back to JSON ledger."""
        # 1. Write authentic record to JSON ledger fallback
        self.json_path.write_text(json.dumps([{
            "decision_id": "DEC-W4-STORE-CORRUPT",
            "operator_id": "operator_alice",
            "metadata": {"approval_origin": "Approval verified from authentic human operator origin."},
            "selected_option": "Opt A",
            "outcome_type": "AFFIRMED",
            "entry_digest": "sha256:valid_digest_123",
        }]), encoding="utf-8")

        # 2. Write valid record to SQLite ledger, then tamper with Merkle digest
        record_operator_answer(
            decision_id="DEC-W4-STORE-CORRUPT",
            question="Question?",
            options=[{"label": "Opt A", "confidence_score": 0.8}],
            selected_option={"label": "Opt A", "confidence_score": 0.8},
            operator_origin="Approval verified from authentic human operator origin.",
            sqlite_path=self.sqlite_path,
        )

        conn = sqlite3.connect(self.sqlite_path)
        conn.execute("UPDATE hitl_decisions SET entry_digest = '0000000000000000000000000000000000000000000000000000000000000000'")
        conn.commit()
        conn.close()

        # 3. Verification MUST FAIL with LEDGER_CHAIN_INTEGRITY_FAILURE without falling back to JSON
        req = ClearanceVerificationRequest(decision_id="DEC-W4-STORE-CORRUPT")
        res = verify_decision_clearance(req, sqlite_path=self.sqlite_path, json_path=self.json_path)
        self.assertFalse(res.cleared)
        self.assertEqual(res.status_code, "REJECTED_CORRUPT_STORE")
        self.assertIn("LEDGER_CHAIN_INTEGRITY_FAILURE", res.reason)

    # Category 4: Exact Regression & Human-Only Protection
    def test_adversarial_boolean_verified_marker_rejected_without_signature(self) -> None:
        """Claiming operator_verified: True without authentic human operator origin fails closed."""
        raw_record = {
            "decision_id": "DEC-W4-UNAUTH-BOOL",
            "operator_verified": True,
            "operator_id": "",
            "metadata": {"operator_verified": True},
        }
        cleared, reason = _evaluate_clearance_record(raw_record)
        self.assertFalse(cleared)
        self.assertIn("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM", reason)

    def test_adversarial_engine_signature_rejected_in_active_ingestion(self) -> None:
        """Engine signatures in operator_id or metadata strictly fail closed in active ingestion."""
        engine_records = [
            {"operator_id": "engine_autonomous_authority_01", "metadata": {}},
            {"operator_id": "human_alice", "metadata": {"signer_role": "AUTONOMOUS_ENGINE"}},
            {"operator_id": "autonomous_engine_worker", "metadata": {}},
            {"operator_id": "alice", "authority_type": "AUTONOMOUS", "metadata": {}},
        ]
        for raw in engine_records:
            cleared, reason = _evaluate_clearance_record(raw)
            self.assertFalse(cleared)
            self.assertTrue(
                "ENGINE_SIGNATURE_REJECTED" in reason or "AUTONOMOUS_AUTHORITY_REJECTED" in reason,
                f"Expected engine rejection for {raw}, got: {reason}",
            )


if __name__ == "__main__":
    unittest.main()
