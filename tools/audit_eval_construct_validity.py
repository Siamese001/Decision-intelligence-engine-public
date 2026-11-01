#!/usr/bin/env python3
"""Mechanized AST & Runtime Construct-Validity Auditor for Evaluation Surfaces.

Enforces INV-EVALS-CONSTRUCT-VALIDITY-GATE and INV-EVALS-ZERO-TAUTOLOGY-GATE across:
- evals/**
- dual_pipeline/evals/**
- tools/*eval*.py, tools/audit_retrieval_gates.py, tools/calibrate_retrieval_thresholds.py

Detects:
1. Tautological assertions (`assert True`, `assert 1 == 1`) inside evaluator modules.
2. Regression-masking bootstrap clamps (`max(0.0, ... - ...)` or `max(0, ... - ...)`) on paired differences.
3. Vacuous evaluator return stubs (`return True` as the sole statement in an `evaluate_*` function).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ConstructValidityViolation:
    """Represents a single AST or runtime construct-validity violation."""

    file: str
    line: int
    rule_id: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_subtraction_expr(node: ast.AST) -> bool:
    """Return True if the AST node or any child expression performs subtraction (`a - b`)."""
    for child in ast.walk(node):
        if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Sub):
            return True
    return False


def scan_source_for_violations(source_code: str, filename: str = "<memory>") -> List[ConstructValidityViolation]:
    """Scan Python source code for evaluation construct-validity anti-patterns."""
    violations: List[ConstructValidityViolation] = []
    try:
        tree = ast.parse(source_code, filename=filename)
    except SyntaxError as exc:
        violations.append(
            ConstructValidityViolation(
                file=filename,
                line=exc.lineno or 1,
                rule_id="SYNTAX_ERROR",
                message=f"Syntax error in evaluation module: {exc}",
            )
        )
        return violations

    for node in ast.walk(tree):
        # 1. Tautological `assert True` or `assert 1 == 1`
        if isinstance(node, ast.Assert):
            if isinstance(node.test, ast.Constant) and node.test.value is True:
                violations.append(
                    ConstructValidityViolation(
                        file=filename,
                        line=node.lineno,
                        rule_id="TAUTOLOGICAL_ASSERT_TRUE",
                        message="Found tautological 'assert True' in evaluation module.",
                    )
                )
            elif (
                isinstance(node.test, ast.Compare)
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.Eq)
                and isinstance(node.test.left, ast.Constant)
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.left.value == node.test.comparators[0].value
            ):
                violations.append(
                    ConstructValidityViolation(
                        file=filename,
                        line=node.lineno,
                        rule_id="TAUTOLOGICAL_CONSTANT_COMPARE",
                        message="Found tautological constant comparison in assert statement.",
                    )
                )

        # 2. Clamped regression delta `max(0.0, cand_diff - base_diff)` inside bootstrap/diff helpers
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name.lower()
            if any(k in fn_name for k in ("bootstrap", "diff", "regression", "delta")):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "max"
                        and len(sub.args) == 2
                        and isinstance(sub.args[0], ast.Constant)
                        and sub.args[0].value in (0, 0.0)
                        and _is_subtraction_expr(sub.args[1])
                    ):
                        violations.append(
                            ConstructValidityViolation(
                                file=filename,
                                line=sub.lineno,
                                rule_id="CLAMPED_REGRESSION_DELTA",
                                message=(
                                    f"Function '{node.name}' clamps a subtraction delta with max(0.0, a - b), "
                                    "which masks negative regressions."
                                ),
                            )
                        )

            # 3. Vacuous `evaluate_*` function that consists solely of `return True`
            if fn_name.startswith("evaluate_"):
                body_stmts = [
                    stmt
                    for stmt in node.body
                    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
                ]
                if (
                    len(body_stmts) == 1
                    and isinstance(body_stmts[0], ast.Return)
                    and isinstance(body_stmts[0].value, ast.Constant)
                    and body_stmts[0].value.value is True
                ):
                    violations.append(
                        ConstructValidityViolation(
                            file=filename,
                            line=node.lineno,
                            rule_id="VACUOUS_EVALUATE_STUB",
                            message=f"Evaluator function '{node.name}' unconditionally returns True without inspecting inputs.",
                        )
                    )

    return violations


def collect_evaluation_files(repo_root: Path) -> List[Path]:
    """Collect all Python evaluation files across the 7 evaluation surfaces."""
    files: List[Path] = []
    for rel_dir in ("evals", "dual_pipeline/evals"):
        d = repo_root / rel_dir
        if d.is_dir():
            files.extend(sorted(d.rglob("*.py")))

    tools_dir = repo_root / "tools"
    if tools_dir.is_dir():
        for p in sorted(tools_dir.rglob("*.py")):
            if any(k in p.name for k in ("eval", "retrieval_gates", "retrieval_thresholds")):
                if p.name != "audit_eval_construct_validity.py":
                    files.append(p)
    return files


def audit_repository(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """Audit all evaluation modules in repo_root for construct-validity violations."""
    target_files = collect_evaluation_files(repo_root)
    all_violations: List[ConstructValidityViolation] = []

    for path in target_files:
        rel_path = str(path.relative_to(repo_root))
        source = path.read_text(encoding="utf-8")
        all_violations.extend(scan_source_for_violations(source, filename=rel_path))

    return {
        "status": "PASS" if len(all_violations) == 0 else "FAIL",
        "files_scanned": len(target_files),
        "violation_count": len(all_violations),
        "violations": [v.to_dict() for v in all_violations],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit evaluation surfaces for construct-validity violations.")
    parser.add_argument("--verify-strict", action="store_true", help="Exit non-zero on any construct-validity violation.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional path to write JSON audit report.")
    args = parser.parse_args(argv)

    report = audit_repository(REPO_ROOT)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"[EVALS-CONSTRUCT-VALIDITY] Status: {report['status']} | "
        f"Scanned: {report['files_scanned']} files | Violations: {report['violation_count']}"
    )
    for v in report["violations"]:
        print(f"  - {v['file']}:{v['line']} [{v['rule_id']}] {v['message']}")

    if args.verify_strict and report["violation_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
