#!/usr/bin/env python3
"""Scheduled drift-guard automation.

Runs the deterministic governance controls and reports any drift from the
expected state. Intended to be invoked on a schedule (e.g., daily) or
manually when checking repository health.

Usage:
    .venv\\Scripts\\python.exe automations\\drift_guard.py

Exit codes:
    0  All checks passed
    1  One or more checks failed
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def get_git_branch(repo_path: Path) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def is_git_dirty(repo_path: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return bool(res.stdout.strip())
    except Exception:
        pass
    return False


def run_check(label: str, args: list[str], cwd: Path) -> bool:
    """Run a single check and return True if it passed."""
    print(f"  [{label}] ", end="", flush=True)
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("PASS")
        return True
    else:
        print("FAIL")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"    {line}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="NEE Drift Guard Automation")
    parser.add_argument("--worktree-root", type=str, default=None, help="Explicit isolated worktree root")
    parser.add_argument("--enforce-worktree", action="store_true", default=True, help="Mandatory execution strictly inside an isolated worktree branch")
    parser.add_argument("--check-sidecars", action="store_true", help="Verify automation sidecar parity and SSOT link integrity")
    args = parser.parse_args()

    root = Path(args.worktree_root).resolve() if args.worktree_root else DEFAULT_ROOT

    # Worktree isolation guard (mandatory)
    enforce_worktree = True
    branch = get_git_branch(root)
    is_in_worktree = ".worktrees" in root.parts or (branch is not None and branch != "main")

    if enforce_worktree and not is_in_worktree:
        print("ERROR: Execution rejected on primary repository root / 'main'.", file=sys.stderr)
        print("Drift Guard must run off an isolated worktree branch from local main.", file=sys.stderr)
        print("Provision with: python tools/pr_governance.py start refactor/drift-guard-<stamp>", file=sys.stderr)
        return 1

    if branch == "main" and is_git_dirty(root):
        print("ERROR: Direct execution on dirty 'main' is barred by Worktree Governance.", file=sys.stderr)
        return 1

    python = next((p for p in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python") if p.exists()), Path(sys.executable))

    print(f"NEE Drift Guard — {datetime.now(timezone.utc).isoformat()}\nRepository: {root} (branch: {branch or 'unknown'})\n")

    checks = [
        ("runtime-evidence enforce-controls", [
            str(python), "-B", "-m", "dual_pipeline.runtime_evidence",
            "enforce-controls", "--repository-root", str(root),
        ]),
        ("no-synthetic-embeddings", [
            str(python), "-B", str(root / "tools" / "validate_no_synthetic_embeddings.py"),
            "--repository-root", str(root),
        ]),
        ("test: runtime-evidence-claims", [
            str(python), "-B", "-m", "unittest",
            "tests.test_runtime_evidence_claims",
        ]),
        ("test: ingestion-directive-invariants", [
            str(python), "-B", "-m", "unittest",
            "tests.test_ingestion_directive_invariants",
        ]),
        ("test: graph-model-integrity", [
            str(python), "-B", "-m", "unittest",
            "tests.test_graph_model_integrity",
        ]),
        ("test: node-type-policy", [
            str(python), "-B", "-m", "unittest",
            "tests.test_node_type_policy",
        ]),
        ("test: sidecar-validation-cache", [
            str(python), "-B", "-m", "unittest",
            "tests.test_sidecar_validation_cache",
        ]),
        ("reconcile-automation-sidecars", [
            str(python), "-B", str(root / "tools" / "reconcile_automation_sidecars.py"),
            "--check",
        ]),
        ("lint-automation-ssot-links", [
            str(python), "-B", str(root / "tools" / "lint_automation_ssot_links.py"),
        ]),
        ("lint-contract-parity", [
            str(python), "-B", str(root / "tools" / "lint_contract_parity.py"),
        ]),
        ("lint-hardcoded-parameters", [
            str(python), "-B", str(root / "tools" / "lint_hardcoded_parameters.py"),
            "--all",
        ]),
        ("scan-ssot-configuration-violations", [
            str(python), "-B", str(root / "tools" / "scan_ssot_configuration_violations.py"),
        ]),
        ("audit-multi-ssot-judge-gate-drift", [
            str(python), "-B", str(root / "tools" / "audit_multi_ssot_judge_gate_drift.py"),
            "--repo-root", str(root),
        ]),
    ]

    results = []
    for label, args in checks:
        results.append(run_check(label, args, cwd=root))

    passed = sum(results)
    failed = len(results) - passed
    print()
    print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
