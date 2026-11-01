"""
tests/test_copilot_scope_quarantine_adversarial.py

Adversarial and boundary test suite for Copilot scope quarantine defenses.
Covers mandatory edge-case categories per e2e-and-edge-case-coverage.md:
1. Exact production regression:
   - Eliminates SCOPE_QUARANTINED collapse during entity resolution (e.g. "partner_advisory")
   - Blocks quarantine:unknown_ownership leakage into cross-workstream candidates
2. Invariant bypass attempts:
   - Strict rejection of quarantined engagement_id / project_scope in check_assertion_validity
   - Cross-tenant and unowned assertion rejection in topology edge matching
3. Boundary & malformed payloads:
   - Non-dict, empty, and corrupted payloads fail closed cleanly
4. Hydration coverage:
   - Source table with source_name column correctly hydrates source_map and infers project scope
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Set
import pytest

from dual_pipeline.in_memory_graph import _hydrate_assertion_scopes, _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import check_assertion_validity
from dual_pipeline.live_meeting_recorder_core.copilot_intents import _collect_cross_workstream_entity_assertions
from dual_pipeline.live_meeting_recorder_core.copilot_connect import _match_topology_assertion


class _MockTopology:
    def __init__(self, assertions: Dict[str, Dict[str, Any]], nodes: Dict[str, Dict[str, Any]] | None = None):
        self.assertions = assertions
        self.nodes = nodes or {}


class _MockRetriever:
    def __init__(
        self,
        topology: _MockTopology,
        engagement_id: str = "project:internal_consulting",
        permitted_engagements: Set[str] | None = None,
    ):
        self._topology = topology
        self.engagement_id = engagement_id
        self.permitted_engagements = permitted_engagements or {"project:enterprise"}

    def _is_assertion_allowed(self, a: Dict[str, Any], claim: str) -> bool:
        return True


def test_adversarial_grounding_verifier_rejects_quarantined_scopes():
    """Category 2: Invariant bypass - quarantined engagement/project scopes fail validation."""
    # 1. Quarantined engagement_id
    ast_quarantined_eng = {
        "assertion_id": "ast-q-01",
        "engagement_id": "quarantine:unknown_ownership",
        "canonical_status": "ACTIVE",
        "claim_text": "PartnerAdvisory handles knowledge transfer.",
    }
    valid, reason = check_assertion_validity(ast_quarantined_eng)
    assert not valid
    assert reason == "SCOPE_QUARANTINED"

    # 2. Quarantined project_scope
    ast_quarantined_proj = {
        "assertion_id": "ast-q-02",
        "project_scope": "quarantine:unverified_source",
        "canonical_status": "ACTIVE",
        "claim_text": "PartnerAdvisory handles team onboarding.",
    }
    valid, reason = check_assertion_validity(ast_quarantined_proj)
    assert not valid
    assert reason == "SCOPE_QUARANTINED"

    # 3. Valid in-scope assertion passes
    ast_valid = {
        "assertion_id": "ast-valid-01",
        "engagement_id": "project:internal_consulting",
        "canonical_status": "ACTIVE",
        "claim_text": "PartnerAdvisory team structure has four workstreams.",
    }
    valid, reason = check_assertion_validity(ast_valid)
    assert valid
    assert reason is None


def test_adversarial_malformed_and_boundary_payloads():
    """Category 3: Boundary & malformed payloads must fail closed without exceptions."""
    # Non-dict payload
    valid, reason = check_assertion_validity(None)  # type: ignore
    assert not valid
    assert reason == "INVALID_PAYLOAD"

    valid, reason = check_assertion_validity("not-a-dict")  # type: ignore
    assert not valid
    assert reason == "INVALID_PAYLOAD"

    # Empty payload
    valid, reason = check_assertion_validity({})
    assert not valid
    assert reason == "MISSING_ASSERTION_ID"

    # Missing assertion ID
    valid, reason = check_assertion_validity({"claim_text": "Hello world"})
    assert not valid
    assert reason == "MISSING_ASSERTION_ID"


def test_adversarial_cross_workstream_quarantine_exclusion_production_regression():
    """Category 1: Exact production regression - cross-workstream entity collection blocks quarantine."""
    nodes = {
        "org_partner_advisory": {"name": "PartnerAdvisory", "entity_type": "ORGANIZATION"},
        "concept_churn": {"name": "Team Churn", "entity_type": "CONCEPT"},
    }
    assertions = {
        # Valid in-scope assertion
        "ast-partner_advisory-01": {
            "assertion_id": "ast-partner_advisory-01",
            "subject_node_id": "org_partner_advisory",
            "object_node_id": "concept_churn",
            "predicate": "MITIGATES",
            "claim_text": "PartnerAdvisory provides change management playbooks to mitigate team churn.",
            "engagement_id": "project:internal_consulting",
        },
        # Quarantined unowned assertion (reproduces root cause bug)
        "ast-partner_advisory-quarantined": {
            "assertion_id": "ast-partner_advisory-quarantined",
            "subject_node_id": "org_partner_advisory",
            "object_node_id": "concept_churn",
            "predicate": "EXPERIENCES",
            "claim_text": "PartnerAdvisory client project experienced churn during transition.",
            "engagement_id": "quarantine:unknown_ownership",
        },
        # Foreign tenant assertion (project:project_nexus)
        "ast-partner_advisory-nexus": {
            "assertion_id": "ast-partner_advisory-nexus",
            "subject_node_id": "org_partner_advisory",
            "object_node_id": "concept_churn",
            "predicate": "OBSERVES",
            "claim_text": "Nexus team observed engineering attrition.",
            "engagement_id": "project:project_nexus",
        },
        # Enterprise policy assertion (allowed cross-engagement)
        "ast-enterprise-01": {
            "assertion_id": "ast-enterprise-01",
            "subject_node_id": "org_partner_advisory",
            "object_node_id": "concept_churn",
            "predicate": "ENFORCES",
            "claim_text": "Enterprise policy mandates offboarding documentation.",
            "engagement_id": "project:enterprise",
            "is_enterprise_policy": True,
        },
    }

    topology = _MockTopology(assertions=assertions, nodes=nodes)
    retriever = _MockRetriever(
        topology=topology,
        engagement_id="project:internal_consulting",
        permitted_engagements={"project:enterprise"},
    )

    seen: Set[str] = set()
    bridged = _collect_cross_workstream_entity_assertions(
        retriever=retriever,
        resolved_nids={"org_partner_advisory"},
        seen=seen,
    )

    bridged_ids = {b["assertion_id"] for b in bridged}
    # In-scope and enterprise must be included
    assert "ast-partner_advisory-01" in bridged_ids, "In-scope assertion must be collected"
    assert "ast-enterprise-01" in bridged_ids, "Enterprise policy assertion must be collected"

    # Quarantined and foreign tenant assertions MUST NOT be collected
    assert "ast-partner_advisory-quarantined" not in bridged_ids, "Quarantined assertion must be strictly excluded"
    assert "ast-partner_advisory-nexus" not in bridged_ids, "Foreign tenant assertion must be strictly excluded"


def test_adversarial_topology_matching_blocks_quarantine_and_c19():
    """Category 2: Topology chain matching rejects quarantined and C-19 assertions."""
    top_assertions = {
        "ast-q-match": {
            "assertion_id": "ast-q-match",
            "subject_node_id": "node_a",
            "object_node_id": "node_b",
            "predicate": "CONNECTS_TO",
            "engagement_id": "quarantine:unknown_ownership",
        },
        "ast-c19-match": {
            "assertion_id": "ast-c19-match",
            "subject_node_id": "node_a",
            "object_node_id": "node_b",
            "predicate": "CONNECTS_TO",
            "engagement_id": "project:internal_consulting",
            "claim_text": "Personal salary trajectory $1.2M advisory note.",
        },
        "ast-valid-match": {
            "assertion_id": "ast-valid-match",
            "subject_node_id": "node_a",
            "object_node_id": "node_b",
            "predicate": "CONNECTS_TO",
            "engagement_id": "project:internal_consulting",
            "claim_text": "Node A connects to Node B cleanly.",
        },
    }

    topology = _MockTopology(assertions=top_assertions)
    retriever = _MockRetriever(topology=topology, engagement_id="project:internal_consulting")

    # Match against quarantined assertion -> must return None
    seen_ids: Set[str] = set()
    match_q = _match_topology_assertion("node_a", "node_b", "CONNECTS_TO", {"ast-q-match": top_assertions["ast-q-match"]}, seen_ids, retriever=retriever)
    assert match_q is None, "Quarantined assertion in topology must not match"

    # Match against C-19 assertion -> must return None
    match_c19 = _match_topology_assertion("node_a", "node_b", "CONNECTS_TO", {"ast-c19-match": top_assertions["ast-c19-match"]}, seen_ids, retriever=retriever)
    assert match_c19 is None, "C-19 assertion in topology must not match"

    # Match against valid in-scope assertion -> returns match dict
    match_valid = _match_topology_assertion("node_a", "node_b", "CONNECTS_TO", {"ast-valid-match": top_assertions["ast-valid-match"]}, seen_ids, retriever=retriever)
    assert match_valid is not None
    assert match_valid["assertion_id"] == "ast-valid-match"


def test_adversarial_source_name_hydration_in_sqlite():
    """Category 4: In-memory graph scope hydration supports source_name column in SQLite."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT,
            uri TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO source (source_id, source_name, uri) VALUES (?, ?, ?)",
        ("src-nexus-01", "20261001 Nexus Architecture Review", "file:///nexus.txt"),
    )
    conn.execute(
        "INSERT INTO source (source_id, source_name, uri) VALUES (?, ?, ?)",
        ("src-lens-01", "20260915 SOW 1 Lens Discovery", "file:///lens.txt"),
    )
    conn.commit()

    assertions = {
        "ast-1": {
            "assertion_id": "ast-1",
            "primary_source_id": "src-nexus-01",
            "subject_node_id": "concept_nexus_core",
            "object_node_id": "concept_api",
            "object_literal": "Nexus Core API",
        },
        "ast-2": {
            "assertion_id": "ast-2",
            "primary_source_id": "src-lens-01",
            "subject_node_id": "concept_lens_analytics",
            "object_node_id": "concept_pipeline",
            "object_literal": "Lens pipeline",
        },
    }

    _hydrate_assertion_scopes(conn, assertions)

    # Scopes must be inferred from source_name rather than defaulting to quarantine:unknown_ownership
    assert assertions["ast-1"]["engagement_id"] == "project:project_nexus"
    assert assertions["ast-1"]["project_scope"] == "project:project_nexus"

    assert assertions["ast-2"]["engagement_id"] == "project:project_lens"
    assert assertions["ast-2"]["project_scope"] == "project:project_lens"
