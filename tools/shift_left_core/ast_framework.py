"""
tools/shift_left_core/ast_framework.py

Shared Single-Pass AST Visitor and Shift-Left Ratchet Engine.
Enforces cognitive complexity <=15, Ruff TID251 banned-api sqlite3.connect,
machine-path bans, monotonic file budgets, and layer architecture in one pass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import os
import re
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent

from tools.file_budget_core import count_lines, load_ratchet_baseline

DEFAULT_MAX_COMPLEXITY = 15
DEFAULT_MAX_FUNCTION_LINES = 60
DEFAULT_MAX_LINES = 400
BASELINE_PATH = ROOT / "reports" / "file_budget_burndown_baseline.json"
EXCLUSIONS_PATH = ROOT / "config" / "governance" / "file_budget_exclusions.json"

_SANITIZED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


def _sanitized_git_env() -> Dict[str, str]:
    env = os.environ.copy()
    for var in _SANITIZED_GIT_VARS:
        env.pop(var, None)
    return env

GOVERNED_DIRS: Set[str] = {
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "unified_pipeline",
    "storage",
    "graph",
    "evals",
    "config",
    "scripts",
    "tools",
}
from tools.lint_ast_invariants import (
    ALLOWED_SQLITE_FILES,
    EXEMPT_DATA_DECOUPLING_PREFIXES,
    MACHINE_PATH_PATTERNS,
    MAX_INLINE_COLLECTION_ELEMENTS,
    MAX_INLINE_STRING_CHARS,
)
ALLOWED_SQLITE_PREFIXES = (
    "storage/",
    "database/",
    "dual_pipeline/storage/",
    "dual_pipeline/l0/wal_evidence.py",
    "dual_pipeline/core.py",
    "dual_pipeline/semantic_cache_core/cache.py",
    "dual_pipeline/evals/sandbox/environment.py",
    "tests/",
    "tools/",
    "scripts/",
)
DECLARATIVE_REGISTRY_PREFIXES = (
    "contracts/",
    "evals/",
    "tests/",
    "tools/",
    "dual_pipeline/model_registry.py",
    "config/credentials.py",
    "graph/migrations/",
)


class FunctionComplexity(NamedTuple):
    name: str
    start_line: int
    end_line: int
    cognitive_complexity: int
    cyclomatic_complexity: int
    effective_lines: int = 0
    is_fixture: bool = False
    qualified_name: str = ""


class ASTViolation(NamedTuple):
    file_path: str
    line_number: int
    category: str
    message: str


from .cognitive import CognitiveWalker, calculate_function_complexity, compute_cognitive_complexity


class SinglePassASTVisitor(ast.NodeVisitor):
    """Single-pass visitor collecting functions, imports, raw sqlite calls, and hardcoded paths."""

    def __init__(self, rel_path: str, source_text: str, is_pure_declarative: bool = False):
        self.rel_path = rel_path
        self.source_lines = source_text.splitlines()
        self.functions: List[FunctionComplexity] = []
        self.ast_violations: List[ASTViolation] = []
        self.imports: List[Tuple[int, str]] = []
        self._docstring_nodes: set[int] = set()
        self._scope_stack: List[str] = []
        self._in_all_target: set[int] = set()
        self._is_pure_declarative = is_pure_declarative
        self._func_depth: int = 0

    def scan_docstrings(self, root: ast.AST) -> None:
        for node in ast.walk(root):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    self._docstring_nodes.add(id(node.body[0].value))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    @staticmethod
    def _is_pytest_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == "fixture":
                return True
            if isinstance(target, ast.Attribute) and target.attr == "fixture":
                return True
        return False

    @staticmethod
    def _compute_effective_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        end_line = getattr(node, "end_lineno", node.lineno)
        raw_lines = max(1, end_line - node.lineno + 1)
        doc_lines = 0
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            doc_node = node.body[0]
            doc_end = getattr(doc_node, "end_lineno", doc_node.lineno)
            doc_lines = max(0, doc_end - doc_node.lineno + 1)

        nested_lines = 0
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n_end = getattr(stmt, "end_lineno", stmt.lineno)
                nested_lines += max(0, n_end - stmt.lineno + 1)

        return max(1, raw_lines - doc_lines - nested_lines)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self._func_depth == 0:
            qname = ".".join(self._scope_stack + [node.name]) if self._scope_stack else node.name
            cog = calculate_function_complexity(node, qname)
            cyc = self._compute_cyclomatic_complexity(node)
            end_line = getattr(node, "end_lineno", node.lineno)
            eff_lines = self._compute_effective_lines(node)
            is_fix = self._is_pytest_fixture(node)
            self.functions.append(
                FunctionComplexity(
                    name=qname,
                    start_line=node.lineno,
                    end_line=end_line,
                    cognitive_complexity=cog,
                    cyclomatic_complexity=cyc,
                    effective_lines=eff_lines,
                    is_fixture=is_fix,
                    qualified_name=qname,
                )
            )
        self._func_depth += 1
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._func_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def _compute_cyclomatic_complexity(self, func_node: ast.AST) -> int:
        branches = 0
        for n in ast.walk(func_node):
            if isinstance(n, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler)):
                branches += 1
            elif isinstance(n, ast.BoolOp):
                branches += max(0, len(n.values) - 1)
        return 1 + branches

    def _compute_cognitive_complexity(self, func_node: ast.AST) -> int:
        return compute_cognitive_complexity(func_node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((node.lineno, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append((node.lineno, node.module))
            for alias in node.names:
                if alias.name != "*":
                    self.imports.append((node.lineno, f"{node.module}.{alias.name}"))
            if node.module == "sqlite3":
                self._check_sqlite_import(node)
        self.generic_visit(node)

    def _check_sqlite_import(self, node: ast.ImportFrom) -> None:
        if self._is_allowed_sqlite_path():
            return
        for alias in node.names:
            if alias.name == "connect":
                self.ast_violations.append(
                    ASTViolation(
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        category="raw_sqlite_banned",
                        message="Direct from sqlite3 import connect prohibited outside storage/. Use storage.connection.",
                    )
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__" and node.value is not None:
                self._in_all_target.add(id(node.value))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "__all__" and node.value is not None:
            self._in_all_target.add(id(node.value))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self._docstring_nodes:
            val = node.value.strip()
            for pattern in MACHINE_PATH_PATTERNS:
                if pattern.match(val):
                    self.ast_violations.append(
                        ASTViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            category="machine_absolute_path",
                            message=f"Hardcoded local filesystem path '{val}' prohibited.",
                        )
                    )
                    break
            if len(node.value) > MAX_INLINE_STRING_CHARS and not self._is_exempt_data_decoupling():
                self.ast_violations.append(
                    ASTViolation(
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        category="data_code_decoupling",
                        message=f"Oversized string literal ({len(node.value)} chars > {MAX_INLINE_STRING_CHARS}).",
                    )
                )
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        parts: List[str] = []
        literal_len = 0
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
                literal_len += len(v.value)
            else:
                parts.append("placeholder")
        joined = "".join(parts).strip()
        for pattern in MACHINE_PATH_PATTERNS:
            if pattern.match(joined):
                self.ast_violations.append(
                    ASTViolation(
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        category="machine_absolute_path",
                        message=f"Hardcoded local filesystem path '{joined[:80]}' prohibited in f-string.",
                    )
                )
                break
        if literal_len > MAX_INLINE_STRING_CHARS and not self._is_exempt_data_decoupling():
            self.ast_violations.append(
                ASTViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    category="data_code_decoupling",
                    message=f"Oversized f-string literal ({literal_len} chars > {MAX_INLINE_STRING_CHARS}).",
                )
            )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if len(node.keys) > MAX_INLINE_COLLECTION_ELEMENTS and not self._is_exempt_data_decoupling():
            self.ast_violations.append(
                ASTViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    category="data_code_decoupling",
                    message=f"Oversized dict literal ({len(node.keys)} keys > {MAX_INLINE_COLLECTION_ELEMENTS}).",
                )
            )
        self.generic_visit(node)

    def _check_collection_size(self, node: ast.List | ast.Set | ast.Tuple, kind: str) -> None:
        if (
            id(node) not in self._in_all_target
            and len(node.elts) > MAX_INLINE_COLLECTION_ELEMENTS
            and not self._is_exempt_data_decoupling()
        ):
            self.ast_violations.append(
                ASTViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    category="data_code_decoupling",
                    message=f"Oversized {kind} literal ({len(node.elts)} elements > {MAX_INLINE_COLLECTION_ELEMENTS}).",
                )
            )
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:
        self._check_collection_size(node, "list")

    def visit_Set(self, node: ast.Set) -> None:
        self._check_collection_size(node, "set")

    def visit_Tuple(self, node: ast.Tuple) -> None:
        self._check_collection_size(node, "tuple")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "connect" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "sqlite3" and not self._is_allowed_sqlite_path():
                    self.ast_violations.append(
                        ASTViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            category="raw_sqlite_banned",
                            message="Direct sqlite3.connect() prohibited outside storage/. Use storage.connection.",
                        )
                    )
        self.generic_visit(node)

    def _is_allowed_sqlite_path(self) -> bool:
        normalized = self.rel_path.replace("\\", "/")
        return (
            normalized in ALLOWED_SQLITE_FILES
            or any(normalized.startswith(pfx) for pfx in ALLOWED_SQLITE_PREFIXES)
            or _is_test_file(normalized)
        )

    def _is_exempt_data_decoupling(self) -> bool:
        if self._is_pure_declarative:
            return True
        normalized = self.rel_path.replace("\\", "/")
        if _is_test_file(normalized):
            return True
        return any(normalized.startswith(pfx) for pfx in EXEMPT_DATA_DECOUPLING_PREFIXES)


def get_staged_diff_line_ranges(repo_root: Path, file_rel_path: str) -> List[Tuple[int, int]]:
    """Extracts (start_line, end_line) ranges added or modified in the staged git diff."""
    ranges: List[Tuple[int, int]] = []
    try:
        diff_out = subprocess.check_output(
            ["git", "diff", "--cached", "-U0", "--", file_rel_path],
            cwd=str(repo_root),
            env=_sanitized_git_env(),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in diff_out.splitlines():
            if line.startswith("@@ "):
                m = re.search(r"\+([0-9]+)(?:,([0-9]+))?", line)
                if m:
                    start = int(m.group(1))
                    count = int(m.group(2)) if m.group(2) is not None else 1
                    if count == 0:
                        continue
                    end = start + count - 1
                    ranges.append((start, end))
    except Exception:
        pass
    return ranges


def _resolve_head_path_for_staged_file(repo_root: Path, file_rel_path: str) -> str:
    """Resolve original HEAD path if file_rel_path was renamed in the git index."""
    try:
        status_out = subprocess.check_output(
            ["git", "diff", "--cached", "-M", "--name-status", "--", file_rel_path],
            cwd=str(repo_root),
            env=_sanitized_git_env(),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in status_out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3 and parts[0].startswith("R") and parts[2] == file_rel_path:
                return parts[1]
    except Exception:
        pass
    return file_rel_path


def _load_head_functions(repo_root: Path, file_rel_path: str) -> List[FunctionComplexity]:
    head_rel_path = _resolve_head_path_for_staged_file(repo_root, file_rel_path)
    try:
        head_src = subprocess.check_output(
            ["git", "show", f"HEAD:{head_rel_path}"],
            cwd=str(repo_root),
            env=_sanitized_git_env(),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        head_tree = ast.parse(head_src, filename=head_rel_path)
        visitor = SinglePassASTVisitor(head_rel_path, head_src)
        visitor.visit(head_tree)
        return visitor.functions
    except Exception:
        return []


def get_head_function_complexity(repo_root: Path, file_rel_path: str) -> Dict[str, int]:
    """Computes cognitive complexity of functions in HEAD version of file."""
    res: Dict[str, int] = {}
    for func in _load_head_functions(repo_root, file_rel_path):
        if func.qualified_name:
            res[func.qualified_name] = func.cognitive_complexity
        res.setdefault(func.name, func.cognitive_complexity)
    return res


def get_head_function_lines(repo_root: Path, file_rel_path: str) -> Dict[str, int]:
    """Computes effective line count of functions in HEAD version of file."""
    res: Dict[str, int] = {}
    for func in _load_head_functions(repo_root, file_rel_path):
        eff = func.effective_lines or max(1, func.end_line - func.start_line + 1)
        if func.qualified_name:
            res[func.qualified_name] = eff
        res.setdefault(func.name, eff)
    return res


def is_function_touched(func: FunctionComplexity, touched_ranges: List[Tuple[int, int]]) -> bool:
    if not touched_ranges:
        return False
    return any(
        not (t_end < func.start_line or t_start > func.end_line)
        for t_start, t_end in touched_ranges
    )


def _is_test_file(file_rel_path: str) -> bool:
    norm = file_rel_path.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    return (
        norm.startswith("tests/")
        or norm.endswith("_test.py")
        or base == "conftest.py"
        or (base.startswith("test_") and base.endswith(".py"))
    )


def _is_file_excluded(file_rel_path: str) -> bool:
    if any(file_rel_path.startswith(pfx) for pfx in DECLARATIVE_REGISTRY_PREFIXES):
        return True
    if not EXCLUSIONS_PATH.is_file():
        return False
    try:
        from tools.lint_file_budgets import _validate_expiry_date

        excl_data = json.loads(EXCLUSIONS_PATH.read_text(encoding="utf-8"))
        for pfx in excl_data.get("exempt_prefixes", []):
            if file_rel_path.startswith(pfx):
                return True
        for ex in excl_data.get("exclusions", []):
            if ex.get("path") == file_rel_path:
                valid_exp, _ = _validate_expiry_date(file_rel_path, ex)
                return valid_exp
    except Exception:
        pass
    return False


def _check_budget_and_ratchet(
    file_rel_path: str,
    line_count: int,
    baselines: Dict[str, int],
    source: str = "",
) -> List[str]:
    violations: List[str] = []
    if _is_test_file(file_rel_path):
        return violations
    first_segment = file_rel_path.split("/")[0]
    if first_segment not in GOVERNED_DIRS:
        return violations
    if any(file_rel_path.startswith(pfx) for pfx in DECLARATIVE_REGISTRY_PREFIXES):
        return violations
    if EXCLUSIONS_PATH.is_file():
        try:
            excl_data = json.loads(EXCLUSIONS_PATH.read_text(encoding="utf-8"))
            if any(file_rel_path.startswith(pfx) for pfx in excl_data.get("exempt_prefixes", [])):
                return violations
        except Exception:
            pass

    if file_rel_path in baselines:
        base_limit = baselines[file_rel_path]
        if line_count > base_limit:
            violations.append(
                f"[FILE BUDGET RATCHET] {file_rel_path}: line count grew ({line_count} > baseline {base_limit}, +{line_count - base_limit} lines)."
            )
        return violations

    if line_count <= DEFAULT_MAX_LINES and not _is_file_excluded(file_rel_path):
        return violations

    from tools.file_budget_core import evaluate_module_source
    from tools.file_budget_core.policy import _load_cached_exclusions

    verdict = evaluate_module_source(
        code=source,
        rel_path=file_rel_path,
        exclusions_dict=_load_cached_exclusions(ROOT),
        baseline_dict=baselines,
    )
    if verdict.is_blocking:
        if verdict.classification == "complexity_violation" and line_count <= 650:
            from tools.lint_cognitive_complexity import is_excluded as is_cc_excluded
            from tools.shift_left_core.cognitive import is_module_complexity_baselined

            if is_cc_excluded(file_rel_path) or is_module_complexity_baselined(file_rel_path, verdict.max_cc):
                return violations
        violations.append(f"[FILE BUDGET] {file_rel_path}: {verdict.reason}")
    return violations


def _read_staged_or_disk_source(repo_root: Path, file_rel_path: str, diff_scoped: bool) -> Optional[str]:
    """Read staged index blob when diff_scoped=True and available, falling back to working-tree disk."""
    file_path = repo_root / file_rel_path
    if diff_scoped:
        try:
            staged_src = subprocess.check_output(
                ["git", "show", f":{file_rel_path}"],
                cwd=str(repo_root),
                env=_sanitized_git_env(),
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return staged_src
        except Exception:
            pass
    if file_path.is_file():
        return file_path.read_text(encoding="utf-8", errors="ignore")
    return None


def _count_source_lines(source: str) -> int:
    if not source:
        return 0
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def _check_function_complexity(
    file_rel_path: str,
    visitor: SinglePassASTVisitor,
    diff_scoped: bool,
    repo_root: Path,
) -> List[str]:
    violations: List[str] = []
    if _is_test_file(file_rel_path) or _is_file_excluded(file_rel_path):
        return violations
    cc_baseline: Dict[str, int] = {}
    try:
        from tools.lint_cognitive_complexity import (
            _lookup_baseline_limit,
            is_excluded as is_cc_excluded,
            load_complexity_baseline,
        )
        if is_cc_excluded(file_rel_path):
            return violations
        local_cc_path = repo_root / "reports" / "cognitive_complexity_baseline.json"
        if local_cc_path.is_file():
            cc_baseline = load_complexity_baseline(local_cc_path)
    except ImportError:
        _lookup_baseline_limit = lambda _r, _q, _b: None  # noqa: E731

    touched_ranges = get_staged_diff_line_ranges(repo_root, file_rel_path) if diff_scoped else []
    head_complexities = (
        get_head_function_complexity(repo_root, file_rel_path) if diff_scoped else {}
    )
    head_lines = (
        get_head_function_lines(repo_root, file_rel_path) if diff_scoped else {}
    )

    for func in visitor.functions:
        if diff_scoped and not is_function_touched(func, touched_ranges):
            continue

        lookup_key = func.qualified_name if (func.qualified_name and func.qualified_name in head_complexities) else func.name
        base_limit = _lookup_baseline_limit(file_rel_path, func.qualified_name or func.name, cc_baseline)
        effective_limit = (
            max(DEFAULT_MAX_COMPLEXITY, base_limit)
            if base_limit is not None
            else DEFAULT_MAX_COMPLEXITY
        )
        if func.cognitive_complexity > DEFAULT_MAX_COMPLEXITY:
            if diff_scoped and lookup_key in head_complexities:
                head_cog = head_complexities[lookup_key]
                if func.cognitive_complexity > head_cog:
                    violations.append(
                        f"[COMPLEXITY REGRESSION] {file_rel_path}:{func.start_line}: Touched function '{func.name}' cognitive complexity worsened ({head_cog} -> {func.cognitive_complexity} > {DEFAULT_MAX_COMPLEXITY})."
                    )
            elif func.cognitive_complexity > effective_limit:
                violations.append(
                    f"[COMPLEXITY VIOLATION] {file_rel_path}:{func.start_line}: Function '{func.name}' cognitive complexity ({func.cognitive_complexity}) exceeds ceiling of {effective_limit}."
                )

        eff_lines = func.effective_lines or max(1, func.end_line - func.start_line + 1)
        if diff_scoped and not func.is_fixture and eff_lines > DEFAULT_MAX_FUNCTION_LINES:
            line_key = func.qualified_name if (func.qualified_name and func.qualified_name in head_lines) else func.name
            if line_key in head_lines:
                prev_lines = head_lines[line_key]
                if eff_lines > max(DEFAULT_MAX_FUNCTION_LINES, prev_lines):
                    violations.append(
                        f"[FUNCTION LENGTH REGRESSION] {file_rel_path}:{func.start_line}: Touched function '{func.name}' effective length grew ({prev_lines} -> {eff_lines} > {DEFAULT_MAX_FUNCTION_LINES} lines)."
                    )
            else:
                violations.append(
                    f"[FUNCTION LENGTH VIOLATION] {file_rel_path}:{func.start_line}: Function '{func.name}' effective length ({eff_lines} lines) exceeds ceiling of {DEFAULT_MAX_FUNCTION_LINES} lines."
                )
    return violations


def _check_layer_architecture(
    file_rel_path: str,
    source: str,
    visitor: SinglePassASTVisitor,
    tree: Optional[ast.AST] = None,
    repo_root: Optional[Path] = None,
) -> List[str]:
    violations: List[str] = []
    if _is_test_file(file_rel_path):
        return violations
    from tools.lint_layer_imports import LayerImportVisitor, get_layer_for_file

    parsed_tree = tree if tree is not None else ast.parse(source, filename=file_rel_path)
    effective_root = repo_root if (repo_root is not None and (repo_root / "dual_pipeline").exists()) else ROOT
    layer_visitor = LayerImportVisitor(
        rel_path=file_rel_path,
        file_lines=source.splitlines(),
        repo_root=effective_root,
    )
    layer_visitor.visit(parsed_tree)
    seen_lines: set[int] = set()
    for v in layer_visitor.violations:
        seen_lines.add(v.line_number)
        violations.append(
            f"[LAYER VIOLATION] {v.file_path}:{v.line_number}: [{v.rule}] {v.detail}"
        )

    src_layer = get_layer_for_file(file_rel_path)
    if src_layer is not None:
        lines = source.splitlines()
        for lineno, imp_mod in visitor.imports:
            if lineno in seen_lines:
                continue
            target_layer = get_layer_for_file(imp_mod.replace(".", "/") + ".py")
            if target_layer is not None and target_layer > src_layer:
                line_text = lines[lineno - 1] if lineno <= len(lines) else ""
                if "# noqa: lazy-decouple" not in line_text:
                    seen_lines.add(lineno)
                    violations.append(
                        f"[LAYER VIOLATION] {file_rel_path}:{lineno}: Prohibited upward import L{src_layer} -> L{target_layer} ({imp_mod})."
                    )
    return violations


def analyze_shift_left_file(
    repo_root: Path,
    file_rel_path: str,
    diff_scoped: bool = True,
    baseline_modules: Optional[Dict[str, int]] = None,
) -> Tuple[bool, List[str]]:
    """Analyzes a Python file against shift-left AST, complexity, budget, and layer invariants."""
    if not file_rel_path.endswith(".py"):
        return True, []

    source = _read_staged_or_disk_source(repo_root, file_rel_path, diff_scoped=diff_scoped)
    if source is None:
        return True, []

    file_path = repo_root / file_rel_path
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        return False, [f"SyntaxError in {file_rel_path}:{e.lineno}: {e.msg}"]
    except Exception as e:
        return False, [f"Failed to parse {file_rel_path}: {e}"]

    violations: List[str] = []

    # 1. Budget & Ratchet Check
    line_count = _count_source_lines(source) if diff_scoped else count_lines(file_path)
    baselines = (
        baseline_modules
        if baseline_modules is not None
        else load_ratchet_baseline(BASELINE_PATH).get("modules", {})
    )
    violations.extend(_check_budget_and_ratchet(file_rel_path, line_count, baselines, source=source))

    # 2. Single-Pass AST Inspection
    from tools.file_budget_core.policy import is_ast_pure_declarative

    visitor = SinglePassASTVisitor(
        file_rel_path,
        source,
        is_pure_declarative=is_ast_pure_declarative(tree),
    )
    visitor.scan_docstrings(tree)
    visitor.visit(tree)
    for v in visitor.ast_violations:
        violations.append(f"[{v.category.upper()}] {v.file_path}:{v.line_number}: {v.message}")

    # 3. Diff-Scoped Function Complexity & Length
    violations.extend(_check_function_complexity(file_rel_path, visitor, diff_scoped, repo_root))

    # 4. Layer Architecture Check
    violations.extend(_check_layer_architecture(file_rel_path, source, visitor, tree=tree, repo_root=repo_root))

    return len(violations) == 0, violations
