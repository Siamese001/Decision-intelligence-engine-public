"""tools/pr_governance_core/observer_shadow.py

Shadow observation scenarios and qualification suite for Antigravity PR Queue Observer (Wave 6).
Evaluates 6 realistic scenarios: baseline, unchanged rerun, rank change, fault alert, fault dedup, lost progress.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from tools.pr_governance_core.queue_snapshot import (
    BranchSnapshot,
    QueueSnapshot,
)


def run_shadow_scenarios(
    repo_root: Path,
    output_dir: Path,
) -> List[Dict[str, Any]]:
    """Runs at least 5 distinct shadow observation scenarios to qualify the observer engine."""
    from tools.pr_governance_core.observer import run_observation

    results = []

    # Scenario 1: Clean Baseline
    snap1 = QueueSnapshot(
        observed_at=datetime.now(timezone.utc).isoformat(),
        branches=[
            BranchSnapshot(branch="feat/gov-alpha", head_sha="1111", ahead=2, behind=0, completed_waves="1", open_waves="2"),
            BranchSnapshot(branch="feat/core-beta", head_sha="2222", ahead=1, behind=1, completed_waves="1", open_waves="—"),
        ],
    )
    r1 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap1)
    results.append({
        "scenario": "scenario_1_clean_baseline",
        "terminal_outcome": r1.assessment.terminal_outcome,
        "notify": r1.assessment.notify,
        "reason": r1.assessment.reason,
        "passed": r1.assessment.notify is True and r1.last_accepted_digest_after == r1.snapshot_digest,
    })

    # Scenario 2: Unchanged Rerun (Suppression)
    r2 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap1)
    results.append({
        "scenario": "scenario_2_unchanged_rerun",
        "terminal_outcome": r2.assessment.terminal_outcome,
        "notify": r2.assessment.notify,
        "reason": r2.assessment.reason,
        "passed": r2.assessment.notify is False and r2.assessment.terminal_outcome == "NO_NOTIFICATION",
    })

    # Scenario 3: Meaningful Rank Transition & Wave Completion
    snap3 = QueueSnapshot(
        observed_at=datetime.now(timezone.utc).isoformat(),
        branches=[
            BranchSnapshot(branch="feat/gov-alpha", head_sha="1112", ahead=4, behind=0, completed_waves="1, 2", open_waves="—"),
            BranchSnapshot(branch="feat/core-beta", head_sha="2222", ahead=1, behind=1, completed_waves="1", open_waves="—"),
        ],
    )
    r3 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap3)
    results.append({
        "scenario": "scenario_3_meaningful_rank_transition",
        "terminal_outcome": r3.assessment.terminal_outcome,
        "notify": r3.assessment.notify,
        "reason": r3.assessment.reason,
        "passed": r3.assessment.notify is True and len(r3.assessment.transitions) > 0,
    })

    # Scenario 4: Inconsistent Collection Fault Alert
    snap4 = QueueSnapshot(
        observed_at=datetime.now(timezone.utc).isoformat(),
        completeness_status="INCONSISTENT",
        errors=["Concurrent write collision during worktree listing"],
        branches=[],
    )
    r4 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap4)
    results.append({
        "scenario": "scenario_4_inconsistent_fault_alert",
        "terminal_outcome": r4.assessment.terminal_outcome,
        "notify": r4.assessment.notify,
        "reason": r4.assessment.reason,
        "passed": r4.assessment.notify is True and r4.assessment.terminal_outcome == "COLLECTION_FAILURE_ALERT",
    })

    # Scenario 5: Duplicate Fault Deduplication (Suppressed)
    r5 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap4)
    results.append({
        "scenario": "scenario_5_duplicate_fault_deduplication",
        "terminal_outcome": r5.assessment.terminal_outcome,
        "notify": r5.assessment.notify,
        "reason": r5.assessment.reason,
        "passed": r5.assessment.notify is False and r5.assessment.terminal_outcome == "NO_NOTIFICATION",
    })

    # Scenario 6: Lost Progress / Reopened Wave Alert
    snap6 = QueueSnapshot(
        observed_at=datetime.now(timezone.utc).isoformat(),
        branches=[
            BranchSnapshot(branch="feat/gov-alpha", head_sha="1113", ahead=4, behind=0, completed_waves="—", open_waves="1"),
            BranchSnapshot(branch="feat/core-beta", head_sha="2222", ahead=1, behind=1, completed_waves="1", open_waves="—"),
        ],
    )
    r6 = run_observation(repo_root=repo_root, output_dir=output_dir, snapshot=snap6)
    results.append({
        "scenario": "scenario_6_lost_progress_alert",
        "terminal_outcome": r6.assessment.terminal_outcome,
        "notify": r6.assessment.notify,
        "reason": r6.assessment.reason,
        "passed": r6.assessment.notify is True and len(r6.assessment.progress_alerts) > 0,
    })

    return results
