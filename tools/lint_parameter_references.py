#!/usr/bin/env python3
"""
tools/lint_parameter_references.py

AST Linter for Parameter Reference & Configuration Integrity (SSOT Governance).
Enforces:
1. UNDECLARED_PARAMETER_KEY: All `get_*parameter("dotted.key")` calls in production code
   must reference a declared key in `config/runtime/parameters.json`. Eliminates typos and silent fallback drift.
2. DEAD_CONFIGURATION_KEY: Flags keys declared in `parameters.json` that have zero consumers.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ssot_config_engine import (
    DECLARED_PARAMETER_KEYS,
    DEFAULT_GOVERNED_DIRS,
    SSOTASTViolation as Violation,
    TARGET_PARAMETER_FUNCTIONS as TARGET_FUNCTIONS,
    UnifiedSSOTASTVisitor,
    collect_declared_keys,
    discover_governed_python_files,
    load_declared_parameter_keys as _engine_load_declared_keys,
)

PARAMETERS_JSON = ROOT / "config" / "runtime" / "parameters.json"
GOVERNED_DIRS = list(DEFAULT_GOVERNED_DIRS)


def load_declared_parameter_keys() -> Set[str]:
    if PARAMETERS_JSON == ROOT / "config" / "runtime" / "parameters.json" and DECLARED_PARAMETER_KEYS:
        return set(DECLARED_PARAMETER_KEYS)
    return _engine_load_declared_keys(PARAMETERS_JSON)


class ParameterReferenceVisitor(UnifiedSSOTASTVisitor):
    """Thin subclass delegating parameter reference AST inspection to UnifiedSSOTASTVisitor."""

    def __init__(self, rel_path: str, declared_keys: Set[str]) -> None:
        super().__init__(
            rel_path=rel_path,
            lines=(),
            declared_keys=declared_keys,
            check_undeclared_keys=True,
        )

    def visit_Call(self, node: ast.Call) -> None:
        self._check_parameter_reference_call(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        return


def scan_file(file_path: Path, declared_keys: Set[str]) -> tuple[List[Violation], Set[str]]:
    try:
        rel_path = str(file_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel_path = str(file_path)

    if not rel_path.endswith(".py"):
        return [], set()

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return [], set()

    visitor = ParameterReferenceVisitor(rel_path, declared_keys)
    visitor.visit(tree)
    return visitor.violations, visitor.referenced_keys


def get_staged_python_files() -> List[Path]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=d"]
    try:
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
        paths = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.endswith(".py"):
                p = ROOT / line
                if p.is_file():
                    paths.append(p)
        return paths
    except Exception:
        return []


def get_all_python_files() -> List[Path]:
    return discover_governed_python_files(ROOT, GOVERNED_DIRS)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AST Parameter Reference & Dead-Config Linter")
    parser.add_argument("--staged", action="store_true", help="Check only staged python files")
    parser.add_argument("--all", action="store_true", help="Check all python files in governed dirs")
    parser.add_argument("--check-dead", action="store_true", help="Flag declared keys with zero callers")
    parser.add_argument("files", nargs="*", help="Specific files to lint")
    args = parser.parse_args(argv)

    declared_keys = load_declared_parameter_keys()
    if not declared_keys:
        print("❌ FAILED: Unable to load declared keys from config/runtime/parameters.json")
        return 1

    if args.staged:
        files = get_staged_python_files()
    elif args.all or not args.files:
        files = get_all_python_files()
    else:
        files = [Path(f).resolve() for f in args.files if f.endswith(".py")]

    violations: List[Violation] = []
    all_referenced: Set[str] = set()

    for f in files:
        if f.exists() and f.is_file():
            v, refs = scan_file(f, declared_keys)
            violations.extend(v)
            all_referenced.update(refs)

    print("=" * 80)
    print("  AST PARAMETER REFERENCE & CONFIGURATION INTEGRITY LINTER")
    print(f"  Files Scanned:   {len(files)}")
    print(f"  Declared Keys:   {len(declared_keys)}")
    print(f"  Referenced Keys: {len(all_referenced)}")
    print("=" * 80)

    if violations:
        print(f"❌ FAILED: Found {len(violations)} undeclared parameter reference(s):\n")
        for v in violations:
            print(f"  [{v.rule}] {v.file_path}:{v.line_number}")
            print(f"    {v.detail}\n")
        print("=" * 80)
        return 1

    print("✅ PASSED: 100% of codebase parameter lookups reference valid declared keys.")
    if args.check_dead and (args.all or not args.files):
        unreferenced = declared_keys - all_referenced
        if unreferenced:
            print(f"❌ FAILED: {len(unreferenced)} declared key(s) not referenced in scanned files:")
            for k in sorted(unreferenced):
                print(f"    - {k}")
            print("=" * 80)
            return 1
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
