"""Test suite for Law 6 (Policy Before Retrieval) and Law 1/2 (Namespace Isolation)."""
from __future__ import annotations

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyContext,
)
from dual_pipeline.l0.policy import (
    PolicyEnforcementOrderTracker,
    filter_candidates_pre_retrieval,
    is_candidate_eligible,
)


def test_tenant_and_namespace_isolation():
    """Verifies that items crossing tenant or namespace boundaries are rejected."""
    ctx_client = PolicyContext(
        tenant_id="partner_advisory",
        allowed_namespaces=[CLIENT_SAFE_NAMESPACE],
        clearance_level="STANDARD",
    )

    # Item from correct tenant & namespace -> Approved
    item_ok = {"tenant_id": "partner_advisory", "namespace": CLIENT_SAFE_NAMESPACE, "is_quarantined": False}
    assert is_candidate_eligible(item_ok, ctx_client) is True

    # Item from foreign tenant -> Rejected
    item_foreign = {"tenant_id": "other_corp", "namespace": CLIENT_SAFE_NAMESPACE}
    assert is_candidate_eligible(item_foreign, ctx_client) is False

    # Advisory item without elevated clearance -> Rejected (Law 1/2)
    item_advisory = {"tenant_id": "partner_advisory", "namespace": INTERNAL_ADVISORY_NAMESPACE}
    assert is_candidate_eligible(item_advisory, ctx_client) is False


def test_soft_quarantine_pre_retrieval_filtering():
    """Verifies that quarantined items are excluded unless explicitly allowed."""
    ctx_strict = PolicyContext(
        tenant_id="partner_advisory",
        allowed_namespaces=[CLIENT_SAFE_NAMESPACE],
        allow_quarantined=False,
    )
    ctx_permissive = PolicyContext(
        tenant_id="partner_advisory",
        allowed_namespaces=[CLIENT_SAFE_NAMESPACE],
        allow_quarantined=True,
    )

    items = [
        {"id": 1, "tenant_id": "partner_advisory", "namespace": CLIENT_SAFE_NAMESPACE, "is_quarantined": False},
        {"id": 2, "tenant_id": "partner_advisory", "namespace": CLIENT_SAFE_NAMESPACE, "is_quarantined": True},
    ]

    filtered_strict = filter_candidates_pre_retrieval(items, ctx_strict)
    assert len(filtered_strict) == 1
    assert filtered_strict[0]["id"] == 1

    filtered_perm = filter_candidates_pre_retrieval(items, ctx_permissive)
    assert len(filtered_perm) == 2


def test_policy_enforcement_order_tracker():
    """Verifies Law 6 call order: policy evaluation must strictly precede retrieval."""
    tracker = PolicyEnforcementOrderTracker()
    assert tracker.policy_checked is False

    # Normal order: policy check -> retrieval
    tracker.record_policy_check()
    order_valid = tracker.record_retrieval()
    assert order_valid is True

    # Inverted order: retrieval called before policy check
    inverted_tracker = PolicyEnforcementOrderTracker()
    inverted_order_valid = inverted_tracker.record_retrieval()
    assert inverted_order_valid is False
