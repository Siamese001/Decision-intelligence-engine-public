"""
tests/adversarial/test_pr_promotion_optimization_safety.py

Adversarial safety test suite for Wave 4 PR promotion optimizations:
1. Base and head cache invalidation.
2. Mock/live and policy/model cache isolation.
3. Corrupt checkpoints and non-reusability of failures.
4. Early deterministic syntax rejection (interrupted repairs).
5. Concurrent merge lock acquisition and serialization.
6. Promotion rollback and fail-closed integrity.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock, LockContentionError
from tools.pr_governance_core.promotion import check_diff_syntax
from tools.readiness_planner import validate_readiness_cache_binding


# ==============================================================================
# 1. Base & Head Invalidation
# ==============================================================================


def test_cache_binding_valid_match():
    entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "abc1234567890",
        "base_commit_sha": "def9876543210",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="abc1234567890",
        current_base_sha="def9876543210",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is True
    assert msg == "VALID_REUSE"


def test_cache_binding_head_sha_mismatch():
    entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "old_head_sha_111",
        "base_commit_sha": "base_sha_999",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="new_head_sha_222",
        current_base_sha="base_sha_999",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Head commit SHA mismatch" in msg


def test_cache_binding_base_sha_mismatch():
    entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "head_sha_111",
        "base_commit_sha": "old_main_base_000",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="head_sha_111",
        current_base_sha="new_main_base_111",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Base commit SHA mismatch" in msg


# ==============================================================================
# 2. Mock/Live & Policy Manifest Isolation
# ==============================================================================


def test_cache_binding_policy_manifest_version_mismatch():
    entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "head_sha_111",
        "base_commit_sha": "base_sha_999",
        "manifest_version": "0.9.0",
        "is_live": True,
    }
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="head_sha_111",
        current_base_sha="base_sha_999",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Policy manifest version mismatch" in msg


def test_cache_binding_mock_live_boundary_isolation():
    entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "head_sha_111",
        "base_commit_sha": "base_sha_999",
        "manifest_version": "1.0.0",
        "is_live": False,
        "is_mock": True,
    }
    # Demanding live mode execution
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="head_sha_111",
        current_base_sha="base_sha_999",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "cannot reuse cache across live/mock boundary" in msg


# ==============================================================================
# 3. Corrupt Checkpoints & Failure Reusability Prevention
# ==============================================================================


def test_cache_binding_corrupt_entry():
    valid, msg = validate_readiness_cache_binding(
        "corrupted_non_json_string",  # type: ignore
        current_head_sha="head_sha_111",
        current_base_sha="base_sha_999",
        current_manifest_version="1.0.0",
    )
    assert valid is False
    assert "Corrupt checkpoint" in msg


def test_cache_binding_failure_never_reusable():
    entry = {
        "terminal_outcome": "FAIL",
        "head_commit_sha": "head_sha_111",
        "base_commit_sha": "base_sha_999",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, msg = validate_readiness_cache_binding(
        entry,
        current_head_sha="head_sha_111",
        current_base_sha="base_sha_999",
        current_manifest_version="1.0.0",
    )
    assert valid is False
    assert "Failure is never reusable" in msg


# ==============================================================================
# 4. Early Deterministic Syntax Rejection
# ==============================================================================


def test_early_syntax_precheck_valid(tmp_path: Path):
    py_file = tmp_path / "valid_code.py"
    py_file.write_text("def foo():\n    return 42\n", encoding="utf-8")
    json_file = tmp_path / "valid_data.json"
    json_file.write_text('{"key": "value", "count": 10}', encoding="utf-8")

    ok, err = check_diff_syntax(["valid_code.py", "valid_data.json"], tmp_path)
    assert ok is True
    assert err is None


def test_early_syntax_precheck_invalid_python(tmp_path: Path):
    py_file = tmp_path / "broken_code.py"
    py_file.write_text("def foo() broken_syntax: \n", encoding="utf-8")

    ok, err = check_diff_syntax(["broken_code.py"], tmp_path)
    assert ok is False
    assert err is not None
    assert "Python syntax error in broken_code.py" in err


def test_early_syntax_precheck_invalid_json(tmp_path: Path):
    json_file = tmp_path / "broken_data.json"
    json_file.write_text('{"key": "unclosed_brace"', encoding="utf-8")

    ok, err = check_diff_syntax(["broken_data.json"], tmp_path)
    assert ok is False
    assert err is not None
    assert "JSON syntax error in broken_data.json" in err


# ==============================================================================
# 5. Concurrent Merge Queue Lock Safety
# ==============================================================================


def test_concurrent_merge_lock_safety(tmp_path: Path):
    repo_mock = tmp_path / "repo"
    repo_mock.mkdir()
    git_dir = repo_mock / ".git"
    git_dir.mkdir()

    lock1 = GovernedMergeQueueLock("feat/branch-1", repo_root=repo_mock, timeout_sec=30.0)
    lock2 = GovernedMergeQueueLock(
        "feat/branch-2", repo_root=repo_mock, timeout_sec=0.2, retry_interval_sec=0.05
    )

    with lock1:
        # lock1 is actively held; lock2 acquisition must fail closed with contention error
        with pytest.raises(LockContentionError):
            lock2.acquire()

    # Once lock1 releases, lock2 should acquire cleanly
    lock2.acquire()
    lock2.release()


# ==============================================================================
# 6. Promotion Fail-Closed Rollback
# ==============================================================================


def test_promotion_early_rejection_fail_closed(tmp_path: Path):
    from tools.pr_governance_core.promotion import promote_branch

    # If instruction intake fails (branch missing), promotion immediately exits 1 without touching git
    rc = promote_branch("nonexistent-branch-xyz", "feat: valid title", repo_root=tmp_path)
    assert rc == 1
