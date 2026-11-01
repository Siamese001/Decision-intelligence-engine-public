#!/usr/bin/env python3
"""
tools/run_governance_linters.py

Unified High-Speed AST Governance Linter Runner.
Executes key architectural, layer, budget, and purity linters in parallel
with live execution timings and aggregate pass/fail reporting.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent


def resolve_python() -> str:
    if os.name == "nt":
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
        if venv_py.exists():
            return str(venv_py)
    else:
        venv_py = ROOT / ".venv" / "bin" / "python"
        if venv_py.exists() and os.access(venv_py, os.X_OK):
            return str(venv_py)
    return sys.executable


CORE_LINTERS: List[Tuple[str, List[str]]] = [
    ("Model-Neutral Naming", ["tools/lint_model_neutral_naming.py"]),
    ("Layer Hierarchy & Cycle Linter", ["tools/lint_layer_imports.py"]),
    ("Module File Budgets", ["tools/lint_file_budgets.py", "--ratchet"]),
    (
        "Governed Storage & SQLite Safety (Ruff TID251)",
        ["-m", "ruff", "check", "--select", "TID251", "."],
    ),
    ("Production Purity Linter", ["tools/lint_production_purity.py", "--all"]),
    ("Stage U0 Direct Ingestion Linter", ["tools/lint_no_custom_ingest_scripts.py", "--check"]),
    ("Credential & Career Firewall Scanner", ["tools/scan_credential_leaks.py"]),
    ("Mock Boundary Linter", ["tools/lint_mock_boundaries.py"]),
    ("Contract Parity Linter", ["tools/lint_contract_parity.py", "--check"]),
    ("Epistemic Boundaries Audit", ["tools/lint_epistemic_boundaries.py"]),
    ("HITL Ledger Merkle Integrity", ["tools/validate_hitl_ledger_integrity.py"]),
    ("Dual Pipeline Contracts Validation", ["tools/validate_dual_pipeline_contracts.py"]),
    (
        "No Synthetic Embeddings Policy",
        ["tools/validate_no_synthetic_embeddings.py", "--repository-root", "."],
    ),
    (
        "Wave Implementation Plan Schema Gate",
        ["tools/validate_implementation_plan.py", "--check-all"],
    ),
    ("Root File Allowlist & Size Guard", ["tools/audit_root_hygiene.py"]),
    ("Risk-Tiered Change Lane Classifier", ["tools/change_lane_classifier.py", "--self-test"]),
    ("Prompt SSOT Integrity Linter", ["tools/lint_prompt_ssot_integrity.py", "--sync"]),
    ("Layman Clarity & Zero-Jargon Linter", ["tools/lint_layman_clarity.py", "--check"]),
    ("Worktree Stale Lock & Socket Auditor", ["tools/audit_worktree_stale_locks.py", "--check"]),
]


def run_single_linter(
    name: str, cmd_args: List[str], python_bin: str
) -> Tuple[str, bool, str, float]:
    start = time.time()
    cmd = [python_bin, "-B"] + cmd_args
    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not current_pp else f"{str(ROOT)}:{current_pp}"

    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120.0,
        )
        elapsed = round(time.time() - start, 3)
        success = proc.returncode == 0
        output = proc.stdout if success else (proc.stdout + "\n" + proc.stderr)
        return name, success, output.strip(), elapsed
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        return name, False, str(e), elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified High-Speed AST Governance Linter Runner")
    parser.add_argument(
        "--sequential", action="store_true", help="Run linters sequentially rather than in parallel"
    )
    args = parser.parse_args()

    python_bin = resolve_python()
    start_total = time.time()

    print("=" * 80)
    print("  UNIFIED AST GOVERNANCE LINTER RUNNER")
    print("=" * 80)

    results: List[Tuple[str, bool, str, float]] = []

    if args.sequential:
        for name, cmd in CORE_LINTERS:
            res = run_single_linter(name, cmd, python_bin)
            results.append(res)
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(CORE_LINTERS), 8)
        ) as executor:
            future_to_linter = {
                executor.submit(run_single_linter, name, cmd, python_bin): name
                for name, cmd in CORE_LINTERS
            }
            for future in concurrent.futures.as_completed(future_to_linter):
                results.append(future.result())

    total_elapsed = round(time.time() - start_total, 3)
    results.sort(key=lambda r: r[0])

    failed = [r for r in results if not r[1]]
    for name, success, out, duration in results:
        status_icon = "✅ PASS" if success else "❌ FAIL"
        print(f"  [{status_icon}] {name:<42} ({duration}s)")
        if not success:
            print(f"\n--- Output for {name} ---\n{out}\n-------------------------\n")

    print("=" * 80)
    print(
        f"Execution complete: {len(results) - len(failed)}/{len(results)} passed in {total_elapsed}s."
    )
    print("=" * 80)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
