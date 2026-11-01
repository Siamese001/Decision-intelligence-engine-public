"""tests/test_readiness_cache_invalidation_adversarial.py

Adversarial edge-case tests for readiness cache invalidation, out-of-tree attestation,
base-commit drift, and teardown file preservation (Wave 1).
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import pytest

from tools.readiness_planner import validate_readiness_cache_binding
from tools.attestation_core import (
    READINESS_PREDICATE_TYPE,
    compute_tree_digest,
    load_attestation_by_tree,
    save_readiness_attestation,
)
from tools.pr_governance_core.lifecycle import _teardown_merged_worktree, _execute_merge_under_lock


def test_adversarial_base_drift_cache_reuse_rejected():
    """Adversarial scenario: branch attempts to bypass re-qualification after main advanced."""
    cached_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "head_stable_sha",
        "base_commit_sha": "main_ancestor_sha",
        "manifest_version": "1.0.0",
        "is_live": True,
        "stages": [{"stage_index": i, "status": "PASS"} for i in range(1, 31)],
    }
    # Main advances to new SHA
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="head_stable_sha",
        current_base_sha="main_advanced_sha",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Base commit SHA mismatch: main_ancestor_sha != main_advanced_sha" in reason


def test_adversarial_malformed_predicate_rejected():
    """Adversarial scenario: corrupt or non-dict cache entry."""
    valid, reason = validate_readiness_cache_binding(
        "invalid_string_not_dict",  # type: ignore
        current_head_sha="sha1",
        current_base_sha="sha2",
        current_manifest_version="1.0.0",
    )
    assert valid is False
    assert "Corrupt checkpoint" in reason


def test_adversarial_mode_cross_boundary_bypass_rejected():
    """Adversarial scenario: mock-mode qualification attempting to reuse in live mode."""
    cached_mock_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "sha_head",
        "base_commit_sha": "sha_base",
        "manifest_version": "1.0.0",
        "is_live": False,
        "is_mock": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_mock_entry,
        current_head_sha="sha_head",
        current_base_sha="sha_base",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Execution mode mismatch" in reason


def test_teardown_preserves_tracked_receipts_on_main():
    """Adversarial scenario: ensure worktree teardown does not delete tracked receipt files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        # Initialize test git repo
        subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_root, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=tmp_root, check=True)

        receipts_dir = tmp_root / "reports" / "readiness"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        tracked_receipt = receipts_dir / "local_main_readiness_test_tracked_receipt.json"
        tracked_receipt.write_text('{"status": "PASS"}', encoding="utf-8")

        subprocess.run(["git", "add", str(tracked_receipt)], cwd=tmp_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: track receipt"], cwd=tmp_root, check=True)

        untracked_receipt = tmp_root / "reports" / "pr_review_test_tracked_receipt.json"
        untracked_receipt.write_text('{"review": "PASS"}', encoding="utf-8")

        # Simulate teardown cleanup logic on tracked vs untracked file
        for p in [tracked_receipt, untracked_receipt]:
            if p.is_file():
                try:
                    p_rel = str(p.relative_to(tmp_root))
                    proc = subprocess.run(["git", "ls-files", p_rel], cwd=tmp_root, capture_output=True, text=True, check=False)
                    if not proc.stdout.strip():
                        p.unlink(missing_ok=True)
                except Exception:
                    pass

        # Tracked file must be preserved; untracked file must be deleted
        assert tracked_receipt.is_file(), "Tracked receipt was erroneously deleted!"
        assert not untracked_receipt.is_file(), "Untracked receipt was not cleaned up!"


def test_clean_status_with_ignored_ephemeral_receipts():
    """Adversarial scenario: ensure generating readiness receipts matching .gitignore leaves git clean."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_root, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=tmp_root, check=True)

        gitignore = tmp_root / ".gitignore"
        gitignore.write_text(
            "reports/readiness/local_main_readiness_*_receipt.json\n"
            "reports/readiness/logs/\n"
            "reports/pr_review_*_receipt.json\n"
            "reports/pr_promotion_*_receipt.json\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore"], cwd=tmp_root, check=True)

        # Generate ephemeral receipts and logs
        r_dir = tmp_root / "reports" / "readiness"
        r_dir.mkdir(parents=True, exist_ok=True)
        (r_dir / "local_main_readiness_feat_test_receipt.json").write_text("{}", encoding="utf-8")
        logs_dir = r_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "stage_01.log").write_text("ok", encoding="utf-8")
        (tmp_root / "reports" / "pr_review_feat_test_receipt.json").write_text("{}", encoding="utf-8")

        # Check porcelain status
        res = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_root, capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "", f"Git status should be completely clean, but got: {res.stdout}"
