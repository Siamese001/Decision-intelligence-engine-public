#!/usr/bin/env python3
"""
tools/lint_u0_hitl_invariants.py

AST Invariant Linter for Stage U0 Ingestion HITL Governance.
Enforces Master Invariant: HITL resolution is 100% mandatory and CANNOT EVER be
bypassed across any ingestion path originating from Stage U0.

Checks:
1. NO_BYPASS_FUNCTION_PARAMETERS: Functions must not declare bypass parameters
   (bypass_speaker_hitl, bypass_hitl, bypass_roster_gate, etc.).
2. NO_BYPASS_METADATA_ASSIGNMENTS: Dict keys matching forbidden bypass keys must not be set to True.
3. MANDATORY_HITL_GUARD_INVOCATION: Key entry points in Stage U0 ingestion must invoke assert_no_u0_hitl_bypass().

Usage:
    python tools/lint_u0_hitl_invariants.py [--check] [--all] [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, NamedTuple, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_BYPASS_KEYS: Set[str] = {
    "bypass_speaker_hitl",
    "bypass_hitl",
    "bypass_roster_gate",
    "no_hitl",
    "skip_hitl",
    "disable_hitl",
    "no_interactive_hitl",
    "skip_speaker_attribution",
    "bypass_attribution",
}

# Exempt files where bypass keys are legitimately defined in anti-bypass rules, guards, or tests
EXEMPT_FILES: Set[str] = {
    "dual_pipeline/transcript_stages_core/hitl_guard.py",
    "dual_pipeline/live_meeting_recorder_core/hitl_manager.py",
    "tools/lint_u0_hitl_invariants.py",
}

CRITICAL_GUARDED_FILES: Set[str] = {
    "dual_pipeline/transcript_stages_core/u0_ingestion.py",
    "dual_pipeline/transcript_pipeline_core/pipeline.py",
    "dual_pipeline/canonical_pipeline/pipeline.py",
    "pipeline_a_intake/agents/ingestion_coordinator.py",
    "pipeline_a_intake/agents/roster_reconciler_agent.py",
    "dual_pipeline/transcript_stages_core/c0_gates.py",
    "dual_pipeline/speaker_attribution_core/attribution_engine.py",
}


class LintViolation(NamedTuple):
    file_path: Path
    line_number: int
    rule: str
    message: str


class U0HitlInvariantVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, rel_path: str):
        self.file_path = file_path
        self.rel_path = rel_path
        self.violations: List[LintViolation] = []
        self.has_hitl_guard_call = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_args(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_args(node)
        self.generic_visit(node)

    def _check_function_args(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        all_args = list(node.args.args) + list(node.args.kwonlyargs)
        if node.args.vararg:
            all_args.append(node.args.vararg)
        if node.args.kwarg:
            all_args.append(node.args.kwarg)

        for arg in all_args:
            name_lower = arg.arg.lower()
            if name_lower in FORBIDDEN_BYPASS_KEYS:
                self.violations.append(
                    LintViolation(
                        file_path=self.file_path,
                        line_number=arg.lineno,
                        rule="NO_BYPASS_FUNCTION_PARAMETERS",
                        message=(
                            f"Function '{node.name}' declares forbidden bypass parameter '{arg.arg}'. "
                            "Stage U0 HITL resolution is mandatory and cannot accept bypass flags."
                        ),
                    )
                )

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name == "assert_no_u0_hitl_bypass":
            self.has_hitl_guard_call = True

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                slice_node = target.slice
                key_val = None
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    key_val = slice_node.value.lower()
                if key_val in FORBIDDEN_BYPASS_KEYS:
                    is_truthy = False
                    if isinstance(node.value, ast.Constant) and bool(node.value.value):
                        is_truthy = True
                    if is_truthy:
                        self.violations.append(
                            LintViolation(
                                file_path=self.file_path,
                                line_number=node.lineno,
                                rule="NO_BYPASS_METADATA_ASSIGNMENTS",
                                message=(
                                    f"Direct assignment to forbidden bypass key '{key_val}' detected. "
                                    "Bypassing Stage U0 HITL is strictly prohibited."
                                ),
                            )
                        )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for k_node, v_node in zip(node.keys, node.values):
            if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                key_str = k_node.value.lower()
                if key_str in FORBIDDEN_BYPASS_KEYS:
                    if isinstance(v_node, ast.Constant) and bool(v_node.value):
                        self.violations.append(
                            LintViolation(
                                file_path=self.file_path,
                                line_number=k_node.lineno,
                                rule="NO_BYPASS_METADATA_ASSIGNMENTS",
                                message=(
                                    f"Dict literal sets forbidden bypass key '{k_node.value}' to True. "
                                    "Stage U0 HITL resolution is mandatory."
                                ),
                            )
                        )
        self.generic_visit(node)


def lint_file(path: Path) -> List[LintViolation]:
    try:
        rel_path = str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel_path = str(path)

    if rel_path in EXEMPT_FILES:
        return []

    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
    except Exception as exc:
        return [LintViolation(path, 1, "PARSE_ERROR", f"Failed to parse {path}: {exc}")]

    visitor = U0HitlInvariantVisitor(path, rel_path)
    visitor.visit(tree)

    violations = list(visitor.violations)
    if rel_path in CRITICAL_GUARDED_FILES and not visitor.has_hitl_guard_call:
        violations.append(
            LintViolation(
                file_path=path,
                line_number=1,
                rule="MANDATORY_HITL_GUARD_INVOCATION",
                message=(
                    f"Critical Stage U0 file '{rel_path}' does not invoke "
                    "'assert_no_u0_hitl_bypass()'. Stage U0 boundaries must enforce anti-bypass guard."
                ),
            )
        )

    return violations


def lint_prompt_file(path: Path) -> List[LintViolation]:
    violations: List[LintViolation] = []
    try:
        content = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            line_lower = line.lower()
            for key in FORBIDDEN_BYPASS_KEYS:
                if key in line_lower:
                    violations.append(
                        LintViolation(
                            file_path=path,
                            line_number=line_no,
                            rule="NO_BYPASS_PROMPT_INSTRUCTIONS",
                            message=f"Prompt file references forbidden bypass token '{key}'. Prompts must enforce mandatory HITL without bypass.",
                        )
                    )
    except Exception as exc:
        violations.append(LintViolation(path, 1, "PARSE_ERROR", f"Failed to read prompt file {path}: {exc}"))
    return violations


def lint_metadata_file(path: Path) -> List[LintViolation]:
    violations: List[LintViolation] = []
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in data.keys():
                if k.lower() in FORBIDDEN_BYPASS_KEYS:
                    violations.append(
                        LintViolation(
                            file_path=path,
                            line_number=1,
                            rule="NO_BYPASS_METADATA_FILES",
                            message=f"Metadata JSON declares forbidden bypass key '{k}'.",
                        )
                    )
    except Exception as exc:
        violations.append(LintViolation(path, 1, "PARSE_ERROR", f"Failed to parse metadata file {path}: {exc}"))
    return violations


def collect_target_files(paths: Sequence[str]) -> List[Path]:
    if paths:
        collected: List[Path] = []
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_file() and path_obj.suffix == ".py":
                collected.append(path_obj)
            elif path_obj.is_dir():
                collected.extend(path_obj.rglob("*.py"))
        return [p for p in collected if "tests/" not in str(p)]

    # Default: scan dual_pipeline/ and pipeline_a_intake/
    targets: List[Path] = []
    for d in ("dual_pipeline", "pipeline_a_intake"):
        dir_path = ROOT / d
        if dir_path.is_dir():
            for p in dir_path.rglob("*.py"):
                rel = str(p.relative_to(ROOT))
                if not rel.startswith("tests/") and "/tests/" not in rel:
                    targets.append(p)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Stage U0 HITL mandatory anti-bypass invariants.")
    parser.add_argument("--check", action="store_true", default=False, help="Run check and return exit code 1 on violations.")
    parser.add_argument("--all", action="store_true", default=False, help="Check all candidate files.")
    parser.add_argument("paths", nargs="*", help="Optional file or directory paths to check.")
    args = parser.parse_args()

    files = collect_target_files(args.paths)
    all_violations: List[LintViolation] = []

    for f in sorted(files):
        all_violations.extend(lint_file(f))

    # Also check prompt files
    prompt_dirs = [ROOT / "governance" / "decision_intelligence", ROOT / "config" / "prompts"]
    for pdir in prompt_dirs:
        if pdir.is_dir():
            for pf in pdir.rglob("*.md"):
                all_violations.extend(lint_prompt_file(pf))

    # Also check metadata inputs
    input_dir = ROOT / "inputs" / "transcripts"
    if input_dir.is_dir():
        for mf in input_dir.glob("*.json"):
            all_violations.extend(lint_metadata_file(mf))

    if all_violations:
        print(f"\n❌ FOUND {len(all_violations)} STAGE U0 HITL INVARIANT VIOLATION(S):")
        for v in all_violations:
            try:
                rel = v.file_path.relative_to(ROOT)
            except ValueError:
                rel = v.file_path
            print(f"  {rel}:{v.line_number}: [{v.rule}] {v.message}")
        return 1

    print(f"✅ STAGE U0 HITL MANDATORY INVARIANTS CLEAN: Checked {len(files)} Python modules, prompts, and metadata files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

