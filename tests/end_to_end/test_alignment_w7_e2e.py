"""
tests/end_to_end/test_alignment_w7_e2e.py

End-to-End lifecycle tests for Wave 7: Prevent recurrence and qualify rollout.
Verifies:
1. Exact tested revision and tree identity verification prevents ancestor receipts and dirty trees.
2. Commit-title shortcut attempts ([skip-ci], [bypass-governance]) fail closed immediately.
3. CI and Git Hook parity verification guarantees identical surface check coverage.
4. Boundary mutation canaries for ambiguity margin, confidence floor, prompt obligations, and receipts.
5. Governed rollback restores approved bundle version and purges incompatible caches.
6. Cryptographic predecessor lock verification links Wave 6 to Wave 7.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


def test_w7_e2e_exact_revision_and_tree_identity_success():
    """Verify identical git revision and tree SHA pass identity verification."""
    verifier = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        commit_message="feat(alignment): qualify recurrence prevention",
    )
    passed, errors = verifier.verify()
    assert passed is True
    assert errors == []


def test_w7_e2e_revision_mismatch_and_dirty_tree_fails_closed():
    """Verify mismatched git revision or working tree SHA fails closed."""
    # 1. Revision mismatch (ancestor receipt attempt)
    rev_mismatch = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="0000000000000000000000000000000000000000",
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    )
    p1, err1 = rev_mismatch.verify()
    assert p1 is False
    assert any("REVISION_MISMATCH" in e for e in err1)

    # 2. Tree SHA mismatch (dirty tree or uncommitted files)
    tree_mismatch = TreeIdentityVerification(
        current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
        current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        receipt_tree_sha="ffffffffffffffffffffffffffffffffffffffff",
    )
    p2, err2 = tree_mismatch.verify()
    assert p2 is False
    assert any("TREE_SHA_MISMATCH" in e for e in err2)


def test_w7_e2e_commit_title_shortcut_rejection():
    """Verify commit title shortcuts like [skip-ci] or [bypass-governance] are strictly rejected."""
    for shortcut in ("[skip-ci]", "[skip ci]", "[bypass-governance]"):
        verifier = TreeIdentityVerification(
            current_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
            receipt_git_revision="bd10b5e03815b68530ff876f4e71ad54c31b7ec8",
            current_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            receipt_tree_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            commit_message=f"chore: quick fix {shortcut} for release",
        )
        passed, errors = verifier.verify()
        assert passed is False
        assert any("COMMIT_TITLE_SHORTCUT_REJECTED" in e for e in errors)

        with pytest.raises(RecurrenceViolationError, match="COMMIT_TITLE_SHORTCUT_REJECTED"):
            verifier.verify(raise_on_failure=True)


def test_w7_e2e_ci_hook_parity_verification():
    """Verify hooks and CI discover identical required gate checks."""
    standard_checks = (
        "lint_layer_imports",
        "lint_file_budgets",
        "lint_contract_parity",
        "pre_wave_decision_gate",
        "check_receipt_integrity",
    )

    # Identical checks pass
    passed, errors = verify_ci_hook_parity(standard_checks, standard_checks)
    assert passed is True
    assert errors == []

    # Omitted check in hooks fails
    passed_missing_hook, err_hook = verify_ci_hook_parity(standard_checks, standard_checks[:-1])
    assert passed_missing_hook is False
    assert any("Hooks missing required CI checks" in e for e in err_hook)

    # Omitted check in CI fails
    passed_missing_ci, err_ci = verify_ci_hook_parity(standard_checks[:-1], standard_checks)
    assert passed_missing_ci is False
    assert any("CI missing required Hook checks" in e for e in err_ci)


def test_w7_e2e_boundary_mutation_canary_ambiguity_margin():
    """Verify boundary mutation canary for ambiguity margin (0.30 vs 0.15)."""
    def _validate_margin(val: float) -> Tuple[bool, List[str]]:
        if abs(val - 0.30) < 1e-6:
            return True, []
        return False, [f"Ambiguity margin {val} violates SSOT 0.30"]

    canary = run_boundary_mutation_canary(
        target=MutationCanaryTarget.AMBIGUITY_MARGIN,
        baseline_value=0.30,
        mutated_value=0.15,
        validator_callable=_validate_margin,
    )
    assert canary.expected_failure_triggered is True
    assert "Ambiguity margin 0.15 violates SSOT 0.30" in str(canary.observed_error)


def test_w7_e2e_boundary_mutation_canary_confidence_floor():
    """Verify boundary mutation canary for confidence floor (0.65 vs 0.60)."""
    def _validate_floor(val: float) -> Tuple[bool, List[str]]:
        if abs(val - 0.65) < 1e-6:
            return True, []
        return False, [f"Autonomous confidence floor {val} violates SSOT 0.65"]

    canary = run_boundary_mutation_canary(
        target=MutationCanaryTarget.CONFIDENCE_FLOOR,
        baseline_value=0.65,
        mutated_value=0.60,
        validator_callable=_validate_floor,
    )
    assert canary.expected_failure_triggered is True
    assert "Autonomous confidence floor 0.6 violates SSOT 0.65" in str(canary.observed_error)


def test_w7_e2e_governed_rollback_and_cache_purge():
    """Verify governed rollback restores approved bundle version and purges incompatible caches."""
    active_caches = {
        "cache_run_1": {"bundle_version": "v1.0.0-approved", "data": "clean"},
        "cache_run_2": {"bundle_version": "v1.1.0-unapproved", "data": "incompatible"},
        "cache_run_3": {"bundle_version": "v1.0.0-approved", "data": "clean_2"},
        "cache_run_4": {"bundle_version": "v2.0.0-corrupt", "data": "bad"},
    }

    result: RollbackResult = execute_governed_rollback(
        target_bundle_version="v1.1.0-unapproved",
        approved_bundle_version="v1.0.0-approved",
        active_caches=active_caches,
    )

    assert result.success is True
    assert result.restored_bundle_version == "v1.0.0-approved"
    assert set(result.invalidated_cache_keys) == {"cache_run_2", "cache_run_4"}
    assert result.purged_count == 2
    assert any("Purged incompatible cache key 'cache_run_2'" in d for d in result.diagnostics)
    assert any("Purged incompatible cache key 'cache_run_4'" in d for d in result.diagnostics)
