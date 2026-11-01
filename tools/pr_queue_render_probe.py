#!/usr/bin/env python3
"""
PR Queue Render & Column Width Probe (RCA Characterization Harness).

Empirically measures and characterizes table column widths, line wrapping,
and min-content compression across multiple viewports and rendering CSS modes.

Used to investigate and verify root causes for PR queue Branch column collapsing
to 2-3 characters and evaluate remediation variants (7-column compact, prose capping,
nowrap spans, W-tier collapse).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_queue_probe_core import (  # noqa: E402
    DEFAULT_VIEWPORTS,
    SAMPLE_12COL_ROW,
    RCASummary,
    VariantMeasurement,
    find_chrome_binary,
    format_summary_table,
    generate_variant_html,
    generate_observer_table_html,
    measure_batched_via_chrome,
    run_probe,
    simulate_layout_deterministic,
)

__all__ = [
    "DEFAULT_VIEWPORTS",
    "SAMPLE_12COL_ROW",
    "RCASummary",
    "VariantMeasurement",
    "find_chrome_binary",
    "format_summary_table",
    "generate_variant_html",
    "generate_observer_table_html",
    "measure_batched_via_chrome",
    "run_probe",
    "simulate_layout_deterministic",
    "main",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="PR queue Branch column readability probe and RCA auditor.")
    parser.add_argument(
        "--viewports",
        default="600,800,1000,1400",
        help="Comma-separated viewport widths in pixels (default: 600,800,1000,1400)",
    )
    parser.add_argument("--receipt", help="Path to write the machine-readable JSON receipt")
    parser.add_argument(
        "--assert-branch-min-chars",
        type=int,
        default=None,
        help="Assert that the winning variant achieves at least N chars/line at 1000px (e.g. 28)",
    )
    parser.add_argument("--chrome-bin", help="Explicit path to Chrome/Chromium headless binary")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout instead of formatted text")
    args = parser.parse_args()

    viewports = [int(v.strip()) for v in args.viewports.split(",") if v.strip()]
    measurements, summary = run_probe(
        viewports=viewports,
        chrome_bin=args.chrome_bin,
        assert_min_chars=args.assert_branch_min_chars,
    )

    if args.json:
        payload = {
            "summary": summary.to_dict(),
            "measurements": [m.to_dict() for m in measurements],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_summary_table(measurements, summary))

    if args.receipt:
        out_path = Path(args.receipt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_data = {
            "terminal_outcome": "PASS" if summary.threshold_passed else "FAIL",
            "audit_timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "rca_verdicts": summary.to_dict(),
            "measurements": [m.to_dict() for m in measurements],
            "recommendation": {
                "variant": summary.winning_variant,
                "branch_chars_at_1000px": summary.winning_branch_chars_at_1000px,
                "action": "Adopt compact 7-column layout with diagnostic ledger and W-tier collapse in Wave 2.",
            },
        }
        out_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"Receipt written to: {out_path}", file=sys.stderr)

    return 0 if summary.threshold_passed else 1


if __name__ == "__main__":
    sys.exit(main())
