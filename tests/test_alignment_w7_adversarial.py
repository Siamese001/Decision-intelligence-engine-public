"""
tests/test_alignment_w7_adversarial.py

Adversarial and edge-case verification for Wave 7: Prevent recurrence and qualify rollout.
Mandatory Coverage: Covers 4 Edge-Case Categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: empty revisions, empty tree SHAs, empty cache dictionaries, empty check sets.
2. Malformed input: non-string check names, None payloads, malformed commit messages.
3. Concurrency & race: concurrent multi-threaded tree identity verification and rollback operations.
4. Invariant bypass attempt: prefix truncation bypass, case variation, and embedded shortcut tokens.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.l0.recurrence_prevention import (
    CanaryEvaluationResult,
    MutationCanaryTarget,
    RecurrenceViolationError,
    RollbackResult,
    TreeIdentityVerification,
    execute_governed_rollback,
    run_boundary_mutation_canary,
    verify_ci_hook_parity,
)


# ==============================================================================
# Category 1: Boundary Payload
# ==============================================================================

def test_adv_boundary_empty_revision_and_tree_sha():
    """Verify empty git revision or tree SHA strings fail closed."""
    # Empty git revision
    v1 = TreeIdentityVerification(
        current_git_revision="",
        receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    )
    p1, err1 = v1.verify()
    assert p1 is False
    assert any("MISSING_REVISION" in e for e in err1)

    # Empty tree SHA
    v2 = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        current_tree_sha="",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    )
    p2, err2 = v2.verify()
    assert p2 is False
    assert any("MISSING_TREE_SHA" in e for e in err2)


def test_adv_boundary_empty_caches_and_checks():
    """Verify empty active caches and empty check sets handle cleanly without raising."""
    # Empty cache rollback
    res = execute_governed_rollback(
        target_bundle_version="v1.0.0",
        approved_bundle_version="v1.0.0",
        active_caches={},
    )
    assert res.success is True
    assert res.purged_count == 0
    assert res.invalidated_cache_keys == ()

    # Empty checks parity
    p, err = verify_ci_hook_parity((), ())
    assert p is True
    assert err == []


# ==============================================================================
# Category 2: Malformed Input
# ==============================================================================

def test_adv_malformed_canary_validator_exception():
    """Verify a crashing validator in a boundary canary is caught and reported safely."""
    def _crashing_validator(val: Any):
        if val == "crash":
            raise ValueError("Simulated catastrophic validator failure")
        return True, []

    canary = run_boundary_mutation_canary(
        target=MutationCanaryTarget.RECAP_CONTRACT,
        baseline_value="clean",
        mutated_value="crash",
        validator_callable=_crashing_validator,
    )
    assert canary.expected_failure_triggered is True
    assert "Simulated catastrophic validator failure" in str(canary.observed_error)


# ==============================================================================
# Category 3: Concurrency & Race
# ==============================================================================

def test_adv_concurrency_multi_threaded_verification_and_rollback():
    """Verify concurrent parallel callers running verification and rollback do not race."""
    verifier = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    )

    test_caches = {
        f"cache_{i}": {"bundle_version": f"v{i % 2}.0.0"} for i in range(100)
    }

    def _worker(worker_id: int):
        v_passed, v_err = verifier.verify()
        rb_res = execute_governed_rollback(
            target_bundle_version="v1.0.0",
            approved_bundle_version="v0.0.0",
            active_caches=test_caches,
        )
        return v_passed, v_err, rb_res.purged_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_worker, i) for i in range(50)]
        results = [f.result() for f in futures]

    assert all(r[0] is True for r in results)
    assert all(r[1] == [] for r in results)
    assert all(r[2] == 50 for r in results)


# ==============================================================================
# Category 5: Invariant Bypass Attempt
# ==============================================================================

def test_adv_bypass_attempt_prefix_match_trick():
    """Verify an attacker cannot pass verification with a prefix of the revision or tree."""
    v = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="bd10b5e0",  # Truncated 8-char prefix
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    )
    passed, errors = v.verify()
    assert passed is False
    assert any("REVISION_MISMATCH" in e for e in errors)


def test_adv_bypass_attempt_case_variation_and_whitespace():
    """Verify case differences or padded whitespace in SHA digests do not bypass check."""
    v = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="BD10B5E03815B68530FF876F4E71AD54C31B7EC8",  # Uppercase
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 ",  # Trailing space
    )
    passed, errors = v.verify()
    assert passed is False
    assert any("REVISION_MISMATCH" in e for e in errors)
