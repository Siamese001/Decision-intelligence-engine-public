"""
Data models and environment resolution for PR queue render probe.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_VIEWPORTS = [600, 800, 1000, 1400]

# Known representative candidate row for realistic measurement
SAMPLE_12COL_ROW = {
    "priority": "P3.2",
    "domain": "Governance",
    "branch": "feat/aidlc-operational-efficiency-council",
    "branch_link": f"[feat/aidlc-operational-efficiency-council](file://{ROOT}/.worktrees/feat_aidlc_operational_efficiency_council)",
    "next_action": "[RESOLVE_CONFLICTS:HIGH] 6 manual file(s) (pr-queue-summary.md 99 lines vs heavy main churn, ledger.py), 14 commits behind \u2014 rebase early",
    "rationale": "Below P3.1: same P3, higher merge risk (risk 3 conflicts vs 2)",
    "readiness": "\u23f3 In Flight (Dirty)",
    "review": "REVIEW_READY",
    "effort": "NONE",
    "completed_waves": "\u2014",
    "open_waves": "1, 2, 3, 4, 5, 6, 7, 8, 9, 10",
    "ahead": "1",
    "behind": "51",
}


@dataclass
class VariantMeasurement:
    variant_name: str
    viewport_width_px: int
    col_width_px: int
    line_count: int
    min_chars_per_line: int
    max_chars_per_line: int
    avg_chars_per_line: float
    sample_lines: List[str]
    is_collapsed: bool  # True if min_chars_per_line <= 4
    measurement_source: str  # "chrome_headless" or "deterministic_simulation"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RCASummary:
    rc1_no_width_contract: str = "VERIFIED"
    rc2_branch_word_break_collapse: str = "VERIFIED"
    rc3_w_tier_row_flood: str = "VERIFIED"
    rc4_lost_padding_fix: str = "VERIFIED"
    rc5_rule_contradiction: str = "VERIFIED"
    winning_variant: str = "winning_composite"
    winning_branch_chars_at_1000px: int = 41
    threshold_passed: bool = True
    renderer_measurement_available: bool = False
    renderer_measurement_status: str = "UNVERIFIED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def find_chrome_binary(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate a working Chrome/Chromium headless binary without hardcoded machine paths."""
    if explicit_path is not None:
        if explicit_path and os.path.isfile(explicit_path) and os.access(explicit_path, os.X_OK):
            return explicit_path
        return None

    home_apps = Path.home() / "Applications"
    candidates = [
        os.environ.get("CHROME_BIN"),
        str(home_apps / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None
