#!/usr/bin/env python3
"""
tools/lint_mock_usage.py

High-speed AST linter enforcing strict mock bans in governed test suites:
Prohibits `unittest.mock` (Mock, MagicMock, patch) and `types.SimpleNamespace`
synthetic stubs in integration, end-to-end, and stress test suites.

Enforces Phase 2 CI Testing Rigor & High-Fidelity Validation standards:
- Zero-tolerance mock ban in `tests/integration/` and `tests/stress/`.
- Ratchet burndown baseline for legacy `tests/end_to_end/` files.
- Fails CI if synthetic mocks are detected in new or un-baselined files.

Usage:
  python tools/lint_mock_usage.py [--staged] [--all] [--check] [paths ...]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]

# Directories and patterns where synthetic mocks are strictly banned
GOVERNED_MOCK_BAN_DIRS: Set[str] = {
    "tests/integration",
    "tests/e2e",
    "tests/end_to_end",
    "tests/stress",
}

# Directories with ZERO tolerance (no baseline exemptions permitted ever)
ZERO_TOLERANCE_DIRS: Set[str] = {
    "tests/integration",
    "tests/stress",
    "tests/architecture",
}

BASELINE_PATH = ROOT / "config" / "governance" / "mock_usage_baseline.json"


class MockViolation(NamedTuple):
    file_path: Path
    line_number: int
    col_offset: int
    rule_code: str
    message: str


class MockUsageVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: List[MockViolation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "unittest.mock" or alias.name.startswith("unittest.mock."):
                self.violations.append(
                    MockViolation(
                        file_path=self.file_path,
                        line_number=node.lineno,
                        col_offset=node.col_offset,
                        rule_code="MOCK01",
                        message=f"Banned import of '{alias.name}'. Integration/E2E tests must use real SQLite engines.",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            if node.module == "unittest.mock" or node.module.startswith("unittest.mock."):
                names = ", ".join(a.name for a in node.names)
                self.violations.append(
                    MockViolation(
                        file_path=self.file_path,
                        line_number=node.lineno,
                        col_offset=node.col_offset,
                        rule_code="MOCK01",
                        message=f"Banned import from '{node.module}' ({names}). Integration/E2E tests must use real SQLite engines.",
                    )
                )
            elif node.module == "types":
                for alias in node.names:
                    if alias.name == "SimpleNamespace":
                        self.violations.append(
                            MockViolation(
                                file_path=self.file_path,
                                line_number=node.lineno,
                                col_offset=node.col_offset,
                                rule_code="MOCK02",
                                message="Banned import of 'SimpleNamespace'. High-fidelity tests must use real graph/storage fixtures.",
                            )
                        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = ""
        if isinstance(node.func, ast.Name):
            call_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            call_name = node.func.attr

        if call_name in {"MagicMock", "Mock", "AsyncMock"}:
            self.violations.append(
                MockViolation(
                    file_path=self.file_path,
                    line_number=node.lineno,
                    col_offset=node.col_offset,
                    rule_code="MOCK01",
                    message=f"Direct instantiation of '{call_name}' is banned in governed test suites.",
                )
            )
        elif call_name == "SimpleNamespace":
            self.violations.append(
                MockViolation(
                    file_path=self.file_path,
                    line_number=node.lineno,
                    col_offset=node.col_offset,
                    rule_code="MOCK02",
                    message="Direct instantiation of 'SimpleNamespace' stub is banned in governed test suites.",
                )
            )

        self.generic_visit(node)


def is_governed_path(file_path: Path) -> bool:
    rel = str(file_path.relative_to(ROOT)) if file_path.is_absolute() else str(file_path)
    rel_normalized = rel.replace("\\", "/")
    return any(rel_normalized.startswith(g_dir) for g_dir in GOVERNED_MOCK_BAN_DIRS)


def check_file(file_path: Path) -> List[MockViolation]:
    if file_path.suffix != ".py":
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
        visitor = MockUsageVisitor(file_path)
        visitor.visit(tree)
        return visitor.violations
    except Exception as exc:
        return [
            MockViolation(
                file_path=file_path,
                line_number=1,
                col_offset=0,
                rule_code="PARSE_ERR",
                message=f"Failed to parse AST: {exc}",
            )
        ]


def load_baseline() -> Dict[str, List[Dict[str, Any]]]:
    if BASELINE_PATH.is_file():
        try:
            return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_staged_files() -> List[Path]:
    try:
        res = subprocess.run(timeout=30,
            args=["git", "diff", "--name-only", "--cached", "--diff-filter=d"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.endswith(".py"):
                p = (ROOT / line).resolve()
                if is_governed_path(p):
                    files.append(p)
        return files
    except Exception:
        return []


def collect_all_governed_files() -> List[Path]:
    files = []
    for g_dir in GOVERNED_MOCK_BAN_DIRS:
        dir_path = ROOT / g_dir
        if dir_path.is_dir():
            for p in dir_path.rglob("*.py"):
                if p.is_file() and not p.name.startswith("."):
                    files.append(p)
    return sorted(files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strict mock usage AST linter for high-fidelity test suites.")
    parser.add_argument("--staged", action="store_true", help="Scan staged git files only")
    parser.add_argument("--all", action="store_true", help="Scan all governed integration/E2E test files")
    parser.add_argument("--check", action="store_true", help="Exit code 1 on un-baselined violations")
    parser.add_argument("--ignore-baseline", action="store_true", help="Disregard baseline and report all violations")
    parser.add_argument("--update-baseline", action="store_true", help="Regenerate baseline from current grandfathered violations")
    parser.add_argument("paths", nargs="*", help="Optional file or directory paths to scan")

    args = parser.parse_args(argv)

    if args.update_baseline:
        all_files = collect_all_governed_files()
        new_baseline: Dict[str, List[Dict[str, Any]]] = {}
        for p in all_files:
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            if any(rel.startswith(zt) for zt in ZERO_TOLERANCE_DIRS):
                continue
            f_viols = check_file(p)
            if f_viols:
                new_baseline[rel] = [
                    {"line": v.line_number, "col": v.col_offset, "rule": v.rule_code, "msg": v.message}
                    for v in f_viols
                ]
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(new_baseline, indent=2) + "\n", encoding="utf-8")
        print(f"Updated baseline at {BASELINE_PATH} with {len(new_baseline)} legacy files.")
        return 0

    paths_to_check: List[Path] = []
    if args.paths:
        for raw in args.paths:
            p = Path(raw)
            if not p.is_absolute():
                p = (ROOT / p).resolve()
            if p.is_file():
                paths_to_check.append(p)
            elif p.is_dir():
                for sub in p.rglob("*.py"):
                    paths_to_check.append(sub)
    elif args.staged:
        paths_to_check = get_staged_files()
    elif args.all:
        paths_to_check = collect_all_governed_files()
    else:
        paths_to_check = collect_all_governed_files()

    baseline = load_baseline() if not args.ignore_baseline else {}

    violations: List[MockViolation] = []
    baselined_count = 0

    for p in paths_to_check:
        if not (p.exists() and p.is_file()):
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        is_zero_tolerance = any(rel.startswith(zt) for zt in ZERO_TOLERANCE_DIRS)

        file_violations = check_file(p)
        if not file_violations:
            continue

        if not is_zero_tolerance and rel in baseline:
            allowed_count = len(baseline[rel])
            if len(file_violations) <= allowed_count:
                baselined_count += len(file_violations)
                continue
            # Some violations exceeded baseline
            new_violations = file_violations[allowed_count:]
            violations.extend(new_violations)
            baselined_count += allowed_count
        else:
            violations.extend(file_violations)

    if not violations:
        print(f"PASSED: Mock usage AST linter verified {len(paths_to_check)} files.")
        if baselined_count > 0:
            print(f"  (Noted {baselined_count} grandfathered legacy mock usages in ratchet baseline).")
        return 0

    print(f"FAILED: Found {len(violations)} mock ban violation(s):", file=sys.stderr)
    for v in violations:
        rel = v.file_path.relative_to(ROOT) if v.file_path.is_relative_to(ROOT) else v.file_path
        print(f"  {rel}:{v.line_number}:{v.col_offset} [{v.rule_code}] {v.message}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
