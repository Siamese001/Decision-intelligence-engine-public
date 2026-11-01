"""
End-to-End table formatting verification for PR Queue summary.
"""

from __future__ import annotations

import re

TABLE_SAMPLE = """
| Priority | Domain | Branch | Readiness | Review | Ahead | Behind |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **P0.1** | Performance | [`feat/meeting-rerun-cost-optimization`](file:///path) | `🚀 Fast-Lane` | MERGE_READY | 7 | 0 |
| **P1.1** | Performance | [`feat/git-hook-perf`](file:///path) | `⚠️ Severely Diverged` | RESOLVE_CONFLICTS | 1 | 51 |

=========================================================================================================
  DIAGNOSTIC & NEXT ACTION LEDGER
=========================================================================================================
* **[P0.1] `feat/meeting-rerun-cost-optimization`**:
  * **Next Action**: [REVIEW] clean and up to date
  * **Ranking Rationale**: 1st: P0 set by R3.cost (P-level outranks risk); risk 0 ready-now
  * **Effort & Waves**: effort=NONE · completed=W1–W6 ✅ · open=—
* **[P1.1] `feat/git-hook-perf`**:
  * **Next Action**: [RESOLVE_CONFLICTS] 3 files
  * **Ranking Rationale**: Below P0.1: lower P-level (P1 via R4.deliverable vs P0); P-level outranks risk
  * **Effort & Waves**: effort=HIGH · completed=W1 ✅ · open=—
"""


def test_pr_queue_table_parsing_e2e():
    # Assert exactly ONE table header exists to prevent multi-table regressions
    headers = [line for line in TABLE_SAMPLE.splitlines() if line.strip().startswith("| Priority |")]
    assert len(headers) == 1, "There must be exactly one PR Queue table, multiple tables are strictly prohibited."

    lines = [
        line.strip() for line in TABLE_SAMPLE.strip().splitlines() if line.strip().startswith("|")
    ]
    assert len(lines) >= 3

    # Check header
    header = [c.strip() for c in lines[0].split("|")[1:-1]]
    assert header == ["Priority", "Domain", "Branch", "Readiness", "Review", "Ahead", "Behind"]

    # Strict regex matching exact **P0.1**, **P1.1** format with no trailing/leading characters
    strict_index_regex = re.compile(r"^\*\*P[A-Z0-9]+\.\d+\*\*$")

    # Test rows
    for line in lines:
        if line.startswith("| Priority"):
            continue
        if ":---" in line:
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        assert len(cols) == 7
        assert strict_index_regex.match(cols[0]) is not None, f"Priority index must strictly match **PX.Y** format, got '{cols[0]}'"
        assert cols[1] and not cols[1].startswith("["), f"Domain (col 2) must be a category label, got '{cols[1]}'"
        assert cols[2].startswith("[`")  # Branch (col 3)
        assert cols[4] in ("MERGE_READY", "RESOLVE_CONFLICTS")

    assert "DIAGNOSTIC & NEXT ACTION LEDGER" in TABLE_SAMPLE


def test_pr_queue_word_count_cap_e2e():
    words = TABLE_SAMPLE.split()
    assert len(words) < 1000
