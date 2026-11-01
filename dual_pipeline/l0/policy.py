"""Dual-Pipeline Layer 0: Security, Tenant Isolation & Pre-Retrieval Policy.

Implements Law 6 (Policy Before Retrieval) and Law 1/2 (Namespace Isolation).
Ensures ACLs, quarantine filters, and tenant boundaries are evaluated
strictly prior to vector indexing, semantic search, or external inference.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

from typing import Any, List, Sequence, TypeVar

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyContext,
)

T = TypeVar("T")


def authorize_namespace_access(
    context: PolicyContext,
    target_namespace: str,
) -> bool:
    """Evaluates whether the caller context is authorized for target namespace (Laws 1 & 2)."""
    # Reject empty or unknown namespaces
    if not target_namespace:
        return False

    # INTERNAL_ADVISORY requires explicit namespace authorization and elevated clearance
    if target_namespace == INTERNAL_ADVISORY_NAMESPACE:
        if INTERNAL_ADVISORY_NAMESPACE not in context.allowed_namespaces:
            return False
        if context.clearance_level not in ("INTERNAL_ADVISORY", "TOP_SECRET", "ADMIN"):
            return False

    # Check caller allowed namespaces
    return target_namespace in context.allowed_namespaces


def is_candidate_eligible(
    candidate: Any,
    context: PolicyContext,
) -> bool:
    """Determines whether a single candidate item satisfies tenant, namespace, and quarantine policy."""
    # Extract properties whether candidate is object, dict, or tuple
    if isinstance(candidate, dict):
        tenant = candidate.get("tenant_id", "default")
        namespace = candidate.get("namespace", CLIENT_SAFE_NAMESPACE)
        is_quarantined = candidate.get("is_quarantined", False)
    else:
        tenant = getattr(candidate, "tenant_id", "default")
        namespace = getattr(candidate, "namespace", CLIENT_SAFE_NAMESPACE)
        is_quarantined = getattr(candidate, "is_quarantined", False)

    # 1. Tenant boundary enforcement
    if tenant != context.tenant_id:
        return False

    # 2. Namespace authorization
    if not authorize_namespace_access(context, namespace):
        return False

    # 3. Soft-quarantine filtering (Law 5 & 6)
    if is_quarantined and not context.allow_quarantined:
        return False

    return True


def filter_candidates_pre_retrieval(
    candidates: Sequence[T],
    context: PolicyContext,
) -> List[T]:
    """
    Executes pre-retrieval filtering (Law 6).
    MUST execute before dense, sparse, BM25, or ColBERT similarity rankings.
    """
    return [c for c in candidates if is_candidate_eligible(c, context)]


class PolicyEnforcementOrderTracker:
    """Auditing utility to verify that policy evaluation strictly precedes retrieval."""

    def __init__(self) -> None:
        self.policy_checked: bool = False
        self.retrieval_executed: bool = False

    def record_policy_check(self) -> None:
        self.policy_checked = True

    def record_retrieval(self) -> bool:
        """Returns True if policy was checked BEFORE retrieval; False if order was violated."""
        self.retrieval_executed = True
        return self.policy_checked
