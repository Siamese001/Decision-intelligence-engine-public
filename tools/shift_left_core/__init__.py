"""
tools/shift_left_core

Shift-left pre-commit gatekeeper package.
Provides single-pass AST visitor, complexity enforcement, monotonic ratchet, and latency benchmark.
"""

from __future__ import annotations

from .ast_framework import (
    DEFAULT_MAX_COMPLEXITY,
    DEFAULT_MAX_LINES,
    ASTViolation,
    CognitiveWalker,
    FunctionComplexity,
    SinglePassASTVisitor,
    analyze_shift_left_file,
    get_head_function_complexity,
    get_staged_diff_line_ranges,
    is_function_touched,
)
from .benchmark import run_benchmark
from .runner import check_staged_shift_left

__all__ = [
    "DEFAULT_MAX_COMPLEXITY",
    "DEFAULT_MAX_LINES",
    "ASTViolation",
    "CognitiveWalker",
    "FunctionComplexity",
    "SinglePassASTVisitor",
    "analyze_shift_left_file",
    "get_head_function_complexity",
    "get_staged_diff_line_ranges",
    "is_function_touched",
    "check_staged_shift_left",
    "run_benchmark",
]
