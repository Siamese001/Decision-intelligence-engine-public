#!/usr/bin/env python3
"""
tools/lint_timeout_enforcement.py

AST Governance Linter for Timeout Enforcement:
- Verifies that all `sqlite3.connect()` calls include an explicit `timeout=` parameter.
- Verifies that `fcntl.flock()` calls include `fcntl.LOCK_NB` to prevent unbounded blocking.
- Verifies that `subprocess.run()`, `subprocess.check_output()`, and `subprocess.check_call()`
  calls specify an explicit `timeout=` parameter across runtime, pipeline, and core tooling.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

GOVERNED_PIPELINE_DIRS = (
    "dual_pipeline",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "storage",
)

GOVERNED_TOOL_PATHS = (
    "tools/ci_gates_core/runner.py",
    "tools/pr_governance_core/conflict_triage.py",
    "tools/pr_governance_core/common.py",
    "tools/pr_governance_core/lifecycle.py",
    "tools/pr_governance_core/merge_queue.py",
    "tools/pr_governance_core/inspection.py",
    "tools/verify_commit_core/categories.py",
    "tools/pr_governance.py",
    "tools/rebase_orchestrator.py",
    "tools/run_tests_with_progress.py",
    "tools/run_governance_linters.py",
    "tools/run_preflight_lints.py",
    "tools/test_timing_db.py",
    "tools/cards_refactor.py",
    "tools/audit_card_drift.py",
    "tools/run_adversarial_entity_audit.py",
    "tools/build_retrieval_human_review_packet.py",
    "tools/lint_epistemic_boundaries.py",
    "tools/lint_timeout_enforcement.py",
)

EXCLUDED_PATHS = {
    "tests",
    ".worktrees",
    ".venv",
}


class TimeoutEnforcementVisitor(ast.NodeVisitor):
    def __init__(self, filename: str, check_subprocess: bool = True) -> None:
        self.filename = filename
        self.check_subprocess = check_subprocess
        self.violations: List[Tuple[int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        self._check_sqlite_connect(node)
        self._check_fcntl_flock(node)
        if self.check_subprocess:
            self._check_subprocess_call(node)
        self.generic_visit(node)

    def _check_sqlite_connect(self, node: ast.Call) -> None:
        """Flags sqlite3.connect() calls lacking timeout= parameter."""
        func = node.func
        is_sqlite_connect = False
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            if isinstance(func.value, ast.Name) and func.value.id in ("sqlite3", "sqlcipher_driver"):
                is_sqlite_connect = True

        if is_sqlite_connect:
            has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
            if not has_timeout:
                self.violations.append(
                    (node.lineno, "SQLITE_TIMEOUT_MISSING", "sqlite3.connect() called without explicit timeout parameter")
                )

    def _check_fcntl_flock(self, node: ast.Call) -> None:
        """Flags fcntl.flock() calls lacking LOCK_NB non-blocking flag (unless unlocking)."""
        func = node.func
        is_flock = False
        if isinstance(func, ast.Attribute) and func.attr == "flock":
            if isinstance(func.value, ast.Name) and func.value.id == "fcntl":
                is_flock = True

        if is_flock and len(node.args) >= 2:
            op_arg = node.args[1]
            op_src = ast.unparse(op_arg)
            if "LOCK_UN" in op_src:
                return
            if ("LOCK_EX" in op_src or "LOCK_SH" in op_src) and "LOCK_NB" not in op_src:
                self.violations.append(
                    (node.lineno, "BLOCKING_FLOCK_DETECTED", f"fcntl.flock() uses blocking operation '{op_src}'; must include LOCK_NB")
                )

    def _check_subprocess_call(self, node: ast.Call) -> None:
        """Flags subprocess functions called without explicit timeout parameter."""
        func = node.func
        subproc_funcs = {"run", "check_output", "check_call"}
        is_subproc = False
        call_name = ""

        if isinstance(func, ast.Attribute) and func.attr in subproc_funcs:
            if isinstance(func.value, ast.Name) and func.value.id in ("subprocess", "subproc"):
                is_subproc = True
                call_name = f"subprocess.{func.attr}"

        if is_subproc:
            has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
            if not has_timeout:
                self.violations.append(
                    (node.lineno, "SUBPROCESS_TIMEOUT_MISSING", f"{call_name}() called without explicit timeout parameter")
                )


def scan_file(file_path: Path, check_subprocess: bool = True) -> List[Tuple[int, str, str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except Exception as e:
        return [(0, "PARSE_ERROR", f"Could not parse file: {e}")]

    visitor = TimeoutEnforcementVisitor(str(file_path), check_subprocess=check_subprocess)
    visitor.visit(tree)
    return visitor.violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan repository for timeout enforcement violations.")
    parser.add_argument("paths", nargs="*", help="Optional specific paths to scan")
    parser.add_argument("--all-tools", action="store_true", help="Enforce subprocess timeouts across all legacy tools")
    args = parser.parse_args()

    explicit_mode = bool(args.paths)
    target_items: List[Tuple[Path, bool]] = []  # (path, check_subprocess)

    if explicit_mode:
        for p in args.paths:
            resolved = Path(p).resolve()
            if resolved.is_dir():
                for py_file in resolved.rglob("*.py"):
                    target_items.append((py_file, True))
            elif resolved.is_file() and resolved.suffix == ".py":
                target_items.append((resolved, True))
    else:
        # Default mode: Pipeline directories strictly enforce all checks
        for d in GOVERNED_PIPELINE_DIRS:
            dir_path = ROOT / d
            if dir_path.is_dir():
                for py_file in dir_path.rglob("*.py"):
                    target_items.append((py_file, True))

        # Core governed tools enforce all checks
        for tp in GOVERNED_TOOL_PATHS:
            t_path = ROOT / tp
            if t_path.is_dir():
                for py_file in t_path.rglob("*.py"):
                    target_items.append((py_file, True))
            elif t_path.is_file() and t_path.suffix == ".py":
                target_items.append((t_path, True))

        # All other tools enforce sqlite3 and flock checks (and subprocess if --all-tools)
        tools_dir = ROOT / "tools"
        if tools_dir.is_dir():
            for py_file in tools_dir.rglob("*.py"):
                if not any(py_file == (ROOT / tp) or (ROOT / tp).is_dir() and py_file.is_relative_to(ROOT / tp) for tp in GOVERNED_TOOL_PATHS):
                    target_items.append((py_file, args.all_tools))

    total_scanned = 0
    all_violations: List[Tuple[Path, int, str, str]] = []
    seen_paths = set()

    for path, check_subproc in sorted(target_items, key=lambda x: str(x[0])):
        if path in seen_paths:
            continue
        seen_paths.add(path)

        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        rel_str = str(rel)
        if any(rel_str.startswith(excl) or f"/{excl}/" in rel_str for excl in EXCLUDED_PATHS):
            continue

        total_scanned += 1
        violations = scan_file(path, check_subprocess=check_subproc)
        for lineno, code, msg in violations:
            all_violations.append((rel, lineno, code, msg))

    print("=" * 80)
    print("  TIMEOUT ENFORCEMENT AST GOVERNANCE LINTER")
    print("=" * 80)
    print(f"Scanned {total_scanned} governed Python module(s).")

    if all_violations:
        print(f"\n❌ FAILED: Found {len(all_violations)} timeout enforcement violation(s):\n")
        for rel, lineno, code, msg in all_violations:
            print(f"  {rel}:{lineno} [{code}] {msg}")
        print("\n" + "=" * 80)
        return 1

    print("✅ PASSED: All scanned modules satisfy timeout enforcement invariants.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
