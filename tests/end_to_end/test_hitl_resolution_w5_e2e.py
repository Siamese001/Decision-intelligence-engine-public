"""
tests/end_to_end/test_hitl_resolution_w5_e2e.py

End-to-end integration tests for Wave 5: Governed Decision Routing and Orchestration.
Verifies:
1. Eligible routine decisions resolve autonomously with zero transport calls.
2. Required human review dispatches one atomic question to a qualified host.
3. Operator decline/cancellation never implies consent and returns no selected option.
4. Unqualified/unsupported host environments stay pending with truthful status.
5. Storage/readback failure fails closed returning no selected option.
6. CLI tool orchestrator integration across evaluation, resolution, and verification.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.autonomous_resolution.models import (
    AuthorityType,
    DecisionMode,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
    PolicySnapshot,
    ResolutionTerminalResult,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.service import (
    AutonomousDecisionResult,
    resolve_governed_decision,
)
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    discover_runtime_capabilities,
    dispatch_interactive_question,
    resolve_or_dispatch_decision,
)


@pytest.mark.timeout(60)
class HitlResolutionW5E2ETests(unittest.TestCase):
    """E2E suite verifying Wave 5 decision routing and orchestration service."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hitl_w5_e2e_")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "autonomous_resolutions.db"
        self.ledger_path = self.root / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_scope(self, wave: int = 5, target: str = "module_x.py") -> DecisionScope:
        return DecisionScope(
            worktree="feat-hitl-w5",
            run_id="run_w5_test",
            wave=wave,
            action_target=target,
            plan_digest="a" * 64,
        )

    def test_e2e_eligible_routine_decision_resolves_autonomously_zero_transport_calls(self) -> None:
        """Eligible routine case: evaluate -> commit -> verify -> return exact option, with transport calls == 0."""
        req = DecisionRequest(
            decision_id="DEC-W5-ROUTINE-E2E-01",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_best", description="Best Option", confidence=0.95),
                DecisionOption(option_id="opt_other", description="Other Option", confidence=0.52),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )

        transport_called = False

        def mock_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal transport_called
            transport_called = True
            return {"result": "opt_best"}

        res: AutonomousDecisionResult = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=mock_transport,
        )

        self.assertEqual(res.status, "RESOLVED_AUTONOMOUSLY")
        self.assertEqual(res.selected_option, "opt_best")
        self.assertEqual(res.terminal_result, ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY)
        self.assertFalse(transport_called, "Transport MUST NOT be called for eligible autonomous resolution.")
        self.assertEqual(res.transport_call_count, 0)
        self.assertIsNotNone(res.record)
        self.assertIsNotNone(res.verification_result)
        self.assertTrue(res.verification_result.cleared)

        # Verify SQLite store and JSON projection exist and are populated
        self.assertTrue(self.db_path.is_file())
        self.assertTrue(self.ledger_path.is_file())
        ledger_data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger_data.get("head_sequence_number"), 1)
        self.assertEqual(len(ledger_data.get("records", [])), 1)

    def test_e2e_human_review_required_dispatch_atomic_question(self) -> None:
        """When ambiguity margin is small (<= 0.30), dispatch one atomic question to qualified host."""
        req = DecisionRequest(
            decision_id="DEC-W5-AMBIGUOUS-E2E-02",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Option 1", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Option 2", confidence=0.65),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )

        transport_calls: list[Dict[str, Any]] = []

        def mock_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            transport_calls.append(packet)
            return {
                "result": "opt_1",
                "operator_id": "operator_primary",
                "metadata": {"approval_origin": "operator_primary"},
            }

        evidence = {"trace_id": "trace-ide-host-01", "status": "QUALIFIED"}

        res: AutonomousDecisionResult = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=mock_transport,
            host_qualification_evidence=evidence,
        )

        self.assertEqual(res.status, "RESOLVED_BY_HUMAN")
        self.assertEqual(res.selected_option, "opt_1")
        self.assertEqual(len(transport_calls), 1, "Exactly one question must be dispatched.")
        self.assertEqual(res.transport_call_count, 1)
        self.assertIsNotNone(res.display_receipt)
        self.assertEqual(res.display_receipt.get("delivery_status"), "DELIVERED")

    def test_e2e_human_declined_and_cancelled_never_implies_consent(self) -> None:
        """Declined or cancelled human responses must never select an option or imply consent."""
        req = DecisionRequest(
            decision_id="DEC-W5-DECLINE-E2E-03",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Option 1", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Option 2", confidence=0.65),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )

        def mock_decline_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "result": "decline",
                "operator_id": "operator_primary",
                "metadata": {"approval_origin": "operator_primary"},
            }

        evidence = {"trace_id": "trace-ide-host-02", "status": "QUALIFIED"}

        res: AutonomousDecisionResult = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=mock_decline_transport,
            host_qualification_evidence=evidence,
        )

        self.assertEqual(res.status, "DECLINED")
        self.assertIsNone(res.selected_option, "Declined decision MUST NOT select an option.")
        self.assertEqual(res.transport_call_count, 1)

    def test_e2e_unsupported_host_stays_pending_truthful_status(self) -> None:
        """When host environment is unqualified, decisions requiring human review stay truthfully pending."""
        req = DecisionRequest(
            decision_id="DEC-W5-UNQUALIFIED-E2E-04",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Option 1", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Option 2", confidence=0.65),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )

        def mock_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            return {"result": "opt_1"}

        # Evidence is None (unsupported host)
        res: AutonomousDecisionResult = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=mock_transport,
            host_qualification_evidence=None,
        )

        self.assertEqual(res.status, "UNSUPPORTED_HOST")
        self.assertIsNone(res.selected_option)
        self.assertEqual(res.transport_call_count, 0)
        self.assertIn("unqualified or unsupported", str(res.error_message).lower())

    def test_e2e_storage_failure_fails_closed_returns_no_choice(self) -> None:
        """Storage write failures must fail closed returning no selected option."""
        req = DecisionRequest(
            decision_id="DEC-W5-FAILCLOSED-E2E-05",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_a", description="Opt A", confidence=0.95),
                DecisionOption(option_id="opt_b", description="Opt B", confidence=0.52),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )

        # Make db_path a directory to trigger SQLite OperationalError
        bad_db = self.root / "blocked_db_dir"
        bad_db.mkdir(parents=True, exist_ok=True)

        res: AutonomousDecisionResult = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=bad_db,
            ledger_path=self.ledger_path,
            max_retries=1,
        )

        self.assertEqual(res.status, "STORAGE_ERROR")
        self.assertIsNone(res.selected_option, "Failed storage MUST NOT return a selected choice.")

    def test_e2e_cli_governed_decision_orchestrator(self) -> None:
        """CLI tool orchestrator executes evaluation, resolution, and verification via subprocess."""
        req_dict = {
            "decision_id": "DEC-W5-CLI-E2E-06",
            "revision": 1,
            "scope": {
                "worktree": "feat-hitl-w5",
                "run_id": "run_cli",
                "wave": 5,
                "action_target": "cli_target.py",
            },
            "options": [
                {"option_id": "opt_x", "description": "Opt X", "confidence": 0.95},
                {"option_id": "opt_y", "description": "Opt Y", "confidence": 0.52},
            ],
            "authority_type": "AUTONOMOUS",
            "mode": "ROUTINE",
            "category": "routine",
        }
        req_file = self.root / "request.json"
        req_file.write_text(json.dumps(req_dict), encoding="utf-8")

        cmd = [
            sys.executable,
            "tools/govern_autonomous_decisions.py",
            "--request-json",
            str(req_file),
            "--runtime-root",
            str(self.root),
            "--mode",
            "resolve",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent.parent))
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertEqual(data.get("status"), "RESOLVED_AUTONOMOUSLY")
        self.assertEqual(data.get("selected_option"), "opt_x")
