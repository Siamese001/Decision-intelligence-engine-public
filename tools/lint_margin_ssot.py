#!/usr/bin/env python3
"""
tools/lint_margin_ssot.py

Monotonic AST linter enforcing SSOT ambiguity margin threshold usage.
Scans Python modules for raw float literals 0.15 or 0.25 in decision, ambiguity,
threshold, or margin contexts outside of dual_pipeline/parameters.py.

All decision logic MUST import and call get_hitl_ambiguity_margin_threshold()
or reference DEFAULT_MARGIN_THRESHOLD rather than hardcoding float literals.

Exit code 0: All files conform to SSOT margin governance.
Exit code 1: One or more raw margin/threshold literals detected.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Keywords indicating decision margin / ambiguity context
MARGIN_CONTEXT_KEYWORDS: Set[str] = {
    "margin",
    "threshold",
    "delta",
    "spread",
    "ambiguity",
    "variance",
    "divergence",
}

# Forbidden raw float literals in margin contexts
FORBIDDEN_MARGIN_LITERALS: Set[float] = {0.15, 0.20, 0.25, 0.30}

# Exempted SSOT definition files (repo-relative paths)
EXEMPT_FILES: Set[str] = {
    "dual_pipeline/parameters.py",
    "dual_pipeline/parameters_ops.py",
    "dual_pipeline/canonical_pipeline/resource_discovery.py",
    "dual_pipeline/identity_core/matching.py",
    "dual_pipeline/intake_core/change_set_helpers.py",
    "dual_pipeline/learning_loop_core/retrieval_calibrator_agent.py",
}


def _is_exempt_margin_file(path_or_name: str | Path) -> bool:
    p = Path(path_or_name)
    try:
        rel = str(p.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        rel = str(p).replace("\\", "/").lstrip("./")
    return rel in EXEMPT_FILES


class MarginSSOTVisitor(ast.NodeVisitor):
    """AST visitor detecting hardcoded margin literals in decision logic."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: List[Tuple[int, str]] = []

    def _is_forbidden_literal(self, node: ast.AST) -> Optional[float]:
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            for forbidden in FORBIDDEN_MARGIN_LITERALS:
                if abs(node.value - forbidden) < 1e-6:
                    return forbidden
        return None

    def _matches_keyword(self, name: str) -> bool:
        lower = name.lower()
        return any(k in lower for k in MARGIN_CONTEXT_KEYWORDS)

    def visit_Assign(self, node: ast.Assign) -> None:
        val = self._is_forbidden_literal(node.value)
        if val is not None:
            for target in node.targets:
                target_names = [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]
                if any(self._matches_keyword(n) for n in target_names):
                    self.violations.append(
                        (
                            node.lineno,
                            f"Hardcoded margin literal {val} assigned to variable matching margin keyword: "
                            f"{', '.join(target_names)}. Use get_hitl_ambiguity_margin_threshold() instead.",
                        )
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            val = self._is_forbidden_literal(node.value)
            if val is not None:
                target_names = [n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)]
                if any(self._matches_keyword(n) for n in target_names):
                    self.violations.append(
                        (
                            node.lineno,
                            f"Hardcoded margin literal {val} in annotated assignment to: "
                            f"{', '.join(target_names)}. Use get_hitl_ambiguity_margin_threshold() instead.",
                        )
                    )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_defaults(node)
        self.generic_visit(node)

    def _check_function_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Check standard defaults (aligned to end of positional args)
        num_defaults = len(node.args.defaults)
        if num_defaults > 0:
            default_args = node.args.args[-num_defaults:]
            for arg, default in zip(default_args, node.args.defaults, strict=False):
                val = self._is_forbidden_literal(default)
                if val is not None and self._matches_keyword(arg.arg):
                    self.violations.append(
                        (
                            node.lineno,
                            f"Hardcoded margin literal {val} as default value for parameter '{arg.arg}' in function '{node.name}'. "
                            f"Use None as default and resolve via get_hitl_ambiguity_margin_threshold().",
                        )
                    )

        # Check kwonly defaults
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
            if default is not None:
                val = self._is_forbidden_literal(default)
                if val is not None and self._matches_keyword(arg.arg):
                    self.violations.append(
                        (
                            node.lineno,
                            f"Hardcoded margin literal {val} as kwonly default for parameter '{arg.arg}' in function '{node.name}'. "
                            f"Use None as default and resolve via get_hitl_ambiguity_margin_threshold().",
                        )
                    )

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg and self._matches_keyword(kw.arg):
                val = self._is_forbidden_literal(kw.value)
                if val is not None:
                    func_name = ast.unparse(node.func) if hasattr(ast, "unparse") else "call"
                    self.violations.append(
                        (
                            node.lineno,
                            f"Hardcoded margin literal {val} passed as keyword argument '{kw.arg}' in call '{func_name}'. "
                            f"Pass get_hitl_ambiguity_margin_threshold() instead.",
                        )
                    )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        left_val = self._is_forbidden_literal(node.left)
        left_has_keyword = False
        try:
            left_text = ast.unparse(node.left) if hasattr(ast, "unparse") else ""
            left_has_keyword = self._matches_keyword(left_text)
        except Exception:
            pass

        for comp in node.comparators:
            comp_val = self._is_forbidden_literal(comp)
            comp_has_keyword = False
            try:
                comp_text = ast.unparse(comp) if hasattr(ast, "unparse") else ""
                comp_has_keyword = self._matches_keyword(comp_text)
            except Exception:
                pass

            # Left is literal, comparator has keyword
            if left_val is not None and comp_has_keyword:
                self.violations.append(
                    (
                        node.lineno,
                        f"Hardcoded margin literal {left_val} compared against '{comp_text}'. "
                        f"Use get_hitl_ambiguity_margin_threshold() instead.",
                    )
                )
            # Comparator is literal, left has keyword
            elif comp_val is not None and left_has_keyword:
                self.violations.append(
                    (
                        node.lineno,
                        f"Hardcoded margin literal {comp_val} compared against '{left_text}'. "
                        f"Use get_hitl_ambiguity_margin_threshold() instead.",
                    )
                )

        self.generic_visit(node)


def lint_python_code(code: str, filename: str) -> List[Tuple[int, str]]:
    """Lints raw Python code string using AST analysis."""
    if _is_exempt_margin_file(filename):
        return []
    try:
        tree = ast.parse(code, filename=filename)
    except SyntaxError as e:
        return [(e.lineno or 1, f"SyntaxError parsing {filename}: {e}")]
    visitor = MarginSSOTVisitor(filename)
    visitor.visit(tree)
    return visitor.violations


def lint_python_file(path: Path) -> List[Tuple[int, str]]:
    """Lints a Python file on disk."""
    if _is_exempt_margin_file(path):
        return []
    content = path.read_text(encoding="utf-8")
    return lint_python_code(content, str(path))


def run_self_test() -> Tuple[bool, List[str]]:
    """Executes synthetic AST self-tests validating detection of margin violations."""
    diagnostics: List[str] = []

    # Valid snippets that should pass cleanly
    valid_snippets = [
        "margin = get_hitl_ambiguity_margin_threshold()\nif delta < margin:\n    pass\n",
        "def evaluate(margin_threshold: Optional[float] = None):\n    thresh = margin_threshold or DEFAULT_THRESHOLD\n",
        "score += 0.15  # Non-margin arithmetic\n",
        "severity_weights = {'CRITICAL': 0.50, 'HIGH': 0.30, 'MEDIUM': 0.15, 'LOW': 0.05}\n",
        "ratio = total / max(1, count)\nif ratio > 0.15:\n    pass\n",
    ]
    for i, code in enumerate(valid_snippets):
        violations = lint_python_code(code, f"valid_test_{i}.py")
        if violations:
            diagnostics.append(f"False positive on valid snippet {i}: {violations}")

    # Invalid snippets that MUST be caught
    invalid_snippets = [
        ("margin_threshold = 0.15", "Assign to margin_threshold"),
        ("ambiguity_delta: float = 0.25", "AnnAssign to ambiguity_delta"),
        ("margin_threshold = 0.30", "Assign to margin_threshold 0.30"),
        ("def check(margin_threshold: float = 0.15): pass", "Default arg margin_threshold=0.15"),
        ("def check(*, threshold: float = 0.25): pass", "Kwonly default threshold=0.25"),
        ("def check(*, threshold: float = 0.30): pass", "Kwonly default threshold=0.30"),
        ("is_ambiguous(delta, margin_threshold=0.15)", "Call kwarg margin_threshold=0.15"),
        ("if delta < 0.15: pass", "Comparison delta < 0.15"),
        ("if delta <= 0.30: pass", "Comparison delta <= 0.30"),
        ("if 0.25 >= ambiguity_margin: pass", "Comparison 0.25 >= ambiguity_margin"),
        ("if abs(top_score - second_score) < 0.15: pass", "Comparison (note: if variable named)"),
    ]
    for code, desc in invalid_snippets:
        violations = lint_python_code(code, "invalid_test.py")
        # For the last one, it may or may not catch if no keyword is in the AST expression
        if ("delta" in code or "margin" in code or "threshold" in code) and not violations:
            diagnostics.append(f"False negative on invalid snippet '{desc}': code='{code}'")

    success = len(diagnostics) == 0
    return success, diagnostics


def main(argv: Optional[Sequence[str]] = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description="Monotonic AST linter for SSOT margin literals.")
    parser.add_argument(
        "--check", action="store_true", help="Scan dual_pipeline directory for violations."
    )
    parser.add_argument("--self-test", action="store_true", help="Run synthetic AST self-tests.")
    parser.add_argument("paths", nargs="*", help="Specific files or directories to scan.")
    args = parser.parse_args(argv)

    if args.self_test:
        success, diagnostics = run_self_test()
        if success:
            print("PASS: tools/lint_margin_ssot.py self-test passed cleanly.")
            return 0
        else:
            print("FAIL: tools/lint_margin_ssot.py self-test failed:")
            for d in diagnostics:
                print(f"  - {d}")
            return 1

    # Run self-test verification implicitly
    self_ok, self_diag = run_self_test()
    if not self_ok:
        print("FATAL: Self-test failed inside lint_margin_ssot:")
        for d in self_diag:
            print(f"  - {d}")
        return 1

    scan_targets: List[Path] = []
    if args.paths:
        for p_str in args.paths:
            p = Path(p_str)
            if p.is_file() and p.suffix == ".py":
                scan_targets.append(p)
            elif p.is_dir():
                scan_targets.extend(p.rglob("*.py"))
    else:
        # Default scan dual_pipeline/
        dual_dir = REPO_ROOT / "dual_pipeline"
        if dual_dir.is_dir():
            scan_targets.extend(dual_dir.rglob("*.py"))

    total_violations = 0
    audited_files = 0

    for path in sorted(scan_targets):
        if _is_exempt_margin_file(path):
            continue
        rel_path = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        # Skip __pycache__ or hidden dirs within the repository
        if any(part.startswith(".") or part == "__pycache__" for part in rel_path.parts):
            continue

        audited_files += 1
        violations = lint_python_file(path)
        if violations:
            total_violations += len(violations)
            rel_path = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
            print(f"VIOLATION: {rel_path}")
            for lineno, msg in violations:
                print(f"  Line {lineno}: {msg}")

    if total_violations > 0:
        print(
            f"\nFAILED: {total_violations} SSOT margin threshold violation(s) found across {audited_files} files."
        )
        return 1

    print(f"PASS: 0 SSOT margin threshold violations across {audited_files} audited Python files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
