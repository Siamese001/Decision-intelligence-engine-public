"""
Delta Auditor (A2) for Graph Freshness (Wave 5).
Computes differences between pre-mutation and post-mutation graph states.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class DeltaFindings:
    metric_diffs: Dict[str, Any]
    unintended_side_effects: bool


class DeltaAuditor:
    """A2: Audits the delta between pre- and post-mutation metric snapshots."""

    def scan(self, pre_metrics: Dict[str, Any], post_metrics: Dict[str, Any]) -> DeltaFindings:
        diffs = {}
        unintended = False

        # Calculate numeric differences
        for k, pre_v in pre_metrics.items():
            post_v = post_metrics.get(k)
            if isinstance(pre_v, (int, float)) and isinstance(post_v, (int, float)):
                diffs[k] = post_v - pre_v

        # Check for unintended side effects during pruning/quarantine:
        # 1. Total assertions should never INCREASE during a freshness pruning run.
        if diffs.get("total_assertions", 0) > 0:
            unintended = True

        # 2. Orphaned evidence should ideally decrease or stay flat, never increase
        if diffs.get("evidence_orphans", 0) > 0:
            unintended = True

        return DeltaFindings(metric_diffs=diffs, unintended_side_effects=unintended)
