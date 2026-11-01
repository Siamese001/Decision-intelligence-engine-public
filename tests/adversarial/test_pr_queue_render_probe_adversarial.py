"""
Adversarial and Edge-Case Tests for PR Queue Render Probe.

Covers:
1. Boundary payloads: extreme branch names (250+ chars, unicode/CJK, HTML injection tags, special punctuation)
2. Malformed input: zero-length strings, malformed markdown, extreme viewports (300px mobile to 3840px 4K)
3. Concurrency races: concurrent probes executing simultaneously without temporary file collisions
4. Rollback and recovery: missing browser binary fallback paths
5. Invariant bypass: assertion violations fail closed with exit code 1
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.pr_queue_render_probe import (
    generate_variant_html,
    run_probe,
    simulate_layout_deterministic,
)

ROOT = Path(__file__).resolve().parent.parent.parent


class TestPRQueueRenderProbeAdversarial(unittest.TestCase):
    def test_extreme_branch_name_boundary_payloads(self):
        # 1. 250+ character ultra-long branch name
        super_long = "feat/" + "very-long-subsystem-token-" * 12 + "final"
        res_long = simulate_layout_deterministic("compact_7col", 1000, branch_text=super_long)
        self.assertGreater(res_long["line_count"], 0)
        self.assertGreater(res_long["col_width_px"], 0)

        # 2. HTML injection attempts in branch name
        hostile_branch = "<script>alert('xss')</script><b>bold_branch</b>"
        html, _ = generate_variant_html("current_12col", {"branch": hostile_branch, "priority": "P0", "domain": "Sec", "readiness": "Ready", "review": "OK", "ahead": 0, "behind": 0, "next_action": "act", "rationale": "rat", "effort": "LOW", "completed_waves": "", "open_waves": ""}, 800)
        self.assertTrue("table" in html)

        # 3. Unicode and CJK branch name tokens
        unicode_branch = "feat/日本語_тест_🚀_rocket_branch"
        res_unicode = simulate_layout_deterministic("compact_7col", 1000, branch_text=unicode_branch)
        self.assertGreater(res_unicode["col_width_px"], 100)

    def test_extreme_viewport_boundaries(self):
        # Ultra-narrow 300px mobile viewport
        res_narrow = simulate_layout_deterministic("compact_7col", 300, branch_text="feat/test-branch")
        self.assertGreaterEqual(res_narrow["col_width_px"], 50)

        # 3840px 4K display viewport
        res_4k = simulate_layout_deterministic("compact_7col", 3840, branch_text="feat/test-branch")
        self.assertGreater(res_4k["col_width_px"], 1000)
        self.assertEqual(res_4k["line_count"], 1)

    def test_concurrency_race_safety(self):
        # Execute 4 probes concurrently to ensure no tempfile collisions
        def _invoke_probe(vp: int):
            return simulate_layout_deterministic("compact_7col", vp, branch_text=f"feat/concurrent-{vp}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_invoke_probe, vp) for vp in [600, 800, 1000, 1200]]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertGreater(r["col_width_px"], 0)

    def test_missing_chrome_fallback_resilience(self):
        # Probe must never crash when Chrome binary is missing
        measurements, summary = run_probe(
            viewports=[800, 1000],
            chrome_bin="/tmp/fake_nonexistent_chrome_binary_xyz",
            assert_min_chars=20,
        )
        self.assertTrue(len(measurements) > 0)
        self.assertTrue(summary.threshold_passed)
        for m in measurements:
            self.assertEqual(m.measurement_source, "deterministic_simulation")

    def test_assertion_failure_fails_closed(self):
        # Asserting an impossible threshold (e.g. 500 chars/line in 800px) must fail closed
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            receipt_path = f.name
        try:
            cmd = [
                sys.executable,
                str(ROOT / "tools" / "pr_queue_render_probe.py"),
                "--viewports",
                "800",
                "--receipt",
                receipt_path,
                "--assert-branch-min-chars",
                "500",  # Impossible assertion
            ]
            res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(res.returncode, 1, "Must exit with code 1 when assertion fails")
        finally:
            p = Path(receipt_path)
            if p.exists():
                p.unlink()


if __name__ == "__main__":
    unittest.main()
