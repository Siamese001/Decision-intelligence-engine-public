#!/usr/bin/env python3
"""
tests/test_cognitive_complexity_ratchet.py

Wave 2 unit and adversarial tests verifying:
1. Path-boundary exclusion matching (no false substring matches like 'ui/' matching 'builder_ui.py').
2. Monotonic complexity ratchet allows baselined functions up to their recorded score and graduations <= 15.
3. Upward complexity drift on a baselined function (+1 score) or a new unbaselined function > 15 is blocked.
4. Full repository ratchet scan passes with exit code 0 across all governed modules.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.lint_cognitive_complexity import (
    COMPLEXITY_BASELINE_PATH,
    generate_complexity_baseline,
    is_excluded,
    load_complexity_baseline,
    scan_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestCognitiveComplexityRatchet(unittest.TestCase):
    def test_path_boundary_exclusion_prevents_substring_false_positives(self) -> None:
        patterns = ["ui/", "tools/", "dual_pipeline/specific_mod.py"]
        self.assertTrue(is_excluded("ui/dashboard.py", patterns=patterns))
        self.assertTrue(is_excluded("tools/linter.py", patterns=patterns))
        self.assertTrue(is_excluded("dual_pipeline/specific_mod.py", patterns=patterns))
        self.assertFalse(is_excluded("dual_pipeline/builder_ui_helper.py", patterns=patterns))
        self.assertFalse(is_excluded("dual_pipeline/my_tools_adapter.py", patterns=patterns))
        self.assertFalse(is_excluded("dual_pipeline/specific_mod_extra.py", patterns=patterns))

    def test_ratchet_allows_baselined_score_and_blocks_upward_drift(self) -> None:
        code = """
class Engine:
    def process(self, a, b, c, d, e):
        if a:
            if b:
                if c:
                    if d:
                        if e:
                            return 1
        return 0
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = root / "dual_pipeline" / "engine.py"
            mod.parent.mkdir(parents=True, exist_ok=True)
            mod.write_text(code, encoding="utf-8")

            # Score of Engine.process is 1+2+3+4+5 = 15; with threshold=10 it exceeds 10
            unbaselined = scan_file(mod, root, max_complexity=10, baseline_functions=None)
            self.assertEqual(len(unbaselined), 1)
            self.assertEqual(unbaselined[0].function_name, "Engine.process")
            self.assertEqual(unbaselined[0].score, 15)

            # With baseline at 15, zero violations
            baselined_ok = scan_file(
                mod,
                root,
                max_complexity=10,
                baseline_functions={"dual_pipeline/engine.py::Engine.process": 15},
            )
            self.assertEqual(len(baselined_ok), 0)

            # With baseline at 14 (simulating +1 upward drift from 14 -> 15), blocked!
            baselined_drift = scan_file(
                mod,
                root,
                max_complexity=10,
                baseline_functions={"dual_pipeline/engine.py::Engine.process": 14},
            )
            self.assertEqual(len(baselined_drift), 1)
            self.assertEqual(baselined_drift[0].limit, 14)

    def test_generate_baseline_monotonic_enforcement_blocks_regression(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = root / "dual_pipeline" / "mod.py"
            mod.parent.mkdir(parents=True, exist_ok=True)
            mod.write_text(
                "def f(a, b, c):\n"
                "    if a:\n"
                "        if b:\n"
                "            if c:\n"
                "                return 1\n"
                "    return 0\n",
                encoding="utf-8",
            )
            base_file = root / "baseline.json"
            base_file.write_text(
                json.dumps({"functions": {"dual_pipeline/mod.py::f": 5}}),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError) as ctx:
                generate_complexity_baseline(
                    repo_root=root,
                    baseline_path=base_file,
                    max_complexity=3,
                    allow_upward_override=False,
                )
            self.assertIn("worsened from 5 to 6", str(ctx.exception))

    def test_repository_baseline_exists_and_ratchet_cli_passes(self) -> None:
        self.assertTrue(COMPLEXITY_BASELINE_PATH.is_file())
        funcs = load_complexity_baseline(COMPLEXITY_BASELINE_PATH)
        self.assertIn(
            "dual_pipeline/normalization_core/a2a.py::normalize_a2a_fact_vector_packet",
            funcs,
        )
        res = subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPO_ROOT / "tools" / "lint_cognitive_complexity.py"),
                "--check",
                "--ratchet",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Ratchet CLI failed:\n{res.stdout}\n{res.stderr}")


if __name__ == "__main__":
    unittest.main()
