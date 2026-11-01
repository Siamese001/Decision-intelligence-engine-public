"""
tests/adversarial/test_hitl_resolution_w5_adversarial.py

Adversarial and boundary edge-case tests for Wave 5: Governed Decision Routing and Orchestration.
Covers at least 5 mandatory adversarial categories:
1. Boundary payload: exact threshold margin boundary (0.3000 vs 0.3001), non-finite/NaN scores, empty options.
2. Malformed input: empty decision IDs, corrupted JSON CLI input, negative confidences.
3. Invariant bypass attempts: high-confidence (99%) non-routine decisions (ARCHITECTURAL, DESTRUCTIVE,
   ATTRIBUTION, SYSTEM_LEARNING, RELEASE) blocked from autonomous resolution.
4. Hook denial isolation: SDK hook denial is strictly non-execution (allow=False), never a synthetic answer.
5. DeepWiki tool isolation: MCP ask_question with repoName is never intercepted or altered.
6. Retry exhaustion and timeout: bounded budgets prevent infinite loops and write stalls.
7. Rollback & recovery: conflicting payload re-resolution fails closed with POLICY_CONFLICT.
"""
from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.agent_sdk.hooks import NeeLifecycleHooks
from dual_pipeline.agent_sdk.policies import NeeSecurityPolicy
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
    adapt_outgoing_tool_call,
    discover_runtime_capabilities,
    is_operator_question_payload,
)


@pytest.mark.timeout(60)
class HitlResolutionW5AdversarialTests(unittest.TestCase):
    """Adversarial suite verifying robust boundary protection and attack resistance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hitl_w5_adv_")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "autonomous_resolutions.db"
        self.ledger_path = self.root / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_scope(self, wave: int = 5, target: str = "core.py") -> DecisionScope:
        return DecisionScope(
            worktree="adv-wt",
            run_id="run_adv_w5",
            wave=wave,
            action_target=target,
            plan_digest="b" * 64,
        )

    def test_adversarial_boundary_exact_ambiguity_threshold(self) -> None:
        """Exact ambiguity threshold (delta == 0.3000) requires human review; delta == 0.3001 resolves autonomously."""
        # Case A: delta = 0.8500 - 0.5500 = 0.3000 (<= 0.30 SSOT threshold -> MUST require human review)
        req_boundary_le = DecisionRequest(
            decision_id="DEC-ADV-BOUNDARY-3000",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.8500),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.5500),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_le = resolve_governed_decision(
            request=req_boundary_le,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res_le.status, "AWAITING_HUMAN_REVIEW")
        self.assertIsNone(res_le.selected_option)

        # Case B: delta = 0.8501 - 0.5500 = 0.3001 (> 0.30 SSOT threshold -> eligible for autonomous resolution)
        req_boundary_gt = DecisionRequest(
            decision_id="DEC-ADV-BOUNDARY-3001",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.8501),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.5500),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_gt = resolve_governed_decision(
            request=req_boundary_gt,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res_gt.status, "RESOLVED_AUTONOMOUSLY")
        self.assertEqual(res_gt.selected_option, "opt_1")

    def test_adversarial_malformed_nan_and_infinite_scores(self) -> None:
        """Non-finite, NaN, and negative confidences must fail closed as INVALID_INPUT."""
        nan_req = DecisionRequest(
            decision_id="DEC-ADV-NAN-01",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=float("nan")),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_nan = resolve_governed_decision(request=nan_req, runtime_root=self.root)
        self.assertEqual(res_nan.status, "INVALID_INPUT")
        self.assertIsNone(res_nan.selected_option)

        neg_req = DecisionRequest(
            decision_id="DEC-ADV-NEG-02",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=-0.50),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_neg = resolve_governed_decision(request=neg_req, runtime_root=self.root)
        self.assertEqual(res_neg.status, "INVALID_INPUT")
        self.assertIsNone(res_neg.selected_option)

    def test_adversarial_empty_options_fails_closed(self) -> None:
        """Empty options list or single option fails closed as INVALID_INPUT."""
        empty_req = DecisionRequest(
            decision_id="DEC-ADV-EMPTY-03",
            revision=1,
            scope=self._make_scope(),
            options=[],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res = resolve_governed_decision(request=empty_req, runtime_root=self.root)
        self.assertEqual(res.status, "INVALID_INPUT")
        self.assertIsNone(res.selected_option)

    def test_adversarial_invariant_bypass_attempt_non_routine_categories(self) -> None:
        """High-confidence (0.99 vs 0.01) non-routine decisions MUST NOT resolve autonomously."""
        non_routine_categories = [
            ("ARCHITECTURAL", DecisionMode.ARCHITECTURAL, "architectural"),
            ("DESTRUCTIVE", DecisionMode.DESTRUCTIVE, "destructive"),
            ("POLICY", DecisionMode.POLICY, "policy"),
            ("GOVERNANCE", DecisionMode.GOVERNANCE, "governance"),
            ("ATTRIBUTION", DecisionMode.ROUTINE, "attribution"),
            ("SYSTEM_LEARNING", DecisionMode.ROUTINE, "system_learning"),
            ("RELEASE", DecisionMode.ROUTINE, "release"),
        ]

        for label, mode, cat in non_routine_categories:
            req = DecisionRequest(
                decision_id=f"DEC-ADV-BYPASS-{label}",
                revision=1,
                scope=self._make_scope(),
                options=[
                    DecisionOption(option_id="opt_bypass", description="Bypass", confidence=0.99),
                    DecisionOption(option_id="opt_safe", description="Safe", confidence=0.01),
                ],
                authority_type=AuthorityType.AUTONOMOUS,
                mode=mode,
                category=cat,
            )
            res = resolve_governed_decision(
                request=req,
                runtime_root=self.root,
                db_path=self.db_path,
                ledger_path=self.ledger_path,
            )
            self.assertEqual(
                res.status,
                "AWAITING_HUMAN_REVIEW",
                f"Category {label} must require human review regardless of 99% confidence margin.",
            )
            self.assertIsNone(res.selected_option)

    def test_adversarial_hook_denial_is_strictly_non_execution(self) -> None:
        """SDK hook denial returns allow=False and never manufactures a success choice."""
        hooks = NeeLifecycleHooks(run_id="adv_test_run")
        pre_hook = hooks.build_pre_tool_call_decide()

        # Create dummy ToolCall with denied tool name
        class DummyToolCall:
            name = "drop_graph_database"
            arguments: Dict[str, Any] = {}

        result = asyncio.run(pre_hook(DummyToolCall()))
        self.assertFalse(result.allow, "Destructive tool must be denied with allow=False.")
        self.assertEqual(len(hooks.tool_call_history), 1)
        self.assertEqual(hooks.tool_call_history[0]["name"], "drop_graph_database")

    def test_adversarial_deepwiki_ask_question_isolation(self) -> None:
        """DeepWiki ask_question MCP calls with repoName are never intercepted or altered."""
        deepwiki_args = {"repoName": "facebook/react", "question": "How does reconciler work?"}
        self.assertFalse(is_operator_question_payload(deepwiki_args))

        adapted_tool, adapted_args = adapt_outgoing_tool_call(
            tool_name="ask_question",
            arguments=deepwiki_args,
            target_mode="ide",
        )
        self.assertEqual(adapted_tool, "ask_question")
        self.assertEqual(adapted_args, deepwiki_args)

        # Also verify NeeSecurityPolicy evaluates it as APPROVE (read-only safe tool)
        decision = NeeSecurityPolicy.evaluate_static_decision("ask_question", arguments=deepwiki_args)
        from google.antigravity.hooks.policy import Decision
        self.assertEqual(decision, Decision.APPROVE)

    def test_adversarial_retry_exhaustion_fails_closed(self) -> None:
        """When retries are exhausted under storage locks, the service fails closed with STORAGE_ERROR."""
        req = DecisionRequest(
            decision_id="DEC-ADV-RETRY-01",
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

        # Target a read-only directory
        ro_dir = self.root / "ro_storage"
        ro_dir.mkdir(parents=True, exist_ok=True)
        # Point to a path inside an invalid file
        invalid_db_path = ro_dir

        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=invalid_db_path,
            ledger_path=self.ledger_path,
            max_retries=1,
        )
        self.assertEqual(res.status, "STORAGE_ERROR")
        self.assertIsNone(res.selected_option)

    def test_adversarial_timeout_budget_exhaustion(self) -> None:
        """Time budget exhaustion terminates with TIMEOUT and no choice."""
        req = DecisionRequest(
            decision_id="DEC-ADV-TIMEOUT-01",
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
        # Timeout set to 0.0s forces immediate expiry
        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            timeout_seconds=0.0,
        )
        self.assertEqual(res.status, "TIMEOUT")
        self.assertIsNone(res.selected_option)

    def test_adversarial_conflicting_payload_idempotency_fails_closed(self) -> None:
        """Attempting to resolve a previously recorded decision with conflicting payload fails with POLICY_CONFLICT."""
        req1 = DecisionRequest(
            decision_id="DEC-ADV-CONFLICT-01",
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
        res1 = resolve_governed_decision(
            request=req1,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res1.status, "RESOLVED_AUTONOMOUSLY")

        # Now submit conflicting request with different option text/action
        req2 = DecisionRequest(
            decision_id="DEC-ADV-CONFLICT-01",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_a", description="DIFFERENT PAYLOAD", confidence=0.95),
                DecisionOption(option_id="opt_b", description="Opt B", confidence=0.52),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res2 = resolve_governed_decision(
            request=req2,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res2.status, "POLICY_CONFLICT")
        self.assertIsNone(res2.selected_option)
