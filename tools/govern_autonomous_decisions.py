#!/usr/bin/env python3
"""
tools/govern_autonomous_decisions.py

Thin CLI and orchestration entrypoint for Governed Autonomous Decision Resolution.
Exposes evaluation, full lifecycle resolution, and readback clearance verification
under bounded retry and time budgets with explicit sandbox context injection.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.hitl_governance_core.autonomous_resolution.eligibility import (  # noqa: E402
    evaluate_decision_request,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.models import (  # noqa: E402
    AuthorityType,
    DecisionMode,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
    PolicySnapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (  # noqa: E402
    load_active_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.rollout import (  # noqa: E402
    RolloutConfiguration,
    RolloutMode,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.service import (  # noqa: E402
    AutonomousDecisionResult,
    resolve_governed_decision,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.models import (  # noqa: E402
    ClearanceVerificationRequest,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.verifier import (  # noqa: E402
    verify_decision_clearance,
)


def run_self_test() -> Tuple[bool, List[str]]:
    """Runs deterministic self-tests covering the resolution service lifecycle."""
    diagnostics: List[str] = []
    temp_dir = tempfile.TemporaryDirectory(prefix="hitl_orch_test_")
    root_path = Path(temp_dir.name)

    try:
        rollout_enabled = RolloutConfiguration(mode=RolloutMode.ENABLED)

        # Case 1: Eligible Routine Decision (0.95 vs 0.52, delta=0.43 > 0.30)
        req_eligible = DecisionRequest(
            decision_id="TEST-DEC-W5-ROUTINE-01",
            revision=1,
            scope=DecisionScope(worktree="test-wt", run_id="run-01", wave=5, action_target="module.py"),
            options=[
                DecisionOption(option_id="opt_a", description="Option A", confidence=0.95),
                DecisionOption(option_id="opt_b", description="Option B", confidence=0.52),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_eligible = resolve_governed_decision(
            request=req_eligible,
            runtime_root=root_path,
            rollout_config=rollout_enabled,
        )
        if res_eligible.status != "RESOLVED_AUTONOMOUSLY":
            diagnostics.append(f"Case 1 failed: expected RESOLVED_AUTONOMOUSLY, got {res_eligible.status}")
        if res_eligible.selected_option != "opt_a":
            diagnostics.append(f"Case 1 failed: expected selected opt_a, got {res_eligible.selected_option}")
        if res_eligible.transport_call_count != 0:
            diagnostics.append(f"Case 1 failed: transport_call_count {res_eligible.transport_call_count} != 0")

        # Case 2: Idempotent Re-Resolution (identical request returns existing record)
        res_re_resolve = resolve_governed_decision(
            request=req_eligible,
            runtime_root=root_path,
            rollout_config=rollout_enabled,
        )
        if res_re_resolve.status != "RESOLVED_AUTONOMOUSLY":
            diagnostics.append(f"Case 2 failed: expected idempotent RESOLVED_AUTONOMOUSLY, got {res_re_resolve.status}")

        # Case 3: Ineligible Small Margin (0.90 vs 0.65, delta=0.25 <= 0.30)
        req_ineligible = DecisionRequest(
            decision_id="TEST-DEC-W5-MARGIN-02",
            revision=1,
            scope=DecisionScope(worktree="test-wt", run_id="run-01", wave=5, action_target="module.py"),
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.65),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
        )
        res_ineligible = resolve_governed_decision(
            request=req_ineligible,
            runtime_root=root_path,
            rollout_config=rollout_enabled,
        )
        if res_ineligible.status != "AWAITING_HUMAN_REVIEW":
            diagnostics.append(f"Case 3 failed: expected AWAITING_HUMAN_REVIEW, got {res_ineligible.status}")
        if res_ineligible.selected_option is not None:
            diagnostics.append(f"Case 3 failed: expected None selected option, got {res_ineligible.selected_option}")

        # Case 4: Non-Routine Category (ARCHITECTURAL must require human review)
        req_arch = DecisionRequest(
            decision_id="TEST-DEC-W5-ARCH-03",
            revision=1,
            scope=DecisionScope(worktree="test-wt", run_id="run-01", wave=5, action_target="module.py"),
            options=[
                DecisionOption(option_id="opt_x", description="Opt X", confidence=0.99),
                DecisionOption(option_id="opt_y", description="Opt Y", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ARCHITECTURAL,
            category="architectural",
        )
        res_arch = resolve_governed_decision(
            request=req_arch,
            runtime_root=root_path,
            rollout_config=rollout_enabled,
        )
        if res_arch.status != "AWAITING_HUMAN_REVIEW":
            diagnostics.append(f"Case 4 failed: expected AWAITING_HUMAN_REVIEW for architectural, got {res_arch.status}")

        # Case 5: Rollout Disabled (Default off behavior)
        res_disabled = resolve_governed_decision(
            request=req_eligible,
            runtime_root=root_path,
            rollout_config=RolloutConfiguration(mode=RolloutMode.DISABLED),
        )
        if res_disabled.status != "AWAITING_HUMAN_REVIEW":
            diagnostics.append(f"Case 5 failed: expected AWAITING_HUMAN_REVIEW when disabled, got {res_disabled.status}")

        # Case 6: Observe-Only Shadow Mode
        res_shadow = resolve_governed_decision(
            request=req_eligible,
            runtime_root=root_path,
            rollout_config=RolloutConfiguration(mode=RolloutMode.OBSERVE_ONLY),
        )
        if not str(res_shadow.status).startswith("SHADOW_OBSERVE_ONLY"):
            diagnostics.append(f"Case 6 failed: expected SHADOW_OBSERVE_ONLY prefix, got {res_shadow.status}")
        if res_shadow.record is not None:
            diagnostics.append("Case 6 failed: shadow mode must not create a persistent record.")

        # Case 7: Kill-Switch Active
        res_kill = resolve_governed_decision(
            request=req_eligible,
            runtime_root=root_path,
            rollout_config=RolloutConfiguration(mode=RolloutMode.ENABLED, kill_switch=True),
        )
        if res_kill.status != "AWAITING_HUMAN_REVIEW":
            diagnostics.append(f"Case 7 failed: expected AWAITING_HUMAN_REVIEW on kill switch, got {res_kill.status}")

        return len(diagnostics) == 0, diagnostics
    finally:
        temp_dir.cleanup()


def _parse_request_payload(args: argparse.Namespace) -> DecisionRequest:
    """Parses DecisionRequest from json file or string."""
    raw_data: Dict[str, Any] = {}
    if args.request_json:
        path = Path(args.request_json)
        if not path.is_file():
            raise FileNotFoundError(f"Request JSON file not found: {args.request_json}")
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    elif args.request_str:
        raw_data = json.loads(args.request_str)
    else:
        raise ValueError("Must provide either --request-json or --request-str.")
    return DecisionRequest.from_dict(raw_data)


def _load_optional_policy(snapshot_path: Optional[Path]) -> Optional[PolicySnapshot]:
    """Loads a policy snapshot if a path is specified."""
    if snapshot_path:
        snap = Path(snapshot_path)
        if snap.is_file():
            return PolicySnapshot.from_dict(json.loads(snap.read_text(encoding="utf-8")))
    return None


def _run_eval_mode(req: DecisionRequest, policy: Optional[PolicySnapshot], as_json: bool) -> int:
    """Execute evaluation mode."""
    active_policy = policy or load_active_policy_snapshot()
    eval_res = evaluate_decision_request(req, active_policy)
    if as_json:
        print(json.dumps(eval_res.to_dict(), indent=2))
    else:
        print(f"Eligibility: {eval_res.terminal_result.value} (Eligible: {eval_res.is_eligible})")
    return 0 if eval_res.is_eligible else 2


def _run_verify_mode(req: DecisionRequest, args: argparse.Namespace) -> int:
    """Execute verification mode."""
    verif_req = ClearanceVerificationRequest(
        decision_id=req.decision_id,
        wave=req.scope.wave,
        run_id=req.scope.run_id,
        worktree=req.scope.worktree,
        expected_action_target=req.scope.action_target,
        require_human_only=args.require_human_only,
    )
    verif_res = verify_decision_clearance(
        verif_req,
        sqlite_path=args.db_path,
        json_path=args.ledger_path,
    )
    out = {
        "cleared": verif_res.cleared,
        "status_code": verif_res.status_code,
        "reason": verif_res.reason,
        "authority_type": verif_res.authority_type.value if verif_res.authority_type else None,
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Clearance: cleared={verif_res.cleared}, status={verif_res.status_code}")
    return 0 if verif_res.cleared else 2


def _run_resolve_mode(
    req: DecisionRequest, args: argparse.Namespace, policy: Optional[PolicySnapshot]
) -> int:
    """Execute resolution mode."""
    rollout_cfg = None
    if getattr(args, "rollout_mode", None) or getattr(args, "kill_switch", False):
        m = RolloutMode(args.rollout_mode) if getattr(args, "rollout_mode", None) else RolloutMode.DISABLED
        rollout_cfg = RolloutConfiguration(mode=m, kill_switch=getattr(args, "kill_switch", False))

    res = resolve_governed_decision(
        request=req,
        runtime_root=args.runtime_root,
        db_path=args.db_path,
        ledger_path=args.ledger_path,
        policy_snapshot=policy,
        require_human_only=args.require_human_only,
        rollout_config=rollout_cfg,
    )

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"Result: {res.status} | Selected Option: {res.selected_option}")
        if res.error_message:
            print(f"  Error: {res.error_message}")

    if res.status in ("RESOLVED_AUTONOMOUSLY", "RESOLVED_BY_HUMAN"):
        return 0
    if res.status in ("HUMAN_REVIEW_REQUIRED", "AWAITING_HUMAN_REVIEW"):
        return 2
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Governed Autonomous Decision Resolution CLI")
    parser.add_argument("--request-json", type=Path, help="Path to decision request JSON")
    parser.add_argument("--request-str", type=str, help="Inline JSON string of decision request")
    parser.add_argument("--runtime-root", type=Path, help="Runtime root directory for stores and projections")
    parser.add_argument("--db-path", type=Path, help="Explicit SQLite database path")
    parser.add_argument("--ledger-path", type=Path, help="Explicit JSON projection ledger path")
    parser.add_argument("--policy-snapshot", type=Path, help="Path to policy snapshot JSON")
    parser.add_argument("--mode", choices=["eval", "resolve", "verify"], default="resolve", help="Execution mode")
    parser.add_argument("--require-human-only", action="store_true", help="Force human operator review path")
    parser.add_argument("--rollout-mode", choices=["DISABLED", "OBSERVE_ONLY", "ENABLED"], help="Explicit rollout mode override")
    parser.add_argument("--kill-switch", action="store_true", help="Engage operational kill-switch")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")
    parser.add_argument("--self-test", action="store_true", help="Execute deterministic internal self-tests")

    args = parser.parse_args(argv)

    if args.self_test:
        success, diagnostics = run_self_test()
        if success:
            print("PASS: tools/govern_autonomous_decisions.py self-tests passed cleanly.")
            return 0
        print("FAIL: tools/govern_autonomous_decisions.py self-tests failed:")
        for diag in diagnostics:
            print(f"  - {diag}")
        return 1

    try:
        req = _parse_request_payload(args)
    except Exception as exc:
        err_dict = {"status": "INVALID_INPUT", "error": str(exc)}
        if args.json:
            print(json.dumps(err_dict, indent=2))
        else:
            print(f"❌ [INVALID INPUT] {exc}", file=sys.stderr)
        return 1

    policy = _load_optional_policy(args.policy_snapshot)

    if args.mode == "eval":
        return _run_eval_mode(req, policy, args.json)

    if args.mode == "verify":
        return _run_verify_mode(req, args)

    return _run_resolve_mode(req, args, policy)


if __name__ == "__main__":
    sys.exit(main())
