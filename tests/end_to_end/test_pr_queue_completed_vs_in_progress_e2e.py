"""End-to-end verification of Queue Agent Completed vs. In-Progress branch separation."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _provision_multi_branch_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    state = repo / ".local" / "pr_queue_observer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Queue Test")
    _git(repo, "config", "user.email", "queue-test@example.invalid")

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: base")

    # 1. Create an in-progress branch with dirty uncommitted file in a worktree
    wt_root = repo / ".worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_dirty = wt_root / "feat_alpha_in_progress_dirty"
    _git(repo, "worktree", "add", "-b", "feat/alpha-in-progress-dirty", str(wt_dirty), "main")
    (wt_dirty / "alpha.py").write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")
    _git(wt_dirty, "add", "alpha.py")
    _git(wt_dirty, "commit", "-m", "feat: alpha committed")
    # Uncommitted edit makes it in-progress
    (wt_dirty / "alpha_draft.py").write_text("def draft() -> int:\n    return 2\n", encoding="utf-8")

    # 2. Create an in-progress branch with open waves in its implementation plan
    wt_open_waves = wt_root / "feat_beta_in_progress_waves"
    _git(repo, "worktree", "add", "-b", "feat/beta-in-progress-waves", str(wt_open_waves), "main")
    (wt_open_waves / "beta.py").write_text("def beta() -> int:\n    return 3\n", encoding="utf-8")
    plan_dir = wt_open_waves / "docs" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "feat_beta_in_progress_waves.md").write_text(
        "# Plan\n\n## Wave Implementation & Completion Status Table\n\n"
        "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| **Wave 1** | Setup | ✅ `COMPLETE` | Done | Passed |\n"
        "| **Wave 2** | Followup | ⏳ `OPEN` | Pending | Pending |\n",
        encoding="utf-8",
    )
    _git(wt_open_waves, "add", "beta.py", "docs/plans/feat_beta_in_progress_waves.md")
    _git(wt_open_waves, "commit", "-m", "feat: beta wave 1")

    # 3. Create a completed & ready branch with clean worktree and all waves complete
    wt_completed = wt_root / "feat_zeta_completed_ready"
    _git(repo, "worktree", "add", "-b", "feat/zeta-completed-ready", str(wt_completed), "main")
    (wt_completed / "zeta.py").write_text("def zeta() -> int:\n    return 42\n", encoding="utf-8")
    c_plan_dir = wt_completed / "docs" / "plans"
    c_plan_dir.mkdir(parents=True, exist_ok=True)
    (c_plan_dir / "feat_zeta_completed_ready.md").write_text(
        "# Plan\n\n## Wave Implementation & Completion Status Table\n\n"
        "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| **Wave 1** | Core | ✅ `COMPLETE` | Done | Passed |\n",
        encoding="utf-8",
    )
    _git(wt_completed, "add", "zeta.py", "docs/plans/feat_zeta_completed_ready.md")
    _git(wt_completed, "commit", "-m", "feat: zeta complete")

    return repo, state


def test_pr_queue_observer_and_executive_view_separate_completed_vs_in_progress_e2e(tmp_path: Path) -> None:
    repo, state = _provision_multi_branch_repo(tmp_path)

    observer_script = ROOT / "tools" / "pr_queue_observer.py"
    res = subprocess.run(
        [
            sys.executable,
            "-B",
            str(observer_script),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(state),
            "--mode",
            "interactive",
            "--launch-origin",
            "manual",
            "--format",
            "table",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].startswith("| Priority | Branch | Category | Complexity | Code Completed? |")

    branch_rows = [
        line for line in lines[2:]
        if "feat/" in line
    ]
    assert len(branch_rows) == 3

    # Verify completed branch sorts ahead of both in-progress branches even though 'zeta' is alphabetically last
    assert "feat/zeta-completed-ready" in branch_rows[0]
    assert "| **Y** |" in branch_rows[0]
    assert "[COMPLETED — READY]" in branch_rows[0]

    for in_prog_row in branch_rows[1:]:
        assert "| **N** |" in in_prog_row
        assert "[IN PROGRESS —" in in_prog_row

    # Verify durable decisions.json readback
    baseline = json.loads((state / "interactive_baseline.json").read_text(encoding="utf-8"))
    run_id = baseline["run_id"]
    decisions_path = state / "runs" / run_id / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))

    assert decisions["completed_branch_count"] == 1
    assert decisions["in_progress_branch_count"] == 2
    assert decisions["completed_branches"] == ["feat/zeta-completed-ready"]
    assert set(decisions["in_progress_branches"]) == {
        "feat/alpha-in-progress-dirty",
        "feat/beta-in-progress-waves",
    }

    by_branch = {b["branch"]: b for b in decisions["branches"]}
    assert by_branch["feat/zeta-completed-ready"]["completion_state"] == "COMPLETED"
    assert by_branch["feat/zeta-completed-ready"]["is_completed"] is True
    assert by_branch["feat/zeta-completed-ready"]["is_in_progress"] is False
    assert by_branch["feat/alpha-in-progress-dirty"]["completion_state"] == "IN_PROGRESS"
    assert by_branch["feat/alpha-in-progress-dirty"]["is_in_progress"] is True
    assert by_branch["feat/beta-in-progress-waves"]["completion_state"] == "IN_PROGRESS"
    assert by_branch["feat/beta-in-progress-waves"]["is_in_progress"] is True

    # Also verify pr_governance.py list --view executive --from-snapshot renders the footer split
    gov_script = ROOT / "tools" / "pr_governance.py"
    gov_res = subprocess.run(
        [
            sys.executable,
            "-B",
            str(gov_script),
            "list",
            "--view",
            "executive",
            "--from-snapshot",
            str(decisions_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert gov_res.returncode == 0, gov_res.stderr
    assert "Completed & ready branches: 1 · In-progress branches: 2 · Decommission: 0" in gov_res.stdout
