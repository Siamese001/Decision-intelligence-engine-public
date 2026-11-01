"""Wave 2 tests: Consolidate AST Walks, Complexity Checks, and Import/SQLite Linters."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from tools import (
    lint_ast_invariants,
    lint_cognitive_complexity,
    lint_file_budgets,
    lint_layer_imports,
    lint_no_custom_ingest_scripts,
)


def test_loading_excluded_patterns_triggers_zero_cohesive_ast_parses() -> None:
    """Verify _load_excluded_patterns(verify_ast=False) and lint_cognitive_complexity do not parse cohesive ASTs."""
    with patch("tools.lint_file_budgets.verify_attestations_against_ast") as mock_verify_ast:
        patterns, cohesive = lint_file_budgets._load_excluded_patterns(verify_ast=False)
        assert len(patterns) > 5
        assert len(cohesive) > 0
        assert mock_verify_ast.call_count == 0

    assert lint_cognitive_complexity.EXCLUSIONS_PATH == lint_file_budgets.EXCLUSIONS_PATH


def test_layer_imports_and_custom_ingest_linter_parse_once_per_file(tmp_path: Path) -> None:
    """Verify scan_file_imports reads once and lint_no_custom_ingest_scripts.lint_file parses AST once."""
    pkg = tmp_path / "dual_pipeline"
    pkg.mkdir()
    mod = pkg / "sample.py"
    mod.write_text("import os\nx = 1\n", encoding="utf-8")

    read_count = 0
    orig_read_text = Path.read_text

    def _spy_read(self: Path, *args: object, **kwargs: object) -> str:
        nonlocal read_count
        if self.resolve() == mod.resolve():
            read_count += 1
        return orig_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "read_text", autospec=True, side_effect=_spy_read):
        viols, _targets = lint_layer_imports.scan_file_imports(mod, tmp_path)
        assert viols == []
        assert read_count == 1

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_file = scripts_dir / "helper_tool.py"
    script_file.write_text("import sys\nprint('ok')\n", encoding="utf-8")

    parse_count = 0
    orig_parse = ast.parse

    def _spy_parse(*args: object, **kwargs: object) -> ast.AST:
        nonlocal parse_count
        parse_count += 1
        return orig_parse(*args, **kwargs)  # type: ignore[arg-type]

    with patch("ast.parse", side_effect=_spy_parse):
        errs = lint_no_custom_ingest_scripts.lint_file("scripts/helper_tool.py", tmp_path)
        assert errs == []
        assert parse_count == 1


def test_lint_ast_invariants_check_mode_scans_all_governed_files(capsys: object) -> None:
    """Verify lint_ast_invariants.py --check without file args scans all governed files rather than 0."""
    with (
        patch.object(lint_ast_invariants, "get_staged_python_files", return_value=[]),
        patch.object(lint_ast_invariants, "scan_file", return_value=[]) as mock_scan,
    ):
        rc = lint_ast_invariants.main(["--check"])
        assert rc == 0
        assert mock_scan.call_count > 500
