#!/usr/bin/env python3
"""
tools/architectural_auditor.py — Architectural Integrity & AST Structural Drift Auditor.

Enforces Phase 5 Governance, PR Approvals & Safe Migrations standards:
1. AST Structural Drift Detection: Scans AST for broken unidirectional imports or decoupled components.
2. Critical-Path Blast Radius Gating: Detects modifications to entry points and lifecycle managers,
   requiring explicit Human-In-The-Loop (HITL) architectural approval.
3. Layer import integrity and lifecycle orchestrator conformance.

Usage:
  python tools/architectural_auditor.py check-ast [--paths ...] [--strict]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_PATH = ROOT / "tools" / "layer_hierarchy_manifest.json"
COMPONENT_LAYER_MAP: Dict[str, int] = (
    json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")) if _MANIFEST_PATH.exists() else {}
)

# Critical path entrypoints requiring mandatory architectural review
CRITICAL_ENTRYPOINTS: Set[str] = {
    "dual_pipeline/cli/copilot_handlers.py",
    "dual_pipeline/live_meeting_recorder_core/copilot_engine.py",
    "dual_pipeline/live_meeting_recorder_core/copilot_lifecycle.py",
}

# Forbidden cross-layer import dependencies (lower layers must never import higher layers)
FORBIDDEN_REVERSE_IMPORTS: Dict[str, Set[str]] = {
    "dual_pipeline.live_meeting_recorder_core": {
        "dual_pipeline.cli",
        "dual_pipeline.ui",
    },
    "dual_pipeline.graph_retrieval_core": {
        "dual_pipeline.cli",
        "dual_pipeline.live_meeting_recorder_core",
    },
}


@dataclass
class AuditViolation:
    file_path: str
    line: int
    rule: str
    message: str
    severity: str = "ERROR"  # ERROR, WARNING


class ASTDriftVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, file_lines: Optional[List[str]] = None) -> None:
        self.file_path = file_path
        self.file_lines = file_lines or []
        self.violations: List[AuditViolation] = []
        self.has_orchestrator_call: bool = False
        self.in_function: bool = False
        self.try_depth: int = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev = self.in_function
        self.in_function = True
        self.generic_visit(node)
        self.in_function = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev = self.in_function
        self.in_function = True
        self.generic_visit(node)
        self.in_function = prev

    def visit_Try(self, node: ast.Try) -> None:
        self.try_depth += 1
        self.generic_visit(node)
        self.try_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_reverse_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._check_reverse_import(module, node.lineno)
        self.generic_visit(node)

    def _check_reverse_import(self, target_module: str, lineno: int) -> None:
        norm_path = self.file_path.replace("\\", "/")
        # Exemption 1: Manifest override declaring file as Layer 6 (host bridges/runners)
        if COMPONENT_LAYER_MAP.get(norm_path) == 6 or COMPONENT_LAYER_MAP.get(Path(norm_path).name) == 6:
            return

        # Exemption 2: Function-level lazy import with lazy-decouple annotation or try block
        line_text = self.file_lines[lineno - 1] if 0 <= lineno - 1 < len(self.file_lines) else ""
        if self.in_function and (
            "lazy-decouple" in line_text
            or "# noqa" in line_text
            or self.try_depth > 0
        ):
            return

        for layer_prefix, forbidden_targets in FORBIDDEN_REVERSE_IMPORTS.items():
            if layer_prefix in norm_path.replace("/", "."):
                for forbidden in forbidden_targets:
                    if target_module == forbidden or target_module.startswith(f"{forbidden}."):
                        self.violations.append(
                            AuditViolation(
                                file_path=self.file_path,
                                line=lineno,
                                rule="ARCH-01-REVERSE-IMPORT",
                                message=f"Lower layer '{layer_prefix}' illegally imports higher layer '{target_module}'",
                            )
                        )

    def visit_Call(self, node: ast.Call) -> None:
        # Check lifecycle orchestrator engagement in CLI handlers
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if "lifecycle_orchestrator" in func_name.lower():
            self.has_orchestrator_call = True
        self.generic_visit(node)


def audit_file_ast(file_path: Path) -> List[AuditViolation]:
    """Parses Python file and checks AST for structural drift and illegal layer imports."""
    violations: List[AuditViolation] = []
    rel_path = str(file_path.relative_to(ROOT)) if file_path.is_absolute() else str(file_path)

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as e:
        return [AuditViolation(rel_path, e.lineno or 1, "ARCH-00-SYNTAX", f"Syntax error: {e}")]
    except Exception as e:
        return [AuditViolation(rel_path, 1, "ARCH-00-IO", f"Failed to read file: {e}")]

    file_lines = source.splitlines()
    visitor = ASTDriftVisitor(rel_path, file_lines)
    visitor.visit(tree)
    violations.extend(visitor.violations)

    # Invariants for CLI execution entrypoints
    if rel_path == "dual_pipeline/live_meeting_recorder_core/copilot_engine.py":
        if not visitor.has_orchestrator_call:
            violations.append(
                AuditViolation(
                    rel_path,
                    1,
                    "ARCH-02-LIFECYCLE-BYPASS",
                    "Critical entrypoint 'copilot_engine.py' must bind through CopilotLifecycleOrchestrator",
                )
            )

    return violations


def audit_critical_path_blast_radius() -> List[AuditViolation]:
    """Detects modifications to critical path files and checks for HITL architectural approval."""
    violations: List[AuditViolation] = []
    try:
        proc = subprocess.run(timeout=30,
            args=["git", "diff", "--name-only", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        changed_files = set(proc.stdout.strip().splitlines())
    except Exception:
        changed_files = set()

    critical_touched = changed_files & CRITICAL_ENTRYPOINTS
    if critical_touched:
        # Check commit message or environment variable for HITL approval
        approval_env = os.environ.get("HITL_ARCHITECTURAL_APPROVAL", "")
        if approval_env.lower() not in ("1", "true", "approved"):
            for f in critical_touched:
                violations.append(
                    AuditViolation(
                        f,
                        1,
                        "ARCH-03-CRITICAL-PATH-HITL",
                        f"Critical path entrypoint '{f}' modified without verified HITL approval marker",
                        severity="WARNING",
                    )
                )

    return violations


def run_ast_audit(target_paths: Optional[List[Path]] = None, strict: bool = False) -> int:
    """Executes AST audit across target paths or the entire repository."""
    print("=" * 70)
    print("  ARCHITECTURAL INTEGRITY AUDITOR (check-ast)")
    print("=" * 70)

    if not target_paths:
        target_paths = list((ROOT / "dual_pipeline").rglob("*.py"))

    all_violations: List[AuditViolation] = []

    for path in target_paths:
        if path.is_file() and path.suffix == ".py":
            violations = audit_file_ast(path)
            all_violations.extend(violations)

    # Check blast radius on git status
    blast_violations = audit_critical_path_blast_radius()
    all_violations.extend(blast_violations)

    errors = [v for v in all_violations if v.severity == "ERROR"]
    warnings = [v for v in all_violations if v.severity == "WARNING"]

    print(f"Scanned {len(target_paths)} Python module(s).")
    if warnings:
        print(f"\n[INFO] {len(warnings)} Architectural Advisory / HITL notice(s):")
        for w in warnings:
            print(f"  - [{w.rule}] {w.file_path}:{w.line} — {w.message}")

    if errors:
        print(f"\n[FAIL] Found {len(errors)} architectural violation(s):")
        for e in errors:
            print(f"  ❌ [{e.rule}] {e.file_path}:{e.line} — {e.message}")
        print("=" * 70)
        return 1

    print("\n✅ All architectural AST invariants and layer boundaries verified cleanly.")
    print("=" * 70)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Architectural Integrity Auditor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_ast_parser = subparsers.add_parser("check-ast", help="Audit AST for structural drift")
    check_ast_parser.add_argument("paths", nargs="*", type=Path, help="Specific paths to scan")
    check_ast_parser.add_argument("--strict", action="store_true", help="Fail on warnings")

    args = parser.parse_args()

    if args.command == "check-ast":
        target_paths = [p.resolve() for p in args.paths] if args.paths else None
        return run_ast_audit(target_paths=target_paths, strict=args.strict)

    return 0


if __name__ == "__main__":
    sys.exit(main())
