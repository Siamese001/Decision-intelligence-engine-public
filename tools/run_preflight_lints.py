#!/usr/bin/env python3
"""
tools/run_preflight_lints.py

High-speed (<1.0s) pre-commit preflight gate runner.
Runs purely static AST and policy linters before any test suite is invoked,
giving immediate sub-second developer feedback and eliminating pre-commit friction.

Gates Executed:
1. Parameter Reference & Parity Linter (tools/lint_parameter_references.py)
2. Hardcoded Parameter & SSOT Linter (tools/lint_hardcoded_parameters.py)
3. AST Invariants & Data/Code Decoupling (tools/lint_ast_invariants.py)
4. Unidirectional Layer Imports & SCC Cycles (tools/lint_layer_imports.py)
5. Module 400-Line Budget Linter (tools/lint_file_budgets.py)
6. Cognitive Complexity Linter (tools/lint_cognitive_complexity.py)
7. Credential & Career Firewall Scanner (tools/scan_credential_leaks.py)
8. Model-Neutral Naming Linter (tools/lint_model_neutral_naming.py)
9. Blast Radius Risk Linter (tools/lint_blast_radius.py)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent


def resolve_python() -> str:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and os.access(venv_py, os.X_OK):
        return str(venv_py)
    win_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if win_py.exists():
        return str(win_py)
    return sys.executable


def run_linter(name: str, cmd: List[str]) -> Tuple[bool, str, float]:
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120.0,
        )
        duration = round(time.time() - start, 3)
        success = (proc.returncode == 0)
        output = proc.stdout if success else (proc.stdout + "\n" + proc.stderr)
        return success, output.strip(), duration
    except Exception as exc:
        duration = round(time.time() - start, 3)
        return False, str(exc), duration


def main() -> int:
    parser = argparse.ArgumentParser(description="High-speed preflight static linter gate")
    parser.add_argument("--staged", action="store_true", default=True, help="Scan staged git files only (default)")
    parser.add_argument("--all", action="store_true", help="Scan all repository files")
    parser.add_argument("--verbose", action="store_true", help="Print verbose output for passes")
    args = parser.parse_args()

    py = resolve_python()
    scan_all = args.all

    if scan_all:
        linters = [
            ("Parameter References", [py, "-B", "tools/lint_parameter_references.py", "--all"]),
            ("Hardcoded Parameters", [py, "-B", "tools/lint_hardcoded_parameters.py", "--all"]),
            ("Layer Imports & Cycles", [py, "-B", "tools/lint_layer_imports.py"]),
            ("File Budgets (400L)", [py, "-B", "tools/lint_file_budgets.py"]),
            ("Cognitive Complexity", [py, "-B", "tools/lint_cognitive_complexity.py", "--check", "--ratchet"]),
            ("Credential & Career Firewall", [py, "-B", "tools/scan_credential_leaks.py"]),
            ("Model-Neutral Naming", [py, "-B", "tools/lint_model_neutral_naming.py"]),
            ("Mock Ban (High-Fidelity)", [py, "-B", "tools/lint_mock_usage.py", "--all", "--check"]),
            ("ReDoS Static Analysis", [py, "-B", "tools/lint_regex_redos.py", "--all", "--check"]),
            ("Blast Radius Limit", [py, "-B", "tools/lint_blast_radius.py", "--check"]),
        ]
    else:
        linters = [
            ("Parameter References", [py, "-B", "tools/lint_parameter_references.py", "--staged"]),
            ("Hardcoded Parameters", [py, "-B", "tools/lint_hardcoded_parameters.py", "--staged"]),
            ("AST Invariants", [py, "-B", "tools/lint_ast_invariants.py", "--staged"]),
            ("Layer Imports & Cycles", [py, "-B", "tools/lint_layer_imports.py", "--staged"]),
            ("File Budgets (400L)", [py, "-B", "tools/lint_file_budgets.py", "--staged"]),
            ("Cognitive Complexity", [py, "-B", "tools/lint_cognitive_complexity.py", "--staged", "--check", "--ratchet"]),
            ("Credential & Career Firewall", [py, "-B", "tools/scan_credential_leaks.py", "--staged"]),
            ("Model-Neutral Naming", [py, "-B", "tools/lint_model_neutral_naming.py", "--staged"]),
            ("Mock Ban (High-Fidelity)", [py, "-B", "tools/lint_mock_usage.py", "--staged", "--check"]),
            ("ReDoS Static Analysis", [py, "-B", "tools/lint_regex_redos.py", "--staged", "--check"]),
            ("Blast Radius Limit", [py, "-B", "tools/lint_blast_radius.py", "--staged", "--check"]),
        ]

    print("=" * 70)
    print("  FAST PREFLIGHT STATIC LINTERS (GATE 0)")
    print(f"  Mode: {'ALL REPOSITORY' if scan_all else 'STAGED'}")
    print("=" * 70)


    total_start = time.time()
    all_passed = True
    failures: List[Tuple[str, str]] = []

    for name, cmd in linters:
        passed, output, duration = run_linter(name, cmd)
        status_sym = "✅" if passed else "❌"
        print(f"  {status_sym} {name:<30} ({duration:.3f}s)")
        if not passed:
            all_passed = False
            failures.append((name, output))
        elif args.verbose and output:
            print(f"     {output}")

    total_duration = round(time.time() - total_start, 3)
    print("-" * 70)
    print(f"  Total Preflight Elapsed: {total_duration:.3f}s")
    print("=" * 70)

    if not all_passed:
        print("\n❌ PREFLIGHT FAILURES DETECTED:")
        for name, output in failures:
            print(f"\n--- [{name}] ---")
            print(output)
        return 1

    print("✅ All preflight static linters passed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
