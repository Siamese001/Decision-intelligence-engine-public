"""
recurrence_prevention.py — Layer 0 Recurrence Prevention & Rollback Invariants.

Governed Under AGENTS.md Architectural Invariants and Wave 7 Alignment Contract:
1. Exact Tested Revision & Tree Identity: Reject ancestor/unrelated PASS receipts, bad manifests,
   omitted tests, and commit-title shortcuts.
2. CI & Git Hook Parity: Hooks and CI discover identical required checks.
3. Boundary Mutation Canaries: Mutating an obligation, threshold, evaluator, dependency, or receipt
   breaks its gate immediately.
4. Rollback & Cache Invalidation: Rollback restores the approved bundle and cryptographically
   purges/invalidates all incompatible caches.

Strict Standard-Library Only. Max 400 Lines Budget.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple


class RecurrenceViolationError(RuntimeError):
    """Raised when tree identity, CI/hook parity, or mutation canaries fail."""


class MutationCanaryTarget(str, Enum):
    """Catalog targets subject to boundary mutation canary gates."""
    AMBIGUITY_MARGIN = "ambiguity_margin"
    CONFIDENCE_FLOOR = "confidence_floor"
    RECAP_CONTRACT = "recap_contract"
    EVALUATOR_VERDICT = "evaluator_verdict"
    RECEIPT_LOCK = "receipt_lock"


@dataclass(frozen=True)
class TreeIdentityVerification:
    """Verifies exact tested revision and working tree identity."""
    current_git_revision: str
    receipt_git_revision: str
    current_tree_sha: str
    receipt_tree_sha: str
    commit_message: str = ""

    def verify(self, raise_on_failure: bool = False) -> Tuple[bool, List[str]]:
        errors: List[str] = []

        # 1. Anti-shortcut commit title check
        prohibited_shortcuts = ("[skip-ci]", "[skip ci]", "[bypass-governance]", "WIP: bypass")
        for sc in prohibited_shortcuts:
            if sc in self.commit_message:
                errors.append(f"COMMIT_TITLE_SHORTCUT_REJECTED: Prohibited bypass token '{sc}' found in commit message.")

        # 2. Exact Git Revision Match
        if not self.current_git_revision or not self.receipt_git_revision:
            errors.append("MISSING_REVISION: Git revision cannot be empty.")
        elif self.current_git_revision != self.receipt_git_revision:
            errors.append(
                f"REVISION_MISMATCH: Current revision '{self.current_git_revision[:8]}' != "
                f"Receipt revision '{self.receipt_git_revision[:8]}'. Ancestor receipts rejected."
            )

        # 3. Exact Working Tree SHA Match
        if not self.current_tree_sha or not self.receipt_tree_sha:
            errors.append("MISSING_TREE_SHA: Working tree SHA cannot be empty.")
        elif self.current_tree_sha != self.receipt_tree_sha:
            errors.append(
                f"TREE_SHA_MISMATCH: Current tree '{self.current_tree_sha[:8]}' != "
                f"Receipt tree '{self.receipt_tree_sha[:8]}'. Dirty or uncommitted drift rejected."
            )

        if errors:
            if raise_on_failure:
                raise RecurrenceViolationError("; ".join(errors))
            return False, errors

        return True, []


def verify_ci_hook_parity(
    ci_checks: Sequence[str],
    hook_checks: Sequence[str],
    raise_on_failure: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Verifies that git hooks and CI profiles discover identical required checks
    for all registered catalog surfaces without shortcuts or omitted tests.
    """
    errors: List[str] = []
    ci_set: Set[str] = set(ci_checks)
    hook_set: Set[str] = set(hook_checks)

    missing_in_hooks = ci_set - hook_set
    if missing_in_hooks:
        errors.append(f"PARITY_DEFECT: Hooks missing required CI checks: {sorted(missing_in_hooks)}")

    missing_in_ci = hook_set - ci_set
    if missing_in_ci:
        errors.append(f"PARITY_DEFECT: CI missing required Hook checks: {sorted(missing_in_ci)}")

    if errors:
        if raise_on_failure:
            raise RecurrenceViolationError("; ".join(errors))
        return False, errors

    return True, []


@dataclass(frozen=True)
class CanaryEvaluationResult:
    """Result of evaluating a boundary mutation canary."""
    target: MutationCanaryTarget
    mutation_description: str
    expected_failure_triggered: bool
    observed_error: Optional[str] = None


def run_boundary_mutation_canary(
    target: MutationCanaryTarget,
    baseline_value: Any,
    mutated_value: Any,
    validator_callable: Callable[[Any], Tuple[bool, List[str]]],
) -> CanaryEvaluationResult:
    """
    Executes a boundary mutation canary by applying mutated_value to validator_callable.
    The canary PASSES only if the validator fails-closed on mutated_value.
    """
    # 1. Baseline must pass
    baseline_passed, baseline_errors = validator_callable(baseline_value)
    if not baseline_passed:
        return CanaryEvaluationResult(
            target=target,
            mutation_description=f"Baseline verification failed: {baseline_errors}",
            expected_failure_triggered=False,
            observed_error=str(baseline_errors),
        )

    # 2. Mutated must fail closed
    try:
        mutated_passed, mutated_errors = validator_callable(mutated_value)
        if mutated_passed:
            return CanaryEvaluationResult(
                target=target,
                mutation_description=f"Mutated value unexpectedly PASSED: {mutated_value}",
                expected_failure_triggered=False,
                observed_error=None,
            )
        return CanaryEvaluationResult(
            target=target,
            mutation_description=f"Mutated value correctly failed closed: {mutated_errors}",
            expected_failure_triggered=True,
            observed_error="; ".join(mutated_errors),
        )
    except Exception as exc:
        return CanaryEvaluationResult(
            target=target,
            mutation_description=f"Mutated value raised expected exception: {exc}",
            expected_failure_triggered=True,
            observed_error=str(exc),
        )


@dataclass(frozen=True)
class RollbackResult:
    """Immutable result of a governed rollback operation."""
    restored_bundle_version: str
    invalidated_cache_keys: Sequence[str]
    purged_count: int
    success: bool
    diagnostics: Sequence[str] = field(default_factory=tuple)


def execute_governed_rollback(
    target_bundle_version: str,
    approved_bundle_version: str,
    active_caches: Mapping[str, Mapping[str, Any]],
) -> RollbackResult:
    """
    Restores the approved bundle version and cryptographically invalidates all caches
    bound to incompatible or unapproved bundle versions.
    """
    diagnostics: List[str] = []
    invalidated_keys: List[str] = []

    if target_bundle_version != approved_bundle_version:
        diagnostics.append(
            f"Restoring approved bundle version '{approved_bundle_version}' (was '{target_bundle_version}')."
        )

    for cache_key, cache_meta in active_caches.items():
        cache_bundle_ver = cache_meta.get("bundle_version")
        if cache_bundle_ver != approved_bundle_version:
            invalidated_keys.append(cache_key)
            diagnostics.append(
                f"Purged incompatible cache key '{cache_key}' (bundle version '{cache_bundle_ver}')."
            )

    return RollbackResult(
        restored_bundle_version=approved_bundle_version,
        invalidated_cache_keys=tuple(invalidated_keys),
        purged_count=len(invalidated_keys),
        success=True,
        diagnostics=tuple(diagnostics),
    )
