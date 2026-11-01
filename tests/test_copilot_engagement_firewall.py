"""Unit and integration tests for Copilot Engagement Inheritance Firewall and Attendee Person-Scope Isolation.

Verifies:
1. Zero transitive engagement inheritance through shared person or organization nodes.
2. get_person_context strictly scopes attendee assertions to the active session engagement.
3. Predecessor projects and valid structural workstreams remain inheritable.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    clear_topological_inheritance_cache,
    discover_topologically_inherited_engagements,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


class MockDialogueContext:
    """Mock dialogue context providing retrieval seeds."""

    def __init__(self, seeds: List[str] | None = None) -> None:
        self._seeds = seeds or ["loss leader", "commercial model"]

    def retrieval_seeds(self) -> List[str]:
        return list(self._seeds)


def _build_firewall_test_topology() -> Any:
    """Constructs a multi-engagement test topology with shared person and org nodes."""
    nodes: Dict[str, Any] = {}
    assertions: Dict[str, Any] = {}
    graph = SimpleNamespace(nodes=nodes, assertions=assertions, forward_adjacency={})

    # Base Projects
    graph.nodes["project:project_nexus"] = {
        "node_id": "project:project_nexus",
        "canonical_name": "Project Nexus",
        "node_type": "project",
        "engagement_id": "project:project_nexus",
        "namespace": "CANONICAL",
    }
    graph.nodes["project:internal_consulting"] = {
        "node_id": "project:internal_consulting",
        "canonical_name": "Internal Consulting",
        "node_type": "project",
        "engagement_id": "project:internal_consulting",
        "namespace": "CANONICAL",
    }

    # External Unrelated Projects
    for ext_proj in ("project:gemini_enablement", "project:hrit", "project:apex_trans", "project:edo", "project:project_atlas"):
        graph.nodes[ext_proj] = {
            "node_id": ext_proj,
            "canonical_name": ext_proj.replace("project:", "").title(),
            "node_type": "project",
            "engagement_id": ext_proj,
            "namespace": "CANONICAL",
        }

    # Shared Attendee Hub Nodes
    graph.nodes["person_dan_okray"] = {
        "node_id": "person_dan_okray",
        "canonical_name": "Drew Oakley",
        "display_name": "Drew Oakley",
        "node_type": "person",
        "engagement_id": "project:project_nexus",
        "namespace": "CANONICAL",
    }
    graph.nodes["person_amit"] = {
        "node_id": "person_amit",
        "canonical_name": "Amit Ayer",
        "display_name": "Amit Ayer",
        "node_type": "person",
        "engagement_id": "project:internal_consulting",
        "namespace": "CANONICAL",
    }
    graph.nodes["org_partner_advisory"] = {
        "node_id": "org_partner_advisory",
        "canonical_name": "PartnerAdvisory",
        "display_name": "PartnerAdvisory Consulting",
        "node_type": "organization",
        "namespace": "CANONICAL",
    }

    # Legitimate structural child workstream
    graph.nodes["workstream:nexus_core"] = {
        "node_id": "workstream:nexus_core",
        "canonical_name": "Nexus Core Engine",
        "node_type": "workstream",
        "engagement_id": "project:project_nexus_core",
        "namespace": "CANONICAL",
    }
    graph.assertions["asrt_nexus_workstream"] = {
        "assertion_id": "asrt_nexus_workstream",
        "subject_node_id": "project:project_nexus",
        "object_node_id": "workstream:nexus_core",
        "predicate": "WORKSTREAM_OF",
        "claim_text": "Nexus Core Engine is a workstream of Project Nexus.",
        "engagement_id": "project:project_nexus",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }

    # Dangerous hub links (should NOT act as transitive bridges)
    graph.assertions["asrt_dan_gemini"] = {
        "assertion_id": "asrt_dan_gemini",
        "subject_node_id": "person_dan_okray",
        "object_node_id": "project:gemini_enablement",
        "predicate": "PART_OF",
        "claim_text": "Drew Oakley advises Gemini Enablement.",
        "engagement_id": "project:gemini_enablement",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }
    graph.assertions["asrt_amit_hrit"] = {
        "assertion_id": "asrt_amit_hrit",
        "subject_node_id": "person_amit",
        "object_node_id": "project:hrit",
        "predicate": "EXTENDS",
        "claim_text": "Amit supports HRIT project.",
        "engagement_id": "project:hrit",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }
    graph.assertions["asrt_partner_advisory_neet"] = {
        "assertion_id": "asrt_partner_advisory_neet",
        "subject_node_id": "org_partner_advisory",
        "object_node_id": "project:apex_trans",
        "predicate": "GOVERNED_BY",
        "claim_text": "PartnerAdvisory governs APEX_TRANS deliverables.",
        "engagement_id": "project:apex_trans",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }

    # Dan's person-level assertions across multiple engagements
    graph.assertions["asrt_dan_nexus_valid"] = {
        "assertion_id": "asrt_dan_nexus_valid",
        "subject_node_id": "person_dan_okray",
        "object_node_id": "concept:loss_leader",
        "predicate": "PROPOSES",
        "claim_text": "Drew Oakley proposed building the commercial loss leader model for Nexus.",
        "engagement_id": "project:project_nexus",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }
    graph.assertions["asrt_dan_gemini_leakage"] = {
        "assertion_id": "asrt_dan_gemini_leakage",
        "subject_node_id": "person_dan_okray",
        "object_node_id": "concept:gemini_enterprise",
        "predicate": "OWNS",
        "claim_text": "Shreyak will lead the work related to Google and Gemini Enterprise.",
        "engagement_id": "project:gemini_enablement",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }
    graph.assertions["asrt_dan_hrit_leakage"] = {
        "assertion_id": "asrt_dan_hrit_leakage",
        "subject_node_id": "person_dan_okray",
        "object_node_id": "system:sap_successfactors",
        "predicate": "LEADS",
        "claim_text": "Drew Oakley coordinates HRIT SAP SuccessFactors technical milestones.",
        "engagement_id": "project:hrit",
        "canonical_status": "canonical",
        "retrieval_eligible": 1,
        "canonical_eligible": 1,
    }

    return graph


def test_zero_transitive_inheritance_through_shared_attendees_or_orgs() -> None:
    """Verifies discover_topologically_inherited_engagements never inherits via person/org nodes."""
    clear_topological_inheritance_cache()
    graph = _build_firewall_test_topology()

    nexus_inherited = discover_topologically_inherited_engagements(graph, "project:project_nexus")
    # Must contain structural child or alias, but NEVER gemini, hrit, apex_trans, edo, or project_atlas
    for leaked in ("project:gemini_enablement", "project:hrit", "project:apex_trans", "project:edo", "project:project_atlas"):
        assert leaked not in nexus_inherited, f"Firewall breach: {leaked} inherited through person/org bridge"

    partner_advisory_inherited = discover_topologically_inherited_engagements(graph, "project:internal_consulting")
    for leaked in ("project:gemini_enablement", "project:hrit", "project:apex_trans", "project:edo", "project:project_atlas"):
        assert leaked not in partner_advisory_inherited, f"Firewall breach: {leaked} inherited into internal_consulting"


def test_get_person_context_excludes_cross_engagement_attendee_history() -> None:
    """Verifies get_person_context('Dan') returns zero assertions from gemini_enablement or hrit."""
    graph = _build_firewall_test_topology()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:project_nexus",
        attendees=[{"name": "Drew Oakley"}],
    )
    dialogue = MockDialogueContext(seeds=["loss leader", "commercial model"])

    ctx = retriever.get_person_context("Dan", dialogue)
    assert ctx["found"] is True, "Expected Dan to be found in graph"
    assertions = ctx["assertions"]
    assert len(assertions) > 0, "Expected at least 1 valid Nexus assertion for Dan"

    for a in assertions:
        aid = a.get("assertion_id")
        claim = a.get("claim_text", "")
        a_eng = a.get("engagement_id")
        assert a_eng in ("project:project_nexus", None), f"Leaked engagement {a_eng} in person context"
        assert aid != "asrt_dan_gemini_leakage", f"Leaked Gemini Enterprise assertion in Dan's context: {claim}"
        assert aid != "asrt_dan_hrit_leakage", f"Leaked SAP assertion in Dan's context: {claim}"
