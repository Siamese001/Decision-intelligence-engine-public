"""Unit and adversarial tests for AST discovery scope and dynamic fallback keys (Wave 2)."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from tools.lint_parameter_references import GOVERNED_DIRS, collect_declared_keys
from tools.ssot_config_engine.ast_rules import (
    GOVERNED_PARAMETER_FALLBACK_KEYS,
    UnifiedSSOTASTVisitor,
)
from tools.ssot_config_engine.discovery import (
    DEFAULT_GOVERNED_DIRS,
    discover_governed_python_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSSOTASTScopeExpansion(unittest.TestCase):
    """Verify expanded directory discovery and dynamic governed fallback key enforcement."""

    def test_governed_dirs_include_config_and_contracts(self) -> None:
        for required in ("config", "contracts", "scripts", "dual_pipeline", "storage", "tools"):
            self.assertIn(required, DEFAULT_GOVERNED_DIRS)
            self.assertIn(required, GOVERNED_DIRS)

    def test_discover_governed_python_files_includes_config_and_contracts(self) -> None:
        discovered = discover_governed_python_files(REPO_ROOT)
        rels = {str(p.relative_to(REPO_ROOT)).replace("\\", "/") for p in discovered}
        self.assertIn("config/credentials.py", rels)
        self.assertIn("contracts/dual_pipeline/graph_contracts.py", rels)

    def test_governed_parameter_fallback_keys_dynamically_cover_parameters_json(self) -> None:
        raw = json.loads((REPO_ROOT / "config" / "runtime" / "parameters.json").read_text(encoding="utf-8"))["parameters"]
        declared = {k.lower() for k in collect_declared_keys(raw)}
        self.assertGreaterEqual(len(declared), 200)
        for key in declared:
            self.assertIn(key, GOVERNED_PARAMETER_FALLBACK_KEYS)

    def test_ast_visitor_flags_dotted_and_leaf_parameter_fallbacks(self) -> None:
        code = (
            "def bad_fn(cfg):\n"
            "    a = cfg.get('engineering_output.max_words', 300)\n"
            "    b = cfg.get('delivery_max_words', 500)\n"
            "    c = cfg.get('max_plan_words', 1000)\n"
            "    return a, b, c\n"
        )
        visitor = UnifiedSSOTASTVisitor(rel_path="dual_pipeline/sample.py", lines=code.splitlines())
        visitor.visit(ast.parse(code))
        rules = [v.rule for v in visitor.violations]
        self.assertEqual(rules, ["HARDCODED_PARAMETER_FALLBACK"] * 3)


if __name__ == "__main__":
    unittest.main()
