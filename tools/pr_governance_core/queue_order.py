"""
Deterministic ordering and sub-ranking for the PR queue table.

Contract (see .agents/rules/pr-queue-summary.md §1 & Rubric v2):
  1. Rows are sorted by P-level first (P0 -> P1 -> P2 -> P3), then decommission rows (D / TOMBSTONE),
     then archived backup snapshots (A). COMMIT_FIRST rows keep their domain P-level (rule §0.6).
  2. Within a tier, sort by (risk asc, value desc, branch asc). Risk never crosses P-levels.
  3. Sub-ranks (P0.1, P0.2, ...) are assigned AFTER sorting so they always read top-to-bottom.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

PRIORITY_ORDER = ("P0", "P1", "P2", "P3")
WORKTREE_LEVELS = ("W",)
DECOMMISSION_LEVELS = ("D", "TOMBSTONE", "—")
ARCHIVE_LEVELS = ("A",)


def base_level(tier: str) -> str:
    """Return the P-level without the sub-rank or override star ('P0.3*' -> 'P0')."""
    return str(tier or "").split(".")[0].replace("*", "")


def level_index(tier: str) -> int:
    base = base_level(tier)
    if base in PRIORITY_ORDER:
        return PRIORITY_ORDER.index(base)
    # Unknown levels sort after every known P-level but before decommission rows.
    return len(PRIORITY_ORDER)


def _group(tier: str) -> int:
    base = base_level(tier)
    if base in ARCHIVE_LEVELS:
        return 3
    if base in DECOMMISSION_LEVELS:
        return 2
    if base in WORKTREE_LEVELS:
        return 1
    return 0


THEME_ORDER_MAP = {
    "COST_REDUCTION": 0,
    "LATENCY_IMPROVEMENT": 1,
    "GRAPH_INTEGRITY": 2,
    "INTEGRITY_PROTECTION": 2,
    "FEATURE_AND_DELIVERABLE": 3,
    "FEATURE_CAPABILITY": 3,
    "CLIENT_DELIVERABLE": 3,
    "HOUSEKEEPING": 4,
    "UNKNOWN": 5,
}


def _theme_order(item: Dict[str, Any]) -> int:
    """Order themes across P0-P3 following the 5-criterion hierarchy."""
    category = str(item.get("benefit_category", item.get("outcome_metadata", {}).get("benefit_category", "")))
    return THEME_ORDER_MAP.get(category, 5)


def _completion_rank(item: Dict[str, Any]) -> int:
    """Order COMPLETED (0) ahead of IN_PROGRESS (1) within the same P-level, theme, and risk."""
    explicit = item.get("completion_rank")
    if isinstance(explicit, int):
        return explicit
    state_str = item.get("completion_state")
    if state_str == "COMPLETED":
        return 0
    if state_str == "IN_PROGRESS":
        return 1
    if state_str == "DECOMMISSION":
        return 2
    from .completion_state import evaluate_branch_completion_state

    return evaluate_branch_completion_state(item).completion_rank


def queue_sort_key(item: Dict[str, Any]) -> Tuple[int, int, int, int, int, float, int, str]:
    """Sort by row group, tier, business criterion theme, risk, completion state, value, complexity, then branch."""
    risk = int(item.get("risk_tier", item.get("risk_score", 0)))
    comp_state_rank = _completion_rank(item)
    value = float(item.get("value_score", item.get("value", 0.0)))
    complexity = item.get("complexity")
    if isinstance(complexity, dict):
        comp_rank = int(complexity.get("rank", 2))
    elif isinstance(complexity, str):
        comp_rank = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}.get(complexity, 2)
    else:
        comp_rank = int(item.get("complexity_rank", 2))

    return (
        _group(item.get("tier", "")),
        level_index(item.get("tier", "")),
        _theme_order(item),
        risk,
        comp_state_rank,
        -value,
        comp_rank,
        str(item.get("branch", "")),
    )


def sort_queue(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort in place and return the queue."""
    items.sort(key=queue_sort_key)
    return items



RISK_LABELS = {0: "ready-now", 1: "low", 2: "moderate", 3: "conflicts", 4: "diverged>=20"}


def _risk(item: Dict[str, Any]) -> int:
    return int(item.get("risk_tier", item.get("risk_score", 0)))


def _risk_label(item: Dict[str, Any]) -> str:
    return f"risk {_risk(item)} {RISK_LABELS.get(_risk(item), 'high')}"


def _theme_name(item: Dict[str, Any]) -> str:
    cat = str(item.get("benefit_category", item.get("outcome_metadata", {}).get("benefit_category", "")))
    return cat if cat else "unclassified"


def _driver(item: Dict[str, Any]) -> str:
    """Rubric rule that set the P-level, e.g. 'R3.cost' from 'P0 R3.cost: agents · 121 LOC'."""
    m = re.search(r"R\d+\.\w+", str(item.get("domain_impact", "")))
    return m.group(0) if m else "registry pin"


def compute_ranking_rationale(curr: Dict[str, Any], prev: Dict[str, Any] | None) -> str:
    """State which element of queue_sort_key (group, P-level, theme, risk, -value, comp_rank, branch) placed this row."""
    tier = str(curr.get("tier", ""))
    base, grp = base_level(tier), _group(tier)
    if prev is None or _group(str(prev.get("tier", ""))) != grp:
        if grp == 1:
            return "Worktree coverage: unregistered directory or stash; ordered after active feature branches"
        if grp == 2:
            return "Decommission group: no mergeable work; ordered after active branches and worktrees"
        if grp == 3:
            return "Archive group: backup snapshot, never a merge candidate; ordered last"
        return f"1st: {base} set by {_driver(curr)} (P-level outranks risk); {_risk_label(curr)}"
    prev_ref = prev.get("sub_rank", "prev")
    if grp != 0:
        return f"Same group as {prev_ref}; tie-broken by branch name"
    if level_index(tier) > level_index(str(prev.get("tier", ""))):
        return f"Below {prev_ref}: lower P-level ({base} via {_driver(curr)} vs {base_level(str(prev.get('tier', '')))}); P-level outranks risk"
    ct, pt = _theme_order(curr), _theme_order(prev)
    if ct > pt:
        return f"Below {prev_ref}: same {base}, prioritized by business criterion ({_theme_name(prev)} outranks {_theme_name(curr)})"
    if _risk(curr) > _risk(prev):
        return f"Below {prev_ref}: same {base}, higher merge risk ({_risk_label(curr)} vs {_risk(prev)})"
    if _completion_rank(curr) > _completion_rank(prev):
        return f"Below {prev_ref}: same {base} and risk {_risk(curr)}; completed branch outranks in-progress branch"
    cv, pv = float(curr.get("value_score", curr.get("value", 0.0))), float(prev.get("value_score", prev.get("value", 0.0)))
    if cv < pv:
        return f"Below {prev_ref}: same {base} and risk {_risk(curr)}; lower value {cv:.1f} vs {pv:.1f}"

    def _comp(it: Dict[str, Any]) -> int:
        c = it.get("complexity")
        if isinstance(c, dict):
            return int(c.get("rank", 2))
        if isinstance(c, str):
            return {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}.get(c, 2)
        return int(it.get("complexity_rank", 2))

    cc, pc = _comp(curr), _comp(prev)
    if cc > pc:
        return f"Below {prev_ref}: same {base}, risk, value; higher complexity ({cc} vs {pc})"
    return f"Below {prev_ref}: same {base}, risk, value; branch-name tie-break"


def assign_sub_ranks(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign P0.1, P0.2, ... in the order of the (already sorted) queue, and generate relative rationales."""
    counters: Dict[str, int] = {}
    for i, item in enumerate(items):
        raw_tier = str(item.get("tier", ""))
        is_override = "*" in raw_tier or item.get("override_active", False)
        base = base_level(raw_tier) or "P3"
        counters[base] = counters.get(base, 0) + 1
        star = "*" if is_override else ""
        item["sub_rank"] = f"{base}.{counters[base]}{star}"
        item["display_tier"] = f"{base}{star}"

        item["ranking_rationale"] = compute_ranking_rationale(item, items[i - 1] if i > 0 else None)

    return items
