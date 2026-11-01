#!/usr/bin/env python3
"""
tools/pre_wave_decision_gate.py

Programmatic Pre-Wave Decision Interceptor for Governed Workflows.
Inspects wave plans, decision ledgers, and decision registries prior to wave execution.
Enforces the Human-in-the-Loop (HITL) Interceptor Invariant:
  - Exit Code 0: All decisions for the wave are ratified/resolved. Clearance granted.
  - Exit Code 2: One or more decisions bound to the wave are OPEN/UNRATIFIED.
                 Halts autonomous execution and emits ready-to-prompt ask_user_question JSON payload.
  - Exit Code 1: Operational failure, file not found, or invalid arguments.

Complies strictly with:
  - Layman Translation Standard (Zero-Jargon Invariant)
  - hitl-option.schema.json v1.1.0
  - Cryptographic ledger verification (anti-synthetic approval firewall)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.hitl_governance_core.pre_wave_interceptor import (  # noqa: E402
    PreWaveDecision,
    PreWaveGateResult,
    evaluate_pre_wave_gate,
    format_ask_question_payload_for_decision,
    format_ask_user_question_payload_for_decision,
    parse_decisions_from_markdown,
    run_self_test,
    verify_decision_ratification,
)

__all__ = [
    "PreWaveDecision",
    "PreWaveGateResult",
    "evaluate_pre_wave_gate",
    "format_ask_user_question_payload_for_decision",
    "format_ask_question_payload_for_decision",
    "parse_decisions_from_markdown",
    "run_self_test",
    "verify_decision_ratification",
    "main",
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Programmatic Pre-Wave Decision Interceptor")
    parser.add_argument("--wave", type=int, default=1, help="Wave number to verify (default: 1)")
    parser.add_argument("--plan-path", type=Path, help="Path to wave plan markdown file")
    parser.add_argument("--decisions-file", type=Path, help="Path to decisions JSON file")
    parser.add_argument("--ledger-path", type=Path, help="Path to hitl_decision_ledger.json")
    parser.add_argument("--allow-zero-decisions", action="store_true", help="Allow execution when zero decisions found in scope")
    parser.add_argument("--self-test", action="store_true", help="Run deterministic internal self-tests")
    parser.add_argument("--json", action="store_true", help="Output full result as JSON")
    args = parser.parse_args(argv)

    if args.self_test:
        success, diagnostics = run_self_test()
        if success:
            print("PASS: tools/pre_wave_decision_gate.py self-test passed cleanly.")
            return 0
        print("FAIL: tools/pre_wave_decision_gate.py self-test failed:")
        for d in diagnostics:
            print(f"  - {d}")
        return 1

    result = evaluate_pre_wave_gate(
        wave=args.wave,
        plan_path=args.plan_path,
        decisions_file=args.decisions_file,
        ledger_path=args.ledger_path,
        allow_zero_decisions=args.allow_zero_decisions,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return result.exit_code

    if result.exit_code == 0:
        print(f"✅ [CLEARANCE GRANTED] No open decisions blocking Wave {args.wave}. Ready for execution.")
        for d in result.resolved_decisions:
            print(f"  ✔ Resolved decision: {d.decision_id}")
        return 0

    if result.exit_code == 3:
        print("🛑 [ZERO DECISIONS FOUND] Automated checkpoint found 0 decision records in scope.")
        print("   Execution halted under Decision D6 policy (Exit Code 3).")
        return 3

    if result.exit_code == 2:
        print(f"🛑 [PRE-WAVE DECISION BLOCKED] OPERATOR_INPUT_REQUIRED: Wave {args.wave} has {len(result.open_decisions)} open decision(s).")
        for od in result.open_decisions:
            print(f"  ❌ Open decision: {od.decision_id} (Category: {od.category})")
        print("\n=== INTERACTIVE ASK_USER_QUESTION PAYLOAD (Prompt directly to operator) ===")
        print(json.dumps(result.ask_user_question_payload, indent=2))
        print("========================================================================\n")
        return 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
