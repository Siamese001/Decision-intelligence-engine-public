"""Interval math, union spans, phase categorization, and missingness handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

MACHINE_PHASES: Set[str] = {
    "readiness",
    "commit_hooks",
    "review",
    "implementation_summary",
    "static_review",
    "semantic_review",
    "base_and_scope",
    "merge",
    "verification",
    "cleanup",
    "rebase",
}

PREP_PHASES: Set[str] = {
    "instruction_intake",
    "preparation",
}

QUEUE_PHASES: Set[str] = {
    "lock_wait",
}


def parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str or ts_str == "UNKNOWN":
        return None
    try:
        # Standardize ISO parse with timezone
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def union_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    if not intervals:
        return []
    # Sort by start time
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: List[Tuple[datetime, datetime]] = []
    for cur_start, cur_end in sorted_intervals:
        if cur_end < cur_start:
            # Swap or handle clock skew in interval
            cur_start, cur_end = cur_end, cur_start
        if not merged:
            merged.append((cur_start, cur_end))
        else:
            prev_start, prev_end = merged[-1]
            if cur_start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, cur_end))
            else:
                merged.append((cur_start, cur_end))
    return merged


def measure_unioned_seconds(intervals: List[Tuple[datetime, datetime]]) -> float:
    merged = union_intervals(intervals)
    total = sum((end - start).total_seconds() for start, end in merged)
    return round(max(0.0, total), 4)


def categorize_phase(phase_name: str) -> str:
    p = phase_name.lower().strip()
    if p in PREP_PHASES:
        return "agent_tool_preparation"
    if p in QUEUE_PHASES:
        return "queue"
    if p in MACHINE_PHASES:
        return "machine_execution"
    return "other_machine_execution"


def classify_gap(
    prev_status: str,
    prev_tier: str,
    next_tier: str,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
) -> Dict[str, Any]:
    if not start_dt or not end_dt:
        return {
            "category": "UNKNOWN",
            "is_missing": True,
            "duration_seconds": None,
            "description": "Missing start or end timestamp; interval cannot be evaluated to zero",
        }
    if end_dt < start_dt:
        return {
            "category": "CLOCK_SKEW",
            "is_missing": False,
            "duration_seconds": round((end_dt - start_dt).total_seconds(), 4),
            "description": "Clock skew detected: next attempt started before previous attempt ended",
        }
    duration = round((end_dt - start_dt).total_seconds(), 4)
    if prev_status.upper() == "FAIL":
        category = "repair"
        desc = "Repair interval following failed attempt"
    elif prev_status.upper() == "PASS" and prev_tier == "pr_review" and next_tier == "pr_merge":
        category = "human_wait"
        desc = "Human or queue wait between successful review and merge invocation"
    else:
        category = "queue"
        desc = "Queue or scheduling delay between attempts"

    return {
        "category": category,
        "is_missing": False,
        "duration_seconds": duration,
        "description": desc,
    }
