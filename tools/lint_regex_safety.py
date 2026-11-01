#!/usr/bin/env python3
"""Static Regex Safety and ReDoS Prevention Linter.

Analyzes regular expressions across Python source files to detect catastrophic
backtracking (ReDoS) vulnerabilities, including nested quantifiers and ambiguous
iteration boundary overlaps.
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path
from re import _constants, _parser
from typing import Any, List, Optional, Sequence, Set, Tuple

PROBE_CHARS: List[int] = list(range(128))

KNOWN_REDOS_FIXTURES: List[Tuple[str, str]] = [
    ("(a+)+", "direct nested quantifier"),
    (r"(\w+\s*)+$", "boundary overlap nested repeat"),
    ("(a*)*", "direct nested quantifier"),
    (r"(\s*\[[\w\.\-]+\]\s*)+", "boundary overlap citation repeat"),
    ("(a|b+)+", "nested branch repeat"),
    ("((a+))+", "wrapped nested repeat"),
    (r"^(\s*\[[\w\.\-]+\]\s*)+$", "historical _CITATION_ONLY_RE fixture"),
]


def matches_char(op: Any, arg: Any, c: int) -> bool:
    """Check if an AST character opcode matches character code c."""
    if op == _constants.LITERAL:
        return c == arg
    if op == _constants.NOT_LITERAL:
        return c != arg
    if op == _constants.ANY:
        return c != 10
    if op == _constants.CATEGORY:
        return _matches_category(arg, c)
    if op == _constants.IN:
        return _matches_in(arg, c)
    return False


def _matches_category(cat: Any, c: int) -> bool:
    if cat == _constants.CATEGORY_DIGIT:
        return 48 <= c <= 57
    if cat == _constants.CATEGORY_NOT_DIGIT:
        return not (48 <= c <= 57)
    if cat == _constants.CATEGORY_SPACE:
        return c in (9, 10, 11, 12, 13, 32)
    if cat == _constants.CATEGORY_NOT_SPACE:
        return c not in (9, 10, 11, 12, 13, 32)
    if cat == _constants.CATEGORY_WORD:
        return (48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122) or (c == 95)
    if cat == _constants.CATEGORY_NOT_WORD:
        return not ((48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122) or (c == 95))
    return False


def _matches_in(items: Any, c: int) -> bool:
    negate = False
    matched = False
    for item_op, item_arg in items:
        if item_op == _constants.NEGATE:
            negate = True
        elif (
            (item_op == _constants.LITERAL and c == item_arg)
            or (item_op == _constants.RANGE and item_arg[0] <= c <= item_arg[1])
            or (item_op == _constants.CATEGORY and _matches_category(item_arg, c))
        ):
            matched = True
    return not matched if negate else matched


def get_char_set(op: Any, arg: Any) -> Set[int]:
    """Return matching ASCII characters for a token."""
    return {c for c in PROBE_CHARS if matches_char(op, arg, c)}


def is_unbounded_repeat(op: Any, arg: Any) -> bool:
    """Return True if the repeat has an unbounded upper limit."""
    return op in (_constants.MAX_REPEAT, _constants.MIN_REPEAT) and (
        arg[1] == _constants.MAXREPEAT or arg[1] >= 100
    )


def can_match_empty(op: Any, arg: Any) -> bool:
    """Determine if a token or sequence can match an empty string."""
    if op in (_constants.MAX_REPEAT, _constants.MIN_REPEAT):
        return arg[0] == 0 or all(can_match_empty(o, a) for o, a in arg[2])
    if op == _constants.SUBPATTERN:
        return all(can_match_empty(o, a) for o, a in arg[3])
    if op == _constants.BRANCH:
        return any(all(can_match_empty(o, a) for o, a in br) for br in arg[1])
    return op in (_constants.AT, _constants.ASSERT, _constants.ASSERT_NOT)


def _extract_repeat_token_sets(subpattern: Sequence[Any]) -> List[Set[int]]:
    char_sets: List[Set[int]] = []
    char_ops = (
        _constants.LITERAL,
        _constants.NOT_LITERAL,
        _constants.ANY,
        _constants.CATEGORY,
        _constants.IN,
    )
    for s_op, s_arg in subpattern:
        if s_op in char_ops:
            char_sets.append(get_char_set(s_op, s_arg))
    return char_sets


def _handle_token_boundary(op: Any, arg: Any, reverse: bool) -> Tuple[List[Set[int]], bool]:
    if is_unbounded_repeat(op, arg):
        return _extract_repeat_token_sets(arg[2]), arg[0] > 0
    if op == _constants.SUBPATTERN:
        sets = _get_boundary_char_sets(arg[3], reverse=reverse)
        return sets, not can_match_empty(op, arg)
    if op == _constants.BRANCH:
        sets = []
        for br in arg[1]:
            sets.extend(_get_boundary_char_sets(br, reverse=reverse))
        stopped = not any(all(can_match_empty(o, a) for o, a in br) for br in arg[1])
        return sets, stopped
    return [], not can_match_empty(op, arg)


def _get_boundary_char_sets(tokens: Sequence[Any], reverse: bool = False) -> List[Set[int]]:
    sets: List[Set[int]] = []
    seq = reversed(tokens) if reverse else tokens
    for op, arg in seq:
        token_sets, stop = _handle_token_boundary(op, arg, reverse)
        sets.extend(token_sets)
        if stop:
            break
    return sets


def is_direct_nested_repeat(sub: Sequence[Any]) -> bool:
    """Detect if subpattern is directly wrapped around another unbounded repeat."""
    non_empty = [t for t in sub if not can_match_empty(t[0], t[1])]
    if len(non_empty) != 1:
        return False
    op, arg = non_empty[0]
    if is_unbounded_repeat(op, arg):
        return True
    if op == _constants.SUBPATTERN:
        return is_direct_nested_repeat(arg[3])
    if op == _constants.BRANCH:
        return any(is_direct_nested_repeat(br) for br in arg[1])
    return False


def _has_overlapping_sets(starts: Sequence[Set[int]], ends: Sequence[Set[int]]) -> bool:
    for s in starts:
        for e in ends:
            if s & e:
                return True
    return False


def _check_repeat_redos(arg: Any) -> Optional[Tuple[str, str]]:
    sub = arg[2]
    if is_direct_nested_repeat(sub):
        return ("direct_nested_repeat", str(sub)[:60])
    starts = _get_boundary_char_sets(sub, reverse=False)
    ends = _get_boundary_char_sets(sub, reverse=True)
    if _has_overlapping_sets(starts, ends):
        return ("boundary_overlap_nested_repeat", str(sub)[:60])
    return None


def check_pattern_redos(tokens: Sequence[Any]) -> List[Tuple[str, str]]:
    """Scan parsed regex AST tokens for ReDoS vulnerabilities."""
    violations: List[Tuple[str, str]] = []
    for op, arg in tokens:
        if is_unbounded_repeat(op, arg):
            v = _check_repeat_redos(arg)
            if v:
                violations.append(v)
            violations.extend(check_pattern_redos(arg[2]))
        elif op == _constants.SUBPATTERN:
            violations.extend(check_pattern_redos(arg[3]))
        elif op == _constants.BRANCH:
            for br in arg[1]:
                violations.extend(check_pattern_redos(br))
    return violations


def _extract_call_regex(node: ast.Call) -> Optional[str]:
    func = node.func
    is_re = (
        (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "re")
        or (isinstance(func, ast.Name) and func.id in ("compile", "search", "match", "findall", "sub", "split"))
    )
    if is_re and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def _extract_assign_regex(node: ast.Assign) -> Optional[str]:
    valid_suffixes = ("_RE", "_PATTERN", "_REGEX")
    for target in node.targets:
        if isinstance(target, ast.Name) and any(target.id.endswith(s) for s in valid_suffixes):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def extract_regexes_from_file(file_path: Path) -> List[Tuple[int, str]]:
    """Extract string literals used as regex patterns in a Python file."""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
        lines = content.splitlines()
    except Exception:
        return []

    regexes: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        pat: Optional[str] = None
        if isinstance(node, ast.Call):
            pat = _extract_call_regex(node)
        elif isinstance(node, ast.Assign):
            pat = _extract_assign_regex(node)
        if pat is not None:
            lineno = getattr(node, "lineno", 0)
            if 1 <= lineno <= len(lines):
                line_text = lines[lineno - 1]
                if "# noqa: redos" in line_text or "# noqa" in line_text:
                    continue
            regexes.append((lineno, pat))
    return regexes


def audit_python_file(file_path: Path) -> List[dict[str, Any]]:
    """Audit all regexes in a single Python file."""
    findings = []
    for lineno, pat in extract_regexes_from_file(file_path):
        try:
            parsed = _parser.parse(pat)
            issues = check_pattern_redos(parsed.data)
            if issues:
                findings.append({
                    "file": str(file_path),
                    "lineno": lineno,
                    "pattern": pat,
                    "issues": issues,
                })
        except Exception:
            continue
    return findings


def get_staged_python_files(repo_root: Path, include_tests: bool = False) -> List[Path]:
    """Retrieve staged Python files via git diff, excluding tests by default."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root, capture_output=True, text=True, check=True
        )
        files: List[Path] = []
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if not line_str.endswith(".py"):
                continue
            if not include_tests and (line_str.startswith("tests/") or "/tests/" in line_str):
                continue
            p = repo_root / line_str
            if p.is_file():
                files.append(p)
        return files
    except Exception:
        return []


def verify_historical_fixtures() -> bool:
    """Verify that known ReDoS fixtures (including _CITATION_ONLY_RE) are positively detected."""
    detected = 0
    for pat, _desc in KNOWN_REDOS_FIXTURES:
        parsed = _parser.parse(pat)
        issues = check_pattern_redos(parsed.data)
        if issues:
            detected += 1
    return detected == len(KNOWN_REDOS_FIXTURES)


def run_regex_audit(files: Sequence[Path], verbose: bool = False) -> Tuple[int, int, List[dict[str, Any]]]:
    """Audit a sequence of files, returning (files_scanned, patterns_scanned, violations)."""
    total_patterns = 0
    all_violations: List[dict[str, Any]] = []
    for f in files:
        extracted = extract_regexes_from_file(f)
        total_patterns += len(extracted)
        file_violations = audit_python_file(f)
        all_violations.extend(file_violations)
        if verbose and file_violations:
            for v in file_violations:
                print(f"  ❌ {f.name}:{v['lineno']}: {v['pattern'][:40]} -> {v['issues']}")
    return len(files), total_patterns, all_violations


def _resolve_target_files(args: argparse.Namespace, repo_root: Path) -> List[Path]:
    if args.files:
        return [f.resolve() for f in args.files if f.is_file() and f.suffix == ".py"]
    if args.staged:
        return get_staged_python_files(repo_root)
    if args.all:
        targets: List[Path] = []
        for d in ("dual_pipeline", "storage", "tools"):
            targets.extend(sorted((repo_root / d).glob("**/*.py")))
        return targets
    return get_staged_python_files(repo_root)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Static Regex Safety and ReDoS Prevention Linter")
    parser.add_argument("files", nargs="*", type=Path, help="Specific Python files to scan")
    parser.add_argument("--all", action="store_true", help="Scan repository modules (dual_pipeline, storage, tools)")
    parser.add_argument("--staged", action="store_true", help="Scan git staged Python files")
    parser.add_argument("--self-test", action="store_true", help="Run self-test on known ReDoS fixtures")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]

    if (args.self_test or args.all) and not verify_historical_fixtures():
        print("❌ SELF-TEST FAILED: Known ReDoS fixtures escaped detection.", file=sys.stderr)
        return 1
    if args.self_test and not args.all and not args.files:
        print("✅ SELF-TEST PASSED: All historical ReDoS fixtures correctly detected.")
        return 0

    target_files = _resolve_target_files(args, repo_root)

    print("=" * 80)
    print("  STATIC REGEX SAFETY & REDOS LINTER")
    print(f"  Target Scope: {len(target_files)} Python file(s)")
    print("=" * 80)

    scanned_files, scanned_patterns, violations = run_regex_audit(target_files, verbose=args.verbose)
    print(f"Scanned {scanned_files} file(s) and {scanned_patterns} regex pattern(s).")

    if violations:
        print(f"\n❌ FOUND {len(violations)} REDOS VULNERABILITY VIOLATION(S):")
        for v in violations:
            print(f"  - {v['file']}:{v['lineno']}: {v['pattern']} -> {v['issues']}")
        return 1

    print("✅ PASSED: Zero ReDoS pattern violations detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
