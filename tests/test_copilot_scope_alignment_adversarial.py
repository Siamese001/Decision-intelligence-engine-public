"""
tests/test_copilot_scope_alignment_adversarial.py

Adversarial and boundary test suite for copilot multi-tenancy scope alignment and connection policy.
Enforces:
1. Boundary payloads: empty dictionaries, null/empty engagement fields, unpartitioned claims fail closed when unowned.
2. Malformed inputs: non-dict records, corrupted string formats, irregular typing.
3. Invariant bypass attempts: cross-tenant assertions spoofing enterprise prefixes or claiming anchor status while assigned to foreign scopes.
4. Concurrency & race conditions: multi-threaded concurrent calls to is_assertion_in_scope and _infer_assertion_scope.
5. Exact production regression: regression test pinning the 2026-10-09 Jarvis standup failure scenario.
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Dict
import pytest

from dual_pipeline.in_memory_graph import _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_enterprise_policy,
    is_technical_anchor_assertion,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payload handling with missing, empty, or None fields."""
    # Completely empty dict
    assert not is_assertion_in_scope({}, "project:project_nexus")

    # Missing engagement_id, project_scope, and scope_text
    unowned_claim = {
        "assertion_id": "ast-boundary-01",
        "subject_node_id": "node-unknown",
        "predicate": "USES",
        "claim_text": "Random claim without scope.",
    }
    assert not is_assertion_in_scope(unowned_claim, "project:project_nexus")

    # Empty string engagement fields
    empty_strings = {
        "assertion_id": "ast-boundary-02",
        "engagement_id": "",
        "project_scope": "   ",
        "scope_text": "",
    }
    assert not is_assertion_in_scope(empty_strings, "project:project_nexus")

    # None bound engagement fails closed
    assert not is_assertion_in_scope(unowned_claim, None)
    assert not is_assertion_in_scope(unowned_claim, "")


def test_adversarial_malformed_inputs():
    """Category 2: Malformed and invalid input types must fail closed gracefully without raising exceptions."""
    # Non-dict inputs to policy checks
    assert not is_assertion_in_scope(None, "project:project_nexus")  # type: ignore
    assert not is_assertion_in_scope("not a dict", "project:project_nexus")  # type: ignore
    assert not is_assertion_in_scope(["list", "of", "items"], "project:project_nexus")  # type: ignore
    assert not is_assertion_in_scope(12345, "project:project_nexus")  # type: ignore

    # Non-dict inputs to helpers
    assert not is_enterprise_policy(None)  # type: ignore
    assert not is_enterprise_policy("invalid")  # type: ignore
    assert not is_technical_anchor_assertion(None)  # type: ignore

    # Malformed bound engagement values
    valid_ast = {"assertion_id": "ast-1", "engagement_id": "project:project_nexus"}
    assert not is_assertion_in_scope(valid_ast, ":::invalid:::")  # type: ignore
    assert not is_assertion_in_scope(valid_ast, "project:")  # type: ignore
    assert not is_assertion_in_scope(valid_ast, "null")


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Ensure foreign tenant assertions cannot spoof enterprise or anchor status."""
    # Attack 1: Foreign tenant assertion with technical anchor prefix, but explicitly bound to foreign project
    foreign_anchor_attack = {
        "assertion_id": "ast-malicious-01",
        "subject_node_id": "anchor_foreign_secret_service",
        "object_node_id": "concept_proprietary_algorithm",
        "engagement_id": "project:foreign_competitor",
        "scope_text": "Proprietary internal architecture",
    }
    # When querying from project:project_nexus without permitted cross-engagement, must be BLOCKED
    assert not is_assertion_in_scope(foreign_anchor_attack, "project:project_nexus", permitted_engagements=None)

    # Attack 2: Substring injection attempting to trick policy into thinking it is enterprise
    substring_injection = {
        "assertion_id": "ast-malicious-02",
        "engagement_id": "project:enterprise_spoof_subtenant",
        "scope_text": "Confidential data product",
    }
    assert not is_assertion_in_scope(substring_injection, "project:project_nexus")

    # Attack 3: Foreign tenant assertion with fake enterprise classification string
    fake_classification = {
        "assertion_id": "ast-malicious-03",
        "engagement_id": "project:foreign_competitor",
        "classification": "CONFIDENTIAL_COMPETITOR",
        "is_enterprise_policy": False,
    }
    assert not is_assertion_in_scope(fake_classification, "project:project_nexus")


def test_adversarial_concurrency_race_conditions():
    """Category 4: Multi-threaded concurrent evaluation of assertion scoping."""
    assertions = [
        {"assertion_id": f"ast-{i}", "engagement_id": "project:project_nexus" if i % 2 == 0 else "project:project_lens"}
        for i in range(100)
    ]

    def evaluate_scope(ast: Dict[str, Any]) -> bool:
        return is_assertion_in_scope(ast, "project:project_nexus")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(evaluate_scope, assertions))

    for idx, allowed in enumerate(results):
        expected = (idx % 2 == 0)
        assert allowed == expected, f"Thread race evaluation mismatch at index {idx}"


def test_adversarial_exact_production_regression():
    """Category 5: Exact regression test for the 2026-10-09 Jarvis standup failure scenario."""
    # In SQLite schema, assertions contain scope_text instead of engagement_id.
    sap_legacy_assertion = {
        "assertion_id": "assertion-4d3dc8ea76b453187e5bf5c297c9",
        "subject_node_id": "entity-a5c25654c704b7037bc01a8d81eb",
        "predicate": "HAS_CLAIM_TEXT",
        "object_node_id": None,
        "object_literal": "SAP is an identified technical_anchor in 20260928 Followup Business Case Jill PartnerAdvisory.",
        "scope_text": "SAP is an identified technical_anchor in 20260928 Followup Business Case Jill PartnerAdvisory.",
        "canonical_status": "ACTIVE",
    }

    # Step 1: Infer scope via in_memory_graph loader helper
    source_map: dict[str, str] = {}
    inferred_scope = _infer_assertion_scope(sap_legacy_assertion, source_map)
    assert inferred_scope == "project:enterprise", f"Expected project:enterprise, got {inferred_scope}"

    # Step 2: Hydrate assertion with inferred scope
    sap_legacy_assertion["engagement_id"] = inferred_scope
    sap_legacy_assertion["project_scope"] = inferred_scope

    # Step 3: Verify is_assertion_in_scope permits this under active Jarvis/Nexus session
    assert is_assertion_in_scope(
        sap_legacy_assertion,
        bound_engagement="project:project_nexus",
    ), "Production regression: SAP enterprise technical anchor must be in scope under project_nexus binding"


def test_adversarial_candidate_control_checklist_quarantined_under_active_project():
    """Verify Candidate AI-SDLC control checklists do not bypass project scope under project_beacon."""
    control_checklist = {
        "assertion_id": "asrt_claim_claim_v42_ai_sdlc_control",
        "engagement_id": "project:enterprise",
        "predicate": "HAS_CLAIM_TEXT",
        "claim_text": "Candidate AI-SDLC control — Tool-vs-Outcome Attribution: minimum enterprise control: Do not equate commit volume with outcome velocity.",
    }
    assert not is_assertion_in_scope(control_checklist, bound_engagement="project:project_beacon")
    assert is_assertion_in_scope(
        control_checklist,
        bound_engagement="project:project_beacon",
        permitted_engagements={"project:enterprise"},
    )
