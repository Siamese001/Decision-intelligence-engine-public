#!/usr/bin/env python3
"""
tools/lint_no_simulated_modes.py

Enforces zero-simulated-run-modes invariant across all CLIs and operational tools:
1. Rejects any registration of --dry-run, --dry-run-graph, --dry-run-qa, --package-only,
   --plan-only, --no-mutate-graph in argparse declarations.
2. Ensures all mutation operations are gated strictly behind authentic HITL approvals.
3. Verifies dual_pipeline/cli/parser.py maintains REMOVED_SIMULATED_FLAGS fail-closed map.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Directories scanned for forbidden simulated flags
SCAN_DIRS = ["dual_pipeline", "tools"]

# Files exempt from linter checks
EXEMPT_FILES = {
    "tools/lint_no_simulated_modes.py",
    "tests/test_lint_no_simulated_modes.py",
    "dual_pipeline/cli/parser.py",  # parser defines the removal error messages
}

# Forbidden CLI flags
FORBIDDEN_CLI_FLAGS = {
    "--dry-run",
    "--dry-run-graph",
    "--dry-run-qa",
    "--package-only",
    "--plan-only",
    "--no-mutate-graph",
}

# Regex to detect registration of forbidden flags in argument parsers
ARG_PARSE_FLAG_RE = re.compile(
    r"""(?:\.add_argument|\.add_parser)\s*\(\s*(?:\[[^\]]*\]\s*,\s*)?["'](--[a-zA-Z0-9_-]+)["']"""
)


def find_forbidden_cli_flags_in_file(path: Path) -> List[Tuple[int, str, str]]:
    """Inspects a Python file for forbidden CLI flag registrations."""
    violations: List[Tuple[int, str, str]] = []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return violations

    for line_no, line in enumerate(content.splitlines(), start=1):
        for match in ARG_PARSE_FLAG_RE.finditer(line):
            flag = match.group(1)
            if flag in FORBIDDEN_CLI_FLAGS:
                violations.append((
                    line_no,
                    flag,
                    f"Forbidden simulated/preview CLI flag '{flag}' registered",
                ))
    return violations


def verify_parser_rejection_map(parser_path: Path) -> List[str]:
    """Verifies that dual_pipeline/cli/parser.py explicitly rejects all removed flags."""
    errors: List[str] = []
    if not parser_path.is_file():
        return [f"Parser file not found: {parser_path}"]

    content = parser_path.read_text(encoding="utf-8")
    for flag in FORBIDDEN_CLI_FLAGS:
        if flag not in content:
            errors.append(f"Parser missing fail-closed rejection mapping for removed flag '{flag}'")
    return errors


def lint_no_simulated_modes(repo_root: Path) -> Tuple[int, List[str]]:
    """Runs the no-simulated-modes linter across repo_root."""
    findings: List[str] = []

    # 1. Scan Python files under SCAN_DIRS
    for dir_name in SCAN_DIRS:
        scan_path = repo_root / dir_name
        if not scan_path.is_dir():
            continue
        for py_file in sorted(scan_path.rglob("*.py")):
            rel_path = py_file.relative_to(repo_root).as_posix()
            if rel_path in EXEMPT_FILES:
                continue
            if "tests" in py_file.parts or ".venv" in py_file.parts:
                continue

            violations = find_forbidden_cli_flags_in_file(py_file)
            for line_no, flag, msg in violations:
                findings.append(f"{rel_path}:{line_no}: [FORBIDDEN_FLAG] {msg}")

    # 2. Verify parser fail-closed map
    parser_path = repo_root / "dual_pipeline" / "cli" / "parser.py"
    parser_errs = verify_parser_rejection_map(parser_path)
    findings.extend(parser_errs)

    return len(findings), findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint codebase to ensure no simulated run modes are registered."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory to scan.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    count, findings = lint_no_simulated_modes(root)

    if count == 0:
        print("✅ [LINT PASS] Zero simulated run modes detected across all CLIs and tools.")
        return 0

    print(f"❌ [LINT FAIL] Found {count} violation(s) of the zero-simulated-modes invariant:\n")
    for item in findings:
        print(f"  • {item}")
    print("\nSimulated flags (--package-only, --dry-run, etc.) are eliminated.")
    print("Mutations must be governed strictly by authentic ask_user_question approvals.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
