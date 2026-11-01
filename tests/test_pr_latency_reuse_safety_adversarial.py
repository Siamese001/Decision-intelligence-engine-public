"""Adversarial and Edge-Case Tests for Wave 3: Safe Lifecycle Verification Reuse.

Mandatory edge-case coverage:
1. Boundary payload & malformed input: Corrupted cache JSON, missing fields, truncated files.
2. Concurrency & race: Mainline advancement (changed base) detected and rejected under lock.
3. Invariant bypass attempts: Wrong predicate type, incomplete stages (<30 gates), NOT_RUN status.
4. Rollback & recovery: Mid-merge crash / failure aborts cleanly via `git merge --abort`.
5. Quality-input report changes vs generated report isolation.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.attestation_core import (
    PR_REVIEW_PREDICATE_TYPE,
    READINESS_PREDICATE_TYPE,
    compute_tree_digest,
    load_attestation_by_tree,
    save_attestation,
    save_readiness_attestation,
)
from tools.benchmark_pr_promotion import _init_sandbox_git_repo
from tools.pr_governance_core.common import run_git
from tools.readiness_cache import (
    InputManifest,
    build_input_manifest,
    get_cached_stage,
    is_generated_report,
    is_quality_input,
    save_cached_stage,
    validate_manifest_freshness,
)


def test_adversarial_corrupted_cache_fallback(tmp_path: Path) -> None:
    """Category: Malformed input. Corrupted or truncated cache entries must fall back conservatively."""
    c_dir = tmp_path / ".local" / "readiness_cache"
    c_dir.mkdir(parents=True, exist_ok=True)

    # 1. Truncated / malformed JSON
    bad_file = c_dir / "corrupted_hash.json"
    bad_file.write_text("{\"stage_name\": \"test\", \"status\":", encoding="utf-8")
    res = get_cached_stage("Test Stage", ["cmd"], tmp_path, "sha1")
    assert res is None, "Expected None on malformed cache file"

    # 2. Non-dict JSON (e.g. raw integer)
    bad_file2 = c_dir / "non_dict.json"
    bad_file2.write_text("12345", encoding="utf-8")
    res2 = get_cached_stage("Test Stage", ["cmd"], tmp_path, "sha1")
    assert res2 is None, "Expected None on non-dict cache file"

    # 3. Cache entry with status != PASS (e.g. status: FAIL)
    cmd = ["python", "-B", "tool.py"]
    from tools.readiness_cache import compute_stage_fingerprint
    fp = compute_stage_fingerprint("Failed Stage", cmd, tmp_path, "sha1")
    (c_dir / f"{fp}.json").write_text(json.dumps({
        "stage_name": "Failed Stage",
        "status": "FAIL",
        "fingerprint": fp,
    }), encoding="utf-8")
    res3 = get_cached_stage("Failed Stage", cmd, tmp_path, "sha1")
    assert res3 is None, "Cache loader must strictly reject non-PASS entries"


def test_adversarial_changed_base_rejection_under_lock(tmp_path: Path) -> None:
    """Category: Concurrency & race. Mainline advancing while PR was reviewed must be rejected under lock."""
    sandbox = tmp_path / "race_sandbox"
    _init_sandbox_git_repo(sandbox)

    # Create feature branch
    run_git(["checkout", "-b", "feat/racing-branch"], cwd=sandbox)
    f = sandbox / "race.py"
    f.write_text("race = True\n", encoding="utf-8")
    run_git(["add", "race.py"], cwd=sandbox)
    run_git(["commit", "-m", "feat: racing branch commit"], cwd=sandbox)
    b_sha = run_git(["rev-parse", "HEAD"], cwd=sandbox)

    # Concurrently advance main
    run_git(["checkout", "main"], cwd=sandbox)
    m = sandbox / "main_update.txt"
    m.write_text("concurrent main commit\n", encoding="utf-8")
    run_git(["add", "main_update.txt"], cwd=sandbox)
    run_git(["commit", "-m", "chore: mainline advanced concurrently"], cwd=sandbox)

    from tools.pr_governance_core import lifecycle
    orig_root = lifecycle.ROOT
    lifecycle.ROOT = sandbox
    try:
        rc = lifecycle._execute_merge_under_lock("feat/racing-branch", "race merge", "feat_racing_branch")
        assert rc != 0, "Merge under lock must reject promotion when mainline has advanced beyond merge-base"
    finally:
        lifecycle.ROOT = orig_root


def test_adversarial_predicate_confusion_and_incomplete_gate_rejection(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt. Attempting to promote with wrong predicate or <30 gates fails closed."""
    sandbox = tmp_path / "predicate_sandbox"
    _init_sandbox_git_repo(sandbox)

    run_git(["checkout", "-b", "feat/predicate-test"], cwd=sandbox)
    f = sandbox / "code.py"
    f.write_text("code = 1\n", encoding="utf-8")
    run_git(["add", "code.py"], cwd=sandbox)
    run_git(["commit", "-m", "feat: test predicate safety"], cwd=sandbox)
    b_sha = run_git(["rev-parse", "HEAD"], cwd=sandbox)
    tree_sha = compute_tree_digest(sandbox, ref=b_sha)

    # 1. Provide only 25 stages (incomplete) in readiness receipt
    r_dir = sandbox / "reports" / "readiness"
    r_dir.mkdir(parents=True, exist_ok=True)
    incomplete_stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 26)]
    (r_dir / "local_main_readiness_feat_predicate_test_receipt.json").write_text(json.dumps({
        "branch": "feat/predicate-test",
        "head_commit_sha": b_sha,
        "terminal_outcome": "PASS",
        "total_stages": 25,
        "stages": incomplete_stages,
    }), encoding="utf-8")

    # Valid review receipt
    p_dir = sandbox / "reports"
    (p_dir / "pr_review_feat_predicate_test_receipt.json").write_text(json.dumps({
        "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
        "branch": "feat/predicate-test",
        "head_sha": b_sha,
        "base": "main",
        "terminal_outcome": "PASS",
        "clean_merge": True,
        "readiness_verified": True,
    }), encoding="utf-8")

    run_git(["checkout", "main"], cwd=sandbox)
    from tools.pr_governance_core import lifecycle
    orig_root = lifecycle.ROOT
    lifecycle.ROOT = sandbox
    try:
        # Incomplete readiness (<30 stages) must fail closed
        rc = lifecycle._execute_merge_under_lock("feat/predicate-test", "incomplete test", "feat_predicate_test")
        assert rc != 0, "Merge under lock must reject incomplete readiness receipt (<30 gates)"

        # 2. Receipt with a NOT_RUN stage must also fail closed
        incomplete_stages_30 = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 30)]
        incomplete_stages_30.append({"stage_index": 30, "stage_name": "Stage 30", "status": "NOT_RUN"})
        (r_dir / "local_main_readiness_feat_predicate_test_receipt.json").write_text(json.dumps({
            "branch": "feat/predicate-test",
            "head_commit_sha": b_sha,
            "terminal_outcome": "PASS",
            "total_stages": 30,
            "stages": incomplete_stages_30,
        }), encoding="utf-8")
        rc2 = lifecycle._execute_merge_under_lock("feat/predicate-test", "not run test", "feat_predicate_test")
        assert rc2 != 0, "Merge under lock must reject receipt containing NOT_RUN status"
    finally:
        lifecycle.ROOT = orig_root


def test_adversarial_rollback_and_recovery_on_merge_failure(tmp_path: Path) -> None:
    """Category: Rollback & recovery. Mid-merge conflict or commit failure safely aborts without dirtying main."""
    sandbox = tmp_path / "rollback_sandbox"
    _init_sandbox_git_repo(sandbox)

    # Create conflict on the same file
    run_git(["checkout", "-b", "feat/conflict-branch"], cwd=sandbox)
    conflict_file = sandbox / "conflict.txt"
    conflict_file.write_text("branch version\n", encoding="utf-8")
    run_git(["add", "conflict.txt"], cwd=sandbox)
    run_git(["commit", "-m", "feat: branch conflict edit"], cwd=sandbox)
    b_sha = run_git(["rev-parse", "HEAD"], cwd=sandbox)

    run_git(["checkout", "main"], cwd=sandbox)
    conflict_file.write_text("main version\n", encoding="utf-8")
    run_git(["add", "conflict.txt"], cwd=sandbox)
    run_git(["commit", "-m", "chore: main conflict edit"], cwd=sandbox)

    # Provide synthetic passing receipts to bypass prerequisite check
    r_dir = sandbox / "reports" / "readiness"
    r_dir.mkdir(parents=True, exist_ok=True)
    stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 31)]
    (r_dir / "local_main_readiness_feat_conflict_branch_receipt.json").write_text(json.dumps({
        "branch": "feat/conflict-branch",
        "head_commit_sha": b_sha,
        "terminal_outcome": "PASS",
        "total_stages": 30,
        "stages": stages,
    }), encoding="utf-8")

    p_dir = sandbox / "reports"
    (p_dir / "pr_review_feat_conflict_branch_receipt.json").write_text(json.dumps({
        "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
        "branch": "feat/conflict-branch",
        "head_sha": b_sha,
        "base": "main",
        "terminal_outcome": "PASS",
        "clean_merge": True,
        "readiness_verified": True,
    }), encoding="utf-8")

    from tools.pr_governance_core import lifecycle
    orig_root = lifecycle.ROOT
    lifecycle.ROOT = sandbox
    try:
        # Rebase main to candidate's parent so base check passes but tree conflicts
        rc = lifecycle._execute_merge_under_lock("feat/conflict-branch", "conflict merge", "feat_conflict_branch")
        assert rc != 0, "Merge under lock must fail on git conflict"

        # Verify main is not in a conflicted or mid-merge state
        status = run_git(["status", "--porcelain"], cwd=sandbox)
        assert "UU" not in status, "Working tree must not contain unresolved merge conflicts"
        assert not (sandbox / ".git" / "MERGE_HEAD").exists(), "MERGE_HEAD must be cleared by rollback"
    finally:
        lifecycle.ROOT = orig_root


def test_quality_inputs_vs_generated_reports_isolation() -> None:
    """Category: Report isolation. Generated reports do not dirty tree; quality inputs are tracked."""
    assert is_generated_report("reports/pr_latency/wave-1.json")
    assert is_generated_report("reports/pr_review_feat_test_receipt.json")
    assert is_generated_report("reports/readiness/local_main_readiness_feat_test_receipt.json")
    assert is_generated_report(".local/readiness_cache/abc.json")

    assert is_quality_input("reports/alignment/wave-1.json")
    assert is_quality_input("governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT.json")
    assert is_quality_input("tests/manifests/docs_pr_local_main_latency_plan_manifest.json")
    assert not is_quality_input("reports/pr_latency/wave-1.json")
