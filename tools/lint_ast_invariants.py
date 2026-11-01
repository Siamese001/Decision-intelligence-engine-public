#!/usr/bin/env python3
"""
AST Invariant & Data/Code Decoupling Linter.

Enforces 2026+ Agentic AI Codebase Standards and AGENTS.md Invariants:
1. NO_RAW_SQLITE: AGENTS.md Invariant 2 prohibits direct raw sqlite3 connections.
   All storage access must use storage.database or storage.connection with WAL mode.
2. NO_MACHINE_ABSOLUTE_PATHS: Machine-specific absolute paths (e.g. /Users/..., /home/..., C:\\...)
   are strictly forbidden in codebase modules and scripts.
3. DATA_CODE_DECOUPLING: Massive inline data payloads (text blobs > 2,000 characters,
   AST Dict literals with > 50 keys, or AST List literals with > 50 elements) are prohibited
   in executable modules. Declarative data must reside in manifests/ or JSON/MD files.

Usage:
  python tools/lint_ast_invariants.py [--staged] [--all] [--check] [files ...]
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]

# Allowed files for raw sqlite3 connections (SSOT storage layer)
ALLOWED_SQLITE_FILES: Set[str] = {
    "storage/__init__.py",
    "storage/database.py",
    "storage/connection.py",
    "storage/sqlite_manager.py",
    "dual_pipeline/storage/governed_context.py",
    "dual_pipeline/core.py",
    "dual_pipeline/l0/wal_evidence.py",
    "dual_pipeline/semantic_cache_core/cache.py",
    "dual_pipeline/evals/sandbox/environment.py",
    "dual_pipeline/_validators.py",
    "dual_pipeline/graph_writer.py",
    "dual_pipeline/candidate_cleanup.py",
    "dual_pipeline/identity_resolver.py",
    "dual_pipeline/node_type_policy.py",
    "dual_pipeline/combined_remediation.py",
    "dual_pipeline/label_quality_core/migration.py",
    "dual_pipeline/source_retirement_core/targets.py",
    "dual_pipeline/source_retirement_core/constants.py",
    "dual_pipeline/index_core/filters.py",
    "dual_pipeline/validation_closeout_core/targets.py",
    "dual_pipeline/validation_closeout_core/constants.py",
    "dual_pipeline/validation_closeout_core/qualifiers.py",
    "dual_pipeline/card_refactor_release_core/constants.py",
    "dual_pipeline/facets_core/domains.py",
    "dual_pipeline/facets_core/topics.py",
    "dual_pipeline/facets_core/schema.py",
    "dual_pipeline/identity_resolver_core/__init__.py",
}

# Machine-specific absolute path patterns
MACHINE_PATH_PATTERNS = [
    re.compile(r"^/(?:Users|home)/[a-zA-Z0-9_\.\-]+(?:/|$)", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\(?:Users|Documents|Desktop)(?:\\|$)", re.IGNORECASE),
]

# Limits for inline AST data literals in executable code
MAX_INLINE_STRING_CHARS = 2000
MAX_INLINE_COLLECTION_ELEMENTS = 50

# Exempt paths from data decoupling (e.g., test fixtures, migration baselines, tools/linters, monolithic engines)
EXEMPT_DATA_DECOUPLING_PREFIXES = (
    "tests/",
    "evals/",
    "dual_pipeline/evals/",
    "dual_pipeline/transcript_qa_core/",
    "dual_pipeline/l2_providers_core/",
    "dual_pipeline/ambiguity_core/",
    "dual_pipeline/transcript_pipeline_core/",
    "dual_pipeline/telemetry_core/",
    "dual_pipeline/lens_evaluation/",
    "dual_pipeline/query_core/",
    "dual_pipeline/canonical_pipeline/",
    "dual_pipeline/l0/__init__.py",
    "dual_pipeline/facets_core/",
    "dual_pipeline/speaker_attribution_core/",
    "dual_pipeline/identity_resolver_core/",
    "dual_pipeline/document_pipeline_core/",
    "dual_pipeline/live_meeting_recorder_core/",
    "dual_pipeline/prompts/",
    "contracts/dual_pipeline/fixtures/",
    "graph/migrations/",
    "config/credentials.py",
    "tools/",
    "dual_pipeline/query.py",
    "scripts/analyze_output_hierarchy_luna.py",
    "scripts/adversarial_review_luna.py",
    "scripts/audit_and_judge_project_nexus.py",
    "scripts/run_adversarial_debate.py",
)



class InvariantViolation(NamedTuple):
    file_path: str
    line_number: int
    rule: str
    detail: str


class ASTInvariantVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, docstrings: Set[int]):
        self.rel_path = rel_path
        self.docstrings = docstrings
        self.violations: List[InvariantViolation] = []
        self._in_all_target: Set[int] = set()
        self.in_type_checking: bool = False
        self._has_raw_sqlite_import: bool = False
        self._raw_sqlite_import_line: int = 1
        self._has_sqlite_attr_usage: bool = False

    def visit_If(self, node: ast.If):
        test = node.test
        if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        ):
            prev = self.in_type_checking
            self.in_type_checking = True
            self.generic_visit(node)
            self.in_type_checking = prev
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        if not self.in_type_checking:
            self._check_sqlite_import(node, [alias.name for alias in node.names])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module and not self.in_type_checking:
            if node.module == "sqlite3" or node.module.startswith("sqlite3."):
                non_type_names = [
                    a.name for a in node.names if a.name not in ("Connection", "Cursor", "Row", "Error", "OperationalError", "IntegrityError", "DatabaseError")
                ]
                if non_type_names:
                    self._check_sqlite_import(node, [node.module])
                    self._has_sqlite_attr_usage = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Detect sqlite3.connect(...)
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "sqlite3":
                if node.func.attr == "connect":
                    self._has_sqlite_attr_usage = True
                    if self.rel_path not in ALLOWED_SQLITE_FILES and not self._is_exempt_test():
                        self.violations.append(
                            InvariantViolation(
                                file_path=self.rel_path,
                                line_number=node.lineno,
                                rule="NO_RAW_SQLITE",
                                detail="Direct 'sqlite3.connect()' call is prohibited by AGENTS.md Invariant 2. Use storage.database.get_governed_db() or connection manager.",
                            )
                        )
                elif node.func.attr not in ("Connection", "Cursor", "Row", "Error", "OperationalError", "IntegrityError", "DatabaseError"):
                    self._has_sqlite_attr_usage = True
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            val = node.value.strip()
            # Rule 2: Machine Absolute Paths
            for pat in MACHINE_PATH_PATTERNS:
                if pat.search(val) and not self._is_exempt_test():
                    self.violations.append(
                        InvariantViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            rule="NO_MACHINE_ABSOLUTE_PATHS",
                            detail=f"Hardcoded local machine path '{val[:80]}' detected. Use relative paths from repository ROOT.",
                        )
                    )
                    break

            # Rule 3: Data/Code Decoupling (Oversized string literal)
            if len(node.value) > MAX_INLINE_STRING_CHARS and not self._is_exempt_data_decoupling():
                if id(node) not in self.docstrings:
                    self.violations.append(
                        InvariantViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            rule="DATA_CODE_DECOUPLING",
                            detail=f"Oversized string literal ({len(node.value)} chars > {MAX_INLINE_STRING_CHARS}). Move data payload to external manifest, markdown, or fixture.",
                        )
                    )
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        parts: List[str] = []
        literal_len = 0
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
                literal_len += len(v.value)
            else:
                parts.append("placeholder")
        joined = "".join(parts).strip()
        for pat in MACHINE_PATH_PATTERNS:
            if pat.search(joined) and not self._is_exempt_test():
                self.violations.append(
                    InvariantViolation(
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        rule="NO_MACHINE_ABSOLUTE_PATHS",
                        detail=f"Hardcoded local machine path '{joined[:80]}' detected in f-string. Use relative paths from repository ROOT.",
                    )
                )
                break
        if literal_len > MAX_INLINE_STRING_CHARS and not self._is_exempt_data_decoupling():
            self.violations.append(
                InvariantViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="DATA_CODE_DECOUPLING",
                    detail=f"Oversized f-string literal ({literal_len} chars > {MAX_INLINE_STRING_CHARS}). Move data payload to external manifest.",
                )
            )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict):
        if len(node.keys) > MAX_INLINE_COLLECTION_ELEMENTS and not self._is_exempt_data_decoupling():
            self.violations.append(
                InvariantViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="DATA_CODE_DECOUPLING",
                    detail=f"Oversized dict literal ({len(node.keys)} keys > {MAX_INLINE_COLLECTION_ELEMENTS}). Decouple into declarative JSON manifest.",
                )
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__" and node.value is not None:
                self._in_all_target.add(id(node.value))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == "__all__" and node.value is not None:
            self._in_all_target.add(id(node.value))
        self.generic_visit(node)

    def visit_List(self, node: ast.List):
        if (
            id(node) not in self._in_all_target
            and len(node.elts) > MAX_INLINE_COLLECTION_ELEMENTS
            and not self._is_exempt_data_decoupling()
        ):
            self.violations.append(
                InvariantViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="DATA_CODE_DECOUPLING",
                    detail=f"Oversized list literal ({len(node.elts)} elements > {MAX_INLINE_COLLECTION_ELEMENTS}). Decouple into declarative JSON manifest or data file.",
                )
            )
        self.generic_visit(node)

    def visit_Set(self, node: ast.Set):
        if (
            id(node) not in self._in_all_target
            and len(node.elts) > MAX_INLINE_COLLECTION_ELEMENTS
            and not self._is_exempt_data_decoupling()
        ):
            self.violations.append(
                InvariantViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="DATA_CODE_DECOUPLING",
                    detail=f"Oversized set literal ({len(node.elts)} elements > {MAX_INLINE_COLLECTION_ELEMENTS}). Decouple into declarative JSON manifest or data file.",
                )
            )
        self.generic_visit(node)

    def visit_Tuple(self, node: ast.Tuple):
        if (
            id(node) not in self._in_all_target
            and len(node.elts) > MAX_INLINE_COLLECTION_ELEMENTS
            and not self._is_exempt_data_decoupling()
        ):
            self.violations.append(
                InvariantViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="DATA_CODE_DECOUPLING",
                    detail=f"Oversized tuple literal ({len(node.elts)} elements > {MAX_INLINE_COLLECTION_ELEMENTS}). Decouple into declarative JSON manifest or data file.",
                )
            )
        self.generic_visit(node)

    def _check_sqlite_import(self, node: ast.AST, module_names: List[str]):
        if any(name == "sqlite3" or name.startswith("sqlite3.") for name in module_names):
            if self.rel_path not in ALLOWED_SQLITE_FILES and not self._is_exempt_test():
                self.violations.append(
                    InvariantViolation(
                        file_path=self.rel_path,
                        line_number=getattr(node, "lineno", 1),
                        rule="NO_RAW_SQLITE",
                        detail="Importing 'sqlite3' directly is prohibited in business logic and scripts. Use storage.database.",
                    )
                )

    def _is_exempt_test(self) -> bool:
        return (
            self.rel_path.startswith("tests/")
            or "/tests/" in self.rel_path
            or Path(self.rel_path).name.startswith("test_")
            or self.rel_path == "scripts/prepare_fpl_manifests.py"
        )

    def _is_exempt_data_decoupling(self) -> bool:
        if self._is_exempt_test():
            return True
        return any(self.rel_path.startswith(prefix) for prefix in EXEMPT_DATA_DECOUPLING_PREFIXES)


def collect_docstring_node_ids(tree: ast.AST) -> Set[int]:
    """Collect object IDs of Constant nodes that serve as docstrings."""
    doc_ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                if isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                    doc_ids.add(id(node.body[0].value))
    return doc_ids


def _check_canonical_query_filter_invariants(
    rel_path: str, content: str
) -> List[InvariantViolation]:
    """Enforce that assertion table queries in governed query/adapter/grounding modules gate canonical_status and canonical_eligible."""
    enforced_files = {
        "dual_pipeline/decision_adapter_core/row_reader.py",
        "dual_pipeline/query_core/graph_first_engine.py",
        "dual_pipeline/query_core/offline_retriever.py",
        "dual_pipeline/grounding/snapshot.py",
    }
    if rel_path not in enforced_files:
        return []
    if "FROM assertion" not in content and "from assertion" not in content:
        return []
    violations: List[InvariantViolation] = []
    if "canonical_status" not in content or "canonical_eligible" not in content:
        violations.append(
            InvariantViolation(
                file_path=rel_path,
                line_number=1,
                rule="CANONICAL_ASSERTION_QUERY_FILTER_REQUIRED",
                detail=(
                    f"Module '{rel_path}' queries 'FROM assertion' without enforcing both "
                    "'canonical_status' and 'canonical_eligible'."
                ),
            )
        )
    return violations


def scan_file(file_path: Path, repo_root: Path) -> List[InvariantViolation]:
    try:
        rel_path = str(file_path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        rel_path = str(file_path)

    if not rel_path.endswith(".py"):
        return []

    if rel_path.startswith("archive/") or "/archive/" in rel_path:
        return []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [
            InvariantViolation(
                file_path=rel_path,
                line_number=getattr(exc, "lineno", 1) or 1,
                rule="SYNTAX_ERROR",
                detail=f"Failed to parse AST for '{rel_path}': {exc}",
            )
        ]

    docstrings = collect_docstring_node_ids(tree)
    visitor = ASTInvariantVisitor(rel_path, docstrings)
    visitor.visit(tree)
    visitor.violations.extend(_check_canonical_query_filter_invariants(rel_path, content))
    return visitor.violations


def get_staged_python_files(repo_root: Path) -> List[Path]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=d"]
    try:
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True)
        paths = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.endswith(".py"):
                p = repo_root / line
                if p.is_file():
                    paths.append(p)
        return paths
    except Exception:
        return []


def get_all_python_files(repo_root: Path) -> List[Path]:
    files: List[Path] = []
    for root_dir in ["dual_pipeline", "scripts", "tools", "storage", "pipeline_a_intake", "pipeline_b_extraction", "pipeline_c_retrieval"]:
        dir_path = repo_root / root_dir
        if dir_path.is_dir():
            files.extend(dir_path.rglob("*.py"))
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AST Invariant & Data/Code Decoupling Linter")
    parser.add_argument("--staged", action="store_true", help="Check only git staged python files")
    parser.add_argument("--all", action="store_true", help="Check all python files in governed directories")
    parser.add_argument("--check", action="store_true", help="Return code 1 if violations found")
    parser.add_argument("files", nargs="*", help="Specific files to lint")
    args = parser.parse_args(argv)

    if args.staged:
        target_files = get_staged_python_files(ROOT)
    elif args.all:
        target_files = get_all_python_files(ROOT)
    elif args.files:
        target_files = [Path(f).resolve() for f in args.files if f.endswith(".py")]
    elif args.check:
        target_files = get_all_python_files(ROOT)
    else:
        target_files = get_staged_python_files(ROOT)

    violations: List[InvariantViolation] = []
    for p in target_files:
        if p.exists() and p.is_file():
            violations.extend(scan_file(p, ROOT))

    print("=" * 80)
    print("  AST INVARIANT & DATA/CODE DECOUPLING LINTER")
    print(f"  Files Scanned: {len(target_files)}")
    print("=" * 80)

    if not violations:
        print("✅ PASSED: Zero AST invariant or data decoupling violations detected.")
        print("=" * 80)
        return 0

    print(f"❌ FAILED: Found {len(violations)} violation(s):\n")
    for v in violations:
        print(f"  [{v.rule}] {v.file_path}:{v.line_number}")
        print(f"    {v.detail}\n")
    print("=" * 80)
    return 1 if (args.check or args.staged or args.all) else 0


if __name__ == "__main__":
    sys.exit(main())
