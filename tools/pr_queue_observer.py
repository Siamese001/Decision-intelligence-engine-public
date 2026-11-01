#!/usr/bin/env python3
"""tools/pr_queue_observer.py

Governed CLI entry point for the Antigravity PR Queue Observer (Wave 6).
Executes read-only observation cycles, shadow qualifications, and inspection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) != sys.path[0]:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.observer import run_observation
from tools.pr_governance_core.observer_shadow import run_shadow_scenarios


class TableArgumentParser(argparse.ArgumentParser):
    """Keep handled command-line failures inside the table-only contract."""

    def error(self, message: str) -> None:
        if "--format" in sys.argv and "table" in sys.argv:
            from tools.pr_governance_core.render import render_pr_queue_observer_table
            print(render_pr_queue_observer_table([], {
                "mode": "UNKNOWN", "outcome": "BLOCKED", "notification": "UNVERIFIED",
                "layout": "UNVERIFIED", "next_action": "MODE_INVALID",
                "detail": "Invalid invocation arguments",
            }))
            raise SystemExit(2)
        super().error(message)


def _parse_args() -> argparse.Namespace:
    parser = TableArgumentParser(description="Antigravity PR Queue Observer")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Target repository root")
    parser.add_argument("--output-dir", type=Path, default=None, help="Observer output destination")
    parser.add_argument("--mode", choices=["interactive", "scheduled"], default=None, help="Observation mode (scheduled is DEPRECATED; defaults to interactive or auto-infers from scheduler launch origin)")
    parser.add_argument("--launch-origin", choices=["manual", "scheduler"], default="manual", help="Trusted host launch context")
    parser.add_argument("--format", choices=["receipt", "table"], default="receipt", help="User-visible output format")
    parser.add_argument("--shadow", action="store_true", help="Run 5+ shadow qualification scenarios")
    parser.add_argument("--force-notify", action="store_true", help="Force notification emission for testing")
    parser.add_argument("--json", action="store_true", help="Output JSON receipt to stdout")
    args = parser.parse_args()
    if args.mode is None:
        args.mode = "scheduled" if args.launch_origin == "scheduler" else "interactive"
    return args


def _run_shadow(args: argparse.Namespace) -> int:
        print("=== Running Antigravity PR Queue Observer Shadow Qualification ===")
        scenarios = run_shadow_scenarios(repo_root=args.repo_root, output_dir=args.output_dir)
        all_passed = True
        for sc in scenarios:
            mark = "✅ PASS" if sc["passed"] else "❌ FAIL"
            print(f"{sc['scenario']:<40} {mark:<10} outcome={sc['terminal_outcome']:<25} reason={sc['reason']}")
            if not sc["passed"]:
                all_passed = False
        return 0 if all_passed else 1


def _print_receipt(args: argparse.Namespace, receipt: Any) -> None:
    if args.format == "table":
        print(receipt.table_markdown)
    elif args.json:
        print(json.dumps(receipt.to_dict(), indent=2))
    else:
        print("=== Antigravity PR Queue Observer Run Receipt ===")
        print(f"Run ID:            {receipt.run_id}")
        print(f"Observed at:       {receipt.observed_at} ({receipt.observed_at_local})")
        print(f"Snapshot digest:   {receipt.snapshot_digest[:16]}...")
        print(f"Terminal outcome:  {receipt.assessment.terminal_outcome}")
        print(f"Notify:            {'YES' if receipt.assessment.notify else 'NO (suppressed)'}")
        print(f"Reason:            {receipt.assessment.reason}")
        if receipt.missed_interval_hours:
            print(f"Sleeping host:     Missed interval detected ({receipt.missed_interval_hours:.1f}h); no replay storm")
        if receipt.is_stale_snapshot:
            print("Stale snapshot:    Snapshot age exceeds stale threshold")
        print(f"Last accepted:     {receipt.last_accepted_digest_after}")


def _print_blocked_table(args: argparse.Namespace, code: str) -> None:
    from tools.pr_governance_core.render import render_pr_queue_observer_table
    print(render_pr_queue_observer_table([], {
        "mode": args.mode.upper(), "outcome": "BLOCKED", "notification": "UNVERIFIED",
        "layout": "UNVERIFIED", "next_action": code,
    }))


def _execute_observation(args: argparse.Namespace) -> int:
    try:
        out_dir = args.output_dir or (args.repo_root / ".local" / "pr_queue_observer")
        approved_state = (args.repo_root / ".local" / "pr_queue_observer").resolve()
        if out_dir.resolve() != approved_state:
            raise ValueError("observer state path must be the repository's .local/pr_queue_observer directory")
        if args.mode == "scheduled":
            sys.stderr.write("DEPRECATION NOTICE: --mode scheduled is deprecated; PR Queue Observer is 100% interactive with ask_user_question.\n")
        if args.launch_origin == "scheduler" and args.mode != "scheduled":
            raise ValueError("scheduler launch origin requires scheduled mode")
        receipt = run_observation(
            repo_root=args.repo_root,
            output_dir=out_dir,
            force_notify=args.force_notify,
            mode=args.mode,
        )
        _print_receipt(args, receipt)
        return 0 if receipt.observation_outcome in ("COMPLETE", "PARTIAL") else 1
    except Exception as exc:
        if args.format == "table":
            safe_reason = "MODE_INVALID" if args.launch_origin == "scheduler" and args.mode != "scheduled" else "OBSERVER_FAILED"
            _print_blocked_table(args, safe_reason)
        else:
            print(f"Observer error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    args = _parse_args()
    return _run_shadow(args) if args.shadow else _execute_observation(args)


if __name__ == "__main__":
    sys.exit(main())
