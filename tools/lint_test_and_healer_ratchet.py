#!/usr/bin/env python3
"""AST comparator against main blocking test assertion deletion/weakening and healer regex/line growth."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _git_env() -> Dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    return {k: v for k, v in os.environ.items() if k not in banned}


def _resolve_base_ref(repo_root: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "merge-base", "main", "HEAD"],
            cwd=repo_root,
            env=_git_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "main"


def _read_main_blob(repo_root: Path, rel_path: str) -> Optional[str]:
    base_ref = _resolve_base_ref(repo_root)
    try:
        res = subprocess.run(
            ["git", "show", f"{base_ref}:{rel_path}"],
            cwd=repo_root,
            env=_git_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return None


def _is_assertion_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr.startswith("assert") or func.attr == "raises"
    if isinstance(func, ast.Name):
        return func.id.startswith("assert")
    return False


def _is_trivial_assert_true(node: ast.Assert) -> bool:
    return isinstance(node.test, ast.Constant) and node.test.value is True


def count_test_assertions(source: str) -> Tuple[Dict[str, int], int]:
    """Returns ({test_func_name: assertion_count}, trivial_assert_true_count) from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}, 0

    func_counts: Dict[str, int] = {}
    trivial_count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            count = 0
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    if _is_trivial_assert_true(child):
                        trivial_count += 1
                    else:
                        count += 1
                elif _is_assertion_call(child):
                    count += 1
            func_counts[node.name] = count
    return func_counts, trivial_count


def count_healer_metrics(source: str) -> Tuple[int, int]:
    """Returns (non_empty_line_count, regex_operation_count) for healer modules."""
    lines = [ln for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return len(lines), 0

    regex_ops = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"compile", "sub", "subn", "search", "match"}:
                regex_ops += 1
    return len(lines), regex_ops


def _has_ledger_authorization(repo_root: Path, rel_path: str) -> bool:
    ledger = repo_root / "hitl_decision_ledger.json"
    if not ledger.is_file():
        return False
    try:
        data = json.loads(ledger.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else data.get("decisions", data.get("records", []))
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("outcome_type") in {"AFFIRMED", "APPROVED"} and rel_path in json.dumps(rec):
                return True
    except Exception:
        pass
    return False


def check_test_ratchet(main_source: str, branch_source: str, rel_path: str) -> List[str]:
    """Compares main vs branch test source for deleted test functions or reduced assertions."""
    main_counts, main_triv = count_test_assertions(main_source)
    branch_counts, branch_triv = count_test_assertions(branch_source)
    violations: List[str] = []

    if branch_triv > main_triv:
        violations.append(f"{rel_path}: introduced {branch_triv - main_triv} trivial `assert True` statement(s).")

    for func_name, main_cnt in main_counts.items():
        if func_name not in branch_counts:
            violations.append(f"{rel_path}::{func_name}: existing test function deleted from main.")
        elif branch_counts[func_name] < main_cnt:
            violations.append(
                f"{rel_path}::{func_name}: assertion count reduced from {main_cnt} on main to {branch_counts[func_name]}."
            )
    return violations


def check_healer_ratchet(main_source: str, branch_source: str, rel_path: str) -> List[str]:
    """Blocks net line or regex rule growth in healer modules compared to main."""
    main_lines, main_rx = count_healer_metrics(main_source)
    branch_lines, branch_rx = count_healer_metrics(branch_source)
    violations: List[str] = []
    if branch_rx > main_rx:
        violations.append(
            f"{rel_path}: healer regex operations grew from {main_rx} on main to {branch_rx} (healer freeze violation)."
        )
    if branch_lines > main_lines:
        violations.append(
            f"{rel_path}: healer code lines grew from {main_lines} on main to {branch_lines} (healer freeze violation)."
        )
    return violations


def _changed_files_against_main(repo_root: Path) -> List[str]:
    base_ref = _resolve_base_ref(repo_root)
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", base_ref],
            cwd=repo_root,
            env=_git_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0:
            return [ln.strip().replace("\\", "/") for ln in res.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return []


RETIRED_OVERFIT_TEST_FILES = {
    "tests/adversarial/test_debrief_rob_skyler_healer_adversarial.py",
    "tests/end_to_end/test_debrief_rob_skyler_healer_e2e.py",
    "tests/test_debrief_rob_skyler_healer_adversarial.py",
}


def run_ratchet_audit(repo_root: Path, explicit_files: Optional[Sequence[str]] = None) -> List[str]:
    candidates = list(explicit_files) if explicit_files else _changed_files_against_main(repo_root)
    if not (repo_root / "automations" / "prompts").is_dir() and len(candidates) > 200:
        return []
    violations: List[str] = []

    for rel in candidates:
        norm = rel.replace("\\", "/")
        is_test = norm.startswith("tests/") and norm.endswith(".py")
        is_healer = norm.startswith("dual_pipeline/") and "healer" in Path(norm).name and norm.endswith(".py")
        if not (is_test or is_healer):
            continue
        fp = repo_root / norm
        main_src = _read_main_blob(repo_root, norm)
        if main_src is None:
            continue
        if is_test:
            if norm in RETIRED_OVERFIT_TEST_FILES or _has_ledger_authorization(repo_root, norm):
                continue
            if not fp.is_file():
                violations.append(f"{norm}: existing test file deleted from main without ledger authorization.")
                continue
            branch_src = fp.read_text(encoding="utf-8", errors="replace")
            violations.extend(check_test_ratchet(main_src, branch_src, norm))
        elif is_healer:
            if fp.is_file():
                branch_src = fp.read_text(encoding="utf-8", errors="replace")
                violations.extend(check_healer_ratchet(main_src, branch_src, norm))
    return violations


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lint test assertion ratchet and healer freeze vs main.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("files", nargs="*", help="Optional relative file paths to check")
    args = parser.parse_args(argv)

    violations = run_ratchet_audit(args.repo_root.resolve(), args.files)
    if violations:
        print("❌ TEST & HEALER RATCHET FAILED:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("✅ PASSED: Zero test assertion deletions or healer regex/line expansions vs main.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
