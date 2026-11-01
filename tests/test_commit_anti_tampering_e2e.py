"""Wave 5 E2E Tests: Commit-Time Anti-Tampering, Main-Pinned Ratchets & Healer Freeze."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from tools.change_lane_classifier import classify_changes, classify_file, load_policy
from tools.file_budget_core.ratchet import load_ratchet_baseline
from tools.pre_commit_orchestrator import (
    verify_pre_commit_receipt_for_head,
    write_pre_commit_receipt,
)
from tools.shift_left_core.cognitive import (
    check_complexity_baseline_inflation,
    load_complexity_baseline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_env() -> dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k not in banned}
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


def test_e2e_baseline_inflation_pinned_to_main(tmp_path: Path) -> None:
    """Proves branch inflation of *_baseline.json is caught and clamped to main via git show main:<path>."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    reports_dir = repo / "reports"
    reports_dir.mkdir()

    fb_path = reports_dir / "file_budget_burndown_baseline.json"
    cc_path = reports_dir / "cognitive_complexity_baseline.json"

    fb_path.write_text(
        json.dumps({"modules": {"dual_pipeline/foo.py": 450}}),
        encoding="utf-8",
    )
    cc_path.write_text(
        json.dumps({"functions": {"dual_pipeline/foo.py::bar": 18}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "seed main baselines"], cwd=repo, env=env, check=True)

    subprocess.run(["git", "checkout", "-b", "feat/inflate-attempt"], cwd=repo, env=env, check=True)
    # Branch attempts to inflate both baselines and add a new unapproved entry
    fb_path.write_text(
        json.dumps({"modules": {"dual_pipeline/foo.py": 900, "dual_pipeline/new_big.py": 700}}),
        encoding="utf-8",
    )
    cc_path.write_text(
        json.dumps({"functions": {"dual_pipeline/foo.py::bar": 35}}),
        encoding="utf-8",
    )

    loaded_fb = load_ratchet_baseline(fb_path)
    assert loaded_fb["modules"]["dual_pipeline/foo.py"] == 450
    assert "dual_pipeline/new_big.py" not in loaded_fb["modules"]
    assert len(loaded_fb.get("_inflation_violations", [])) == 2

    cc_violations = check_complexity_baseline_inflation(cc_path)
    assert len(cc_violations) == 1
    assert "exceeds main ceiling 18" in cc_violations[0]
    clamped_cc = load_complexity_baseline(cc_path)
    assert clamped_cc["dual_pipeline/foo.py::bar"] == 18


def test_e2e_pre_commit_receipt_detects_no_verify_bypass(tmp_path: Path) -> None:
    """Proves valid pre-commit writes tree-bound receipt while --no-verify commit fails receipt verification."""
    repo = tmp_path / "repo_receipt"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, env=env, check=True)
    write_pre_commit_receipt(repo, staged=True)
    subprocess.run(["git", "commit", "-m", "verified commit"], cwd=repo, env=env, check=True)

    ok, msg = verify_pre_commit_receipt_for_head(repo)
    assert ok is True, msg

    # Now simulate a commit that bypassed pre-commit (--no-verify) so receipt was not updated for new tree
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "--no-verify", "-m", "unverified commit"], cwd=repo, env=env, check=True)

    ok2, msg2 = verify_pre_commit_receipt_for_head(repo)
    assert ok2 is False
    assert "no-verify" in msg2


def test_e2e_risk_lanes_and_scheduled_task_registry_policies() -> None:
    """Verifies reports/*_baseline.json escalates to Lane 2 and task_registry.json is report_only."""
    policy = load_policy(REPO_ROOT / "config" / "governance" / "risk_lanes_policy.json")
    lane1, _ = classify_file("reports/file_budget_burndown_baseline.json", policy)
    lane2, _ = classify_file("reports/cognitive_complexity_baseline.json", policy)
    assert lane1 == 2 and lane2 == 2
    res = classify_changes(
        ["reports/file_budget_burndown_baseline.json", "reports/cognitive_complexity_baseline.json"],
        policy_path=REPO_ROOT / "config" / "governance" / "risk_lanes_policy.json",
    )
    assert res["effective_lane"] == 2

    reg = json.loads((REPO_ROOT / "config" / "scheduled_tasks" / "task_registry.json").read_text(encoding="utf-8"))
    assert reg.get("execution_mode") == "report_only"
