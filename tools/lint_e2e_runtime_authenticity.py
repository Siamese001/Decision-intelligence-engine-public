#!/usr/bin/env python3
"""tools/lint_e2e_runtime_authenticity.py

Linter to enforce E2E Runtime & Observable-Result Authenticity.

Enforces:
1. RECEIPT_ONLY: Prohibits tests in tests/end_to_end/ or *_e2e.py from solely reading
   reports/*.json receipts without executing production runtime code or subprocesses.
2. SOURCE_GREP_ONLY: Prohibits E2E tests from solely reading .py/.md source text via
   .read_text() to assert substrings/AST patterns without runtime execution.
3. CORE_STUBBED_MOCK: Prohibits E2E tests from mocking out the core execution engine,
   pipeline orchestrator, or database writer under evaluation.
4. MUTABLE_LOCAL_COUPLING: Prohibits E2E tests from directly reading or coupling to
   mutable .local/dual_pipeline persistent runtime state instead of using an isolated
   ephemeral sandbox (tempfile.TemporaryDirectory or provision_isolated_test_db).
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Known core entry points that must not be mocked in E2E suites
CORE_ENTRY_POINTS = {
    "run_tests_with_progress",
    "run_fast_bootstrap",
    "LiveMeetingRecorder",
    "GovernedGraphWriter",
    "UniversalWriteGateway",
    "LocalMLXAudioTranscriber",
    "CanonicalAgenticPipeline",
    "PreSynthesisEntityResolver",
    "AssertionEligibilityAuditor",
    "GroundedDeliveryGuard",
    "execute_copilot_request",
    "rerank_assertions_vectorized",
    "verify_intent_candidates",
    "coalesce_contiguous_turns",
}

# Legacy debt grandfathered from blocking pre-commit until converted
LEGACY_DEBT_FILES: Set[str] = {
    "tests/end_to_end/test_copilot_wave_8.py",
    "tests/end_to_end/test_gpu_hardware_capacity_wave3_e2e.py",
    "tests/end_to_end/test_gpu_hardware_capacity_wave4_e2e.py",
    "tests/end_to_end/test_gpu_wave3_e2e.py",
    "tests/end_to_end/test_iphone_speaker_mlx_whisper_e2e.py",
    "tests/end_to_end/test_pr_latency_optimization_e2e.py",
    "tests/end_to_end/test_pr_overlap_evidence_e2e.py",
    "tests/end_to_end/test_pr_promotion_real_benchmark.py",
    "tests/end_to_end/test_pre_wave_decision_gate_e2e.py",
    "tests/end_to_end/test_antigravity_task_modes_e2e.py",
    "tests/end_to_end/test_mock_offline_enforcement_wave5_e2e.py",
}


class E2ERuntimeAuthenticityAuditor:
    """Audits E2E test files for real runtime execution vs fake receipts/mocks."""

    def __init__(self, rel_path: str, content: str, tree: ast.AST):
        self.rel_path = rel_path
        self.content = content
        self.tree = tree
        self.violations: List[Tuple[int, str, str]] = []
        self.imported_modules: Set[str] = set()
        self.prod_imported_symbols: Set[str] = set()
        self.called_functions: Set[str] = set()
        self.has_subproc: bool = False
        self.has_prod_import: bool = False
        self.has_prod_call: bool = False
        self.reads_reports: bool = False
        self.reads_source_text: bool = False
        self.couples_local_state: bool = False
        self.stubbed_core_mocks: List[str] = []

    def audit(self) -> List[Tuple[int, str, str]]:
        self._inspect_imports_and_calls()
        self._inspect_text_patterns()
        self._evaluate_rules()
        return self.violations

    def _inspect_imports_and_calls(self) -> None:
        prod_prefixes = (
            "dual_pipeline",
            "unified_pipeline",
            "storage",
            "pipeline_a_intake",
            "pipeline_b_extraction",
            "evals",
            "tools",
            "benchmarks",
        )
        call_root_names: Set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.imported_modules.add(a.name)
                    if any(a.name.startswith(prefix) for prefix in prod_prefixes):
                        bound = a.asname or a.name.split(".")[0]
                        self.prod_imported_symbols.add(bound)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.imported_modules.add(node.module)
                if any(node.module.startswith(prefix) for prefix in prod_prefixes):
                    for a in node.names:
                        self.prod_imported_symbols.add(a.asname or a.name)
            elif isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                    call_root_names.add(name)
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    curr: ast.AST = node.func
                    while isinstance(curr, ast.Attribute):
                        call_root_names.add(curr.attr)
                        curr = curr.value
                    if isinstance(curr, ast.Name):
                        call_root_names.add(curr.id)
                if name:
                    self.called_functions.add(name)
                if name in ("patch", "object"):
                    for arg in node.args:
                        val = ""
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            val = arg.value
                        elif isinstance(arg, ast.Name):
                            val = arg.id
                        elif isinstance(arg, ast.Attribute):
                            val = arg.attr
                        for core_fn in CORE_ENTRY_POINTS:
                            if core_fn in val:
                                self.stubbed_core_mocks.append(core_fn)

        self.has_prod_import = any(
            any(imp.startswith(prefix) for prefix in prod_prefixes)
            for imp in self.imported_modules
        )
        self.has_prod_call = bool(self.prod_imported_symbols & call_root_names)
        self.has_subproc = "subprocess" in self.imported_modules and bool(
            {"run", "Popen", "check_output", "check_call", "call"} & self.called_functions
        )

    def _inspect_text_patterns(self) -> None:
        lower = self.content.lower()
        if "reports/" in lower and (".read_text" in lower or "json.load" in lower):
            self.reads_reports = True
        if (".py" in lower or ".md" in lower) and ".read_text" in lower:
            self.reads_source_text = True
        if '".local/dual_pipeline"' in self.content or "'.local/dual_pipeline'" in self.content:
            if "tempfile" not in lower and "tmp_path" not in lower and "isolated" not in lower:
                self.couples_local_state = True

    def _evaluate_rules(self) -> None:
        effective_prod_exec = (self.has_prod_import and self.has_prod_call) or self.has_subproc
        # Rule 1: RECEIPT_ONLY
        if not effective_prod_exec and self.reads_reports:
            self.violations.append(
                (
                    1,
                    "ERR_E2E_RECEIPT_ONLY",
                    f"{self.rel_path} reads pre-existing reports/*.json receipts without executing "
                    "production runtime modules or subprocess entry points.",
                )
            )

        # Rule 2: SOURCE_GREP_ONLY
        if not effective_prod_exec and self.reads_source_text:
            self.violations.append(
                (
                    1,
                    "ERR_E2E_SOURCE_GREP_ONLY",
                    f"{self.rel_path} inspects .py/.md source text without executing runtime code. "
                    "Reclassify as unit/governance test or execute code under test.",
                )
            )

        # Rule 3: CORE_STUBBED_MOCK
        if self.stubbed_core_mocks:
            self.violations.append(
                (
                    1,
                    "ERR_E2E_CORE_STUBBED_MOCK",
                    f"{self.rel_path} stubs out core engine entry points with mocks: "
                    f"{sorted(self.stubbed_core_mocks)}. E2E tests must verify real execution.",
                )
            )

        # Rule 4: MUTABLE_LOCAL_COUPLING
        if self.couples_local_state:
            self.violations.append(
                (
                    1,
                    "ERR_E2E_MUTABLE_LOCAL_COUPLING",
                    f"{self.rel_path} directly reads mutable .local/dual_pipeline state on disk. "
                    "Use an isolated temporary sandbox (tempfile.TemporaryDirectory or provision_isolated_test_db).",
                )
            )


def audit_test_file(file_path: Path, repo_root: Path) -> List[Tuple[int, str, str]]:
    """Audits a single test file and returns list of violations."""
    rel = file_path.relative_to(repo_root).as_posix()
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as e:
        return [(1, "ERR_PARSE_ERROR", f"Could not parse {rel}: {e}")]

    auditor = E2ERuntimeAuthenticityAuditor(rel, content, tree)
    return auditor.audit()


def get_staged_e2e_files(repo_root: Path) -> List[Path]:
    """Retrieves staged E2E Python test files from git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--diff-filter=d"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root,
        )
        files = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.endswith(".py") and ("tests/end_to_end/" in line or line.endswith("_e2e.py")):
                p = repo_root / line
                if p.is_file():
                    files.append(p)
        return files
    except Exception:
        return []


def discover_all_e2e_files(repo_root: Path) -> List[Path]:
    """Finds all E2E test files under tests/."""
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return []
    files: List[Path] = []
    for p in sorted(tests_dir.rglob("test_*.py")):
        rel = p.relative_to(repo_root).as_posix()
        if "tests/end_to_end/" in rel or p.name.endswith("_e2e.py"):
            files.append(p)
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lint E2E tests for runtime and results authenticity.")
    parser.add_argument("--staged", action="store_true", help="Audit only staged E2E test files.")
    parser.add_argument("--all", action="store_true", help="Audit all E2E test files in repository.")
    parser.add_argument("--strict", action="store_true", help="Fail on legacy debt files as well.")
    parser.add_argument("files", nargs="*", help="Explicit test files to audit.")
    args = parser.parse_args(argv)

    repo_root = ROOT
    targets: List[Path] = []

    if args.files:
        targets = [Path(f).resolve() for f in args.files if Path(f).is_file()]
    elif args.staged:
        targets = get_staged_e2e_files(repo_root)
        if not targets:
            print("✅ No staged E2E test files found.")
            return 0
    elif args.all:
        targets = discover_all_e2e_files(repo_root)
    else:
        # Default: if run with no args, check staged files
        targets = get_staged_e2e_files(repo_root)
        if not targets:
            print("Usage: python tools/lint_e2e_runtime_authenticity.py [--staged | --all | --strict | files...]")
            return 0

    all_violations: List[Tuple[str, int, str, str]] = []
    legacy_count = 0

    for path in targets:
        rel = path.relative_to(repo_root).as_posix()
        violations = audit_test_file(path, repo_root)
        for line, code, msg in violations:
            if rel in LEGACY_DEBT_FILES and not args.strict and not args.staged:
                legacy_count += 1
            else:
                all_violations.append((rel, line, code, msg))

    print("=" * 80)
    print(f"  E2E RUNTIME AUTHENTICITY LINTER: {len(targets)} module(s) scanned")
    print("=" * 80)

    if legacy_count > 0:
        print(f"ℹ️  [LEGACY DEBT] {legacy_count} grandfathered violation(s) skipped (Wave 3 remediation target).")

    if all_violations:
        print(f"❌ {len(all_violations)} blocking violation(s) detected:\n")
        for rel, line, code, msg in all_violations:
            print(f"  - {rel}:{line} [{code}] {msg}")
        print("\nRemediation Guidance:")
        print("  1. E2E tests must execute production code or subprocesses, not static report JSONs.")
        print("  2. Source text inspections belong in tests/governance/ or tests/unit/.")
        print("  3. Do not mock out core engines; verify observable outputs (DB rows, files, APIs).")
        print("  4. Isolate mutable state via tempfile.TemporaryDirectory or provision_isolated_test_db().")
        return 1

    print(f"✅ All {len(targets)} E2E test module(s) verified authentic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
