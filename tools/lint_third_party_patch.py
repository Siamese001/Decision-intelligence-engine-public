#!/usr/bin/env python3
"""
tools/lint_third_party_patch.py

AST Governance Linter: Bans unittest.mock.patch on third-party library paths
(subprocess, requests, urllib, httpx) in new test files.
Requires tests to use real disposable resources or pure policy tests.
Grandfathers legacy test files via config/governance/third_party_patch_allowlist.json.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent

BANNED_PREFIXES = ("subprocess", "requests", "urllib", "httpx")
ALLOWLIST_PATH = ROOT / "config" / "governance" / "third_party_patch_allowlist.json"


def load_allowlist() -> Set[str]:
    """Loads grandfathered legacy test files from allowlist."""
    if not ALLOWLIST_PATH.exists():
        return set()
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        return set(data.get("allowlisted_legacy_test_files", []))
    except Exception:
        return set()


def _resolve_target_name(node: ast.AST) -> Optional[str]:
    """Resolves an AST node to a dotted name string if possible."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _resolve_target_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    return None


def _is_banned_target(target: Optional[str]) -> bool:
    if not target:
        return False
    for prefix in BANNED_PREFIXES:
        if target == prefix or target.startswith(f"{prefix}."):
            return True
    return False


class ThirdPartyPatchVisitor(ast.NodeVisitor):
    """AST visitor detecting patch calls targeting third-party modules."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: List[Tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        is_patch_object = False
        is_setattr = False

        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name == "setattr":
                is_setattr = True
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if func_name == "object":
                val = node.func.value
                if (isinstance(val, ast.Name) and val.id in ("patch", "p")) or (
                    isinstance(val, ast.Attribute) and val.attr == "patch"
                ):
                    is_patch_object = True
            elif func_name == "setattr":
                is_setattr = True

        if func_name in ("patch", "multiple") and node.args:
            target = _resolve_target_name(node.args[0])
            if _is_banned_target(target):
                self.violations.append(
                    (
                        node.lineno,
                        f"Direct patch('{target}') on third-party library is banned in new tests. "
                        f"Use real disposable resources or pure policy tests instead.",
                    )
                )
        elif is_patch_object and node.args:
            target = _resolve_target_name(node.args[0])
            if _is_banned_target(target):
                self.violations.append(
                    (
                        node.lineno,
                        f"Direct patch.object({target}, ...) on third-party library is banned in new tests. "
                        f"Use real disposable resources or pure policy tests instead.",
                    )
                )
        elif is_setattr and node.args:
            target = _resolve_target_name(node.args[0])
            if _is_banned_target(target):
                self.violations.append(
                    (
                        node.lineno,
                        f"Direct setattr({target}, ...) on third-party library is banned in new tests. "
                        f"Use real disposable resources or pure policy tests instead.",
                    )
                )

        self.generic_visit(node)


def audit_test_file(path: Path, allowlist: Set[str]) -> List[str]:
    """Audits a single test file for banned third-party patch calls."""
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    rel_str = rel.as_posix()

    if rel_str in allowlist:
        return []

    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except Exception as e:
        return [f"{rel_str}: Parse error: {e}"]

    visitor = ThirdPartyPatchVisitor(rel_str)
    visitor.visit(tree)

    return [f"{rel_str}:{line}: {msg}" for line, msg in visitor.violations]


def get_staged_test_files() -> List[Path]:
    """Retrieves staged test Python files from git."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        files = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("tests/") and line.endswith(".py"):
                p = ROOT / line
                if p.is_file():
                    files.append(p)
        return files
    except Exception:
        return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Linter banning direct patch() on third-party paths in new tests."
    )
    parser.add_argument("--staged", action="store_true", help="Audit only staged test files.")
    parser.add_argument("--check", action="store_true", help="Audit all tests in tests/ directory.")
    parser.add_argument("files", nargs="*", help="Explicit test files to audit.")
    args = parser.parse_args(argv)

    allowlist = load_allowlist()

    if args.files:
        targets = [Path(f).resolve() for f in args.files if Path(f).suffix == ".py"]
    elif args.staged:
        targets = get_staged_test_files()
    else:
        # Default scan all tests
        targets = sorted(list((ROOT / "tests").rglob("*.py")))

    violations: List[str] = []
    scanned_count = 0
    for target in targets:
        if target.is_file():
            scanned_count += 1
            v = audit_test_file(target, allowlist)
            violations.extend(v)

    if scanned_count == 0 and not args.staged:
        print("❌ Third-Party Patch Linter FAILED: Unexpected empty scope (0 test files scanned).", file=sys.stderr)
        return 1

    if violations:
        print(f"❌ Third-Party Patch Linter FAILED ({len(violations)} violations in {scanned_count} files):")
        for v in violations:
            print(f"   {v}")
        return 1

    print(f"✅ Third-Party Patch Linter PASS: {scanned_count} test file(s) compliant (allowlist: {len(allowlist)} legacy files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
