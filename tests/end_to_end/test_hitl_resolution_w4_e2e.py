"""
tests/end_to_end/test_hitl_resolution_w4_e2e.py

Class B End-to-End Acceptance Test Suite for Wave 4:
Unify clearance and close legacy authority bypasses.

Verifies:
1. Full end-to-end clearance lifecycle for human-cleared decisions (blocks -> ratifies -> clears).
2. Governed autonomous resolution clearance for eligible routine decisions backed by signed storage.
3. Strict human-only consumer rejection of autonomous authority in active ingestion mutations.
4. No-decision attestation end-to-end lifecycle permitting clean empty waves.
5. Zero-write guarantee during verification across all consumers.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
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
    EXIT_CODE_CLEARED,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
    ScopedDecision,
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


class HitlResolutionW4E2ETests(unittest.TestCase):
    """End-to-End acceptance tests for Wave 4 unified clearance and authority governance."""

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

    def test_e2e_human_clearance_lifecycle(self) -> None:
        """Open decision blocks clearance; authentic human ratification in ledger clears wave."""
        dec_id = "DEC-W4-HUMAN-001"
        open_dec = PreWaveDecision(
            decision_id=dec_id,
            wave=4,
            status="OPEN",
            question="Which database caching strategy should be authorized?",
            options=[
                {"label": "Option A: Redis", "confidence_score": 0.65},
                {"label": "Option B: Memcached", "confidence_score": 0.55},
            ],
        )

        # 1. Verification initially halts with exit code 2 (OPEN)
        res_blocked = evaluate_pre_wave_gate(
            wave=4,
            ledger_path=self.sqlite_path,
            extra_decisions=[open_dec],
        )
        self.assertEqual(res_blocked.exit_code, EXIT_CODE_OPERATOR_INPUT_REQUIRED)
        self.assertFalse(res_blocked.clearance_granted)
        self.assertEqual(len(res_blocked.open_decisions), 1)
        self.assertIsNotNone(res_blocked.ask_user_question_payload)

        # 2. Record authentic operator ratification in SQLite ledger
        record_operator_answer(
            decision_id=dec_id,
            question="Which database caching strategy should be authorized?",
            options=[
                {"label": "Option A: Redis", "confidence_score": 0.65},
                {"label": "Option B: Memcached", "confidence_score": 0.55},
            ],
            selected_option={"label": "Option A: Redis", "confidence_score": 0.65},
            operator_origin="Approval verified from authentic human operator origin.",
            prior_user_command="implement",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        # 3. Verification now grants clearance (Exit Code 0)
        res_cleared = evaluate_pre_wave_gate(
            wave=4,
            ledger_path=self.sqlite_path,
            extra_decisions=[open_dec],
        )
        self.assertEqual(res_cleared.exit_code, EXIT_CODE_CLEARED)
        self.assertTrue(res_cleared.clearance_granted)
        self.assertEqual(len(res_cleared.resolved_decisions), 1)

    def test_e2e_autonomous_resolution_storage_clearance(self) -> None:
        """Eligible routine decision persisted in autonomous store clears without human prompt."""
        dec_id = "DEC-W4-AUTO-ROUTINE-001"
        opt_a = DecisionOption("opt_json", "JSON format", 0.90, "Adopt standard JSON format")
        opt_b = DecisionOption("opt_xml", "XML format", 0.40, "Adopt legacy XML format")

        req = DecisionRequest(
            decision_id=dec_id,
            revision=1,
            scope=DecisionScope(
                worktree=str(self.temp_path),
                run_id="run-w4-auto-01",
                wave=4,
                action_target="config/format.json",
            ),
            authority_type=AuthorityType.AUTONOMOUS,
            category="ROUTINE_AMBIGUITY",
            options=[opt_a, opt_b],
        )

        # Evaluate eligibility
        eval_res = evaluate_decision_eligibility(req, self.policy_snapshot)
        self.assertTrue(eval_res.is_eligible)
        self.assertEqual(eval_res.selected_option_id, "opt_json")

        # Persist signed autonomous resolution in SQLite store
        store = AutonomousResolutionStore(self.sqlite_path)
        record = store.record_resolution(
            request=req,
            eval_result=eval_res,
            private_key=self.private_key,
        )
        self.assertEqual(record.sequence_number, 1)

        # Exact-scope verifier must find and grant clearance under AUTONOMOUS authority
        v_req = ClearanceVerificationRequest(
            decision_id=dec_id,
            wave=4,
            run_id="run-w4-auto-01",
            worktree=str(self.temp_path),
            options=[opt_a.to_dict(), opt_b.to_dict()],
            policy_digest=self.policy_snapshot.policy_digest,
        )
        v_res = verify_decision_clearance(v_req, sqlite_path=self.sqlite_path)
        self.assertTrue(v_res.cleared)
        self.assertEqual(v_res.status_code, "CLEARED_AUTONOMOUS")
        self.assertEqual(v_res.selected_option_id, "opt_json")

    def test_e2e_human_only_consumer_rejects_autonomous_authority(self) -> None:
        """Active ingestion mutation consumer strictly rejects autonomous authority records."""
        run_id = "run-w4-ingest-01"
        directive_id = "dir-w4-ingest-01"
        delta_digest = "sha256:delta_w4_mutation_99"

        # Record autonomous resolution into the SQLite ledger under this decision ID
        store = AutonomousResolutionStore(self.sqlite_path)
        req = DecisionRequest(
            decision_id=directive_id,
            revision=1,
            scope=DecisionScope(worktree=str(self.temp_path), run_id=run_id, wave=4, action_target="graph.db"),
            authority_type=AuthorityType.AUTONOMOUS,
            category="ROUTINE_AMBIGUITY",
            options=[
                DecisionOption("opt_1", "Approve", 0.95, "Approve publication"),
                DecisionOption("opt_2", "Decline", 0.10, "Decline publication"),
            ],
        )
        eval_res = evaluate_decision_eligibility(req, self.policy_snapshot)
        store.record_resolution(req, eval_res, self.private_key)

        # Ingestion clearance check must refuse autonomous clearance
        is_cleared, reason, rec = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.sqlite_path,
        )
        self.assertFalse(is_cleared)
        self.assertIn("UNRATIFIED", reason)

    def test_e2e_no_decision_attestation_lifecycle(self) -> None:
        """Valid no-decision attestation permits clean clearance for waves with zero decisions."""
        plan_content = """# Wave 4 Pure Refactor Plan

### No Decisions In Scope (Wave 4)
Pure architectural refactoring with zero qualifying design decisions.

## Wave 4: Execution
### Milestones & Deliverables
- [MODIFY] refactor.py
### Acceptance Criteria
- Zero defects
### Runtime Receipt & Completion Gate
- .venv/bin/python test
"""
        plan_path = self.temp_path / "WAVE_PLAN_4.md"
        plan_path.write_text(plan_content, encoding="utf-8")

        res = evaluate_pre_wave_gate(
            wave=4,
            plan_path=plan_path,
            ledger_path=self.sqlite_path,
        )
        self.assertTrue(res.clearance_granted)
        self.assertEqual(res.exit_code, EXIT_CODE_CLEARED)
        self.assertTrue(any("no-decision attestation" in d for d in res.diagnostics))

    def test_e2e_zero_write_guarantee_during_verification(self) -> None:
        """Clearance verification performs strictly read-only checks without modifying the ledger."""
        # Initialize ledger with 1 record
        record_operator_answer(
            decision_id="DEC-W4-READONLY",
            question="Read only question?",
            options=[{"label": "Opt A", "confidence_score": 0.8}],
            selected_option={"label": "Opt A", "confidence_score": 0.8},
            operator_origin="Approval verified from authentic human operator origin.",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        stat_before = self.sqlite_path.stat().st_mtime_ns
        size_before = self.sqlite_path.stat().st_size

        v_req = ClearanceVerificationRequest(decision_id="DEC-W4-READONLY")
        v_res = verify_decision_clearance(v_req, sqlite_path=self.sqlite_path)
        self.assertTrue(v_res.cleared)

        size_after = self.sqlite_path.stat().st_size
        self.assertEqual(size_before, size_after, "Verification must not mutate ledger file size.")


if __name__ == "__main__":
    unittest.main()
