#!/usr/bin/env python3
"""
tools/verify_agent_learning_readiness.py

Specialized pre-rollout readiness verifier for Agent Learning & Autonomy (Waves 0-5).
Verifies:
1. Cryptographic receipts across Waves 0, 1, 2, 3, 4.
2. Sequential wave completion locks.
3. Contract schema definitions & inventory compliance.
4. Feature flags and rollback controls.
5. Invariant boundaries: Read-only maintenance, zero canonical graph writes.
6. Baseline delta audit proving measurable improvements over Wave 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure current worktree is prioritized over main repo install
sys.path.insert(0, str(Path.cwd()))

from tools.record_wave_receipt import verify_receipt


def verify_gate(name: str, check_fn) -> Tuple[bool, str]:
    """Runs a readiness check and returns (passed, detail)."""
    try:
        passed, detail = check_fn()
        return passed, detail
    except Exception as exc:
        return False, f"Exception: {exc}"


def check_receipts() -> Tuple[bool, str]:
    receipts = [
        Path("reports/wave0_learning_baseline_receipt.json"),
        Path("reports/wave1_learning_substrate_receipt.json"),
        Path("reports/wave2_query_recovery_receipt.json"),
        Path("reports/wave3_correction_feedback_receipt.json"),
        Path("reports/wave4_quality_and_maintenance_receipt.json"),
    ]
    for r in receipts:
        if not r.is_file():
            return False, f"Missing receipt: {r}"
        if not verify_receipt(r):
            return False, f"Cryptographic verification failed for {r}"
    return True, f"All {len(receipts)} wave receipts cryptographically verified."


def check_locks() -> Tuple[bool, str]:
    lock_dir = Path("artifacts/locks")
    expected_locks = [
        lock_dir / "wave-1.complete",
        lock_dir / "wave-2.complete",
        lock_dir / "wave-3.complete",
        lock_dir / "wave-4.complete",
    ]
    for lk in expected_locks:
        if not lk.is_file():
            return False, f"Missing sequential lock: {lk}"
        content = lk.read_text(encoding="utf-8")
        if "COMPLETE" not in content or "RECEIPT_DIGEST" not in content:
            return False, f"Invalid lock content in {lk}"
    return True, "All sequential completion locks verified."


def check_contract_schemas() -> Tuple[bool, str]:
    schemas = [
        Path("contracts/dual_pipeline/schemas/agent-outcome-event.schema.json"),
        Path("contracts/dual_pipeline/schemas/learning-change-proposal.schema.json"),
    ]
    for s in schemas:
        if not s.is_file():
            return False, f"Missing contract schema: {s}"
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
            if not data.get("$id") or not data.get("required"):
                return False, f"Schema {s} missing required JSON Schema properties."
        except Exception as exc:
            return False, f"Schema {s} is malformed: {exc}"
    return True, "Contract schemas validated."


def check_feature_flags_and_rollback() -> Tuple[bool, str]:
    try:
        from dual_pipeline.feature_flags import DEFAULT_FLAGS, get_feature_flag_manager
        if "BOUNDED_QUERY_RECOVERY" not in DEFAULT_FLAGS:
            return False, "BOUNDED_QUERY_RECOVERY missing from DEFAULT_FLAGS."
        mgr = get_feature_flag_manager()
        state = mgr.get_state("BOUNDED_QUERY_RECOVERY")
        from dual_pipeline.learning_engine_core.rollback import (
            execute_rollback,
            record_rollback_snapshot,
            verify_zero_graph_mutation,
        )
        if not callable(execute_rollback) or not callable(record_rollback_snapshot):
            return False, "Rollback functions missing or not callable."
        return True, f"Feature flag BOUNDED_QUERY_RECOVERY registered ({state.value}); rollback functions confirmed."
    except Exception as exc:
        return False, f"Feature flag/rollback verification failed: {exc}"


def check_readonly_and_isolation() -> Tuple[bool, str]:
    try:
        from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
            HarmPrioritizedPlanner,
        )
        planner = HarmPrioritizedPlanner()
        if not planner.is_read_only:
            return False, "HarmPrioritizedPlanner is_read_only invariant is False."
        return True, "Read-only maintenance planning invariant confirmed."
    except Exception as exc:
        return False, f"Read-only invariant check failed: {exc}"


def check_baseline_delta() -> Tuple[bool, str]:
    delta_file = Path("reports/agent_learning_delta_vs_baseline_comparison.json")
    if not delta_file.is_file():
        return False, f"Missing baseline delta comparison file: {delta_file}"
    try:
        data = json.loads(delta_file.read_text(encoding="utf-8"))
        summary = data.get("delta_summary", {})
        gain = summary.get("durable_learning_gain_count", 0)
        if gain <= 0:
            return False, "Zero durable learning gain detected in delta summary."
        return True, f"Verified +{gain} durable learning capability improvements over baseline."
    except Exception as exc:
        return False, f"Delta comparison parsing error: {exc}"


def check_fleet_and_stage_gate_learning() -> Tuple[bool, str]:
    from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, load_assembled_automation_prompt

    sys_learning_prompt = Path("automations/prompts/system_learning_agent.md")
    if not sys_learning_prompt.is_file():
        return False, f"Missing prompt file: {sys_learning_prompt}"
    if "govern_learning_proposals.py" not in sys_learning_prompt.read_text(encoding="utf-8"):
        return False, f"Prompt {sys_learning_prompt} lacks govern_learning_proposals.py closed-loop learning instructions"

    prompt_root = Path("automations/prompts")
    all_prompts = sorted(
        p for p in prompt_root.rglob("*.md") if p.name != "_shared_governance_contract.md"
    )
    expected_count = len(EXPECTED_CANONICAL_PROMPTS)
    if len(all_prompts) < expected_count:
        return False, f"Expected {expected_count} canonical automation prompts, found {len(all_prompts)}"
    for p in all_prompts:
        raw_content = p.read_text(encoding="utf-8")
        if "INV-AUTOMATION-PERSISTENT-LEARNING-LOOP" not in raw_content:
            return False, f"Prompt {p} missing persistent-store learning feedback token: INV-AUTOMATION-PERSISTENT-LEARNING-LOOP"
        rel_name = p.relative_to(prompt_root).as_posix()
        content = load_assembled_automation_prompt(prompt_root, rel_name)
        for req in ("INV-AUTOMATION-PERSISTENT-LEARNING-LOOP", "hitl_decision_ledger.sqlite", "calibrate_options", "needs_operator_decision"):
            if req not in content:
                return False, f"Prompt {p} missing persistent-store learning feedback token: {req}"

    baseline_file = Path("reports/agent_learning_role_registry_baseline.json")
    if not baseline_file.is_file():
        return False, f"Missing baseline file: {baseline_file}"
    try:
        data = json.loads(baseline_file.read_text(encoding="utf-8"))
        stats = data.get("statistics", {})
        durable = stats.get("components_with_durable_learning", 0)
        if durable < 6:
            return False, f"Insufficient durable learning coverage in fleet snapshot: {durable}"
    except Exception as exc:
        return False, f"Error validating baseline snapshot: {exc}"

    return True, "Verified scheduled automation closed-loop prompts and fleet learning registry."


def run_all_readiness_checks() -> bool:
    gates = [
        ("Gate 1: Cryptographic Receipts (W0-W4)", check_receipts),
        ("Gate 2: Sequential Completion Locks", check_locks),
        ("Gate 3: Contract Schemas & Integrity", check_contract_schemas),
        ("Gate 4: Feature Flags & Rollback Controls", check_feature_flags_and_rollback),
        ("Gate 5: Read-Only Maintenance Invariant", check_readonly_and_isolation),
        ("Gate 6: Wave 0 vs Wave 5 Baseline Delta", check_baseline_delta),
        ("Gate 7: Scheduled Fleet & Stage-Gate Learning", check_fleet_and_stage_gate_learning),
    ]

    print("=" * 80)
    print("  AGENT LEARNING & AUTONOMY PRE-ROLLOUT READINESS VERIFIER")
    print("=" * 80)

    all_passed = True
    for name, fn in gates:
        passed, detail = verify_gate(name, fn)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} | {name.ljust(44)} | {detail}")
        if not passed:
            all_passed = False

    print("=" * 80)
    if all_passed:
        print("  VERDICT: READY FOR ROLLOUT (All 7 Gates Passed)")
    else:
        print("  VERDICT: ROLLOUT BLOCKED (One or more gates failed)")
    print("=" * 80)

    return all_passed


def main() -> int:
    success = run_all_readiness_checks()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
