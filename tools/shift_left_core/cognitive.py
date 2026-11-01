"""
tools/shift_left_core/cognitive.py

Canonical Sonar-compliant Cognitive Complexity calculator using non-mutating AST visitation.
Shared across shift-left pre-commit gates, standalone complexity linting, and MMI file budgets.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple


class ComplexityContribution(NamedTuple):
    line_number: int
    increment: int
    reason: str


class CognitiveComplexityVisitor(ast.NodeVisitor):
    """Calculates Sonar-compliant Cognitive Complexity for a single function AST without mutating nodes."""

    def __init__(self, function_name: str, root_node: Optional[ast.AST] = None):
        self.function_name = function_name
        self.root_name = function_name
        self.root_node = root_node
        self.complexity = 0
        self.nesting_level = 0
        self.prev_bool_op: Optional[type] = None
        self._elif_nodes: Set[int] = set()
        self.contributions: List[ComplexityContribution] = []

    def _add_complexity(self, line_number: int, increment: int, reason: str) -> None:
        self.complexity += increment
        self.contributions.append(
            ComplexityContribution(line_number=line_number, increment=increment, reason=reason)
        )

    def _is_nested_function(self, node: ast.AST) -> bool:
        if self.root_node is not None:
            return node is not self.root_node
        return getattr(node, "name", "") != self.function_name.split(".")[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._is_nested_function(node):
            self.nesting_level += 1
            self.generic_visit(node)
            self.nesting_level -= 1
        else:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self._is_nested_function(node):
            self.nesting_level += 1
            self.generic_visit(node)
            self.nesting_level -= 1
        else:
            self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        is_elif = id(node) in self._elif_nodes
        if is_elif:
            self._add_complexity(node.lineno, 1, "elif branch (+1)")
        else:
            inc = 1 + self.nesting_level
            reason = (
                f"if branch (+1, nesting +{self.nesting_level})"
                if self.nesting_level
                else "if branch (+1)"
            )
            self._add_complexity(node.lineno, inc, reason)

        self.visit(node.test)

        self.nesting_level += 1
        for stmt in node.body:
            self.visit(stmt)
        self.nesting_level -= 1

        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                self._elif_nodes.add(id(node.orelse[0]))
                self.visit(node.orelse[0])
            else:
                else_line = getattr(node.orelse[0], "lineno", node.lineno)
                self._add_complexity(else_line, 1, "else branch (+1)")
                self.nesting_level += 1
                for stmt in node.orelse:
                    self.visit(stmt)
                self.nesting_level -= 1

    def visit_IfExp(self, node: ast.IfExp) -> None:
        inc = 1 + self.nesting_level
        reason = (
            f"conditional expression (+1, nesting +{self.nesting_level})"
            if self.nesting_level
            else "conditional expression (+1)"
        )
        self._add_complexity(node.lineno, inc, reason)
        self.generic_visit(node)

    def _visit_loop_or_block(self, node: ast.AST, label: str) -> None:
        inc = 1 + self.nesting_level
        reason = (
            f"{label} (+1, nesting +{self.nesting_level})"
            if self.nesting_level
            else f"{label} (+1)"
        )
        self._add_complexity(getattr(node, "lineno", 0), inc, reason)
        self.nesting_level += 1
        self.generic_visit(node)
        self.nesting_level -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop_or_block(node, "for loop")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop_or_block(node, "async for loop")

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop_or_block(node, "while loop")

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._visit_loop_or_block(node, "except handler")

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_loop_or_block(node, "match statement")

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        op_type = type(node.op)
        if self.prev_bool_op is None or self.prev_bool_op != op_type:
            self._add_complexity(node.lineno, 1, "boolean operator chain (+1)")
            self.prev_bool_op = op_type

        for val in node.values:
            self.visit(val)

        self.prev_bool_op = None

    def visit_Call(self, node: ast.Call) -> None:
        bare_name = self.function_name.split(".")[-1]
        is_direct_recursion = isinstance(node.func, ast.Name) and node.func.id == self.function_name
        is_method_recursion = (
            "." in self.function_name
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("self", "cls")
            and node.func.attr == bare_name
        )
        if is_direct_recursion or is_method_recursion:
            self._add_complexity(node.lineno, 1, "recursive call (+1)")
        self.generic_visit(node)


CognitiveWalker = CognitiveComplexityVisitor


def evaluate_function_complexity(
    func_node: ast.AST, name: Optional[str] = None
) -> Tuple[int, List[ComplexityContribution]]:
    """Computes cognitive complexity score and line-level contributions for an AST function node."""
    resolved_name = name or getattr(func_node, "name", "")
    visitor = CognitiveComplexityVisitor(resolved_name, root_node=func_node)
    visitor.visit(func_node)
    return visitor.complexity, visitor.contributions


def calculate_function_complexity(node: ast.AST, name: Optional[str] = None) -> int:
    """Calculates the cognitive complexity of a function node."""
    score, _ = evaluate_function_complexity(node, name)
    return score


def compute_cognitive_complexity(func_node: ast.AST) -> int:
    """Computes cognitive complexity score for an AST function node."""
    return calculate_function_complexity(func_node, getattr(func_node, "name", ""))


def collect_function_nodes(
    tree: ast.AST, skip_dunder: bool = False
) -> List[Tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Collects top-level functions and class methods without double-counting nested closures."""
    results: List[Tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def _walk_scope(stmts: list[ast.stmt], class_prefix: str = "") -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.ClassDef):
                next_prefix = f"{class_prefix}.{stmt.name}" if class_prefix else stmt.name
                _walk_scope(stmt.body, next_prefix)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if skip_dunder and stmt.name.startswith("__") and stmt.name.endswith("__"):
                    continue
                qual_name = f"{class_prefix}.{stmt.name}" if class_prefix else stmt.name
                results.append((qual_name, stmt))

    body = getattr(tree, "body", None)
    if isinstance(body, list):
        _walk_scope(body)
    return results


def check_complexity_baseline_inflation(path: Path) -> List[str]:
    """Checks if local cognitive_complexity_baseline.json inflates any function limit vs main."""
    if not path.is_file():
        return []
    try:
        local_data = json.loads(path.read_text(encoding="utf-8"))
        local_funcs = local_data.get("functions", {}) if isinstance(local_data, dict) else {}
    except Exception:
        return ["Malformed cognitive_complexity_baseline.json"]

    from tools.file_budget_core.ratchet import detect_numeric_map_inflation, read_git_main_json

    main_data = read_git_main_json(path)
    if main_data and isinstance(main_data.get("functions"), dict):
        return detect_numeric_map_inflation(
            local_funcs, main_data["functions"], "Cognitive complexity baseline"
        )
    return []


def load_complexity_baseline(path: Path) -> Dict[str, int]:
    """Loads the monotonic function-level cognitive complexity baseline dictionary pinned to main."""
    local_funcs: Dict[str, int] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            funcs = data.get("functions", {})
            if isinstance(funcs, dict):
                local_funcs = {str(k): int(v) for k, v in funcs.items()}
        except Exception:
            pass

    from tools.file_budget_core.ratchet import read_git_main_json

    main_data = read_git_main_json(path)
    if main_data and isinstance(main_data.get("functions"), dict):
        main_funcs = {str(k): int(v) for k, v in main_data["functions"].items()}
        return {
            k: min(local_funcs.get(k, mv), mv)
            for k, mv in main_funcs.items()
        }
    return local_funcs


def lookup_baseline_limit(
    rel_path: str,
    qual_name: str,
    baseline_functions: Optional[Dict[str, int]],
) -> Optional[int]:
    """Looks up the baselined max complexity limit for a function by qualified or bare name."""
    if not baseline_functions:
        return None
    exact_key = f"{rel_path}::{qual_name}"
    if exact_key in baseline_functions:
        return baseline_functions[exact_key]
    bare_name = qual_name.split(".")[-1]
    bare_key = f"{rel_path}::{bare_name}"
    return baseline_functions.get(bare_key)


def format_top_contributions(contribs: Sequence[ComplexityContribution], top_n: int = 5) -> str:
    """Formats the top N line-level complexity contributions for diagnostic output."""
    if not contribs:
        return ""
    ranked = sorted(contribs, key=lambda c: (-c.increment, c.line_number))[:top_n]
    parts = [f"L{c.line_number}: +{c.increment} ({c.reason})" for c in ranked]
    if len(contribs) > top_n:
        parts.append(f"... +{len(contribs) - top_n} more")
    return " | ".join(parts)


def is_module_complexity_baselined(
    rel_path: str,
    max_cc: int,
    baseline_path: Optional[Path] = None,
) -> bool:
    """Returns True if the module's max cognitive complexity is within its monotonic baseline."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    reports_path = repo_root / "reports" / "cognitive_complexity_baseline.json"
    config_path = repo_root / "config" / "governance" / "cognitive_complexity_baseline.json"
    path = baseline_path or (reports_path if reports_path.is_file() else config_path)
    baseline_functions = load_complexity_baseline(path)
    if not baseline_functions:
        return False
    prefix = f"{rel_path.replace(chr(92), '/')}::"
    module_limits = [limit for key, limit in baseline_functions.items() if key.startswith(prefix)]
    return bool(module_limits) and max_cc <= max(module_limits)

