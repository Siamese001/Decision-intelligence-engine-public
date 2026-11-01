"""
tests/test_copilot_predicate_quality_adversarial.py

Adversarial tests for Wave 3: Question Predicate Quality Gate & Scope Containment.
Covers 4 mandatory edge-case categories:
1. Boundary payload: empty candidates, None predicates, empty strings.
2. Malformed input: missing fields, non-string predicates, integer object literals.
3. Invariant bypass attempt: cross-tenant scope evasion through missing schema fields.
4. Exact production regression: reproduction scenario from the 5-Agent Forensic RCA.
"""
from __future__ import annotations

from typing import Any, Dict
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology, _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_node_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _create_test_topology(
    nodes: Dict[str, Dict[str, Any]],
    assertions: Dict[str, Dict[str, Any]],
) -> InMemoryGraphTopology:
    forward_adj = {nid: [] for nid in nodes}
    reverse_adj = {nid: [] for nid in nodes}
    for aid, a in assertions.items():
        s = a.get("subject_node_id")
        o = a.get("object_node_id")
        if s and s in forward_adj:
            forward_adj[s].append({"assertion_id": aid, "predicate": a.get("predicate"), "target": o})
        if o and o in reverse_adj:
            reverse_adj[o].append({"assertion_id": aid, "predicate": a.get("predicate"), "target": s})
    return InMemoryGraphTopology(
        graph_path="/tmp/mock_adv.sqlite",
        graph_sha256="mock_hash_adv",
        nodes=nodes,
        assertions=assertions,
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency=forward_adj,
        reverse_adjacency=reverse_adj,
        undirected_adjacency=forward_adj,
        canonical_edges=[],
    )


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - None, empty strings, and empty topologies."""
    topo = _create_test_topology({}, {})
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    assert retriever.get_next_questions("", max_questions=3) == []
    assert retriever.get_next_questions("   ", max_questions=3) == []
    assert retriever.get_next_actions("", max_actions=3) == []
    assert not is_assertion_in_scope({}, bound_engagement=None)
    assert not is_node_in_scope("", {}, bound_engagement=None)


def test_adversarial_malformed_inputs():
    """Category 2: Malformed inputs - non-string predicates, integer object literals, corrupt dicts."""
    nodes = {
        "node-1": {
            "node_id": "node-1",
            "node_type": 12345,  # non-string node_type
            "canonical_name": None,
            "description": None,
        }
    }
    assertions = {
        "ast-corrupt-1": {
            "assertion_id": "ast-corrupt-1",
            "subject_node_id": "node-1",
            "predicate": None,  # None predicate
            "claim_text": None,
            "object_literal": 999999,  # integer literal
            "engagement_id": "project:project_nexus",
        },
        "ast-corrupt-2": {
            "assertion_id": "ast-corrupt-2",
            "subject_node_id": "node-1",
            "predicate": ["INVALID_LIST_PREDICATE"],
            "claim_text": {"invalid": "dict_claim"},
            "engagement_id": "project:project_nexus",
        },
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    # Corrupt records must not crash the engine or qualify as valid questions
    questions = retriever.get_next_questions("node", max_questions=3)
    assert questions == []


def test_adversarial_cross_tenant_scope_evasion():
    """Category 3: Invariant bypass attempt - assertion with missing engagement_id mentioning external project."""
    # Assertion claims from Lens Pipeline Review with no engagement_id column
    evasion_assertion = {
        "assertion_id": "ast-evasion-1",
        "subject_node_id": "node-db",
        "predicate": "HAS_RISK",
        "object_literal": "Database lock failure in 20260930 Pipeline Review.",
        "scope_text": "Database lock failure in 20260930 Pipeline Review.",
        "primary_source_id": "src-pipeline-review",
    }
    source_map = {"src-pipeline-review": "20260930 Pipeline Review"}

    # Inferred scope must isolate to project_lens, not leak to enterprise
    inferred = _infer_assertion_scope(evasion_assertion, source_map)
    assert inferred == "project:project_lens", f"Expected project:project_lens, got {inferred}"

    evasion_assertion["engagement_id"] = inferred
    evasion_assertion["project_scope"] = inferred

    # Cross-tenant check: must fail closed under Project Nexus
    assert not is_assertion_in_scope(
        evasion_assertion,
        bound_engagement="project:project_nexus",
    ), "Cross-tenant assertion must fail closed under foreign project binding"


def test_exact_production_regression_pipeline_and_pgd_assertions():
    """Category 4: Exact regression test for the 2026-10-12 copilot RCA failure scenario."""
    # 1. Pipeline Review assertion: Persistent Database
    ast_pipeline = {
        "assertion_id": "assertion-c4564f58f4b214de633a1e94ba3c",
        "subject_node_id": "entity-03cd78c50a0e662a045afe3be69b",
        "predicate": "HAS_CLAIM_TEXT",
        "object_literal": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
        "scope_text": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
        "primary_source_id": "source-9d957076154d014ffd4fe4dd",
    }
    source_map = {
        "source-9d957076154d014ffd4fe4dd": "20260930 Pipeline Review",
        "source-c2497f5ec2ae9a738d93d469": "20260929 Pgd Followup Anthony Eduardo",
    }

    # 2. PGD Followup assertion: Atlas Persistent Learning Database
    ast_pgd = {
        "assertion_id": "assertion-2cbfbcbed5aad5014791057c5681",
        "subject_node_id": "entity-4a93a041b5471ead2151b8fc5e5f",
        "predicate": "HAS_CLAIM_TEXT",
        "object_literal": "Atlas Persistent Learning Database is an identified technical_anchor in 20260929 Pgd Followup Anthony Eduardo.",
        "scope_text": "Atlas Persistent Learning Database is an identified technical_anchor in 20260929 Pgd Followup Anthony Eduardo.",
        "primary_source_id": "source-c2497f5ec2ae9a738d93d469",
    }

    # Scope inference must resolve both to their respective projects, not enterprise
    scope_pipeline = _infer_assertion_scope(ast_pipeline, source_map)
    assert scope_pipeline == "project:project_lens", f"Pipeline review must map to project_lens, got {scope_pipeline}"
    ast_pipeline["engagement_id"] = scope_pipeline
    ast_pipeline["project_scope"] = scope_pipeline

    scope_pgd = _infer_assertion_scope(ast_pgd, source_map)
    assert scope_pgd == "project:pgd", f"PGD followup must map to project:pgd, got {scope_pgd}"
    ast_pgd["engagement_id"] = scope_pgd
    ast_pgd["project_scope"] = scope_pgd

    # Neither assertion may be in scope for Project Nexus
    assert not is_assertion_in_scope(ast_pipeline, bound_engagement="project:project_nexus")
    assert not is_assertion_in_scope(ast_pgd, bound_engagement="project:project_nexus")

    # Even if they were somehow in candidate pool, HAS_CLAIM_TEXT must be blocked by get_next_questions
    nodes = {
        "entity-03cd78c50a0e662a045afe3be69b": {
            "node_id": "entity-03cd78c50a0e662a045afe3be69b",
            "node_type": "technical_anchor",
            "canonical_name": "Persistent Database",
            "description": "Database receiving the final output from the LENS processing sequence.",
            "engagement_id": "project:project_nexus",  # intentionally force project_nexus
        }
    }
    assertions = {
        ast_pipeline["assertion_id"]: {
            **ast_pipeline,
            "engagement_id": "project:project_nexus",  # force into pool
        }
    }
    topo = _create_test_topology(nodes, assertions)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    questions = retriever.get_next_questions("vector database", max_questions=3)
    assert questions == [], "Raw HAS_CLAIM_TEXT extraction triple must NEVER qualify as next question"
