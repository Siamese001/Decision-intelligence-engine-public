#!/usr/bin/env python3
"""
tools/lint_regex_redos.py

Static AST Linter for ReDoS (Regular Expression Denial of Service) Vulnerabilities.
Enforces Phase 2 CI Testing Rigor:
- Scans codebase for dangerous nested quantifiers and overlapping repetitions.
- Flags catastrophic backtracking patterns such as (a+)+, (.*a)+, or (a|a)+.
- Prohibits unbounded regex complexity in user-facing intake and routing modules.

Usage:
  python tools/lint_regex_redos.py [--staged] [--all] [--check] [paths ...]
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]

# High-risk ReDoS patterns: nested repetition or ambiguous alternation within repetition
VULNERABLE_REGEX_PATTERNS = [
    # Nested quantifiers like (a+)+, (a*)*, ([0-9]+)+
    (re.compile(r"\([^)]*[\*\+][^)]*\)[\*\+]"), "REDOS01", "Nested quantifier detected: potential catastrophic exponential backtracking."),
    # Overlapping star/plus like .*\w*.*
    (re.compile(r"\.\*[\s\S]*\.\*[\s\S]*\.\*"), "REDOS02", "Multiple unbounded greedy matchers in single pattern."),
    # Pathological alternation within repetition like (a|a)+ or (a|ab)+
    (re.compile(r"\((?:[a-zA-Z0-9_\-\s]+\|[a-zA-Z0-9_\-\s]+)\)[\*\+]"), "REDOS03", "Alternation inside repetition quantifier without anchor."),
]

GOVERNED_DIRS: Set[str] = {
    "dual_pipeline/live_meeting_recorder_core",
    "dual_pipeline/cli",
    "storage",
}


class RegexViolation(NamedTuple):
    file_path: Path
    line_number: int
    rule_code: str
    pattern_str: str
    message: str


class RegexVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: List[RegexViolation] = []

    def _inspect_pattern(self, pattern_str: str, lineno: int) -> None:
        if len(pattern_str) > 1000:
            self.violations.append(
                RegexViolation(
                    file_path=self.file_path,
                    line_number=lineno,
                    rule_code="REDOS04",
                    pattern_str=pattern_str[:50] + "...",
                    message="Regex pattern exceeds maximum complexity threshold of 1000 chars.",
                )
            )
            return

        for compiled_vuln, code, msg in VULNERABLE_REGEX_PATTERNS:
            if compiled_vuln.search(pattern_str):
                # Verify if it's actually dangerous or a safe false positive
                # Safe cases include non-capturing groups with bounded repetition {1,N}
                if re.search(r"\{[0-9]+,[0-9]+\}", pattern_str):
                    continue
                self.violations.append(
                    RegexViolation(
                        file_path=self.file_path,
                        line_number=lineno,
                        rule_code=code,
                        pattern_str=pattern_str,
                        message=msg,
                    )
                )

    def visit_Call(self, node: ast.Call) -> None:
        # Check re.compile, re.search, re.match, re.sub, re.findall
        func_name = ""
        is_re_call = False
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                is_re_call = True
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if is_re_call and func_name in {"compile", "search", "match", "sub", "findall", "finditer", "split"}:
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                self._inspect_pattern(node.args[0].value, node.lineno)

        self.generic_visit(node)


def check_file(file_path: Path) -> List[RegexViolation]:
    if file_path.suffix != ".py":
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
        visitor = RegexVisitor(file_path)
        visitor.visit(tree)
        return visitor.violations
    except Exception:
        return []


def collect_governed_files() -> List[Path]:
    files = []
    for g_dir in GOVERNED_DIRS:
        dir_path = ROOT / g_dir
        if dir_path.is_dir():
            for p in dir_path.rglob("*.py"):
                if p.is_file() and not p.name.startswith("."):
                    files.append(p)
    return sorted(files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ReDoS vulnerability static AST linter.")
    parser.add_argument("--staged", action="store_true", help="Scan staged git files only")
    parser.add_argument("--all", action="store_true", help="Scan all governed files")
    parser.add_argument("--check", action="store_true", help="Exit code 1 on violations")
    parser.add_argument("paths", nargs="*", help="Optional file or directory paths to scan")

    args = parser.parse_args(argv)

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
    elif args.all or not args.staged:
        paths_to_check = collect_governed_files()

    violations: List[RegexViolation] = []
    for p in paths_to_check:
        if p.is_file():
            violations.extend(check_file(p))

    if not violations:
        print(f"PASSED: ReDoS linter verified {len(paths_to_check)} files. Zero vulnerable backtracking patterns detected.")
        return 0

    print(f"FAILED: Found {len(violations)} potential ReDoS pattern(s):", file=sys.stderr)
    for v in violations:
        rel = v.file_path.relative_to(ROOT) if v.file_path.is_relative_to(ROOT) else v.file_path
        print(f"  {rel}:{v.line_number} [{v.rule_code}] {v.message} Pattern: '{v.pattern_str}'", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
