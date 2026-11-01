#!/usr/bin/env python3
"""
dual_pipeline/hitl_governance_core/decision_clearance.py

Operator Decision Clearance Gate (Wave 4/6).
Ensures that asking is mandatory and unavoidable in practice.
Any state-changing action, receipt sealing, or wave advancement strictly refuses
to execute while a qualifying operator decision lacks an authentic answer in the ledger.

Refactored under Wave 4 to delegate parsing, domain models, and exact-scope
verification to decision_clearance_core.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.hitl_governance_core.decision_clearance_core import (
    EXIT_CODE_CLEARED,
    EXIT_CODE_NO_DECISIONS,
    EXIT_CODE_OPERATIONAL_ERROR,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
    ClearanceResult,
    ClearanceVerificationRequest,
    ScopedDecision,
    discover_implementation_plans,
    is_decision_ratified_in_ledger,
    parse_confidence_pairs,
    parse_decisions_from_table,
    validate_decision_collection,
    verify_decision_clearance,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    resolve_ledger_paths,
    verify_chain_integrity,
)
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder

__all__ = [
    "EXIT_CODE_CLEARED",
    "EXIT_CODE_NO_DECISIONS",
    "EXIT_CODE_OPERATIONAL_ERROR",
    "EXIT_CODE_OPERATOR_INPUT_REQUIRED",
    "ClearanceResult",
    "ScopedDecision",
    "discover_implementation_plans",
    "is_decision_ratified_in_ledger",
    "main",
    "parse_confidence_pairs",
    "parse_decisions_from_table",
    "require_cleared",
]


def _build_blocked_question_payload(blocked: ScopedDecision) -> Dict[str, Any]:
    try:
        cat = getattr(blocked, "category", None) or "decision_clearance"
        builder = QuestionBuilder(blocked.question, metadata={"category": cat})
        for opt in blocked.options:
            builder.add_option(
                label=opt.get("label", "Option"),
                plain_english_summary=opt.get("plain_english_summary", opt.get("label", "Option")),
                impact_statement=opt.get("impact_statement", "Ensures predictable operational execution."),
                confidence_score=float(opt.get("confidence_score", 0.50)),
                recommended=bool(opt.get("recommended", False)),
            )
        return builder.build().to_tool_call()
    except Exception as exc:
        return {
            "questions": [
                {
                    "question": blocked.question,
                    "options": [
                        f"{o.get('label')} (Confidence: {int(round(float(o.get('confidence_score', 0.5))*100))}%, Tier: Medium)"
                        for o in blocked.options
                    ],
                    "is_multi_select": False,
                }
            ],
            "builder_error": str(exc),
        }


def _collect_decisions_from_plans(
    target_plans: Sequence[Optional[Path]],
    extra_decisions: Optional[Sequence[ScopedDecision]],
    diagnostics: List[str],
) -> Tuple[Optional[List[ScopedDecision]], Optional[ClearanceResult]]:
    discovered: List[ScopedDecision] = []
    for p in target_plans:
        if p and p.is_file():
            try:
                table_decs = parse_decisions_from_table(p.read_text(encoding="utf-8"), source_file=str(p.name))
                p_valid, p_errors = validate_decision_collection(table_decs)
                if not p_valid:
                    diagnostics.extend(p_errors)
                    return None, ClearanceResult(
                        cleared=False,
                        exit_code=EXIT_CODE_OPERATIONAL_ERROR,
                        status_code="ERROR",
                        diagnostics=diagnostics,
                    )
                discovered.extend(table_decs)
            except Exception as e:
                diagnostics.append(f"Warning: Failed parsing plan {p}: {e}")

    if extra_decisions:
        ex_valid, ex_errors = validate_decision_collection(extra_decisions)
        if not ex_valid:
            diagnostics.extend(ex_errors)
            return None, ClearanceResult(
                cleared=False,
                exit_code=EXIT_CODE_OPERATIONAL_ERROR,
                status_code="ERROR",
                diagnostics=diagnostics,
            )
        discovered.extend(extra_decisions)

    return discovered, None


def _evaluate_decisions(
    scoped_decs: Sequence[ScopedDecision],
    target_wave: Optional[int],
    s_path: Path,
    j_path: Path,
    diagnostics: List[str],
) -> ClearanceResult:
    open_decisions: List[ScopedDecision] = []
    resolved_decisions: List[ScopedDecision] = []

    for d in scoped_decs:
        req = ClearanceVerificationRequest(
            decision_id=d.decision_id,
            wave=d.wave or target_wave,
            options=d.options,
        )
        v_res = verify_decision_clearance(req, sqlite_path=s_path, json_path=j_path)
        if v_res.cleared:
            resolved_decisions.append(d)
            diagnostics.append(f"Decision '{d.decision_id}' cleared: {v_res.reason}")
            continue

        if not d.requires_asking:
            resolved_decisions.append(d)
            diagnostics.append(
                f"Decision '{d.decision_id}' (gap={d.gap:.2%}) resolved autonomously under SSOT margin rule."
            )
            continue

        open_decisions.append(d)
        diagnostics.append(f"Decision '{d.decision_id}' is OPEN: {v_res.reason}")

    if not open_decisions:
        return ClearanceResult(
            cleared=True,
            exit_code=EXIT_CODE_CLEARED,
            status_code="CLEARED",
            resolved_decisions=resolved_decisions,
            diagnostics=diagnostics,
        )

    blocked = open_decisions[0]
    next_payload = _build_blocked_question_payload(blocked)

    return ClearanceResult(
        cleared=False,
        exit_code=EXIT_CODE_OPERATOR_INPUT_REQUIRED,
        status_code="OPERATOR_INPUT_REQUIRED",
        open_decisions=open_decisions,
        resolved_decisions=resolved_decisions,
        blocked_decision=blocked,
        next_question_payload=next_payload,
        diagnostics=diagnostics,
    )


def require_cleared(
    scope: Optional[Union[Path, str, int]] = None,
    wave: Optional[int] = None,
    plan_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    allow_zero_decisions: bool = False,
    extra_decisions: Optional[Sequence[ScopedDecision]] = None,
) -> ClearanceResult:
    """Finds all decisions in scope, verifies clearance in persistent ledger, and halts if blocked."""
    diagnostics: List[str] = []
    resolved_root = Path(repo_root or Path.cwd()).resolve()

    target_wave = wave
    if isinstance(scope, int):
        target_wave = scope
    elif isinstance(scope, (str, Path)) and str(scope).isdigit():
        target_wave = int(str(scope))

    s_path, j_path = resolve_ledger_paths(
        repo_root=resolved_root, sqlite_path=sqlite_path, json_path=json_path
    )

    # 1. Authoritative SQLite Merkle integrity check
    if s_path.is_file():
        chain_ok, chain_msg = verify_chain_integrity(s_path)
        if not chain_ok:
            diagnostics.append(f"LEDGER_CHAIN_INTEGRITY_FAILURE: {chain_msg}")
            return ClearanceResult(
                cleared=False,
                exit_code=EXIT_CODE_OPERATIONAL_ERROR,
                status_code="ERROR",
                diagnostics=diagnostics,
            )

    # 2. Discover plans and parse decisions
    target_plans = [plan_path] if plan_path else ([] if extra_decisions is not None else discover_implementation_plans(resolved_root))
    discovered, err_res = _collect_decisions_from_plans(target_plans, extra_decisions, diagnostics)
    if err_res is not None or discovered is None:
        return err_res

    # Filter by wave if specified
    scoped_decs = [d for d in discovered if target_wave is None or d.wave is None or d.wave == target_wave]

    # 3. Decision D6: Zero decisions behavior
    if not scoped_decs:
        if allow_zero_decisions:
            diagnostics.append("Zero decisions found in scope; allowed by explicit authorization.")
            return ClearanceResult(
                cleared=True,
                exit_code=EXIT_CODE_CLEARED,
                status_code="CLEARED",
                diagnostics=diagnostics,
            )
        diagnostics.append(
            "FAIL_CLOSED_ZERO_DECISIONS: Automated checkpoint found 0 decision records in scope. "
            "Execution halted under Decision D6 policy."
        )
        return ClearanceResult(
            cleared=False,
            exit_code=EXIT_CODE_NO_DECISIONS,
            status_code="NO_DECISIONS_FOUND",
            diagnostics=diagnostics,
        )

    # 4. Evaluate each decision
    return _evaluate_decisions(scoped_decs, target_wave, s_path, j_path, diagnostics)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for checking decision clearance."""
    import argparse

    parser = argparse.ArgumentParser(description="Operator Decision Clearance Gate (Wave 4/6)")
    parser.add_argument("--wave", type=int, help="Target wave number to verify")
    parser.add_argument("--scope", type=str, help="Wave number or scope identifier")
    parser.add_argument("--plan-path", type=Path, help="Specific implementation plan to inspect")
    parser.add_argument("--repo-root", type=Path, help="Custom repository root path")
    parser.add_argument("--allow-zero-decisions", action="store_true", help="Allow execution when zero decisions found")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    args = parser.parse_args(argv)
    wave_target = args.wave
    if wave_target is None and args.scope and args.scope.isdigit():
        wave_target = int(args.scope)

    res = require_cleared(
        scope=args.scope,
        wave=wave_target,
        plan_path=args.plan_path,
        repo_root=args.repo_root,
        allow_zero_decisions=args.allow_zero_decisions,
    )

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return res.exit_code

    if res.exit_code == EXIT_CODE_CLEARED:
        print("✅ [DECISION CLEARANCE GRANTED] All qualifying decisions are ratified and verified.")
        for d in res.resolved_decisions:
            print(f"  ✔ Resolved: {d.decision_id} (Gap: {d.gap:.1%})")
        return EXIT_CODE_CLEARED

    if res.exit_code == EXIT_CODE_NO_DECISIONS:
        print("🛑 [ZERO DECISIONS FOUND] Automated checkpoint found 0 decision records in scope.")
        print(f"   Execution halted under Decision D6 policy (Exit Code {EXIT_CODE_NO_DECISIONS}).")
        return EXIT_CODE_NO_DECISIONS

    if res.exit_code == EXIT_CODE_OPERATOR_INPUT_REQUIRED:
        print(f"🛑 [OPERATOR_INPUT_REQUIRED] Blocked by {len(res.open_decisions)} open decision(s).")
        for od in res.open_decisions:
            print(f"  ❌ Open decision: {od.decision_id} (Gap: {od.gap:.1%}, Handling: {od.handling})")
        if res.next_question_payload:
            print("\n=== INTERACTIVE ASK_USER_QUESTION PAYLOAD (Prompt directly to operator) ===")
            print(json.dumps(res.next_question_payload, indent=2))
            print("========================================================================\n")
        return EXIT_CODE_OPERATOR_INPUT_REQUIRED

    print(f"❌ Operational error evaluating decision clearance (Exit Code {res.exit_code}):")
    for d in res.diagnostics:
        print(f"  - {d}")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
