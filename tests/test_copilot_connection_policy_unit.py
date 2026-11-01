"""
tests/test_copilot_connection_policy_unit.py

Unit test suite for Executive Copilot connection policy, topological inheritance,
multi-tenant isolation, and Rule C-19 boundary enforcement.
Verifies:
1. Automatic topological predecessor and tangential inheritance (project:jarvis -> project:nexus).
2. Natural language queries resolve inherited scopes without requiring syntactic project mentions.
3. Foreign tenants (project:solar) fail closed with 0 leaked scopes or assertions.
4. Unowned assertions fail closed.
5. Missing or corrupted topologies fail closed gracefully without throwing unhandled exceptions.
6. Topological discovery caching by (base_engagement, graph_sha256).
7. Strict Rule C-19 enforcement rejecting quarantined nodes and assertions.
"""
from __future__ import annotations

from typing import Any, Dict, Set
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    TOPOLOGICAL_INHERITANCE_PREDICATES,
    _TOPOLOGICAL_INHERITANCE_CACHE,
    clear_topological_inheritance_cache,
    detect_cross_engagement_request,
    discover_topologically_inherited_engagements,
    find_engagement_node_ids,
    is_assertion_in_scope,
    is_enterprise_policy,
    is_node_in_scope,
    resolve_permitted_engagements,
    verify_evidenced_relationship,
)
from tests.fixtures.copilot_engagement.loader import build_in_memory_graph


def _build_multi_tenant_topology() -> InMemoryGraphTopology:
    """Builds a test topology with JARVIS, NEXUS (predecessor), and SOLAR (foreign tenant)."""
    graph = build_in_memory_graph()

    # JARVIS base project node
    graph.nodes["project:jarvis"] = {
        "node_id": "project:jarvis",
        "canonical_name": "Project Jarvis",
        "node_type": "project",
        "engagement_id": "project:jarvis",
        "namespace": "CANONICAL",
    }
    graph.nodes["concept:jarvis_portal"] = {
        "node_id": "concept:jarvis_portal",
        "canonical_name": "Jarvis Web Portal",
        "node_type": "system",
        "engagement_id": "project:jarvis",
        "namespace": "CANONICAL",
    }

    # NEXUS predecessor project node and SAP ERP concept
    graph.nodes["project:nexus"] = {
        "node_id": "project:nexus",
        "canonical_name": "Project Nexus",
        "node_type": "project",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }
    graph.nodes["system:sap_erp"] = {
        "node_id": "system:sap_erp",
        "canonical_name": "SAP ERP",
        "node_type": "system",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }

    # SOLAR unrelated foreign tenant
    graph.nodes["project:solar"] = {
        "node_id": "project:solar",
        "canonical_name": "Project Solar",
        "node_type": "project",
        "engagement_id": "project:solar",
        "namespace": "CANONICAL",
    }
    graph.nodes["system:solar_grid"] = {
        "node_id": "system:solar_grid",
        "canonical_name": "Solar Grid Management",
        "node_type": "system",
        "engagement_id": "project:solar",
        "namespace": "CANONICAL",
    }

    # Evidenced relationship: JARVIS -> NEXUS (PREDECESSOR_PROJECT)
    graph.assertions["assertion-jarvis-predecessor-nexus"] = {
        "assertion_id": "assertion-jarvis-predecessor-nexus",
        "subject_node_id": "project:jarvis",
        "object_node_id": "project:nexus",
        "predicate": "PREDECESSOR_PROJECT",
        "claim_text": "Project Jarvis inherits architecture and SAP integration from predecessor Project Nexus.",
        "engagement_id": "project:jarvis",
        "canonical_status": "ACTIVE",
    }

    # NEXUS SAP assertion
    graph.assertions["assertion-nexus-sap-01"] = {
        "assertion_id": "assertion-nexus-sap-01",
        "subject_node_id": "project:nexus",
        "object_node_id": "system:sap_erp",
        "predicate": "OWNS_OR_LEADS",
        "claim_text": "Daniel Brankin owns technical architecture for SAP ERP under Project Nexus.",
        "engagement_id": "project:nexus",
        "canonical_status": "ACTIVE",
    }

    # SOLAR assertion (unrelated)
    graph.assertions["assertion-solar-01"] = {
        "assertion_id": "assertion-solar-01",
        "subject_node_id": "project:solar",
        "object_node_id": "system:solar_grid",
        "predicate": "MAINTAINS",
        "claim_text": "Solar team maintains photovoltaic inverters.",
        "engagement_id": "project:solar",
        "canonical_status": "ACTIVE",
    }

    # Enterprise policy assertion
    graph.assertions["assertion-policy-sso"] = {
        "assertion_id": "assertion-policy-sso",
        "subject_node_id": "policy:enterprise_sso",
        "object_node_id": "concept:auth",
        "predicate": "MANDATES",
        "claim_text": "All internal apps must authenticate via enterprise SSO.",
        "engagement_id": "project:enterprise",
        "is_enterprise_policy": True,
        "canonical_status": "ACTIVE",
    }

    return graph


def test_discover_topologically_inherited_engagements() -> None:
    """Verifies that discover_topologically_inherited_engagements resolves evidenced predecessor projects."""
    clear_topological_inheritance_cache()
    graph = _build_multi_tenant_topology()

    inherited = discover_topologically_inherited_engagements(graph, "project:jarvis")
    assert inherited == {"project:nexus"}, f"Expected {{'project:nexus'}}, got {inherited}"

    # Foreign tenant must NOT be inherited
    assert "project:solar" not in inherited


def test_natural_language_query_scope_resolution_without_syntactic_keyword() -> None:
    """Verifies that query 'Who is the tech owner for SAP?' automatically inherits project:nexus."""
    clear_topological_inheritance_cache()
    graph = _build_multi_tenant_topology()

    permitted = resolve_permitted_engagements(
        "Who is the tech owner for SAP?",
        "project:jarvis",
        graph,
        include_topological_inheritance=True,
    )
    assert "project:nexus" in permitted, "project:nexus must be permitted via topological inheritance"
    assert "project:solar" not in permitted, "Foreign tenant project:solar must not be permitted"


def test_explicit_query_resolution_with_and_without_topological_inheritance() -> None:
    """Verifies behavior when topological inheritance is disabled vs enabled."""
    clear_topological_inheritance_cache()
    graph = _build_multi_tenant_topology()

    # Without topological inheritance, query without explicit 'nexus' keyword yields empty set
    permitted_no_topo = resolve_permitted_engagements(
        "Who is the tech owner for SAP?",
        "project:jarvis",
        graph,
        include_topological_inheritance=False,
    )
    assert "project:nexus" not in permitted_no_topo

    # With explicit mention of Nexus, permitted even without topological inheritance
    permitted_explicit = resolve_permitted_engagements(
        "What does Nexus documentation say about SAP?",
        "project:jarvis",
        graph,
        include_topological_inheritance=False,
    )
    assert "project:nexus" in permitted_explicit


def test_foreign_tenant_solar_fails_closed() -> None:
    """Verifies that unrelated tenant project:solar is never admitted into scopes or assertions."""
    clear_topological_inheritance_cache()
    graph = _build_multi_tenant_topology()

    # Query attempting to access Solar from Jarvis
    permitted = resolve_permitted_engagements(
        "Tell me about Project Solar grid management",
        "project:jarvis",
        graph,
    )
    assert "project:solar" not in permitted, "Foreign tenant must fail closed"

    # Assertion scope check
    solar_assertion = graph.assertions["assertion-solar-01"]
    assert not is_assertion_in_scope(solar_assertion, "project:jarvis", permitted)

    # Node scope check
    solar_node = graph.nodes["system:solar_grid"]
    assert not is_node_in_scope("system:solar_grid", solar_node, "project:jarvis", permitted)


def test_unowned_assertion_fails_closed() -> None:
    """Verifies that assertions lacking engagement_id fail closed."""
    graph = _build_multi_tenant_topology()
    unowned = {
        "assertion_id": "assertion-orphan",
        "claim_text": "Orphan claim without ownership.",
        "predicate": "PROPOSES",
    }
    assert not is_assertion_in_scope(unowned, "project:jarvis", {"project:nexus"})


def test_missing_and_corrupted_topology_resilience() -> None:
    """Verifies graceful handling of None or malformed graph topologies."""
    clear_topological_inheritance_cache()

    # None topology
    assert discover_topologically_inherited_engagements(None, "project:jarvis") == set()
    assert resolve_permitted_engagements("query", "project:jarvis", None) == set()
    assert find_engagement_node_ids(None, "project:jarvis") == set()

    # Corrupted topology with invalid assertions type
    class CorruptedTopology:
        assertions = "NOT_A_DICT"
        nodes = None
        graph_sha256 = "dummy-sha"

    corrupted = CorruptedTopology()
    assert discover_topologically_inherited_engagements(corrupted, "project:jarvis") == set()
    assert resolve_permitted_engagements("query", "project:jarvis", corrupted) == set()
    assert find_engagement_node_ids(corrupted, "project:jarvis") == set()


def test_topological_inheritance_cache_lifecycle() -> None:
    """Verifies that discovery results are cached and invalidation works."""
    clear_topological_inheritance_cache()
    graph = _build_multi_tenant_topology()

    assert len(_TOPOLOGICAL_INHERITANCE_CACHE) == 0
    res1 = discover_topologically_inherited_engagements(graph, "project:jarvis")
    assert len(_TOPOLOGICAL_INHERITANCE_CACHE) == 1

    # Second call uses cache
    res2 = discover_topologically_inherited_engagements(graph, "project:jarvis")
    assert res1 == res2

    # Clear cache
    clear_topological_inheritance_cache()
    assert len(_TOPOLOGICAL_INHERITANCE_CACHE) == 0


def test_rule_c19_boundary_enforcement_in_connection_policy() -> None:
    """Verifies that nodes and assertions with Rule C-19 markers are strictly rejected."""
    # Quarantined assertion by namespace
    advisory_assertion = {
        "assertion_id": "assertion-adv-01",
        "claim_text": "Strategic advisory notes.",
        "engagement_id": "project:jarvis",
        "namespace": "INTERNAL_ADVISORY",
    }
    assert not is_assertion_in_scope(advisory_assertion, "project:jarvis")

    # Quarantined assertion by marker
    career_assertion = {
        "assertion_id": "assertion-career-01",
        "claim_text": "Executive target compensation trajectory and career leverage.",
        "engagement_id": "project:jarvis",
        "namespace": "CANONICAL",
    }
    assert not is_assertion_in_scope(career_assertion, "project:jarvis")

    # Quarantined node by is_quarantined flag
    quarantined_node = {
        "node_id": "person:shadow_exec",
        "canonical_name": "Shadow Executive",
        "engagement_id": "project:jarvis",
        "is_quarantined": 1,
    }
    assert not is_node_in_scope("person:shadow_exec", quarantined_node, "project:jarvis")

    # Quarantined node by C-19 marker in description
    advisory_node = {
        "node_id": "person:advisor",
        "canonical_name": "Advisory Lead",
        "description": "Networking wedge for career positioning",
        "engagement_id": "project:jarvis",
    }
    assert not is_node_in_scope("person:advisor", advisory_node, "project:jarvis", is_attendee=True)
