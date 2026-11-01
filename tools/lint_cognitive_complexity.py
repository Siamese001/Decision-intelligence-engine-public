#!/usr/bin/env python3
"""
tools/lint_cognitive_complexity.py

AST-based Cognitive Complexity Linter for Agentic Codebase Governance.
Enforces the 2026+ SonarQube / Google DeepMind Cognitive Complexity specification:
- Replaces McCabe Cyclomatic Complexity with human- and LLM-centric flow-break penalties.
- Penalizes nesting depth, breaks in linear flow, and compound boolean logic.
- Default ceiling: max 15 cognitive complexity per function/method, with monotonic ratchet support.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXCLUSIONS_PATH = ROOT / "config" / "governance" / "file_budget_exclusions.json"
from tools.shift_left_core.cognitive import (  # noqa: E402
    CognitiveComplexityVisitor,
    ComplexityContribution,
    calculate_function_complexity,
    collect_function_nodes,
    evaluate_function_complexity,
    format_top_contributions as _format_top_contributions,
    load_complexity_baseline as _core_load_complexity_baseline,
    lookup_baseline_limit as _lookup_baseline_limit,
)

DEFAULT_MAX_COMPLEXITY = 15
_REPORTS_CC_PATH = ROOT / "reports" / "cognitive_complexity_baseline.json"
_CONFIG_CC_PATH = ROOT / "config" / "governance" / "cognitive_complexity_baseline.json"
COMPLEXITY_BASELINE_PATH = _REPORTS_CC_PATH if _REPORTS_CC_PATH.is_file() else _CONFIG_CC_PATH

GOVERNED_DIRS = [
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "storage",
    "scripts",
]


def _load_governed_complexity_exclusions(exclusions_path: Path = EXCLUSIONS_PATH) -> List[str]:
    patterns = [
        "tests/",
        "contracts/",
        "evals/",
        "reports/",
        "tools/",
        "scripts/",
        "Figma/",
        "ui/",
        "archive/",
    ]
    if exclusions_path.is_file():
        try:
            data = json.loads(exclusions_path.read_text(encoding="utf-8"))
            patterns.extend(data.get("exempt_prefixes", []))
            patterns.extend(data.get("cognitive_complexity_exempt_prefixes", []))
            for item in data.get("exclusions", []):
                if isinstance(item, dict) and item.get("path"):
                    patterns.append(str(item["path"]))
        except Exception:
            pass
    return sorted(set(p.replace("\\", "/").strip() for p in patterns if p))


EXCLUDED_PATTERNS = _load_governed_complexity_exclusions()


def _matches_path_pattern(rel_path: str, pattern: str) -> bool:
    norm_rel = rel_path.replace("\\", "/").strip("/")
    norm_pat = pattern.replace("\\", "/").strip()
    if not norm_pat:
        return False
    if norm_pat.endswith("/"):
        prefix = norm_pat.rstrip("/")
        return norm_rel == prefix or norm_rel.startswith(prefix + "/")
    return norm_rel == norm_pat or norm_rel.startswith(norm_pat + "/")


def is_excluded(rel_path: str, patterns: Optional[Sequence[str]] = None) -> bool:
    norm_rel = rel_path.replace("\\", "/")
    fname = Path(norm_rel).name
    if fname.startswith("test_") or fname.endswith("_test.py") or fname == "__init__.py":
        return True
    active_patterns = patterns if patterns is not None else EXCLUDED_PATTERNS
    return any(_matches_path_pattern(norm_rel, p) for p in active_patterns)


class ComplexityViolation(NamedTuple):
    file_path: str
    function_name: str
    line_number: int
    score: int
    limit: int
    contributions: Tuple[ComplexityContribution, ...] = ()


def load_complexity_baseline(path: Path = COMPLEXITY_BASELINE_PATH) -> Dict[str, int]:
    return _core_load_complexity_baseline(path)


def _rel_path(file_path: Path, repo_root: Path) -> str:
    try:
        return str(file_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(file_path).replace("\\", "/")


def scan_file(
    file_path: Path,
    repo_root: Path,
    max_complexity: int = DEFAULT_MAX_COMPLEXITY,
    baseline_functions: Optional[Dict[str, int]] = None,
) -> List[ComplexityViolation]:
    """Scans a single Python file for functions exceeding max or ratcheted cognitive complexity."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return []

    rel = _rel_path(file_path, repo_root)
    violations: List[ComplexityViolation] = []

    for qual_name, node in collect_function_nodes(tree, skip_dunder=True):
        score, contribs = evaluate_function_complexity(node, qual_name)
        base_limit = _lookup_baseline_limit(rel, qual_name, baseline_functions)
        effective_limit = max(max_complexity, base_limit) if base_limit is not None else max_complexity
        if score > effective_limit:
            violations.append(
                ComplexityViolation(
                    file_path=rel,
                    function_name=qual_name,
                    line_number=node.lineno,
                    score=score,
                    limit=effective_limit,
                    contributions=tuple(contribs),
                )
            )

    return violations


def generate_complexity_baseline(
    repo_root: Path,
    baseline_path: Path = COMPLEXITY_BASELINE_PATH,
    max_complexity: int = DEFAULT_MAX_COMPLEXITY,
    allow_upward_override: bool = False,
) -> int:
    """Generates or updates the monotonic function-level cognitive complexity baseline."""
    existing = load_complexity_baseline(baseline_path)
    target_files = _resolve_target_files(repo_root, staged=False, explicit_files=[])
    current_over: Dict[str, int] = {}

    for fp in sorted(target_files):
        rel = _rel_path(fp, repo_root)
        if is_excluded(rel):
            continue
        for v in scan_file(fp, repo_root, max_complexity=max_complexity, baseline_functions=None):
            key = f"{v.file_path}::{v.function_name}"
            current_over[key] = max(current_over.get(key, 0), v.score)

    if existing and not allow_upward_override:
        regressions: List[str] = []
        for key, score in current_over.items():
            if key not in existing:
                regressions.append(
                    f"New unbaselined function '{key}' has score {score} > {max_complexity}."
                )
            elif score > existing[key]:
                regressions.append(
                    f"Baselined function '{key}' worsened from {existing[key]} to {score}."
                )
        if regressions:
            raise RuntimeError(
                "Monotonic Complexity Ratchet Violation:\n  - " + "\n  - ".join(regressions)
            )

    sorted_funcs = dict(sorted(current_over.items(), key=lambda x: (-x[1], x[0])))
    payload = {
        "schema_version": "1.0.0",
        "governance_invariant": f"Monotonic function-level cognitive complexity baseline (ceiling {max_complexity})",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "default_max_complexity": max_complexity,
        "total_baselined_functions": len(sorted_funcs),
        "functions": sorted_funcs,
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"✅ Generated monotonic cognitive complexity baseline: {baseline_path} ({len(sorted_funcs)} functions).")
    return 0


def _is_git_merging(repo_root: Path) -> bool:
    try:
        m_res = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return m_res.returncode == 0 and bool(m_res.stdout.strip())
    except Exception:
        return False


def get_staged_files(repo_root: Path) -> List[Path]:
    """Retrieves staged python files from git index."""
    try:
        cmd = (
            ["git", "diff", "--name-only", "--diff-filter=ACM", "HEAD"]
            if _is_git_merging(repo_root)
            else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
        )
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True)
        files = []
        for line in res.stdout.splitlines():
            p = (repo_root / line.strip()).resolve()
            if p.suffix == ".py" and p.exists():
                files.append(p)
        return files
    except Exception:
        return []


def _resolve_target_files(repo_root: Path, staged: bool, explicit_files: List[str]) -> List[Path]:
    if staged:
        return get_staged_files(repo_root)
    if explicit_files:
        return [Path(f).resolve() for f in explicit_files if Path(f).suffix == ".py"]
    targets: List[Path] = []
    for gdir in GOVERNED_DIRS:
        dp = repo_root / gdir
        if dp.exists():
            for p in dp.rglob("*.py"):
                targets.append(p.resolve())
    return targets


def _write_json_report(
    report_path: Path,
    max_complexity: int,
    scanned_count: int,
    violations: List[ComplexityViolation],
) -> None:
    report_data = {
        "max_complexity_threshold": max_complexity,
        "scanned_modules": scanned_count,
        "violation_count": len(violations),
        "violations": [
            {
                "file": v.file_path,
                "function": v.function_name,
                "line": v.line_number,
                "score": v.score,
                "limit": v.limit,
                "contributions": [
                    {"line": c.line_number, "increment": c.increment, "reason": c.reason}
                    for c in v.contributions
                ],
            }
            for v in violations
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"Report written to: {report_path}")


def _print_complexity_report(
    max_complexity: int,
    scanned_count: int,
    violations: List[ComplexityViolation],
    check: bool,
) -> int:
    print("=" * 80)
    print(f"  COGNITIVE COMPLEXITY LINTER (Threshold: {max_complexity})")
    print("=" * 80)
    print(f"Scanned {scanned_count} module(s).")

    if violations:
        print(f"\n❌ FAILED: Found {len(violations)} function(s) exceeding cognitive complexity limits:\n")
        for v in sorted(violations, key=lambda x: x.score, reverse=True)[:25]:
            print(f"  - {v.file_path}:{v.line_number} in `{v.function_name}()` -> score {v.score} (limit {v.limit})")
            breakdown = _format_top_contributions(v.contributions)
            if breakdown:
                print(f"      Breakdown: {breakdown}")
        if len(violations) > 25:
            print(f"  ... and {len(violations) - 25} more violation(s).")
        print("\nRemediation: Refactor nested logic into small, single-responsibility helper functions.")
        print("=" * 80)
        return 1 if check else 0

    print("✅ PASSED: All scanned functions satisfy cognitive complexity limits.")
    print("=" * 80)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="AST-based Cognitive Complexity Linter for 2026+ agentic codebase health."
    )
    parser.add_argument("--max-complexity", type=int, default=DEFAULT_MAX_COMPLEXITY)
    parser.add_argument("--staged", action="store_true", help="Only check staged git files")
    parser.add_argument("--ratchet", action="store_true", help="Enforce monotonic complexity ratchet")
    parser.add_argument("--generate-baseline", action="store_true", help="Generate or update complexity baseline")
    parser.add_argument("--baseline-path", type=Path, default=COMPLEXITY_BASELINE_PATH, help="Path to complexity baseline JSON")
    parser.add_argument("--json-report", type=Path, help="Write JSON summary report to path")
    parser.add_argument("--check", action="store_true", help="Fail closed with exit code 1 on violations")
    parser.add_argument("files", nargs="*", help="Specific files to check")

    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent

    if args.generate_baseline:
        return generate_complexity_baseline(
            repo_root=repo_root,
            baseline_path=args.baseline_path,
            max_complexity=args.max_complexity,
        )

    from tools.shift_left_core.cognitive import check_complexity_baseline_inflation

    inflation_errs = check_complexity_baseline_inflation(args.baseline_path)
    baseline_functions = load_complexity_baseline(args.baseline_path) if args.ratchet else None
    target_files = _resolve_target_files(repo_root, args.staged, args.files)
    all_violations: List[ComplexityViolation] = [
        ComplexityViolation(
            file_path="reports/cognitive_complexity_baseline.json",
            function_name="BASELINE_INFLATION",
            line_number=1,
            score=999,
            limit=args.max_complexity,
            contributions=(ComplexityContribution(1, 999, err),),
        )
        for err in inflation_errs
    ]
    scanned_file_count = 0

    for fp in target_files:
        rel = _rel_path(fp, repo_root)
        if is_excluded(rel):
            continue
        scanned_file_count += 1
        all_violations.extend(
            scan_file(fp, repo_root, max_complexity=args.max_complexity, baseline_functions=baseline_functions)
        )

    if args.json_report:
        _write_json_report(args.json_report, args.max_complexity, scanned_file_count, all_violations)

    return _print_complexity_report(
        args.max_complexity,
        scanned_file_count,
        all_violations,
        args.check or args.ratchet or bool(inflation_errs),
    )


if __name__ == "__main__":
    sys.exit(main())
