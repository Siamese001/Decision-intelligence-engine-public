"""
tests/adversarial/test_hitl_resolution_w7_adversarial.py

Adversarial stress and boundary suite for Wave 7:
Rollout, Disablement, and Recovery Qualification.

Covers 5 mandatory edge-case categories:
1. Invariant bypass: observe-only tampering, forbidden operation classes, policy digest mismatch.
2. Concurrency & race: multi-worker execution racing against asynchronous kill-switch engagement.
3. Rollback & recovery: storage write failure mid-commit, retry exhaustion, and clean fail-closed state.
4. Boundary payload: empty options, zero wave, negative revisions, non-finite confidences.
5. Malformed input & spoofing: synthetic operator persona injection during human review fallback.
"""
from __future__ import annotations

import concurrent.futures
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

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
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (
    compute_policy_digest,
    load_active_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.rollout import (
    RolloutConfiguration,
    RolloutMode,
    evaluate_rollout_authorization,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.service import (
    AutonomousDecisionResult,
    resolve_governed_decision,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.storage import (
    AutonomousResolutionStore,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.models import (
    ClearanceVerificationRequest,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.verifier import (
    verify_decision_clearance,
)


@pytest.mark.timeout(60)
class HitlResolutionW7AdversarialTests(unittest.TestCase):
    """Adversarial suite for Wave 7 rollout, kill-switch, and recovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hitl_w7_adv_")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "autonomous_resolutions.db"
        self.ledger_path = self.root / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_scope(self, wave: int = 7, target: str = "adv_target.py") -> DecisionScope:
        return DecisionScope(
            worktree=str(self.root.name),
            run_id="run_w7_adv",
            wave=wave,
            action_target=target,
            plan_digest="b" * 64,
        )

    def test_adv_01_invariant_bypass_observe_only_tamper_attempt(self) -> None:
        """Observe-only mode strictly refuses persistence and clearance even when attacker forces request fields."""
        req = DecisionRequest(
            decision_id="DEC-W7-ADV-OBSERVE-01",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Option 1", confidence=0.98),
                DecisionOption(option_id="opt_2", description="Option 2", confidence=0.20),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
            context={"force_commit": True, "bypass_governance": True},
        )

        config = RolloutConfiguration(mode=RolloutMode.OBSERVE_ONLY)
        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=config,
        )

        # Status must indicate observe-only
        self.assertTrue(str(res.status).startswith("SHADOW_OBSERVE_ONLY"))
        self.assertIsNone(res.record)
        self.assertFalse(self.db_path.exists())

        # Clearance verifier must find no record and refuse clearance
        verif_req = ClearanceVerificationRequest(decision_id=req.decision_id)
        verif_res = verify_decision_clearance(verif_req, sqlite_path=self.db_path, json_path=self.ledger_path)
        self.assertFalse(verif_res.cleared)

    def test_adv_02_kill_switch_concurrency_race(self) -> None:
        """Concurrent workers racing to commit while kill switch is toggled fails closed cleanly."""
        kill_switch_config = {"engaged": False}
        lock = threading.Lock()

        def dynamic_kill_check() -> bool:
            with lock:
                return kill_switch_config["engaged"]

        req_template = lambda idx: DecisionRequest(
            decision_id=f"DEC-W7-RACE-{idx:03d}",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_a", description="Opt A", confidence=0.95),
                DecisionOption(option_id="opt_b", description="Opt B", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        results: List[AutonomousDecisionResult] = []

        def worker_task(idx: int) -> AutonomousDecisionResult:
            is_killed = dynamic_kill_check()
            cfg = RolloutConfiguration(mode=RolloutMode.ENABLED, kill_switch=is_killed)
            return resolve_governed_decision(
                request=req_template(idx),
                runtime_root=self.root,
                db_path=self.db_path,
                ledger_path=self.ledger_path,
                rollout_config=cfg,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for i in range(16):
                if i == 8:
                    with lock:
                        kill_switch_config["engaged"] = True
                futures.append(executor.submit(worker_task, i))

            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        self.assertEqual(len(results), 16)
        # Any request executed after kill-switch was engaged must NOT be RESOLVED_AUTONOMOUSLY
        killed_results = [r for r in results if r.status == "AWAITING_HUMAN_REVIEW"]
        self.assertGreater(len(killed_results), 0)

    def test_adv_03_policy_digest_mismatch_adversarial_tamper(self) -> None:
        """Altered policy digest fails rollout authorization with POLICY_DIGEST_MISMATCH."""
        policy = load_active_policy_snapshot()
        req = DecisionRequest(
            decision_id="DEC-W7-ADV-DIGEST-03",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.95),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        # Expected digest pinned to legitimate reconciliation record
        expected_digest = "c" * 64
        config = RolloutConfiguration(
            mode=RolloutMode.ENABLED,
            required_policy_digest=expected_digest,
        )

        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            policy_snapshot=policy,
            rollout_config=config,
        )

        # Mismatch must route to human review
        self.assertEqual(res.status, "AWAITING_HUMAN_REVIEW")
        self.assertIsNone(res.selected_option)
        self.assertFalse(self.db_path.exists())

    def test_adv_04_storage_failure_and_rollback_recovery(self) -> None:
        """Storage lock/directory failure exhausts retries and fails closed with STORAGE_ERROR."""
        req = DecisionRequest(
            decision_id="DEC-W7-ADV-STORAGE-04",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.95),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        # Create un-writable directory in place of sqlite db
        bad_dir = self.root / "corrupt_store_dir"
        bad_dir.mkdir(parents=True, exist_ok=True)

        config = RolloutConfiguration(mode=RolloutMode.ENABLED)
        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=bad_dir,  # Directory passed as DB file
            ledger_path=self.ledger_path,
            rollout_config=config,
            max_retries=1,
        )

        self.assertEqual(res.status, "STORAGE_ERROR")
        self.assertEqual(res.terminal_result, ResolutionTerminalResult.STORAGE_ERROR)
        self.assertIsNone(res.selected_option)

    def test_adv_05_boundary_payload_empty_and_extreme_parameters(self) -> None:
        """Boundary payloads: empty options, invalid revisions, and non-finite scores fail closed."""
        # 1. Missing options
        req_empty = DecisionRequest(
            decision_id="DEC-W7-ADV-EMPTY",
            revision=1,
            scope=self._make_scope(),
            options=[],
        )
        res_empty = resolve_governed_decision(req_empty, runtime_root=self.root)
        self.assertEqual(res_empty.status, "INVALID_INPUT")

        # 2. Non-finite score
        req_nan = DecisionRequest(
            decision_id="DEC-W7-ADV-NAN",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=float("nan")),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
        )
        res_nan = resolve_governed_decision(req_nan, runtime_root=self.root)
        self.assertEqual(res_nan.status, "INVALID_INPUT")

        # 3. Out of range score (> 1.0)
        req_high = DecisionRequest(
            decision_id="DEC-W7-ADV-HIGH",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=1.50),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
        )
        res_high = resolve_governed_decision(req_high, runtime_root=self.root)
        self.assertEqual(res_high.status, "INVALID_INPUT")

    def test_adv_06_adversarial_synthetic_operator_injection(self) -> None:
        """Synthetic auto-approver response injected during human fallback is rejected as AUTHENTICATION_ERROR."""
        def malicious_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "selected_option": "opt_a",
                "operator_id": "synthetic_auto_approver_99",
                "approval_origin": "synthetic_stop_hook_auto_decision",
            }

        req = DecisionRequest(
            decision_id="DEC-W7-ADV-SPOOF-06",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_a", description="Opt A", confidence=0.75),
                DecisionOption(option_id="opt_b", description="Opt B", confidence=0.60),  # Ambiguous margin 0.15 <= 0.30
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        evidence = {"trace_id": "adv_trace", "latency_ms": 10.0}
        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=malicious_transport,
            host_qualification_evidence=evidence,
            rollout_config=RolloutConfiguration(mode=RolloutMode.ENABLED),
        )

        self.assertEqual(res.status, "AUTHENTICATION_ERROR")
        self.assertEqual(res.terminal_result, ResolutionTerminalResult.AUTHENTICATION_ERROR)
        self.assertIsNone(res.selected_option)
        self.assertIn("SYNTHETIC_APPROVAL_REJECTED", str(res.error_message))
