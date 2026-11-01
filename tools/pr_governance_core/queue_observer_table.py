"""tools/pr_governance_core/queue_observer_table.py

Single-table renderer for interactive and scheduled PR Queue observations (10-column Executive format).
Aligns strictly with the canonical Executive Candidate Queue SSOT (Schema 4.0.0).
"""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from .executive_render import evaluate_executive_row

COLUMNS = (
    "Priority",
    "Branch",
    "Category",
    "Complexity",
    "Code Completed?",
    "Rebase Required?",
    "PR to Main Required?",
    "Graph Mutated (Y/N)",
    "Recommended Disposition",
    "Immediate Blocker / Action",
)
HEADER = "| " + " | ".join(COLUMNS) + " |"
SEPARATOR = "|:---:|:----------------------------------:|:---|:---:|:---:|:---:|:---:|:---:|:---|:---|"


def _cell(value: Any) -> str:
    return escape(str(value or "N/A"), quote=False).replace("|", "&#124;").replace("\n", "<br>")


def render_observer_table(
    items: Iterable[Dict[str, Any]],
    summary: Dict[str, Any],
    movements: Optional[Dict[str, str]] = None,
    recent_merges: Iterable[Dict[str, str]] = (),
    detail_path: Optional[Path] = None,
    show_council: bool = False,
) -> str:
    """Return exactly one GFM table matching the 10-column Executive Candidate Queue SSOT (Schema 4.0.0)."""
    lines = [HEADER, SEPARATOR]
    items_list = list(items)

    if summary.get("outcome", "") == "BLOCKED" or not items_list:
        detail = summary.get("detail") or summary.get("next_action") or "Queue inspection blocked or empty"
        lines.append(f"| — | System | Governance | — | **N/A** | **N/A** | **N** | **N** | **Blocked** | {_cell(detail)} |")
        return "\n".join(lines)

    active_branches: List[Dict[str, Any]] = []
    collapsed_stashes, collapsed_stubs = 0, 0

    for x in items_list:
        tier, branch = x.get("tier"), str(x.get("branch", ""))
        if tier in ("MERGED", "SUMMARY") or branch in ("MERGED", "SUMMARY"):
            continue
        if tier == "W":
            if branch.startswith("stash@{"):
                collapsed_stashes += 1
            else:
                collapsed_stubs += 1
            continue
        active_branches.append(x)

    for idx, x in enumerate(active_branches, start=1):
        p_val, b_link, dom, c_size, c_done, r_req, pr_req, g_mut, disp, h_act = evaluate_executive_row(x, idx)
        lines.append(
            f"| **{p_val}** | {b_link} | {_cell(dom)} | **{c_size}** | **{c_done}** | **{r_req}** | **{pr_req}** | **{g_mut}** | **{_cell(disp)}** | {_cell(h_act)} |"
        )

    target = f"file://{detail_path}" if detail_path else "reports/pr_queue_detail.json"
    if collapsed_stashes > 0 or collapsed_stubs > 0:
        parts = ([f"{collapsed_stubs} session stub(s)"] if collapsed_stubs else []) + ([f"{collapsed_stashes} git stash(es)"] if collapsed_stashes else [])
        lines.append(
            f"| — | [{' & '.join(parts)} collapsed]({target}) | Housekeeping | — | **N/A** | **N** | **N** | **N** | **Quarantine / Triage** | Sealed in reports/pr_queue_detail.json |"
        )

    return "\n".join(lines)


def _branch_cell(branch: str, wt: Any = None) -> str:
    branch = branch or "N/A"
    if not wt:
        return branch
    wt_path = wt.get("path") if isinstance(wt, dict) else str(wt)
    return f"[{branch}](file://{quote(wt_path)})"


def _evidence_cell(item: Dict[str, Any]) -> str:
    links = item.get("evidence_links") or []
    return " ".join(f"[{link.get('label', 'Link')}](<{link.get('url', '')}>)" for link in links) if links else "N/A"
