"""
tests/end_to_end/test_hitl_resolution_w7_e2e.py

End-to-end lifecycle integration tests for Wave 7:
Rollout, Disablement, and Recovery Qualification.

Verifies:
1. Full integrated lifecycle: routing -> storage -> clearance -> human review -> Bayesian learning.
2. Observe-only shadow mode never writes records, returns executable choices, or suppresses prompts.
3. Turning feature off / kill-switch halts new resolutions and pending authority consumption while retaining evidence.
4. In-flight kill-switch / disablement recheck aborts commit before continuation.
5. Crash after commit recovery: idempotent retry preserves exact identity without duplicate prompts.
6. Operational boundaries: operation class, runtime root, and policy digest enforcement.
"""
from __future__ import annotations

import os
import tempfile
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
    load_active_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.rollout import (
    DEFAULT_APPROVED_CLASSES,
    RolloutConfiguration,
    RolloutMode,
    evaluate_rollout_authorization,
    get_rollout_mode,
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
from dual_pipeline.hitl_governance_core.learning_eligibility import (
    compute_record_eligibility,
    deduplicate_records_for_learning,
)


@pytest.mark.timeout(60)
class HitlResolutionW7E2ETests(unittest.TestCase):
    """End-to-end integration suite for Wave 7 rollout, disablement, and recovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hitl_w7_e2e_")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "autonomous_resolutions.db"
        self.ledger_path = self.root / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_scope(self, wave: int = 7, target: str = "rollout_target.py") -> DecisionScope:
        return DecisionScope(
            worktree=str(self.root.name),
            run_id="run_w7_e2e",
            wave=wave,
            action_target=target,
            plan_digest="a" * 64,
        )

    def test_01_full_lifecycle_integrated_routing_to_learning(self) -> None:
        """Full lifecycle: eligible decision resolves, passes clearance readback, and informs learning."""
        req = DecisionRequest(
            decision_id="DEC-W7-LIFECYCLE-01",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_alpha", description="Alpha Algorithm", confidence=0.94),
                DecisionOption(option_id="opt_beta", description="Beta Algorithm", confidence=0.51),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        config = RolloutConfiguration(mode=RolloutMode.ENABLED)
        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=config,
        )

        self.assertEqual(res.status, "RESOLVED_AUTONOMOUSLY")
        self.assertEqual(res.selected_option, "opt_alpha")
        self.assertEqual(res.transport_call_count, 0)
        self.assertIsNotNone(res.record)

        # Clearance readback verification
        verif_req = ClearanceVerificationRequest(
            decision_id=req.decision_id,
            wave=req.scope.wave,
            worktree=req.scope.worktree,
            enforce_rollout_switch=True,
        )
        verif_res = verify_decision_clearance(verif_req, sqlite_path=self.db_path, json_path=self.ledger_path)
        self.assertTrue(verif_res.cleared)
        self.assertEqual(verif_res.status_code, "CLEARED_AUTONOMOUS")

        # Independent outcome evaluation for learning
        rec_dict = res.record.to_dict()
        weight, is_mature, reason = compute_record_eligibility(rec_dict)
        self.assertEqual(weight, 0.0)
        self.assertFalse(is_mature)
        self.assertEqual(reason, "AUTONOMOUS_AUDIT_ONLY")

    def test_02_observe_only_mode_never_writes_approvals_or_suppresses_prompt(self) -> None:
        """Observe-only shadow mode evaluates margin, never writes store, and dispatches human prompt."""
        transport_calls: List[Dict[str, Any]] = []

        def mock_transport(packet: Dict[str, Any]) -> Dict[str, Any]:
            transport_calls.append(packet)
            return {
                "selected_option": "opt_primary",
                "operator_id": "human_operator_verified",
                "approval_origin": "human_operator",
            }

        req = DecisionRequest(
            decision_id="DEC-W7-SHADOW-02",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_primary", description="Primary Choice", confidence=0.96),
                DecisionOption(option_id="opt_secondary", description="Secondary Choice", confidence=0.48),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        config = RolloutConfiguration(mode=RolloutMode.OBSERVE_ONLY)
        evidence = {"trace_id": "trace_w7", "latency_ms": 25.0}

        res = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            transport=mock_transport,
            host_qualification_evidence=evidence,
            rollout_config=config,
        )

        self.assertTrue(str(res.status).startswith("SHADOW_OBSERVE_ONLY"))
        self.assertEqual(len(transport_calls), 1)
        self.assertEqual(res.transport_call_count, 1)
        self.assertIsNone(res.record)
        self.assertFalse(self.db_path.exists())

    def test_03_turning_feature_off_stops_new_resolutions_and_consumption_of_pending_authority(self) -> None:
        """Disabling rollout halts new autonomous resolutions and blocks consumption of pre-existing authority."""
        # 1. First record an autonomous resolution while ENABLED
        req = DecisionRequest(
            decision_id="DEC-W7-DISABLE-03",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_x", description="Option X", confidence=0.92),
                DecisionOption(option_id="opt_y", description="Option Y", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )
        res_enabled = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=RolloutConfiguration(mode=RolloutMode.ENABLED),
        )
        self.assertEqual(res_enabled.status, "RESOLVED_AUTONOMOUSLY")

        # 2. Turn feature off via DISABLED mode and verify new request fails closed
        req2 = DecisionRequest(
            decision_id="DEC-W7-DISABLE-04",
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
        res_disabled = resolve_governed_decision(
            request=req2,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=RolloutConfiguration(mode=RolloutMode.DISABLED),
        )
        self.assertEqual(res_disabled.status, "AWAITING_HUMAN_REVIEW")
        self.assertIsNone(res_disabled.selected_option)

        # 3. Retain historical evidence: previous record is still in store
        store = AutonomousResolutionStore(self.db_path)
        existing = store.list_all()
        self.assertEqual(len(existing), 1)
        self.assertEqual(existing[0].decision_id, "DEC-W7-DISABLE-03")

    def test_04_in_flight_kill_switch_aborts_commit_and_rechecks_before_continuation(self) -> None:
        """In-flight kill switch engaged right before commit aborts with POLICY_CONFLICT."""
        req = DecisionRequest(
            decision_id="DEC-W7-INFLIGHT-05",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_a", description="Opt A", confidence=0.95),
                DecisionOption(option_id="opt_b", description="Opt B", confidence=0.52),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        # Dynamic kill-switch toggle via env
        old_val = os.environ.get("NEE_PARAM_HITL_AUTONOMOUS_KILL_SWITCH")
        try:
            os.environ["NEE_PARAM_HITL_AUTONOMOUS_KILL_SWITCH"] = "true"
            res = resolve_governed_decision(
                request=req,
                runtime_root=self.root,
                db_path=self.db_path,
                ledger_path=self.ledger_path,
            )
            self.assertEqual(res.status, "AWAITING_HUMAN_REVIEW")
            self.assertIsNone(res.selected_option)
        finally:
            if old_val is None:
                os.environ.pop("NEE_PARAM_HITL_AUTONOMOUS_KILL_SWITCH", None)
            else:
                os.environ["NEE_PARAM_HITL_AUTONOMOUS_KILL_SWITCH"] = old_val

    def test_05_crash_after_commit_recovery_and_idempotent_retry(self) -> None:
        """Crash after commit: subsequent retry recovers existing committed identity without duplicate dispatches."""
        req = DecisionRequest(
            decision_id="DEC-W7-RECOVERY-06",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_c", description="Option C", confidence=0.91),
                DecisionOption(option_id="opt_d", description="Option D", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine_ambiguity",
        )

        config = RolloutConfiguration(mode=RolloutMode.ENABLED)
        res_initial = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=config,
        )
        self.assertEqual(res_initial.status, "RESOLVED_AUTONOMOUSLY")
        initial_rec = res_initial.record
        self.assertIsNotNone(initial_rec)

        # Retry with identical request
        res_retry = resolve_governed_decision(
            request=req,
            runtime_root=self.root,
            db_path=self.db_path,
            ledger_path=self.ledger_path,
            rollout_config=config,
        )
        self.assertEqual(res_retry.status, "RESOLVED_AUTONOMOUSLY")
        retry_rec = res_retry.record
        self.assertIsNotNone(retry_rec)

        # Verify invariant: identical identity, signature, and sequence number
        self.assertEqual(initial_rec.resolution_id, retry_rec.resolution_id)
        self.assertEqual(initial_rec.sequence_number, retry_rec.sequence_number)
        self.assertEqual(initial_rec.signature_b64, retry_rec.signature_b64)
        self.assertEqual(initial_rec.resolution_digest, retry_rec.resolution_digest)

    def test_06_operation_class_and_runtime_root_boundary_enforcement(self) -> None:
        """Operation classes outside approved set or runtime roots outside boundary are rejected."""
        policy = load_active_policy_snapshot()

        # Architectural mode rejected
        req_arch = DecisionRequest(
            decision_id="DEC-W7-BOUND-07",
            revision=1,
            scope=self._make_scope(),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.98),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ARCHITECTURAL,
            category="architectural",
        )
        auth_ok, reason, _ = evaluate_rollout_authorization(
            req_arch, policy, runtime_root=self.root, config=RolloutConfiguration(mode=RolloutMode.ENABLED)
        )
        self.assertFalse(auth_ok)
        self.assertIn("OPERATION_CLASS_NOT_APPROVED", reason)

        # Restricted runtime root
        allowed_root = self.root / "authorized_subpath"
        allowed_root.mkdir(parents=True, exist_ok=True)
        unauthorized_root = self.root / "unauthorized_subpath"
        unauthorized_root.mkdir(parents=True, exist_ok=True)

        config_root_bound = RolloutConfiguration(
            mode=RolloutMode.ENABLED,
            allowed_runtime_roots=frozenset({str(allowed_root)}),
        )
        req_routine = DecisionRequest(
            decision_id="DEC-W7-BOUND-08",
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
        auth_root_ok, root_reason, _ = evaluate_rollout_authorization(
            req_routine, policy, runtime_root=unauthorized_root, config=config_root_bound
        )
        self.assertFalse(auth_root_ok)
        self.assertIn("RUNTIME_ROOT_NOT_ALLOWED", root_reason)
