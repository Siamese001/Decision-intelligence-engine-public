"""Test suite verifying L0 downward-only dependency direction and 400-line module budgets."""
from __future__ import annotations

import ast
from pathlib import Path

L0_DIR = Path(__file__).resolve().parents[2] / "dual_pipeline" / "l0"


def test_l0_modules_zero_upward_dependencies():
    """Verifies that no file in dual_pipeline/l0 imports higher layers L1-L6."""
    assert L0_DIR.exists(), f"L0 directory {L0_DIR} must exist."

    py_files = list(L0_DIR.glob("*.py"))
    assert len(py_files) >= 5, "Expected at least 5 Python modules in dual_pipeline/l0."

    forbidden_patterns = [
        "pipeline_a_intake",
        "pipeline_b_extraction",
        "pipeline_c_retrieval",
        "dual_pipeline.decision",
        "dual_pipeline.retrieval",
        "dual_pipeline.uwg",
        "dual_pipeline.index",
        "dual_pipeline.query",
        "dual_pipeline.transcript",
        "app",
    ]

    for py_file in py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()
            tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_patterns:
                        assert not alias.name.startswith(forbidden), (
                            f"Layer inversion: {py_file.name} imports higher-layer '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in forbidden_patterns:
                    assert not mod.startswith(forbidden), (
                        f"Layer inversion: {py_file.name} imports higher-layer '{mod}'"
                    )


def test_l0_modules_400_line_budget():
    """Verifies that every module in dual_pipeline/l0 complies with the 400-line budget."""
    py_files = list(L0_DIR.glob("*.py"))
    for py_file in py_files:
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        assert line_count <= 400, (
            f"Module {py_file.name} has {line_count} lines, exceeding the 400-line budget."
        )
