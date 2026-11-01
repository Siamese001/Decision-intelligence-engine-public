"""Unit and regression tests for PR review runtime acceleration, cache stability, and worktree cleanliness."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from tools.benchmark_pr_promotion import _init_sandbox_git_repo
from tools.pr_governance_core.common import run_git
from tools.pr_governance_core.worktree import (
    BENIGN_IGNORED_SUBSTRINGS,
)
from tools.readiness_cache import (
    InputManifest,
    compute_stage_fingerprint,
    get_cached_stage,
    save_cached_stage,
)


def test_manifest_digest_invariant_to_head_sha_when_tree_unchanged(tmp_path: Path) -> None:
    """Verifies that manifest_digest binds strictly to content and tree_hash, ignoring head_sha."""
    tree_hash = "abcdef0123456789abcdef0123456789abcdef01"
    diff_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    env_digest = "env123"
    configs = {"cfg": "hash1"}
    qualities = {"q": "hash2"}
    deps = {"dep": "hash3"}
    targets = {"file.py": "hash4"}

    payload = {
        "tree_hash": tree_hash,
        "working_tree_diff_hash": diff_hash,
        "environment_digest": env_digest,
        "config_digests": configs,
        "quality_inputs_digests": qualities,
        "dependencies_digests": deps,
        "target_files_digests": targets,
    }
    expected_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    m1 = InputManifest(
        head_sha="1111111111111111111111111111111111111111",
        tree_hash=tree_hash,
        working_tree_diff_hash=diff_hash,
        environment_digest=env_digest,
        config_digests=configs,
        quality_inputs_digests=qualities,
        dependencies_digests=deps,
        target_files_digests=targets,
        created_at_utc="2026-10-14T10:00:00Z",
        manifest_digest=expected_digest,
    )

    m2 = InputManifest(
        head_sha="2222222222222222222222222222222222222222",  # Different commit SHA (e.g. rebase/amend)
        tree_hash=tree_hash,
        working_tree_diff_hash=diff_hash,
        environment_digest=env_digest,
        config_digests=configs,
        quality_inputs_digests=qualities,
        dependencies_digests=deps,
        target_files_digests=targets,
        created_at_utc="2026-10-14T10:05:00Z",
        manifest_digest=expected_digest,
    )

    fp1 = compute_stage_fingerprint(
        "Gate 01", ["echo", "test"], tmp_path, m1.head_sha, manifest=m1
    )
    fp2 = compute_stage_fingerprint(
        "Gate 01", ["echo", "test"], tmp_path, m2.head_sha, manifest=m2
    )
    assert fp1 == fp2, "Stage fingerprints must match across different commit SHAs when tree is unchanged"


def test_stage_cache_hit_across_commits_with_same_tree(tmp_path: Path) -> None:
    """Verifies that a stage cached on commit A hits on commit B when tree hash matches."""
    repo = tmp_path / "sandbox_repo"
    _init_sandbox_git_repo(repo)

    manifest_a = InputManifest(
        head_sha="aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111",
        tree_hash="tree9999",
        working_tree_diff_hash="",
        environment_digest="env1",
        config_digests={},
        quality_inputs_digests={},
        dependencies_digests={},
        target_files_digests={},
        created_at_utc="2026-10-14T10:00:00Z",
        manifest_digest="manifest_digest_fixed",
    )
    manifest_b = InputManifest(
        head_sha="bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222",
        tree_hash="tree9999",
        working_tree_diff_hash="",
        environment_digest="env1",
        config_digests={},
        quality_inputs_digests={},
        dependencies_digests={},
        target_files_digests={},
        created_at_utc="2026-10-14T10:02:00Z",
        manifest_digest="manifest_digest_fixed",
    )

    cmd = ["python", "-m", "tools.lint_gate"]

    import tools.readiness_cache_core.storage as storage_mod
    orig_val = storage_mod.validate_manifest_freshness
    storage_mod.validate_manifest_freshness = lambda m, r: (True, "fresh")
    try:
        save_cached_stage(
            stage_name="Gate Lint",
            cmd=cmd,
            repo_root=repo,
            head_sha=manifest_a.head_sha,
            output_summary="Passed cleanly",
            duration_seconds=1.23,
            manifest=manifest_a,
        )

        cached = get_cached_stage(
            stage_name="Gate Lint",
            cmd=cmd,
            repo_root=repo,
            head_sha=manifest_b.head_sha,
            manifest=manifest_b,
        )
        assert cached is not None, "Expected cache hit on commit B reusing stage cached from commit A"
        assert cached.status == "PASS"
        assert cached.duration_seconds == 1.23
    finally:
        storage_mod.validate_manifest_freshness = orig_val


def test_benign_ignored_proposals_and_attempts() -> None:
    """Verifies that proposals and attempts folders are classified as benign ignored."""
    assert "/proposals" in BENIGN_IGNORED_SUBSTRINGS
    assert "/attempts" in BENIGN_IGNORED_SUBSTRINGS

    rel_proposals = "output/florida/10-14-2026/Meeting/proposals"
    assert any(b in rel_proposals for b in BENIGN_IGNORED_SUBSTRINGS)

    rel_attempts = "output/florida/10-14-2026/Meeting/attempts"
    assert any(b in rel_attempts for b in BENIGN_IGNORED_SUBSTRINGS)


def test_pre_commit_regex_rejects_ephemeral_receipts() -> None:
    """Verifies that the pre-commit hook regex matches ephemeral readiness and review receipts."""
    pattern = re.compile(r"^reports/(readiness/local_main_readiness_|pr_review_|pr_promotion_)")
    assert pattern.match("reports/readiness/local_main_readiness_feat_test_receipt.json")
    assert pattern.match("reports/pr_review_feat_test_receipt.json")
    assert pattern.match("reports/pr_promotion_feat_test_receipt.json")

    # Permanent quality reports must NOT be matched
    assert not pattern.match("reports/coverage_audit.json")
    assert not pattern.match("reports/aidlc/mutation_score_baseline.json")
