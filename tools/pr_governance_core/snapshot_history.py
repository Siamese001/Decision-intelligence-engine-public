"""tools/pr_governance_core/snapshot_history.py

Immutable snapshot history and rank transition tracking for PR Queue Prioritization (Wave 4).
Enforces reproducible replay and explicit causal attribution for every rank change.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RankChangeEvent:
    branch: str
    previous_position: Optional[str]
    current_position: str
    previous_tier: Optional[str]
    current_tier: str
    changed_evidence: List[str]
    cause_summary: str
    policy_version: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _compute_field_diffs(prev: Dict[str, Any], curr: Dict[str, Any]) -> List[str]:
    diffs: List[str] = []
    for key in ("ahead", "behind"):
        if prev.get(key, 0) != curr.get(key, 0):
            diffs.append(f"{key}: {prev.get(key, 0)} -> {curr.get(key, 0)}")
    for bool_key, label in (("clean_merge", "clean_merge"), ("dirty", "worktree_dirty")):
        if prev.get(bool_key, True) != curr.get(bool_key, True):
            diffs.append(f"{label}: {prev.get(bool_key, True)} -> {curr.get(bool_key, True)}")
    for wave_key in ("open_waves", "completed_waves"):
        if prev.get(wave_key) != curr.get(wave_key):
            diffs.append(f"{wave_key}: '{prev.get(wave_key)}' -> '{curr.get(wave_key)}'")
    if prev.get("merge_eligibility") != curr.get("merge_eligibility"):
        diffs.append(f"eligibility: {prev.get('merge_eligibility')} -> {curr.get('merge_eligibility')}")
    return diffs


def _derive_cause_summary(
    prev: Dict[str, Any],
    curr: Dict[str, Any],
    diffs: List[str],
    prev_pos: str,
    curr_pos: str,
    prev_tier: str,
    curr_tier: str,
) -> str:
    causes = []
    if prev_tier != curr_tier:
        causes.append(f"Tier changed ({prev_tier} -> {curr_tier})")
    if "ahead: 0 -> 1" in str(diffs) or (prev.get("ahead") == 0 and curr.get("ahead", 0) > 0):
        causes.append("Authored inception work committed")
    if prev.get("behind", 0) > 5 and curr.get("behind", 0) <= 5:
        causes.append("Rebased onto main (risk penalty resolved)")
    if not prev.get("clean_merge", True) and curr.get("clean_merge", True):
        causes.append("Merge conflicts resolved")
    if prev.get("open_waves") != curr.get("open_waves") or prev.get("completed_waves") != curr.get("completed_waves"):
        causes.append("Wave progress advanced")

    if causes:
        return "; ".join(causes)
    return f"Rank adjusted ({prev_pos} -> {curr_pos}) due to relative score or neighbor updates"


def _build_single_branch_transition(
    branch: str,
    prev: Optional[Dict[str, Any]],
    curr: Dict[str, Any],
    policy_version: str,
) -> Optional[RankChangeEvent]:
    curr_pos = curr.get("sub_rank") or curr.get("tier", "—")
    curr_tier = curr.get("tier", "—")

    if prev is None:
        return RankChangeEvent(
            branch=branch,
            previous_position=None,
            current_position=curr_pos,
            previous_tier=None,
            current_tier=curr_tier,
            changed_evidence=["New branch/worktree introduced into queue"],
            cause_summary="Initial queue registration",
            policy_version=policy_version,
        )

    prev_pos = prev.get("sub_rank") or prev.get("tier", "—")
    prev_tier = prev.get("tier", "—")
    diffs = _compute_field_diffs(prev, curr)

    if prev_pos != curr_pos or prev_tier != curr_tier or diffs:
        cause = _derive_cause_summary(prev, curr, diffs, prev_pos, curr_pos, prev_tier, curr_tier)
        return RankChangeEvent(
            branch=branch,
            previous_position=prev_pos,
            current_position=curr_pos,
            previous_tier=prev_tier,
            current_tier=curr_tier,
            changed_evidence=diffs if diffs else ["Relative priority / neighbor adjustment"],
            cause_summary=cause,
            policy_version=policy_version,
        )
    return None


def compute_rank_transitions(
    prev_items: List[Dict[str, Any]],
    curr_items: List[Dict[str, Any]],
    policy_version: str = "2.1",
) -> List[RankChangeEvent]:
    """Compares previous and current queue states and attributes the cause for all changed ranks."""
    prev_map = {it["branch"]: it for it in prev_items if "branch" in it}
    events: List[RankChangeEvent] = []
    for curr in curr_items:
        branch = curr.get("branch", "")
        if not branch:
            continue
        ev = _build_single_branch_transition(branch, prev_map.get(branch), curr, policy_version)
        if ev is not None:
            events.append(ev)
    return events


def record_snapshot_history(
    history_file: Path,
    decision_payload: Dict[str, Any],
    policy_version: str = "2.1",
    max_records: int = 50,
) -> List[RankChangeEvent]:
    """Appends an immutable snapshot record and returns detected rank change transitions."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    prev_branches: List[Dict[str, Any]] = []

    if history_file.is_file():
        try:
            lines = history_file.read_text(encoding="utf-8").splitlines()
            if lines:
                last_record = json.loads(lines[-1])
                prev_branches = last_record.get("branches", [])
        except Exception:
            prev_branches = []

    curr_branches = decision_payload.get("branches", [])
    transitions = compute_rank_transitions(prev_branches, curr_branches, policy_version=policy_version)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy_version,
        "branches_count": len(curr_branches),
        "execution_sequence": decision_payload.get("execution_sequence", []),
        "blocked_branches": decision_payload.get("blocked_branches", {}),
        "rank_transitions": [t.to_dict() for t in transitions],
        "branches": curr_branches,
    }

    # Append to JSONL history ledger
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return transitions


def replay_history(history_file: Path) -> List[Dict[str, Any]]:
    """Replays chronological snapshot history records from a JSONL file."""
    if not history_file.is_file():
        return []
    records = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
