"""
Unit tests for tools/pr_queue_render_probe.py.

Verifies HTML generation, deterministic layout simulation, RCA calculation,
and receipt generation across variants and viewports.
"""

from __future__ import annotations

import unittest

from tools.pr_queue_render_probe import (
    SAMPLE_12COL_ROW,
    format_summary_table,
    generate_variant_html,
    run_probe,
    simulate_layout_deterministic,
)


class TestPRQueueRenderProbe(unittest.TestCase):
    def test_generate_variant_html_12col(self):
        html, col_idx = generate_variant_html("current_12col", SAMPLE_12COL_ROW, 800)
        self.assertIn("<table>", html)
        self.assertIn("<th>Next Action</th>", html)
        self.assertIn("<th>Ranking Rationale</th>", html)
        self.assertEqual(col_idx, 3)
        self.assertIn(SAMPLE_12COL_ROW["branch"], html)

    def test_generate_variant_html_compact_7col(self):
        html, col_idx = generate_variant_html("compact_7col", SAMPLE_12COL_ROW, 1000)
        self.assertIn("<th>Priority</th>", html)
        self.assertIn("<th>Domain</th>", html)
        self.assertIn("<th>Branch</th>", html)
        self.assertIn("<th>Readiness</th>", html)
        self.assertIn("<th>Review</th>", html)
        self.assertIn("<th>Ahead</th>", html)
        self.assertIn("<th>Behind</th>", html)
        # Should not include prose columns in the compact table
        self.assertNotIn("<th>Next Action</th>", html)
        self.assertNotIn("<th>Ranking Rationale</th>", html)
        self.assertEqual(col_idx, 3)

    def test_generate_variant_html_codespan(self):
        html, col_idx = generate_variant_html("codespan_branch", SAMPLE_12COL_ROW, 800)
        self.assertIn(f"<code>{SAMPLE_12COL_ROW['branch']}</code>", html)
        self.assertEqual(col_idx, 3)

    def test_generate_variant_html_nowrap(self):
        html, col_idx = generate_variant_html("nowrap_branch", SAMPLE_12COL_ROW, 800)
        self.assertIn('<span style="white-space: nowrap">', html)
        self.assertEqual(col_idx, 3)

    def test_simulate_layout_deterministic_reproduces_compression(self):
        # 12-col layout at 600px should compress column width
        res_12col = simulate_layout_deterministic("current_12col_break_all", 600, SAMPLE_12COL_ROW["branch"])
        self.assertLess(res_12col["col_width_px"], 100)
        self.assertGreater(res_12col["line_count"], 1)

        # 7-col layout at 1000px should have ample space
        res_7col = simulate_layout_deterministic("compact_7col", 1000, SAMPLE_12COL_ROW["branch"])
        self.assertGreaterEqual(res_7col["col_width_px"], 200)
        self.assertGreaterEqual(res_7col["min_chars"], 28)

    def test_run_probe_summary_and_receipt(self):
        measurements, summary = run_probe(
            viewports=[800, 1000],
            chrome_bin="/nonexistent/chrome",  # forces deterministic fallback path
            assert_min_chars=28,
        )
        self.assertTrue(len(measurements) > 0)
        self.assertEqual(summary.rc1_no_width_contract, "VERIFIED")
        self.assertEqual(summary.rc3_w_tier_row_flood, "VERIFIED")
        self.assertEqual(summary.rc4_lost_padding_fix, "VERIFIED")
        self.assertEqual(summary.rc5_rule_contradiction, "VERIFIED")
        self.assertTrue(summary.threshold_passed)

        # Format summary table
        text_report = format_summary_table(measurements, summary)
        self.assertIn("RCA CHARACTERIZATION REPORT", text_report)
        self.assertIn("winning_composite", text_report)


if __name__ == "__main__":
    unittest.main()
