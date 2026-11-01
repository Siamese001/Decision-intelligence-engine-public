"""End-to-End Tests for Wave 3: Safe Lifecycle Verification Reuse.

Validates:
1. Benchmark replay for `--phase reuse` producing chained immutable receipt.
2. InputManifest generation and immutability.
3. Pre-seal revalidation detecting mid-run modifications.
4. Attestation namespace separation (READINESS vs PR_REVIEW).
5. Governed merge under lock with base/head/predicate verification and post-merge certification.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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
from tools.benchmark_pr_promotion import _init_sandbox_git_repo, run_benchmark
from tools.pr_governance_core.common import run_git
from tools.readiness_cache import (
    InputManifest,
    build_input_manifest,
    get_cached_stage,
    save_cached_stage,
    validate_manifest_freshness,
)
from tools.verify_post_merge import verify_latest_merge


def test_wave3_benchmark_reuse_phase_e2e(tmp_path: Path) -> None:
    """Verifies benchmark execution under --phase reuse produces valid chained receipt."""
    receipt_file = tmp_path / "wave-3-test.json"
    res = run_benchmark(
        phase="reuse",
        receipt_path=receipt_file,
        workloads=["documentation", "application"],
        iterations=1,
        fast=True,
        repo_root=ROOT,
    )
    assert res["terminal_outcome"] == "PASS"
    assert res["phase"] == "reuse"
    assert receipt_file.is_file()
    data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert data["terminal_outcome"] == "PASS"
    assert "reuse_qualification" in data
    assert "chained_wave2_receipt_digest" in data


def test_input_manifest_immutability_and_freshness(tmp_path: Path) -> None:
    """Verifies InputManifest captures full environment, configs, diffs, and validates freshness."""
    manifest = build_input_manifest(ROOT, "HEAD")
    assert len(manifest.manifest_digest) == 64
    assert len(manifest.environment_digest) == 64
    assert isinstance(manifest.config_digests, dict)
    assert isinstance(manifest.quality_inputs_digests, dict)

    is_fresh, reason = validate_manifest_freshness(manifest, ROOT)
    assert is_fresh, f"Expected clean workspace to be fresh: {reason}"

    # Altered manifest must fail freshness validation
    mutated_manifest = InputManifest(
        head_sha=manifest.head_sha,
        tree_hash=manifest.tree_hash,
        working_tree_diff_hash="tampered_hash_12345",
        environment_digest=manifest.environment_digest,
        config_digests=manifest.config_digests,
        quality_inputs_digests=manifest.quality_inputs_digests,
        dependencies_digests=manifest.dependencies_digests,
        target_files_digests=manifest.target_files_digests,
        created_at_utc=manifest.created_at_utc,
        manifest_digest="tampered_manifest_digest",
    )
    is_fresh_bad, reason_bad = validate_manifest_freshness(mutated_manifest, ROOT)
    assert not is_fresh_bad
    assert "Working tree diff modified" in reason_bad


def test_attestation_namespace_separation(tmp_path: Path) -> None:
    """Verifies readiness and pr-review attestations occupy isolated namespaces without collision."""
    sandbox = tmp_path / "attestation_sandbox"
    _init_sandbox_git_repo(sandbox)
    s_sha = run_git(["rev-parse", "HEAD"], cwd=sandbox)
    s_tree = compute_tree_digest(sandbox, ref=s_sha)

    # Save readiness attestation
    r_path = save_readiness_attestation(
        sandbox,
        {"terminal_outcome": "PASS", "head_commit_sha": s_sha, "total_stages": 30},
        tree_digest=s_tree,
        head_sha=s_sha,
        predicate_type=READINESS_PREDICATE_TYPE,
    )
    assert "readiness" in str(r_path)

    # Save PR review attestation
    p_path = save_attestation(
        sandbox,
        {"terminal_outcome": "PASS", "head_sha": s_sha, "clean_merge": True, "readiness_verified": True},
        tree_digest=s_tree,
        head_sha=s_sha,
        predicate_type=PR_REVIEW_PREDICATE_TYPE,
    )
    assert "pr_review" in str(p_path)
    assert r_path != p_path

    # Retrieval by predicate type returns correct envelope
    r_stmt = load_attestation_by_tree(sandbox, s_tree, head_sha=s_sha, predicate_type=READINESS_PREDICATE_TYPE)
    assert r_stmt is not None
    assert r_stmt["predicateType"] == READINESS_PREDICATE_TYPE

    p_stmt = load_attestation_by_tree(sandbox, s_tree, head_sha=s_sha, predicate_type=PR_REVIEW_PREDICATE_TYPE)
    assert p_stmt is not None
    assert p_stmt["predicateType"] == PR_REVIEW_PREDICATE_TYPE


def test_governed_merge_under_lock_and_post_merge_certification(tmp_path: Path) -> None:
    """Verifies governed merge under lock validates predicates and passes post-merge certification."""
    sandbox = tmp_path / "merge_sandbox"
    _init_sandbox_git_repo(sandbox)

    # Create feature branch
    run_git(["checkout", "-b", "feat/clean-feature"], cwd=sandbox)
    feat_file = sandbox / "feature.py"
    feat_file.write_text('"""Feature code."""\n', encoding="utf-8")
    run_git(["add", "feature.py"], cwd=sandbox)
    run_git(["commit", "-m", "feat: add feature module"], cwd=sandbox)
    b_sha = run_git(["rev-parse", "HEAD"], cwd=sandbox)
    tree_sha = compute_tree_digest(sandbox, ref=b_sha)

    # Create valid readiness receipt (30 stages, all PASS)
    r_dir = sandbox / "reports" / "readiness"
    r_dir.mkdir(parents=True, exist_ok=True)
    stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS", "duration_seconds": 0.01} for i in range(1, 31)]
    readiness_rcpt = {
        "branch": "feat/clean-feature",
        "head_commit_sha": b_sha,
        "terminal_outcome": "PASS",
        "total_stages": 30,
        "stages": stages,
    }
    (r_dir / "local_main_readiness_feat_clean_feature_receipt.json").write_text(json.dumps(readiness_rcpt), encoding="utf-8")

    # Also save readiness attestation
    save_readiness_attestation(sandbox, readiness_rcpt, tree_digest=tree_sha, head_sha=b_sha)

    # Create valid PR review receipt
    p_dir = sandbox / "reports"
    review_rcpt = {
        "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
        "branch": "feat/clean-feature",
        "head_sha": b_sha,
        "base": "main",
        "terminal_outcome": "PASS",
        "clean_merge": True,
        "readiness_verified": True,
    }
    (p_dir / "pr_review_feat_clean_feature_receipt.json").write_text(json.dumps(review_rcpt), encoding="utf-8")
    save_attestation(sandbox, review_rcpt, tree_digest=tree_sha, head_sha=b_sha, predicate_type=PR_REVIEW_PREDICATE_TYPE)

    # Checkout main and merge under lock
    run_git(["checkout", "main"], cwd=sandbox)
    from tools.pr_governance_core import lifecycle
    orig_root = lifecycle.ROOT
    lifecycle.ROOT = sandbox
    try:
        rc = lifecycle._execute_merge_under_lock("feat/clean-feature", "clean feature merge", "feat_clean_feature")
        assert rc == 0, "Expected clean governed merge to succeed"
    finally:
        lifecycle.ROOT = orig_root

    # Verify post-merge certification on main
    passed, detail = verify_latest_merge(sandbox)
    assert passed, f"Post-merge certification failed: {detail}"
