"""Wave 2 Adversarial Tests: Tree-OID / Delta-OID Out-of-Tree Receipts, Conflict-Free Merge Auth & Hermetic HITL Store.

Covers:
1. Disjoint rebase invariance without receipt JSON mutation or `_rebind_receipts_after_catchup`.
2. Invariant bypass / tree-tampering rejection on modified branch blobs.
3. Worktree-local `learning.sqlite` isolation, `CalibratedScore` anti-spoofing, and strict `_match_keys`.
4. Zero `reports/pr_merge_*` files committed to `main` during governed merge.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from dual_pipeline.hitl_core.bayesian import CalibratedScore, _match_keys
from dual_pipeline.transcript_qa_core.healing_decision_store import (
    HealingDecisionRecord,
    _normalize_record,
    get_default_healing_sqlite_path,
)
from tools.pr_governance_core import lifecycle
from tools.pr_governance_core.attestations import (
    PR_REVIEW_PREDICATE_TYPE,
    READINESS_PREDICATE_TYPE,
    check_readiness_qualification,
    compute_git_tree_oid,
    resolve_branch_tree_attestation,
    seal_branch_tree_attestation,
)
from tools.pr_governance_core.merge_queue import (
    record_branch_merge_authorization,
    verify_branch_merge_authorization,
)
from tools.pr_governance_core.sync import rebase_branch_onto_main
from tools.verify_post_merge import verify_latest_merge


def _git_env() -> dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k not in banned}
    env["GIT_AUTHOR_NAME"] = "Test Operator"
    env["GIT_AUTHOR_EMAIL"] = "operator@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Operator"
    env["GIT_COMMITTER_EMAIL"] = "operator@example.com"
    return env


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30.0,
    )
    return proc.stdout.strip()


def test_disjoint_rebase_preserves_dsse_qualification_without_mutating_reports(tmp_path: Path) -> None:
    """Two branches reviewed against main@A: Branch 1 merges (main@B), Branch 2 rebases onto main@B and stays valid without JSON rewrites."""
    assert not hasattr(lifecycle, "_rebind_receipts_after_catchup")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Operator")
    _git(repo, "config", "user.email", "operator@example.com")
    (repo / "core.py").write_text("CORE = 1\n", encoding="utf-8")
    _git(repo, "add", "core.py")
    _git(repo, "commit", "-m", "chore: initial main")
    main_a = _git(repo, "rev-parse", "main")

    # Branch 1 modifies b1.py
    _git(repo, "checkout", "-b", "feat/branch-one")
    (repo / "b1.py").write_text("B1 = True\n", encoding="utf-8")
    _git(repo, "add", "b1.py")
    _git(repo, "commit", "-m", "feat: add b1")

    # Branch 2 modifies b2.py from main@A
    _git(repo, "checkout", "-b", "feat/branch-two", main_a)
    (repo / "b2.py").write_text("B2 = True\n", encoding="utf-8")
    _git(repo, "add", "b2.py")
    _git(repo, "commit", "-m", "feat: add b2")
    b2_sha_before = _git(repo, "rev-parse", "feat/branch-two")

    # Seal out-of-tree review and readiness attestations for Branch 2 against main@A
    seal_branch_tree_attestation(
        repo_root=repo,
        branch="feat/branch-two",
        head_sha=b2_sha_before,
        predicate={
            "branch": "feat/branch-two",
            "head_sha": b2_sha_before,
            "base_commit_sha": main_a,
            "terminal_outcome": "PASS",
            "readiness_verified": True,
            "clean_merge": True,
        },
        predicate_type=PR_REVIEW_PREDICATE_TYPE,
    )
    seal_branch_tree_attestation(
        repo_root=repo,
        branch="feat/branch-two",
        head_sha=b2_sha_before,
        predicate={
            "branch": "feat/branch-two",
            "head_commit_sha": b2_sha_before,
            "base_commit_sha": main_a,
            "terminal_outcome": "PASS",
            "total_stages": 30,
        },
        predicate_type=READINESS_PREDICATE_TYPE,
    )

    # Merge Branch 1 into main -> main@B
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge branch 'feat/branch-one' into main (Branch 1)", "feat/branch-one")
    main_b = _git(repo, "rev-parse", "main")
    assert main_b != main_a

    # Rebase Branch 2 onto main@B
    outcome = rebase_branch_onto_main(
        branch="feat/branch-two",
        base_branch="main",
        repo_root=repo,
        work_dir=repo,
        use_lock=False,
    )
    assert outcome.status == "SUCCESS"
    b2_sha_after = outcome.new_head_sha
    assert b2_sha_after != b2_sha_before

    # Verify zero files in reports/ were created or mutated
    assert not (repo / "reports").exists()

    # Verify Branch 2 review & readiness attestations resolve cleanly for b2_sha_after via delta OID
    resolved_rev = resolve_branch_tree_attestation(repo, "feat/branch-two", b2_sha_after)
    assert resolved_rev is not None
    assert resolved_rev["head_sha"] == b2_sha_after
    assert resolved_rev["base_commit_sha"] == main_b

    rd_ok, rd_stmt, _ = check_readiness_qualification(
        repo, "feat/branch-two", b2_sha_after, "feat_branch_two"
    )
    assert rd_ok is True
    assert rd_stmt is not None
    assert rd_stmt["predicate"]["base_commit_sha"] == main_b


def test_adversarial_tree_tampering_invalidates_attestations_and_merge_auth(tmp_path: Path) -> None:
    """Modifying a branch file after qualification invalidates both review attestation and per-branch merge auth."""
    repo = tmp_path / "tamper_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "base.py").write_text("X = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "chore: base")

    _git(repo, "checkout", "-b", "feat/tamper")
    (repo / "mod.py").write_text("SAFE = 1\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, "commit", "-m", "feat: safe commit")
    safe_sha = _git(repo, "rev-parse", "feat/tamper")
    safe_tree = compute_git_tree_oid(repo, ref=safe_sha)

    seal_branch_tree_attestation(
        repo_root=repo,
        branch="feat/tamper",
        head_sha=safe_sha,
        predicate={"branch": "feat/tamper", "head_sha": safe_sha, "terminal_outcome": "PASS"},
    )
    record_branch_merge_authorization(repo, "feat/tamper", branch_tree_oid=safe_tree)
    ok_before, _ = verify_branch_merge_authorization(repo, "feat/tamper", branch_tree_oid=safe_tree)
    assert ok_before is True

    # Tamper with mod.py on feat/tamper
    (repo / "mod.py").write_text("SAFE = 0  # tampered\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, "commit", "-m", "feat: tamper commit")
    tampered_sha = _git(repo, "rev-parse", "feat/tamper")
    tampered_tree = compute_git_tree_oid(repo, ref=tampered_sha)

    assert resolve_branch_tree_attestation(repo, "feat/tamper", tampered_sha) is None
    ok_after, _ = verify_branch_merge_authorization(repo, "feat/tamper", branch_tree_oid=tampered_tree)
    assert ok_after is False


def test_zero_tracked_pr_merge_receipt_in_merge_commit(tmp_path: Path) -> None:
    """Governed merge commit with trailers and untracked receipt leaves zero `reports/pr_merge_*` in git diff-tree."""
    repo = tmp_path / "merge_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "service.py").write_text("V = 1\n", encoding="utf-8")
    _git(repo, "add", "service.py")
    _git(repo, "commit", "-m", "chore: init")
    main_base = _git(repo, "rev-parse", "main")

    _git(repo, "checkout", "-b", "feat/no-tracked-receipt")
    (repo / "feature.py").write_text("F = 2\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "feat: add feature")
    branch_head = _git(repo, "rev-parse", "feat/no-tracked-receipt")

    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "--no-commit", "feat/no-tracked-receipt")
    title_line = "Merge branch 'feat/no-tracked-receipt' into main (Clean trailer merge)"
    full_msg = (
        f"{title_line}\n\n"
        f"PR-Merge-Target: main\n"
        f"PR-Branch-Head: {branch_head}\n"
        f"PR-Main-Base: {main_base}\n"
        f"PR-Terminal-Outcome: PASS"
    )
    _git(repo, "commit", "-m", full_msg)

    committed_files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    assert not any(f.startswith("reports/pr_merge_") for f in committed_files)
    ok, detail = verify_latest_merge(repo)
    assert ok is True, detail


def test_hermetic_worktree_learning_sqlite_and_bayesian_anti_spoofing(tmp_path: Path, monkeypatch) -> None:
    """Worktree CWD never traverses upward to pollute root learning.sqlite; CalibratedScore and _match_keys are strict."""
    fake_root = tmp_path / "primary_repo"
    fake_wt = fake_root / ".worktrees" / "feat_isolated"
    fake_wt.mkdir(parents=True)

    monkeypatch.delenv("NEE_TEST_LEARNING_SQLITE", raising=False)
    monkeypatch.delenv("NEE_HEALING_SQLITE_PATH", raising=False)
    monkeypatch.delenv("NEE_LEARNING_SQLITE_PATH", raising=False)
    monkeypatch.delenv("NEE_ALLOW_TEST_FIXTURES", raising=False)
    monkeypatch.delenv("MOCK_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("NEE_RUNTIME_ROOT", raising=False)
    monkeypatch.chdir(fake_wt)

    resolved = get_default_healing_sqlite_path()
    assert resolved == (fake_wt / ".local" / "ledger" / "learning.sqlite").resolve()
    assert not str(resolved).startswith(str((fake_root / ".local").resolve()))

    norm = _normalize_record({"subsystem": "qa", "action_taken": "NORMALIZE"})
    assert isinstance(norm, HealingDecisionRecord)
    assert norm.confidence_top < 1.0

    score = CalibratedScore(0.75)
    assert not hasattr(score, "replace")
    assert _match_keys(
        "Approve schema migration for billing table",
        "Approve schema migration for user auth",
    ) is False
