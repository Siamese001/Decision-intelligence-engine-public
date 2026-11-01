"""
Python AST symbol extraction and touched-symbol analysis.
Complies with Decision D2: Python AST symbol sets; non-Python falls back to hunk-only.
"""
from __future__ import annotations

import ast
from typing import List

from .models import BranchFileSymbols, HunkRange, SymbolSpan


def is_python_file(path_str: str) -> bool:
    """Checks whether the file is a Python source file."""
    return path_str.endswith(".py") or path_str.endswith(".pyi")


class SymbolVisitor(ast.NodeVisitor):
    """AST visitor that collects classes, functions, and methods with line spans."""

    def __init__(self) -> None:
        self.symbols: List[SymbolSpan] = []
        self._scope_stack: List[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        name = node.name
        qualified_name = ".".join(self._scope_stack + [name])
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line)

        parent = self._scope_stack[-1] if self._scope_stack else None
        self.symbols.append(
            SymbolSpan(
                symbol_name=qualified_name,
                symbol_type="class",
                start_line=start_line,
                end_line=end_line,
                parent_symbol=parent,
            )
        )

        self._scope_stack.append(name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, "async_function")

    def _handle_function(self, node: ast.AST, base_type: str) -> None:
        name = getattr(node, "name", "unknown")
        is_method = len(self._scope_stack) > 0
        sym_type = "method" if is_method else base_type
        qualified_name = ".".join(self._scope_stack + [name])
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)

        parent = self._scope_stack[-1] if self._scope_stack else None
        self.symbols.append(
            SymbolSpan(
                symbol_name=qualified_name,
                symbol_type=sym_type,
                start_line=start_line,
                end_line=end_line,
                parent_symbol=parent,
            )
        )

        self._scope_stack.append(name)
        self.generic_visit(node)
        self._scope_stack.pop()


def extract_python_symbols(source_code: str, file_path: str = "") -> List[SymbolSpan]:
    """
    Parses Python source code and extracts all class, function, and method definitions.
    Returns empty list if syntax is invalid or non-Python.
    """
    if file_path and not is_python_file(file_path):
        return []

    try:
        tree = ast.parse(source_code, filename=file_path or "<string>")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return []

    visitor = SymbolVisitor()
    visitor.visit(tree)
    return visitor.symbols


def find_touched_symbols(
    symbols: List[SymbolSpan],
    hunks: List[HunkRange],
    use_new_coords: bool = True,
) -> List[SymbolSpan]:
    """
    Identifies which symbols were touched by any of the given hunks.
    If use_new_coords is True, compares against [new_start, new_end] of target content.
    Otherwise compares against [old_start, old_end] of base content.
    """
    if not symbols or not hunks:
        return []

    touched: List[SymbolSpan] = []
    for sym in symbols:
        is_touched = False
        for h in hunks:
            h_start = h.new_start if use_new_coords else h.old_start
            h_end = h.new_end if use_new_coords else h.old_end

            # Check for range intersection
            if max(sym.start_line, h_start) <= min(sym.end_line, h_end):
                is_touched = True
                break
        if is_touched:
            touched.append(sym)

    return touched


def analyze_file_symbols(
    file_path: str,
    source_content: str,
    hunks: List[HunkRange],
) -> BranchFileSymbols:
    """
    Extracts all symbols from file content and marks touched symbols based on hunks.
    Gracefully falls back to empty symbols for non-Python or unparseable files.
    """
    if not is_python_file(file_path) or not source_content:
        return BranchFileSymbols(path=file_path, all_symbols=[], touched_symbols=[])

    all_symbols = extract_python_symbols(source_content, file_path=file_path)
    touched = find_touched_symbols(all_symbols, hunks, use_new_coords=True)

    return BranchFileSymbols(
        path=file_path,
        all_symbols=all_symbols,
        touched_symbols=touched,
    )
