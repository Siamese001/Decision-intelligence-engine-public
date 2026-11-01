"""Test suite for single-trip evidence pack retrieval (C -> D handoff)."""
from __future__ import annotations

from dual_pipeline.l0.contracts import PolicyContext
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack


def test_single_trip_evidence_pack_assembly():
    """Verifies that evidence pack combines facts, citations, and policy in a single call."""
    ctx = PolicyContext(
        tenant_id="partner_advisory",
        allowed_namespaces=["CLIENT_SAFE"],
        allow_quarantined=False,
    )

    raw_candidates = [
        {
            "node_id": "node_1",
            "evidence_id": "ev_1",
            "name": "Cloud Architecture Decision",
            "snippet": "Approved AWS multi-region strategy.",
            "tenant_id": "partner_advisory",
            "namespace": "CLIENT_SAFE",
            "is_quarantined": False,
        },
        {
            "node_id": "node_2",
            "evidence_id": "ev_2",
            "name": "Quarantined Tactical Note",
            "snippet": "Jira ticket status update.",
            "tenant_id": "partner_advisory",
            "namespace": "CLIENT_SAFE",
            "is_quarantined": True,  # Filtered out by Law 6
        },
        {
            "node_id": "node_3",
            "evidence_id": "ev_3",
            "name": "Foreign Tenant Node",
            "snippet": "Other company data.",
            "tenant_id": "foreign_corp",  # Filtered out by tenant isolation
            "namespace": "CLIENT_SAFE",
        },
    ]

    pack = assemble_decision_evidence_pack(
        inquiry_id="inq_400",
        query_text="What is our cloud architecture strategy?",
        candidates=raw_candidates,
        policy_context=ctx,
    )

    # Only node_1 is authorized
    assert len(pack.canonical_facts) == 1
    assert pack.canonical_facts[0]["node_id"] == "node_1"

    # Citation extracted automatically in same trip
    assert len(pack.citations) == 1
    assert pack.citations[0]["evidence_id"] == "ev_1"
    assert pack.tenant_id == "partner_advisory"
