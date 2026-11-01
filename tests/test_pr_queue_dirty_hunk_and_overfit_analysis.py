"""tests/test_pr_queue_dirty_hunk_and_overfit_analysis.py

Unit and edge-case tests for accurate dirty worktree diffing, deleted-on-main file detection,
anti-overfitting hazard scanning, and 100%-absorbed branch decommissioning (Wave 1).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from tools.pr_governance_core.code_review import _detect_governance_hazards, compute_code_review
from tools.pr_governance_core.code_review_rules import DeterministicSignals, ReviewVerdict, evaluate_review_verdict
from tools.pr_governance_core.conflict_triage import analyze_branch_file_diffs
from tools.pr_governance_core.verdict_verifier import (
    _score_partial_absorption,
    compute_dirty_file_in_main_pct,
    verify_branch_verdict_and_disposition,
)


def test_score_partial_absorption_returns_none_for_fully_absorbed() -> None:
    """When all changed files are absorbed (unabs_cnt == 0), _score_partial_absorption must return None."""
    res = _score_partial_absorption(
        abs_cnt=56,
        unabs_cnt=0,
        absorbed_files=["file1.py", "file2.py"],
        unabsorbed_files=[],
        hunk_ratio=0.50,
        ahead=1,
        dirty=False,
    )
    assert res is None


def test_verify_branch_verdict_100_percent_absorbed_is_decommission() -> None:
    """A branch with 100% absorbed files must evaluate to Decommission (No Value)."""
    item = {
        "branch": "feat/live-jarvis-10-09-2026-standup-9-30-mitch-partner_advisory",
        "ahead": 1,
        "behind": 90,
        "dirty": False,
        "clean_merge": True,
        "open_waves": "—",
        "review_verdict": "SUPERSEDED_TOMBSTONE",
        "diff_category": "CODE_ACTIVE",
        "absorbed_files": ["synthesis.py", "telemetry.py"],
        "salvageable_files": [],
        "signals": {
            "file_hunks": [{"file_path": "synthesis.py"}],
            "hunk_absorption_ratio": 0.50,
        },
    }
    verdict_res = verify_branch_verdict_and_disposition(item)
    assert verdict_res.verified_disposition == "Decommission (No Value)"
    assert verdict_res.c_top >= 0.90
    assert verdict_res.requires_hitl_clarification is False


def test_compute_dirty_file_in_main_pct_tracked_vs_head(tmp_path: Path) -> None:
    """Tracked worktree files must be diffed against worktree HEAD, not main."""
    # Setup temporary git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)

    # Initial commit on main
    f = repo / "tracked_file.py"
    f.write_text("line1\nline2\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked_file.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Create and checkout feature branch
    subprocess.run(["git", "checkout", "-b", "feat/test"], cwd=repo, check=True)

    # Commit 500 lines to main in the meantime
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    f.write_text("line1\nline2\n" + "\n".join(f"main_line_{i}" for i in range(50)), encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "main updates"], cwd=repo, check=True)

    # In feature branch, add 1 line locally without committing
    subprocess.run(["git", "checkout", "feat/test"], cwd=repo, check=True)
    f.write_text("line1\nline2\nlocal_unique_line\n", encoding="utf-8")

    pct, (added, deleted) = compute_dirty_file_in_main_pct("tracked_file.py", repo, "main", repo)

    # Added must be 1 (local_unique_line), deleted must be 0 (vs HEAD)
    # NOT 50 deleted lines from main!
    assert added == 1
    assert deleted == 0
    assert pct == 0.0


def test_detect_governance_hazards_flags_anti_overfitting(tmp_path: Path) -> None:
    """Anti-overfitting patterns in dual_pipeline must trigger governance hazards."""
    hazards = _detect_governance_hazards(
        merge_base="HEAD",
        branch="HEAD",
        file_stats={
            "dual_pipeline/transcript_qa_core/graph_healer.py": (10, 0),
            "dual_pipeline/transcript_qa_core/standup_healer.py": (5, 0),
        },
        root=tmp_path,
        worktree_path=tmp_path,
    )
    # Standup healer is a prohibited filename
    assert any("standup_healer.py" in h for h in hazards)


def test_evaluate_review_verdict_governance_block_on_hazard() -> None:
    """When governance_hazards exist, review verdict must be GOVERNANCE_BLOCK."""
    signals = DeterministicSignals(
        governance_hazards=["Anti-overfitting violation in dual_pipeline/transcript_qa_core/graph_healer.py: hardcoded meeting string"]
    )
    verdict, reason, action = evaluate_review_verdict(signals, is_clean_merge=True, branch_name="feat/rca-output3")
    assert verdict == ReviewVerdict.GOVERNANCE_BLOCK
    assert "GOVERNANCE_BLOCK" in action


def test_detect_governance_hazards_flags_hardcoded_symphony_string(tmp_path: Path) -> None:
    """Hardcoded meeting strings in graph_healer.py must trigger anti-overfitting hazard."""
    repo = tmp_path / "wt"
    repo.mkdir()
    f = repo / "dual_pipeline" / "transcript_qa_core" / "graph_healer.py"
    f.parent.mkdir(parents=True)
    f.write_text('proposal = "proposal:symphony-programmatic-updates"\n', encoding="utf-8")

    hazards = _detect_governance_hazards(
        merge_base="HEAD",
        branch="HEAD",
        file_stats={"dual_pipeline/transcript_qa_core/graph_healer.py": (1, 0)},
        root=tmp_path,
        worktree_path=repo,
    )
    assert any("Anti-overfitting violation in dual_pipeline/transcript_qa_core/graph_healer.py" in h for h in hazards)


def test_detect_governance_hazards_flags_score_tampering(tmp_path: Path) -> None:
    """Artificial score bonuses or tampering in judge code must trigger hazard."""
    repo = tmp_path / "wt"
    repo.mkdir()
    f = repo / "dual_pipeline" / "llm_judge_core" / "judge_clarification.py"
    f.parent.mkdir(parents=True)
    f.write_text('eval_data["score"] += 1.5\n', encoding="utf-8")

    hazards = _detect_governance_hazards(
        merge_base="HEAD",
        branch="HEAD",
        file_stats={"dual_pipeline/llm_judge_core/judge_clarification.py": (1, 0)},
        root=tmp_path,
        worktree_path=repo,
    )
    assert any("Artificial judge score tampering detected" in h for h in hazards)


def test_analyze_branch_file_diffs_marks_deleted_on_main_as_absorbed(tmp_path: Path) -> None:
    """Files deleted on main since merge_base must be marked absorbed/superseded and not salvageable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)

    # Initial commit with a file that will later be deleted
    f1 = repo / "standup_healer.py"
    f1.write_text("def heal(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "standup_healer.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add standup_healer"], cwd=repo, check=True)

    # Create feature branch before deletion
    subprocess.run(["git", "checkout", "-b", "feat/stale"], cwd=repo, check=True)

    # In main, delete standup_healer.py
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    subprocess.run(["git", "rm", "standup_healer.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "delete standup_healer"], cwd=repo, check=True)

    # In feat/stale, modify standup_healer.py locally
    subprocess.run(["git", "checkout", "feat/stale"], cwd=repo, check=True)
    f1.write_text("def heal(): pass\n# stale modification\n", encoding="utf-8")

    topo = analyze_branch_file_diffs("feat/stale", root=repo, base="main", worktree_path=repo)
    # standup_healer.py was deleted on main; must be in absorbed, NOT salvageable
    assert "standup_healer.py" in topo.absorbed_files
    assert "standup_healer.py" not in topo.salvageable_files


def test_eval_decommission_or_salvage_governance_block() -> None:
    """GOVERNANCE_BLOCK verdict must prevent D.1 decommission and render Hold / Blocked."""
    from tools.pr_governance_core.executive_render import _eval_decommission_or_salvage

    item = {
        "branch": "feat/rca-output3-retry-and-judge-failure",
        "ahead": 0,
        "behind": 0,
        "dirty": True,
        "tier": "D.1",
        "next_action": "[PRUNE] stale meeting branch",
        "review_verdict": "GOVERNANCE_BLOCK",
        "signals": {
            "governance_hazards": ["Anti-overfitting violation in dual_pipeline/transcript_qa_core/graph_healer.py: hardcoded meeting string"]
        },
    }
    res = _eval_decommission_or_salvage(item, stat_str="")
    assert res is not None
    code_done, rebase_req, pr_req, disp, act = res
    assert disp == "Hold / Blocked"
    assert "Resolve failing governance gate" in act


def test_eval_decommission_or_salvage_salvage_subset_overrides_d1() -> None:
    """When unique salvageable files exist, Salvage Subset & Archive overrides D.1 prune."""
    from tools.pr_governance_core.executive_render import _eval_decommission_or_salvage

    item = {
        "branch": "feat/rca-apex_trans-ingestion-runtime",
        "ahead": 1,
        "behind": 447,
        "dirty": False,
        "tier": "D.1",
        "next_action": "[PRUNE] package ready",
        "review_verdict": "REVIEW_READY",
        "absorbed_files": ["publish_helper.py"],
        "salvageable_files": ["tests/test_unique_e2e.py"],
        "requires_hitl_clarification": True,
    }
    res = _eval_decommission_or_salvage(item, stat_str="")
    assert res is not None
    code_done, rebase_req, pr_req, disp, act = res
    assert disp == "Salvage Subset & Archive"
    assert "Extract 1 unique file(s)" in act


