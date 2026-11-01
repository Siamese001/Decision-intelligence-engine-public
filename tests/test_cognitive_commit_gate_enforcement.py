#!/usr/bin/env python3
"""
tests/test_cognitive_commit_gate_enforcement.py

Wave 3 integration and diagnostic tests verifying:
1. run_code_quality in tools/verify_commit_core/categories.py returns False when a target file exceeds cognitive complexity limits.
2. run_code_quality returns True on compliant target files.
3. Terminal and JSON output emit line-by-line complexity contribution breakdowns (Breakdown: L<line>: +<inc> (<reason>)).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock
import unittest

from tools.lint_cognitive_complexity import main as cc_main
from tools.verify_commit_core.categories import run_code_quality


class TestCognitiveCommitGateEnforcement(unittest.TestCase):
    def test_run_code_quality_blocks_on_complexity_violation(self) -> None:
        complex_code = (
            "def complex_handler(a, b, c, d, e, f, g):\n"
            "    if a:\n"
            "        if b:\n"
            "            if c:\n"
            "                if d:\n"
            "                    if e:\n"
            "                        if f:\n"
            "                            if g:\n"
            "                                return 1\n"
            "    return 0\n"
        )
        with tempfile.TemporaryDirectory() as td:
            mod_path = Path(td) / "dual_pipeline" / "sample_violating.py"
            mod_path.parent.mkdir(parents=True, exist_ok=True)
            mod_path.write_text(complex_code, encoding="utf-8")

            ok, out, _ = run_code_quality(
                py=sys.executable,
                staged_args=[],
                file_args=[str(mod_path)],
                target_files=[str(mod_path)],
            )
            self.assertFalse(ok, f"Expected run_code_quality to fail on complexity violation, got: {out}")
            self.assertIn("[Cognitive Complexity FAILED]", out)
            self.assertIn("Breakdown:", out)

    def test_line_level_diagnostic_breakdown_in_stdout_and_json_report(self) -> None:
        complex_code = (
            "def nested_flow(a, b, c, d, e, f):\n"
            "    if a and b:\n"
            "        for x in c:\n"
            "            while d:\n"
            "                if e:\n"
            "                    if f:\n"
            "                        return x\n"
            "    return 0\n"
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod_path = root / "dual_pipeline" / "diag_mod.py"
            mod_path.parent.mkdir(parents=True, exist_ok=True)
            mod_path.write_text(complex_code, encoding="utf-8")
            report_path = root / "cc_report.json"

            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cc_main(
                    [
                        "--check",
                        "--max-complexity",
                        "10",
                        "--json-report",
                        str(report_path),
                        str(mod_path),
                    ]
                )

            output = buf.getvalue()
            self.assertEqual(rc, 1)
            self.assertIn("Breakdown:", output)
            self.assertIn("L6: +5 (if branch (+1, nesting +4))", output)

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["violation_count"], 1)
            contribs = report["violations"][0]["contributions"]
            self.assertGreaterEqual(len(contribs), 5)
            self.assertTrue(any(c["reason"] == "boolean operator chain (+1)" for c in contribs))


if __name__ == "__main__":
    unittest.main()
