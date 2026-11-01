#!/usr/bin/env python3
"""Strict Static Type Annotation Gate.

Enforces full static type annotations (parameter types and return types) on
governed core modules using AST analysis. Rejects untyped function definitions,
missing parameter annotations, and missing return types to guarantee API
contract safety across agentic pipelines.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_GOVERNED_PACKAGES: Tuple[str, ...] = (
    "storage",
    "dual_pipeline/telemetry_core",
    "dual_pipeline/query_core",
)


@dataclass
class TypeViolation:
    filepath: str
    line: int
    function_name: str
    parameter_name: Optional[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileTypeAuditResult:
    filepath: str
    function_count: int
    violations: List[TypeViolation]

    @property
    def is_compliant(self) -> bool:
        return len(self.violations) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filepath": self.filepath,
            "function_count": self.function_count,
            "is_compliant": self.is_compliant,
            "violations": [v.to_dict() for v in self.violations],
        }


def _is_self_or_cls(arg_name: str) -> bool:
    return arg_name in ("self", "cls")


def _check_arg_annotations(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath_str: str,
) -> List[TypeViolation]:
    violations: List[TypeViolation] = []
    args = func_node.args

    all_args = list(args.posonlyargs) + list(args.args)
    for a in all_args:
        if _is_self_or_cls(a.arg):
            continue
        if a.annotation is None:
            violations.append(
                TypeViolation(
                    filepath=filepath_str,
                    line=a.lineno,
                    function_name=func_node.name,
                    parameter_name=a.arg,
                    message=f"Parameter '{a.arg}' is missing a type annotation",
                )
            )

    for a in args.kwonlyargs:
        if a.annotation is None:
            violations.append(
                TypeViolation(
                    filepath=filepath_str,
                    line=a.lineno,
                    function_name=func_node.name,
                    parameter_name=a.arg,
                    message=f"Keyword-only parameter '{a.arg}' is missing a type annotation",
                )
            )

    return violations


def _check_return_annotation(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath_str: str,
    allow_init_without_return: bool,
) -> Optional[TypeViolation]:
    if func_node.name == "__init__" and allow_init_without_return:
        return None
    if func_node.returns is None:
        return TypeViolation(
            filepath=filepath_str,
            line=func_node.lineno,
            function_name=func_node.name,
            parameter_name=None,
            message=f"Function '{func_node.name}' is missing a return type annotation",
        )
    return None


def audit_file_types(
    filepath: Path,
    allow_init_without_return: bool = True,
) -> FileTypeAuditResult:
    """Scan a Python file for missing parameter or return type annotations."""
    filepath_str = str(filepath)
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=filepath_str)
    except Exception as err:
        return FileTypeAuditResult(
            filepath=filepath_str,
            function_count=0,
            violations=[
                TypeViolation(
                    filepath=filepath_str,
                    line=1,
                    function_name="<module>",
                    parameter_name=None,
                    message=f"Syntax/Parse error: {err}",
                )
            ],
        )

    violations: List[TypeViolation] = []
    func_count = 0

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_count += 1
            violations.extend(_check_arg_annotations(node, filepath_str))
            ret_violation = _check_return_annotation(
                node, filepath_str, allow_init_without_return
            )
            if ret_violation:
                violations.append(ret_violation)

    return FileTypeAuditResult(
        filepath=filepath_str,
        function_count=func_count,
        violations=violations,
    )


def collect_governed_files(
    packages: Sequence[str],
    root: Path = ROOT,
) -> List[Path]:
    """Find all Python files belonging to governed packages."""
    files: List[Path] = []
    for pkg in packages:
        pkg_path = root / pkg
        if pkg_path.is_file() and pkg_path.suffix == ".py":
            files.append(pkg_path)
        elif pkg_path.is_dir():
            files.extend(sorted(pkg_path.rglob("*.py")))
    return [f for f in files if "__pycache__" not in str(f)]


def get_staged_python_files(root: Path = ROOT) -> List[Path]:
    """Retrieve Python files currently staged in Git index."""
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    try:
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True)
        paths = [
            root / p.strip()
            for p in res.stdout.splitlines()
            if p.strip().endswith(".py")
        ]
        return [p for p in paths if p.is_file()]
    except Exception:
        return []


def is_path_in_packages(path: Path, packages: Sequence[str], root: Path = ROOT) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(rel == pkg or rel.startswith(f"{pkg}/") for pkg in packages)


def build_type_gate_receipt(
    results: List[FileTypeAuditResult],
    governed_packages: Sequence[str],
) -> Dict[str, Any]:
    """Generate an immutable, deterministic runtime receipt for the type gate."""
    total_violations = sum(len(r.violations) for r in results)
    total_functions = sum(r.function_count for r in results)
    all_compliant = total_violations == 0

    summary_data = {
        "files_scanned": len(results),
        "functions_scanned": total_functions,
        "violations_count": total_violations,
        "is_compliant": all_compliant,
    }
    digest = hashlib.sha256(json.dumps(summary_data, sort_keys=True).encode("utf-8")).hexdigest()

    return {
        "receipt_contract": "urn:nee:dual-pipeline:strict-type-gate-receipt:1.0.0",
        "wave_id": "Wave-5",
        "wave_title": "MCP Tool Schema Token Optimization & Static Type Gate",
        "status": "PASS" if all_compliant else "FAIL",
        "governed_packages": list(governed_packages),
        "metrics": summary_data,
        "canonical_digest": digest,
    }


def print_type_gate_report(results: List[FileTypeAuditResult]) -> int:
    """Print human-readable type gate diagnostics and return exit code."""
    print("=" * 80)
    print("  STRICT STATIC TYPE ANNOTATION GATE")
    print("=" * 80)

    total_funcs = sum(r.function_count for r in results)
    violations = [v for r in results for v in r.violations]

    print(f"Scanned {len(results)} module(s) ({total_funcs} functions).")

    if not violations:
        print("✅ PASSED: All functions have complete parameter and return type annotations.")
        print("=" * 80)
        return 0

    print(f"\n❌ FAILED: Found {len(violations)} type annotation violation(s):\n")
    for v in violations:
        param_desc = f" [{v.parameter_name}]" if v.parameter_name else ""
        print(f"  - {v.filepath}:{v.line} in {v.function_name}(){param_desc}")
        print(f"    {v.message}")

    print("=" * 80)
    print("Remediation: Add explicit PEP 484 type annotations for all parameters and return types.")
    print("=" * 80)
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Strict static type annotation gate.")
    parser.add_argument("files", nargs="*", help="Specific files to check")
    parser.add_argument("--packages", type=str, default="", help="Comma-separated package paths to audit")
    parser.add_argument("--staged", action="store_true", help="Audit only staged git files")
    parser.add_argument("--check", action="store_true", help="Exit with non-zero code on violations")
    parser.add_argument("--receipt", type=Path, default=None, help="Path to write runtime receipt JSON")

    args = parser.parse_args(argv)

    packages = (
        [p.strip() for p in args.packages.split(",") if p.strip()]
        if args.packages
        else list(DEFAULT_GOVERNED_PACKAGES)
    )

    if args.files:
        resolved_args = [
            Path(f).resolve()
            for f in args.files
            if Path(f).is_file() and Path(f).suffix == ".py"
        ]
        root_resolved = ROOT.resolve()
        target_files = [
            f
            for f in resolved_args
            if not f.is_relative_to(root_resolved) or is_path_in_packages(f, packages, root_resolved)
        ]
    elif args.staged:
        staged_files = get_staged_python_files(ROOT)
        target_files = [f for f in staged_files if is_path_in_packages(f, packages, ROOT)]
    else:
        target_files = collect_governed_files(packages, ROOT)

    results = [audit_file_types(f) for f in target_files]

    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt = build_type_gate_receipt(results, packages)
        with open(args.receipt, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)

    exit_code = print_type_gate_report(results)
    return exit_code if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
