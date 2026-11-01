"""
Adversarial and boundary tests for Wave 1 worktree supersession and zero-touch reversible archive.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.pr_governance_core.conflict_triage import (
    analyze_branch_file_diffs,
)
from tools.pr_governance_core.container_triage import (
    extract_meeting_containers,
    is_meeting_container_sealed_on_main,
)
from tools.pr_governance_core.merge_proof import BranchMergeState
from tools.pr_governance_core.verdict_verifier import verify_branch_verdict_and_disposition


def test_extract_meeting_containers() -> None:
    files = [
        "output/project_lens/10-09-2026/Standup_Amit/01_transcript.md",
        "output/project_lens/10-09-2026/Standup_Amit/02_recap.md",
        "output/apex_trans/09-28-2026/Intro/03_projection.json",
        "docs/plans/feat_plan.md",
        "dual_pipeline/some_code.py",
    ]
    containers = extract_meeting_containers(files)
    assert "output/project_lens/10-09-2026/Standup_Amit" in containers
    assert "output/apex_trans/09-28-2026/Intro" in containers
    assert len(containers) == 2


def test_superseded_meeting_container_and_ephemeral_files(tmp_path: Path) -> None:
    # Initialize a hermetic git repository fixture
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)

    # Initial commit on main with sealed container triad
    container_dir = repo / "output" / "project_lens" / "10-09-2026" / "Standup_Amit"
    container_dir.mkdir(parents=True)
    (container_dir / "01_Standup_Amit_hitl_resolved_transcript.md").write_text("01", encoding="utf-8")
    (container_dir / "02_Standup_Amit_executive_recap.md").write_text("02", encoding="utf-8")
    (container_dir / "03_Standup_Amit_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat(ingest): add standup amit sealed triad"], cwd=repo, check=True)

    # Verify is_meeting_container_sealed_on_main
    assert is_meeting_container_sealed_on_main("output/project_lens/10-09-2026/Standup_Amit", repo) is True
    assert is_meeting_container_sealed_on_main("output/project_lens/10-09-2026/Unknown", repo) is False

    # Create feature branch with leftover scratch files and redundant output files
    subprocess.run(["git", "checkout", "-b", "feat/live-standup-amit"], cwd=repo, check=True)
    (container_dir / "backtest_comparison_report.md").write_text("diff report", encoding="utf-8")
    reports_dir = repo / "reports"
    reports_dir.mkdir()
    (reports_dir / "dry_run_receipt.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "chore: add dry-run and comparison report"], cwd=repo, check=True)

    # Analyze branch diff topology
    topo = analyze_branch_file_diffs("feat/live-standup-amit", root=repo, base="main")

    # Verify disposition verdict using root=repo to enable container supersession filtering
    res = verify_branch_verdict_and_disposition({
        "branch": "feat/live-standup-amit",
        "ahead": 1,
        "behind": 0,
        "dirty": False,
        "absorbed_files": topo.absorbed_files,
        "salvageable_files": topo.salvageable_files,
    }, root=repo)

    # All files should be filtered to absorbed under sealed container or ephemeral receipt
    assert len(res.unabsorbed_files) == 0
    assert len(res.absorbed_files) == 2
    assert res.verified_disposition == "Decommission (No Value)"
    assert res.requires_hitl_clarification is False
    assert res.confidence_tier == "HIGH"
    assert res.c_top >= 0.95


def test_genuine_unabsorbed_work_still_requires_salvage(tmp_path: Path) -> None:
    repo = tmp_path / "repo2"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)

    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "-b", "feat/real-work"], cwd=repo, check=True)
    (repo / "dual_pipeline" / "my_module.py").parent.mkdir(parents=True)
    (repo / "dual_pipeline" / "my_module.py").write_text("def run(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: genuine code"], cwd=repo, check=True)

    topo = analyze_branch_file_diffs("feat/real-work", root=repo, base="main")
    assert "dual_pipeline/my_module.py" in topo.salvageable_files
    assert len(topo.absorbed_files) == 0


def test_cross_slug_meeting_container_and_stash_supersession(tmp_path: Path) -> None:
    """Verify cross-slug meeting container sealing and stash-backed ahead==0 worktree pruning vs code stash protection."""
    import json
    from tools.pr_governance_core.container_triage import (
        are_branch_stashes_superseded_on_main,
        filter_topology_absorbed_files,
    )
    from tools.pr_topology_core.disposition import _check_ahead_zero_worktree_safety
    from tools.pr_topology_core.matrix import BranchDiff

    root = tmp_path / "repo_cross_slug"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=root, check=True)

    # 1. Cross-slug PACKAGE_READY deliverable on main (Day_Life_Interview_1_Dustin_PartnerAdvisory)
    canon_dir = root / "output" / "project_jarvis" / "10-23-2026" / "Day_Life_Interview_1_Dustin_PartnerAdvisory"
    canon_dir.mkdir(parents=True, exist_ok=True)
    (canon_dir / "final_release_decision.json").write_text(json.dumps({
        "terminal_outcome": "PASS",
        "date": "10-23-2026",
        "project": "nea",
        "meeting_name": "JARVIS Day in the Life Interview 1",
    }))
    (canon_dir / "metadata.json").write_text(json.dumps({
        "date": "10/23/2026",
        "project": "nea",
        "meeting_name": "JARVIS Day in the Life Interview 1",
    }))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "Add sealed deliverables on main"], cwd=root, check=True)

    # Verify cross-slug match and dateless non-match
    assert is_meeting_container_sealed_on_main(
        "output/project_jarvis/10-23-2026/Jarvis_Day_Life_Interview_1_Traci", root
    ) is True
    assert is_meeting_container_sealed_on_main("output/project_jarvis/no_date_folder", root) is False

    # Verify filter_topology_absorbed_files marks cross-slug deliverable as absorbed
    plan_rel = "output/project_jarvis/10-23-2026/Jarvis_Day_Life_Interview_1_Traci/00_Jarvis_Day_Life_Interview_1_Traci_ingestion_plan.json"
    abs_out, salv_out, pcts_out = filter_topology_absorbed_files([plan_rel], root, [], [plan_rel], {plan_rel: 0.0})
    assert abs_out == [plan_rel]
    assert salv_out == []
    assert pcts_out[plan_rel] == 1.0

    # 2. Feature branch worktree with superseded untracked deliverable + superseded stash
    branch = "ingest/doc-project-jarvis-10-23-2026-day-life-interview-1-traci"
    wt_path = root / ".worktrees" / "ingest_doc_project_jarvis_10_23_2026_day_life_interview_1_traci"
    subprocess.run(["git", "worktree", "add", "-b", branch, str(wt_path), "main"], cwd=root, check=True, capture_output=True)

    assert are_branch_stashes_superseded_on_main("", root) is False
    assert are_branch_stashes_superseded_on_main(branch, root) is False

    plan_file = wt_path / plan_rel
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text('{"plan_id": "doc-ingest-plan-5c688ca9304ea3e3"}\n')
    ephem_file = wt_path / "reports" / "temp_receipt.json"
    ephem_file.parent.mkdir(parents=True, exist_ok=True)
    ephem_file.write_text("{}\n")
    subprocess.run(["git", "stash", "push", "-u", "-m", "pre-dehydrate-traci-10-27-2026"], cwd=wt_path, check=True, capture_output=True)

    # Re-create untracked plan file on disk (mirroring exact production worktree state)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text('{"plan_id": "doc-ingest-plan-5c688ca9304ea3e3"}\n')

    assert are_branch_stashes_superseded_on_main(branch, root) is True
    diff = BranchDiff(branch=branch, ahead=0, behind=25, files=[])
    wt_dict = {"path": str(wt_path), "branch": branch, "dirty": True, "stash_count": 1, "authored_files_count": 1}
    disp = _check_ahead_zero_worktree_safety(branch, diff, wt_dict, wt_path, {}, root, "main")
    assert disp is not None
    assert disp.action == "PRUNE"
    assert "Superseded by PACKAGE_READY deliverable on main" in disp.reason

    # 3. Adversarial check: if branch has a stash with unabsorbed code, it MUST NOT prune (must return COMMIT_FIRST)
    code_f = wt_path / "dual_pipeline" / "new_logic.py"
    code_f.parent.mkdir(parents=True, exist_ok=True)
    code_f.write_text("x = 1\n")
    subprocess.run(["git", "stash", "push", "-u", "-m", "code-stash"], cwd=wt_path, check=True, capture_output=True)
    assert are_branch_stashes_superseded_on_main(branch, root) is False
    disp_code_stash = _check_ahead_zero_worktree_safety(branch, diff, wt_dict, wt_path, {}, root, "main")
    assert disp_code_stash is not None
    assert disp_code_stash.action == "COMMIT_FIRST"
