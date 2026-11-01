#!/usr/bin/env python3
"""
Tier 1: Fast Deterministic Pre-Commit Gatekeeper.

Governing Principle:
  "Is this change structurally legal and hygienic?"

In fast Tier 1 staged mode (<3s):
1. Pre-Flight: Working tree & branch integrity (core.bare check, direct-to-main barrier)
2. Standard Code Hygiene (AST syntax compilation of staged Python files)
3. Ruff Linter Gatekeeper (Ruff checks on staged Python files)
4. Credential & Career Firewall Scanner (scans staged files for leaked secrets)
5. Output Hierarchy & Wave Plan Schema Gate (validates output structures and plans)

In full mode (--full / --all):
Runs all 7 canonical structural and architectural categories via tools.verify_commit_core.

Usage:
  python tools/verify_commit.py [--staged] [--full] [--all] [-v/--verbose] [files ...]
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.branch_integrity import (  # noqa: E402
    run_branch_and_worktree_governance,
)
from tools.verify_commit_core import (  # noqa: E402
    run_architectural_governance,
    run_code_quality,
    run_command,
    run_contract_validation,
    run_production_purity,
    run_security_and_runtime,
    run_system_budgets,
)


def resolve_python() -> str:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and os.access(venv_py, os.X_OK):
        return str(venv_py)
    win_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if win_py.exists():
        return str(win_py)
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True
        )
        if res.returncode == 0:
            c_dir = Path(res.stdout.strip())
            c_root = (
                (ROOT / c_dir).resolve().parent
                if not c_dir.is_absolute()
                else c_dir.resolve().parent
            )
            c_py = c_root / ".venv" / "bin" / "python"
            if c_py.exists() and os.access(c_py, os.X_OK):
                return str(c_py)
    except Exception:
        pass
    return sys.executable


def get_staged_python_files() -> List[str]:
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return [
            line_item.strip()
            for line_item in res.stdout.splitlines()
            if line_item.strip().endswith(".py")
        ]
    except Exception:
        return []


def get_branch_target_files() -> List[str]:
    files: set[str] = set()
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        files.update(res.stdout.splitlines())
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["git", "diff", "main...HEAD", "--name-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        files.update(res.stdout.splitlines())
    except Exception:
        pass
    return sorted([f for f in files if f.endswith(".py") and (ROOT / f).exists()])


def _is_read_only_main_check() -> bool:
    """Distinguish a completed merge check from a staged commit on main."""
    try:
        command = ["git", "symbolic-ref", "--short", "-q", "HEAD"]
        branch = subprocess.run(command, timeout=10, cwd=ROOT, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False
    if branch.returncode or branch.stdout.strip() not in {"main", "master"}:
        return False
    try:
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], timeout=10, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return False
    return staged.returncode == 0


# ── Category 1: Standard Code Hygiene ─────────────────────────────────────────


def _collect_all_governed_python_paths() -> List[Path]:
    gov_dirs = (
        "pipeline_a_intake",
        "pipeline_b_extraction",
        "pipeline_c_retrieval",
        "dual_pipeline",
        "unified_pipeline",
        "storage",
        "scripts",
        "tools",
    )
    paths: List[Path] = []
    for d in gov_dirs:
        d_path = ROOT / d
        if d_path.is_dir():
            paths.extend(sorted(d_path.rglob("*.py")))
    return paths


def check_code_hygiene(files: Sequence[str], staged: bool = False) -> Tuple[bool, str, float]:
    start = time.time()
    errors: List[str] = []
    scanned = 0

    if not files and staged:
        files = get_staged_python_files()
        if not files:
            dur = round(time.time() - start, 3)
            return True, "Verified syntax integrity of 0 staged Python module(s).", dur
    elif not files:
        files = get_staged_python_files()

    target_paths = (
        [ROOT / f for f in files] if files else _collect_all_governed_python_paths()
    )

    for p in target_paths:
        if p.is_file() and p.suffix == ".py":
            scanned += 1
            try:
                code = p.read_text(encoding="utf-8", errors="ignore")
                compile(code, str(p), "exec")
            except SyntaxError as e:
                rel_p = p.relative_to(ROOT) if p.is_relative_to(ROOT) else p
                errors.append(f"SyntaxError in {rel_p}:{e.lineno}: {e.msg}")
            except Exception as e:
                rel_p = p.relative_to(ROOT) if p.is_relative_to(ROOT) else p
                errors.append(f"CompileError in {rel_p}: {e}")

    dur = round(time.time() - start, 3)
    if errors:
        return False, "\n".join(errors), dur
    return True, f"Verified syntax integrity of {scanned} Python module(s).", dur


# ── Category: Ruff Fast Linter Gatekeeper ────────────────────────────────────


def check_ruff_hygiene(files: Sequence[str]) -> Tuple[bool, str, float]:
    start = time.time()
    target_files = [f for f in files if f.endswith(".py") and (ROOT / f).exists()]
    if not target_files:
        return True, "No staged Python files for Ruff check.", 0.0

    ruff_bin = ROOT / ".venv" / "bin" / "ruff"
    if not ruff_bin.exists():
        py_ruff = Path(resolve_python()).parent / "ruff"
        ruff_bin = py_ruff if py_ruff.exists() else Path("ruff")

    cmd = [str(ruff_bin), "check"] + target_files
    ok, out, _ = run_command(cmd)
    dur = round(time.time() - start, 3)
    if not ok:
        return False, f"[Ruff Violations]\n{out}", dur
    return True, f"Ruff checked {len(target_files)} staged module(s): 100% PASS.", dur


from tools.shift_left_core import check_staged_shift_left, run_benchmark


# ── Category: Fast Credential & Privacy Gate ─────────────────────────────────


def check_fast_credentials(
    py: str, staged: bool = True, files: Optional[Sequence[str]] = None
) -> Tuple[bool, str, float]:
    cmd = [py, "-B", "tools/scan_credential_leaks.py"]
    if staged:
        cmd.append("--staged")
    elif files:
        cmd.extend(files)
    ok, out, dur = run_command(cmd)
    if not ok:
        return False, f"[Credential Leak Scanner FAILED]\n{out}", dur
    return True, "No exposed secrets or credentials detected.", dur


# ── Category: Fast Output & Schema Hygiene ───────────────────────────────────


def check_fast_hierarchy_and_plan(py: str, staged: bool = True) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        (
            "Output Hierarchy",
            [py, "-B", "tools/validate_output_hierarchy.py", "--check"]
            + (["--staged"] if staged else []),
        ),
        (
            "Implementation Plan",
            [py, "-B", "tools/validate_implementation_plan.py"] + (["--staged"] if staged else []),
        ),
        (
            "Root File Allowlist & Size Guard",
            [py, "-B", "tools/audit_root_hygiene.py", "--check"] + (["--staged"] if staged else []),
        ),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")

    from tools.lint_test_and_healer_ratchet import run_ratchet_audit

    ratchet_files = get_staged_python_files() if staged else None
    ratchet_violations = run_ratchet_audit(ROOT, ratchet_files)
    if ratchet_violations:
        joined = "\n".join(f"  - {v}" for v in ratchet_violations)
        return False, f"[Test & Healer Ratchet FAILED]\n{joined}", round(time.time() - start, 3)
    outputs.append("Test & Healer Ratchet: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def check_nocturnal_fatigue_window() -> Tuple[bool, str]:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    edt_hour = (now_utc.hour - 4) % 24
    if 4 <= edt_hour < 8:
        return (
            True,
            f"⚠️  NOCTURNAL FATIGUE WINDOW ADVISORY: Current time {edt_hour:02d}:{now_utc.minute:02d} EDT falls in 04:00–07:59 nocturnal fatigue window. Double-check verification gates.",
        )
    return False, ""


def _print_category_result(
    passed: bool, output: str, duration: float, quiet: bool, verbose: bool
) -> None:
    if quiet:
        return
    if passed:
        print(f"✅ PASS ({duration}s)")
        if verbose and output:
            print(f"    {output}")
    else:
        print(f"❌ FAIL ({duration}s)")
        print("-" * 50)
        print(output)
        print("-" * 50)


def _execute_categories(
    categories: Sequence[Tuple[str, Any]],
    quiet: bool,
    verbose: bool,
) -> Tuple[bool, float]:
    total_categories = len(categories)
    overall_pass = True
    total_duration = 0.0

    for idx, (title, runner) in enumerate(categories, start=1):
        if not quiet:
            print(f"[{idx}/{total_categories}] Checking {title}...", end=" ", flush=True)
        passed, output, duration = runner()
        total_duration += duration
        _print_category_result(passed, output, duration, quiet, verbose)
        if not passed:
            overall_pass = False

    return overall_pass, total_duration


def verify_commit(
    staged: bool = True,
    all_files: bool = False,
    full: bool = False,
    verbose: bool = False,
    explicit_files: Optional[Sequence[str]] = None,
    quiet: bool = False,
) -> int:
    py = resolve_python()
    staged_args = ["--staged"] if staged and not all_files and not explicit_files else []
    file_args = list(explicit_files) if explicit_files else []
    target_py_files = file_args or get_staged_python_files()

    is_full_mode = full or all_files

    if not quiet:
        print("=" * 80)
        print(
            "  FULL STRUCTURAL LEGALITY GATE (7 CATEGORIES)"
            if is_full_mode
            else "  TIER 1: FAST PRE-COMMIT STRUCTURAL GATEKEEPER (<3s)"
        )
        print(f"  Repository Root: {ROOT}")
        print(f"  Mode:            {'ALL' if all_files else 'FULL' if full else 'STAGED (FAST)'}")
        print("=" * 80)

    # Pre-Flight Gate: Working Tree & Branch Integrity
    if not quiet:
        print("[0/N] Pre-Flight: Checking Working Tree & Branch Integrity...", end=" ", flush=True)
    pf_passed, pf_output, pf_duration = run_branch_and_worktree_governance(ROOT)
    if pf_passed:
        if not quiet:
            print(f"✅ PASS ({pf_duration}s)")
            if verbose and pf_output:
                print(f"    {pf_output}")
            fatigue_active, fatigue_msg = check_nocturnal_fatigue_window()
            if fatigue_active:
                print(f"  {fatigue_msg}")
    else:
        print(f"❌ FAIL ({pf_duration}s)")
        print("-" * 50)
        print(pf_output)
        print("-" * 50)
        print("=" * 80)
        print(
            f"⛔ PRE-COMMIT REJECTED: Pre-Flight structural violations detected in {round(pf_duration, 2)}s."
        )
        print("=" * 80)
        return 1

    if not is_full_mode:
        # Fast Tier 1 Category Set (<3s)
        categories = [
            (
                "1. Code Hygiene (AST Compilation)",
                lambda: check_code_hygiene(target_py_files, staged=bool(staged_args)),
            ),
            (
                "2. Fast Linter (Ruff Gatekeeper & TID251)",
                lambda: check_ruff_hygiene(target_py_files),
            ),
            (
                "3. Staged Shift-Left & Ratchet Gate",
                lambda: check_staged_shift_left(target_py_files, staged=bool(staged_args)),
            ),
            (
                "4. Credential & Privacy Firewall",
                lambda: check_fast_credentials(py, staged=bool(staged_args), files=file_args),
            ),
            (
                "5. Output Hierarchy & Plan Hygiene",
                lambda: check_fast_hierarchy_and_plan(py, staged=bool(staged_args)),
            ),
            (
                "6. Production Purity & Ingestion Protection",
                lambda: run_production_purity(py, staged_args, file_args),
            ),
        ]

    else:
        # Full 7-Category Set
        target_files = file_args or (
            get_staged_python_files() if staged_args else get_branch_target_files()
        )
        categories = [
            (
                "1. Standard Code Hygiene (Syntax & AST Compilation)",
                lambda: check_code_hygiene(file_args or get_staged_python_files()),
            ),
            (
                "2. Architectural & Layer Governance",
                lambda: run_architectural_governance(py, staged_args, file_args),
            ),
            (
                "3. Production Purity & Ingestion Protection",
                lambda: run_production_purity(py, staged_args, file_args),
            ),
            (
                "4. Code Quality & Cognitive Complexity",
                lambda: run_code_quality(py, staged_args, file_args, target_files),
            ),
            (
                "5. Contract, Schema & Hierarchy Integrity",
                lambda: run_contract_validation(py, staged=bool(staged_args)),
            ),
            ("6. Workspace Skills & MCP Budgets", lambda: run_system_budgets(py)),
            (
                "7. Secrets, Privacy & Runtime Controls",
                lambda: run_security_and_runtime(py, staged_args, file_args),
            ),
        ]

    total_categories = len(categories)
    overall_pass, total_duration = _execute_categories(categories, quiet, verbose)

    if not quiet:
        print("=" * 80)
        if overall_pass:
            print(
                f"🎉 PRE-COMMIT GATE VERIFIED: All {total_categories} categories passed in {round(total_duration, 2)}s."
            )
            print("   Change is structurally legal for local feature commit.")
        else:
            print(
                f"⛔ PRE-COMMIT REJECTED: Structural violations detected in {round(total_duration, 2)}s."
            )
            print("   Resolve violations before committing to local feature branch.")
        print("=" * 80)
    return 0 if overall_pass else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tier 1 Pre-Commit Structural Legality Gate.")
    parser.add_argument(
        "--staged",
        action="store_true",
        default=True,
        help="Check staged git files (default fast mode)",
    )
    parser.add_argument(
        "--full", action="store_true", help="Run full 7-category validation on staged files"
    )
    parser.add_argument(
        "--all", action="store_true", help="Check entire repository across all 7 categories"
    )
    parser.add_argument(
        "--benchmark", action="store_true", help="Benchmark pre-commit latency over N iterations"
    )
    parser.add_argument(
        "--runs", type=int, default=10, help="Number of benchmark iterations (default 10)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("files", nargs="*", help="Specific files to check")
    args = parser.parse_args(argv)

    if args.benchmark:
        return run_benchmark(runs=args.runs, verbose=args.verbose)

    if args.staged and not (args.full or args.all or args.files) and _is_read_only_main_check():
        from tools.verify_post_merge import verify_latest_merge

        passed, detail = verify_latest_merge(ROOT)
        print(f"POST-MERGE {'PASS' if passed else 'FAIL'}: {detail}")
        return 0 if passed else 1

    is_all = args.all
    is_full = args.full or is_all
    is_staged = not is_all and (args.staged or not args.files)
    return verify_commit(
        staged=is_staged,
        all_files=is_all,
        full=is_full,
        verbose=args.verbose,
        explicit_files=args.files,
    )


if __name__ == "__main__":
    sys.exit(main())
