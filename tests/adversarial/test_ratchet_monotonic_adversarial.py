"""
tests/adversarial/test_ratchet_monotonic_adversarial.py

Adversarial Test Suite for Wave 4 Monotonic Ratchet & Invariant Enforcement.
Verifies rejection of synthetic +1 line growth, upward baseline regeneration,
expired exclusions, and prohibited raw sqlite calls.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from tools.file_budget_core import (
    RatchetMonotonicityViolationError,
    generate_baseline,
)
from tools.lint_file_budgets import validate_exclusion_entry
from tools.shift_left_core import (
    SinglePassASTVisitor,
    analyze_shift_left_file,
)


class TestRatchetMonotonicAdversarial(unittest.TestCase):
    def test_01_synthetic_line_growth_rejected_by_shift_left(self) -> None:
        """Verify that a baselined module with +1 line growth is rejected."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            rel_path = "dual_pipeline/subsystem.py"
            test_file = tmp_path / rel_path
            test_file.parent.mkdir(parents=True, exist_ok=True)

            # Write 51 lines (exceeding baseline of 50 by +1 line)
            content = "\n".join([f"# Line {i}" for i in range(1, 52)])
            test_file.write_text(content, encoding="utf-8")

            mock_baseline = {rel_path: 50}

            ok, violations = analyze_shift_left_file(
                repo_root=tmp_path,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules=mock_baseline,
            )

            self.assertFalse(ok)
            self.assertTrue(
                any("line count grew (51 > baseline 50, +1 lines)" in v for v in violations)
            )

    def test_02_monotonic_ratchet_generation_blocks_upward_growth(self) -> None:
        """Verify that attempting to regenerate baseline with upward line bumps raises RatchetMonotonicityViolationError."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            baseline_file = tmp_path / "baseline.json"
            rel_mod = "dual_pipeline/worker.py"

            # Initial baseline: worker was 100 lines
            initial_data = {
                "schema_version": "1.0.0",
                "modules": {rel_mod: 100},
            }
            baseline_file.write_text(json.dumps(initial_data), encoding="utf-8")

            # Now worker has grown to 120 lines
            worker_file = tmp_path / rel_mod
            worker_file.parent.mkdir(parents=True, exist_ok=True)
            worker_file.write_text(
                "\n".join([f"# Line {i}" for i in range(1, 121)]), encoding="utf-8"
            )

            # Attempting to generate baseline without override must raise RatchetMonotonicityViolationError
            with self.assertRaises(RatchetMonotonicityViolationError) as exc_info:
                generate_baseline(
                    repo_root=tmp_path,
                    baseline_path=baseline_file,
                    max_lines=50,
                    is_excluded_fn=lambda _: False,
                    allow_upward_override=False,
                )

            self.assertIn("grew from 100 to 120 lines", str(exc_info.exception))

    def test_03_expired_exclusion_rejected(self) -> None:
        """Verify that an exclusion entry with an expired date is rejected."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            expired_entry = {
                "path": "tools/legacy_tool.py",
                "classification": "offline_diagnostic",
                "justification": "Diagnostic utility for legacy debugging.",
                "expires_at": "2020-01-01",  # Expired in past
            }
            valid, reason = validate_exclusion_entry(expired_entry, repo_root=tmp_path)
            self.assertFalse(valid)
            self.assertIn("expired on 2020-01-01", reason)

    def test_04_missing_expiry_on_exclusion_rejected(self) -> None:
        """Verify that an exclusion entry lacking mandatory expires_at is rejected."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            no_expiry_entry = {
                "path": "tools/legacy_tool.py",
                "classification": "offline_diagnostic",
                "justification": "Diagnostic utility for legacy debugging.",
            }
            valid, reason = validate_exclusion_entry(no_expiry_entry, repo_root=tmp_path)
            self.assertFalse(valid)
            self.assertIn("missing mandatory 'expires_at'", reason)

    def test_05_direct_sqlite3_connect_outside_storage_rejected(self) -> None:
        """Verify that direct sqlite3.connect outside storage is flagged as raw_sqlite_banned."""
        code = """
import sqlite3
def run_query():
    conn = sqlite3.connect("prohibited.db")
"""
        visitor = SinglePassASTVisitor("pipeline_b_extraction/bad_module.py", code)
        tree = ast.parse(code)
        visitor.visit(tree)

        violations = [v for v in visitor.ast_violations if v.category == "raw_sqlite_banned"]
        self.assertGreaterEqual(len(violations), 1)
        self.assertIn("Direct sqlite3.connect() prohibited outside storage/", violations[0].message)

    def test_06_docstring_and_nested_helper_excluded_from_60_line_ceiling(self) -> None:
        """Verify multi-line docstrings and nested helper defs do not falsely trigger 60-line limit."""
        doc = "\n".join([f"    Doc line {i}" for i in range(45)])
        helper = "\n".join(["    def _inner_helper(v: int) -> int:"] + [f"        v += {i}" for i in range(30)] + ["        return v"])
        body = "\n".join([f"    x += {i}" for i in range(15)])
        code = f'def well_documented_fn(x: int) -> int:\n    """\n{doc}\n    """\n{helper}\n{body}\n    return x\n'

        visitor = SinglePassASTVisitor("dual_pipeline/doc_mod.py", code)
        tree = ast.parse(code)
        visitor.scan_docstrings(tree)
        visitor.visit(tree)

        outer_funcs = [f for f in visitor.functions if f.name == "well_documented_fn"]
        self.assertEqual(len(outer_funcs), 1)
        self.assertLessEqual(outer_funcs[0].effective_lines, 60)

    def test_07_deletion_hunk_and_qualified_class_method_ratchet(self) -> None:
        """Verify deletion hunks (+start,0) do not false-touch functions and class methods don't collide."""
        import subprocess
        from tools.shift_left_core.ast_framework import (
            get_head_function_complexity,
            get_staged_diff_line_ranges,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)

            mod_rel = "dual_pipeline/classes.py"
            mod_file = tmp_path / mod_rel
            mod_file.parent.mkdir(parents=True, exist_ok=True)

            head_code = """# Header comment 1
# Header comment 2
class Alpha:
    def run(self, a, b, c, d, e, f):
        if a:
            if b:
                if c:
                    if d:
                        if e:
                            if f:
                                return 1
        return 0

class Beta:
    def run(self, x):
        return x + 1
"""
            mod_file.write_text(head_code, encoding="utf-8")
            subprocess.run(["git", "add", mod_rel], cwd=tmp_path, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

            head_map = get_head_function_complexity(tmp_path, mod_rel)
            self.assertGreater(head_map.get("Alpha.run", 0), 15)
            self.assertEqual(head_map.get("Beta.run", -1), 0)

            # Stage a pure line deletion at top of file (+1,0 hunk) without touching Alpha.run
            new_code = head_code.replace("# Header comment 1\n# Header comment 2\n", "")
            mod_file.write_text(new_code, encoding="utf-8")
            subprocess.run(["git", "add", mod_rel], cwd=tmp_path, check=True)

            ranges = get_staged_diff_line_ranges(tmp_path, mod_rel)
            self.assertEqual(ranges, [])
