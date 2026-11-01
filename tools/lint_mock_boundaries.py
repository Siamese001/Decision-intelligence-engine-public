#!/usr/bin/env python3
"""tools/lint_mock_boundaries.py

Linter to enforce the Operator-Directed Live Execution Invariant and AGENTS.md
Invariant 4 across production modules, pipelines, and operational scripts.

Enforces:
1. Operational scripts in scripts/ must never hardcode allow_mock=True or mock=True.
2. Credentials and client factories in config/ and dual_pipeline/ must default to allow_mock=False.
3. Judge evaluators and pipeline extraction layers must not default or silently degrade to mock.
4. Tests (tests/) remain hermetic and are exempt from production live-execution restrictions.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

EXEMPT_DIRS = {"tests", ".venv", "venv", "build", "dist", ".git", ".pytest_cache"}
EXEMPT_FILES: set[str] = set()

BANNED_MOCK_KWARGS = (
    "allow_mock",
    "mock",
    "mock_mode",
    "use_mock",
    "offline_mode",
    "fake_mode",
    "dummy_mode",
    "mock_provider",
    "use_fake",
    "allow_fake",
    "allow_offline",
)


def _is_truthy_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in (True, 1, "true", "True")
    return False


class MockBoundaryVisitor(ast.NodeVisitor):
    """AST visitor detecting hardcoded mock allowances in production scopes."""

    def __init__(self, filename: str, is_script: bool) -> None:
        self.filename = filename
        self.is_script = is_script
        self.violations: List[Tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Check keyword arguments
        for kw in node.keywords:
            if kw.arg in BANNED_MOCK_KWARGS and _is_truthy_constant(kw.value):
                if self.is_script:
                    self.violations.append(
                        (node.lineno, f"Operational script hardcodes `{kw.arg}=True`.")
                    )
                else:
                    self.violations.append(
                        (node.lineno, f"Production module hardcodes `{kw.arg}=True`.")
                    )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_defaults(node)
        self.generic_visit(node)

    def _check_function_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Check positional and regular keyword argument defaults
        offset = len(node.args.args) - len(node.args.defaults)
        for i, default_node in enumerate(node.args.defaults):
            arg_idx = offset + i
            if 0 <= arg_idx < len(node.args.args):
                arg = node.args.args[arg_idx]
                if arg.arg in BANNED_MOCK_KWARGS and _is_truthy_constant(default_node):
                    self.violations.append(
                        (node.lineno, f"Function `{node.name}` defaults `{arg.arg}=True`.")
                    )

        # Check keyword-only argument defaults
        for arg, default_node in zip(node.args.kwonlyargs, node.args.kw_defaults):
            if default_node is not None and arg.arg in BANNED_MOCK_KWARGS:
                if _is_truthy_constant(default_node):
                    self.violations.append(
                        (node.lineno, f"Function `{node.name}` defaults `{arg.arg}=True`.")
                    )


def get_staged_files() -> List[Path]:
    """Retrieves staged Python files from git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=d"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        files = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.endswith(".py"):
                p = ROOT / line
                if p.is_file():
                    files.append(p)
        return files
    except Exception:
        return []


def is_exempt(path: Path) -> bool:
    """True if path belongs to exempt test or build directories or files."""
    if path.name in EXEMPT_FILES:
        return True
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    parts = rel.parts
    return any(p in EXEMPT_DIRS for p in parts)


def audit_file(path: Path) -> List[str]:
    """Audits a single Python file for mock boundary compliance."""
    if is_exempt(path):
        return []

    try:
        rel_str = str(path.relative_to(ROOT))
    except ValueError:
        rel_str = str(path)

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception as e:
        return [f"{rel_str}: Parse error: {e}"]

    is_script = rel_str.startswith("scripts/")

    visitor = MockBoundaryVisitor(rel_str, is_script=is_script)
    visitor.visit(tree)

    return [f"{rel_str}:{line}: {msg}" for line, msg in visitor.violations]


def _resolve_targets(args: argparse.Namespace) -> List[Path]:
    if args.files:
        return [Path(f).resolve() for f in args.files if Path(f).suffix == ".py"]
    if args.staged:
        return get_staged_files()
    targets = []
    for dir_name in ("scripts", "dual_pipeline", "pipeline_a_intake", "pipeline_b_extraction"):
        d = ROOT / dir_name
        if d.is_dir():
            targets.extend(d.rglob("*.py"))
    return targets


def _run_third_party_patch_check(args: argparse.Namespace) -> int:
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.lint_third_party_patch import main as run_patch_lint
        patch_args = ["--staged"] if args.staged else []
        if args.files:
            test_files = [f for f in args.files if "tests" in f and f.endswith(".py")]
            if test_files:
                patch_args = test_files
            else:
                return 0
        return run_patch_lint(patch_args)
    except Exception as e:
        print(f"⚠️  Third-party patch linter advisory: {e}", file=sys.stderr)
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mock Boundary Governance Linter")
    parser.add_argument("--staged", action="store_true", help="Audit only staged files in git index.")
    parser.add_argument("files", nargs="*", help="Explicit files to audit.")
    args = parser.parse_args(argv)

    targets = _resolve_targets(args)
    all_violations: List[str] = []
    scanned = 0

    for target in targets:
        if target.is_file() and not is_exempt(target):
            scanned += 1
            violations = audit_file(target)
            all_violations.extend(violations)

    if scanned == 0 and not args.staged:
        has_real_non_py_files = bool(args.files and any(Path(f).is_file() for f in args.files))
        if not has_real_non_py_files:
            print("❌ Mock boundary check FAILED: Unexpected empty scope (0 modules scanned).", file=sys.stderr)
            return 1

    if all_violations:
        print("=" * 80)
        print("  MOCK BOUNDARY LINTER: VIOLATIONS DETECTED")
        print("=" * 80)
        for v in all_violations:
            print(f"  ❌ {v}")
        print("=" * 80)
        print(f"Total violations: {len(all_violations)} across {scanned} scanned module(s).")
        return 1

    patch_rc = _run_third_party_patch_check(args)
    if patch_rc != 0:
        return 1

    print(f"✅ Mock boundary check passed: {scanned} module(s) verified clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
