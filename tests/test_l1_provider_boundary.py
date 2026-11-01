#!/usr/bin/env python3
"""Static Wave 2 boundary checks for the pure controller kernel."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "dual_pipeline" / "l1_control.py"


class L1ProviderBoundaryTests(unittest.TestCase):
    def test_controller_imports_only_pure_stdlib_and_core_primitives(self) -> None:
        source = CONTROL.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertIn(
            imports,
            (
                ["__future__", "dataclasses", "enum", "typing", "core"],
                ["__future__", "dual_pipeline.l1_control_core"],
            ),
        )

    def test_controller_has_no_external_reasoning_or_runtime_side_effect_literals(self) -> None:
        source = CONTROL.read_text(encoding="utf-8").casefold()
        forbidden = (
            "openai", "anthropic", "gemini", "bedrock", "boto", "urllib", "requests",
            "socket", "subprocess", "pathlib", "sqlite", "webbrowser", "http://", "https://",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_kernel_does_not_use_wall_clock_or_filesystem_apis(self) -> None:
        source = CONTROL.read_text(encoding="utf-8")
        for token in ("time.", "datetime", "read_text", "write_text", "open(", "Path("):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
