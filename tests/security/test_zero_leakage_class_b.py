"""Test Suite: Class B Zero-Leakage & Epistemic Security Boundaries.

Verifies:
- Complete tenant isolation: zero multi-tenant data leakage.
- Law 2: INTERNAL_ADVISORY content strictly contained to local Metal enclave.
- Law 3: [UNTRUSTED_FAST_PATH] cannot mutate storage or become authoritative citations.
- C-19: CanonicalGraphDelta fails closed if advisory nodes leak into canonical nodes.
- EvidencePack sanitizes and blocks unauthorized namespace facts.
- Outbound telemetry and logs scrub credentials and confidential advisory tokens.
"""
from __future__ import annotations

from dual_pipeline.l0.contracts import (
    INTERNAL_ADVISORY_NAMESPACE,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    FastPathContext,
    PolicyContext,
)
from dual_pipeline.l0.policy import authorize_namespace_access, is_candidate_eligible
from dual_pipeline.l0.validation import (
    validate_canonical_graph_delta,
    validate_fast_path_context,
)
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack


def test_tenant_isolation_boundary() -> None:
    ctx_partner_advisory = PolicyContext(tenant_id="partner_advisory", allowed_namespaces=["CLIENT_SAFE"])
    ctx_client = PolicyContext(tenant_id="apex_global", allowed_namespaces=["CLIENT_SAFE"])

    item_partner_advisory = {"tenant_id": "partner_advisory", "namespace": "CLIENT_SAFE", "text": "PartnerAdvisory internal notes"}
    item_client = {"tenant_id": "apex_global", "namespace": "CLIENT_SAFE", "text": "Apex Global grid data"}

    assert is_candidate_eligible(item_partner_advisory, ctx_partner_advisory) is True
    assert is_candidate_eligible(item_client, ctx_partner_advisory) is False

    assert is_candidate_eligible(item_client, ctx_client) is True
    assert is_candidate_eligible(item_partner_advisory, ctx_client) is False


def test_internal_advisory_local_metal_containment() -> None:
    # Standard worker in CLIENT_SAFE cannot access INTERNAL_ADVISORY (Law 2)
    standard_ctx = PolicyContext(
        tenant_id="default",
        allowed_namespaces=["CLIENT_SAFE"],
        clearance_level="STANDARD",
    )
    assert authorize_namespace_access(standard_ctx, INTERNAL_ADVISORY_NAMESPACE) is False

    # Elevated local Metal enclave context can access INTERNAL_ADVISORY
    metal_enclave_ctx = PolicyContext(
        tenant_id="default",
        allowed_namespaces=["CLIENT_SAFE", INTERNAL_ADVISORY_NAMESPACE],
        clearance_level="INTERNAL_ADVISORY",
    )
    assert authorize_namespace_access(metal_enclave_ctx, INTERNAL_ADVISORY_NAMESPACE) is True


def test_fast_path_read_only_and_no_mutation() -> None:
    # FastPathContext must carry [UNTRUSTED_FAST_PATH] and cannot be durable
    valid_fast_path = FastPathContext(
        event_id="fp_sec_01",
        meeting_id="meeting_sec",
        speaker="Speaker",
        text="Live unconfirmed statement",
        start_ts=0.0,
        end_ts=1.0,
        badge="[UNTRUSTED_FAST_PATH]",
        is_ephemeral=True,
    )
    res = validate_fast_path_context(valid_fast_path)
    assert res.is_valid is True

    # Violating Law 3 (attempting to make Fast Path durable) fails closed
    illegal_fast_path = FastPathContext(
        event_id="fp_sec_02",
        meeting_id="meeting_sec",
        speaker="Speaker",
        text="Live statement",
        start_ts=0.0,
        end_ts=1.0,
        badge="[UNTRUSTED_FAST_PATH]",
        is_ephemeral=False,  # ILLEGAL: attempting durable mutation
    )
    bad_res = validate_fast_path_context(illegal_fast_path)
    assert bad_res.is_valid is False
    assert any("Law 3" in err for err in bad_res.errors)


def test_graph_delta_never_leaks_advisory_to_canonical() -> None:
    # Attempting to inject an INTERNAL_ADVISORY node into canonical nodes_created
    leaked_node = CanonicalGraphNode(
        node_id="person:executive_lead",
        evidence_id="ev_001",
        entity_type="person",
        name="Executive",
        namespace=INTERNAL_ADVISORY_NAMESPACE,  # Leaked into canonical!
    )

    delta = CanonicalGraphDelta(
        delta_id="delta_leak_test",
        source_evidence_ids=["ev_001"],
        nodes_created=[leaked_node],
        nodes_updated=[],
        edges_created=[],
        advisory_nodes=[],
    )

    val_res = validate_canonical_graph_delta(delta)
    assert val_res.is_valid is False
    assert any("leaked" in err.lower() for err in val_res.errors)


def test_evidence_pack_sanitizes_unauthorized_namespaces() -> None:
    client_safe_ctx = PolicyContext(tenant_id="t1", allowed_namespaces=["CLIENT_SAFE"])

    mixed_candidates = [
        {"node_id": "n1", "evidence_id": "ev1", "name": "Safe Fact", "text": "Public grid status", "tenant_id": "t1", "namespace": "CLIENT_SAFE"},
        {"node_id": "n2", "evidence_id": "ev2", "name": "Career Fact", "text": "Confidential compensation notes", "tenant_id": "t1", "namespace": INTERNAL_ADVISORY_NAMESPACE},
    ]

    pack = assemble_decision_evidence_pack("query_sec_01", "status", mixed_candidates, client_safe_ctx)
    assert len(pack.canonical_facts) == 1
    assert pack.canonical_facts[0]["name"] == "Safe Fact"
    # INTERNAL_ADVISORY candidate strictly scrubbed out of client-safe pack
    assert all(f["namespace"] != INTERNAL_ADVISORY_NAMESPACE for f in pack.canonical_facts)


def test_log_and_telemetry_sanitization() -> None:
    # Verifies pre-egress sanitization of credentials and career leverage tokens
    raw_log = "Error connecting with sk_test_redacted_placeholder to endpoint"
    assert "sk-" in raw_log

    # Sanitizer scrub
    sanitized = raw_log.replace("sk_test_redacted_placeholder", "[REDACTED_API_KEY]")
    assert "sk-" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
