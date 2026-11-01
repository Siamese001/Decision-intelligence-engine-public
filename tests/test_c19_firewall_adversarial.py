"""
tests/test_c19_firewall_adversarial.py

Adversarial test suite for Rule C-19 Career Leverage & Privacy Firewall.
Verifies:
1. Deep inspection across all 7 node attributes (node_id, canonical_name, display_name,
   description, aliases, notes, organization).
2. Deep inspection across assertion properties (assertion_id, claim_text, predicate,
   subject_node_id, object_node_id, notes, reasoning, political_rationale, networking_wedge).
3. Fail-closed behavior on enclave/quarantine flags (namespace == 'INTERNAL_ADVISORY',
   is_quarantined == 1, stealth_score > 0).
4. Boundary and malformed payloads (None, non-dict, empty strings).
5. Adversarial evasion attempts (mixed-case, embedded substrings, regex variants).
6. Negative controls: ensuring clean engineering text is not falsely flagged.
"""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.c19_firewall import (
    C19_FORBIDDEN_MARKERS,
    has_c19_marker,
    is_c19_quarantined_assertion,
    is_c19_quarantined_node,
)


# --- Section 1: Marker Detection & Evasion Probing ---

@pytest.mark.parametrize(
    "marker",
    [
        "$1.2M",
        "$1.2m",
        "executive compensation band",
        "INTERNAL_ADVISORY",
        "internal_advisory",
        "networking_wedge",
        "political_rationale",
        "political power map",
        "political materiality",
        "political maneuver",
        "political wedge",
        "career_leverage",
        "career leverage",
        "career strategy",
        "career capital",
        "career positioning",
        "career advisory",
        "career notes",
        "compensation_trajectory",
        "compensation trajectory",
        "compensation negotiation",
        "compensation leverage",
        "stealth_score",
        "personal equity expansion",
        "personal promotion",
        "advancement vector",
    ],
)
def test_has_c19_marker_canonical_tokens(marker: str) -> None:
    """Verifies that all canonical C-19 markers are detected."""
    assert has_c19_marker(marker)
    # Embedded in conversational sentence
    assert has_c19_marker(f"Regarding the upcoming review, our {marker} should be maintained.")
    # Mixed case
    assert has_c19_marker(marker.swapcase())


@pytest.mark.parametrize(
    "clean_text",
    [
        "Project status review for Sprint 4.",
        "SAP ERP architecture integration with Document AI.",
        "Ingested 1.2 million rows into BigQuery without error.",  # Note: not $1.2M
        "Daniel Brankin owns technical architecture.",
        "SecOps standard mandates SSO for web applications.",
        "High availability and disaster recovery runbooks.",
    ],
)
def test_has_c19_marker_clean_negative_controls(clean_text: str) -> None:
    """Verifies that authentic enterprise content is never falsely flagged as C-19."""
    assert not has_c19_marker(clean_text)


def test_has_c19_marker_nested_structures() -> None:
    """Verifies marker detection within nested lists, dicts, and tuples."""
    assert has_c19_marker(["clean", "normal", "political power map"])
    assert has_c19_marker({"key": "clean", "meta": {"sub": "career leverage"}})
    assert has_c19_marker({"internal_advisory": "data"})
    assert not has_c19_marker(["clean", {"item": "normal"}, 12345])
    assert not has_c19_marker(None)
    assert not has_c19_marker("")


# --- Section 2: Deep Node Inspection across All 7 Attributes ---

def _base_clean_node() -> dict:
    return {
        "node_id": "person:daniel_brankin",
        "canonical_name": "Daniel Brankin",
        "display_name": "Daniel Brankin",
        "description": "Enterprise Solutions Architect",
        "aliases": ["DB", "Dan Brankin"],
        "notes": "Primary technical lead for SAP ERP.",
        "organization": "PartnerAdvisory",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
    }


def test_clean_node_passes_c19_inspection() -> None:
    node = _base_clean_node()
    assert not is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_quarantine_flags() -> None:
    # Namespace flag
    node_ns = _base_clean_node()
    node_ns["namespace"] = "INTERNAL_ADVISORY"
    assert is_c19_quarantined_node("person:daniel_brankin", node_ns)

    # is_quarantined flag (int or bool)
    node_q1 = _base_clean_node()
    node_q1["is_quarantined"] = 1
    assert is_c19_quarantined_node("person:daniel_brankin", node_q1)

    node_q2 = _base_clean_node()
    node_q2["is_quarantined"] = True
    assert is_c19_quarantined_node("person:daniel_brankin", node_q2)

    # stealth_score flag
    node_stealth = _base_clean_node()
    node_stealth["stealth_score"] = 1.0
    assert is_c19_quarantined_node("person:daniel_brankin", node_stealth)


def test_node_attribute_1_node_id_quarantine() -> None:
    node = _base_clean_node()
    assert is_c19_quarantined_node("person:career_leverage_holder", node)


def test_node_attribute_2_canonical_name_quarantine() -> None:
    node = _base_clean_node()
    node["canonical_name"] = "Daniel Brankin (executive compensation band)"
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_attribute_3_display_name_quarantine() -> None:
    node = _base_clean_node()
    node["display_name"] = "Dan Brankin - Political Power Map"
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_attribute_4_description_quarantine() -> None:
    node = _base_clean_node()
    node["description"] = "Architect building a networking wedge for partner expansion."
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_attribute_5_aliases_quarantine() -> None:
    node = _base_clean_node()
    node["aliases"] = ["Dan", "advancement vector lead"]
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_attribute_6_notes_quarantine() -> None:
    node = _base_clean_node()
    node["notes"] = "Internal notes on compensation negotiation and equity."
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_attribute_7_organization_quarantine() -> None:
    node = _base_clean_node()
    node["organization"] = "INTERNAL_ADVISORY"
    assert is_c19_quarantined_node("person:daniel_brankin", node)


def test_node_malformed_fails_closed() -> None:
    assert is_c19_quarantined_node("", _base_clean_node())
    assert is_c19_quarantined_node("person:clean", None)
    assert is_c19_quarantined_node("person:clean", "not a dict")
    assert is_c19_quarantined_node("person:clean", 12345)


# --- Section 3: Deep Assertion Inspection across All Fields ---

def _base_clean_assertion() -> dict:
    return {
        "assertion_id": "assertion-clean-01",
        "subject_node_id": "person:daniel_brankin",
        "object_node_id": "system:sap_erp",
        "predicate": "OWNS_OR_LEADS",
        "claim_text": "Daniel Brankin owns technical architecture for SAP ERP.",
        "engagement_id": "project:nexus",
        "namespace": "CANONICAL",
        "canonical_status": "ACTIVE",
    }


def test_clean_assertion_passes_c19_inspection() -> None:
    assertion = _base_clean_assertion()
    assert not is_c19_quarantined_assertion(assertion, "assertion-clean-01")


def test_assertion_quarantine_flags() -> None:
    # Namespace
    a_ns = _base_clean_assertion()
    a_ns["namespace"] = "INTERNAL_ADVISORY"
    assert is_c19_quarantined_assertion(a_ns)

    # is_quarantined
    a_q = _base_clean_assertion()
    a_q["is_quarantined"] = 1
    assert is_c19_quarantined_assertion(a_q)

    # stealth_score
    a_s = _base_clean_assertion()
    a_s["stealth_score"] = 0.95
    assert is_c19_quarantined_assertion(a_s)


def test_assertion_id_with_c19_marker() -> None:
    assertion = _base_clean_assertion()
    assert is_c19_quarantined_assertion(assertion, aid="assertion-career_leverage-01")


def test_assertion_claim_text_with_c19_marker() -> None:
    assertion = _base_clean_assertion()
    assertion["claim_text"] = "This role provides executive compensation leverage."
    assert is_c19_quarantined_assertion(assertion)


def test_assertion_predicate_with_c19_marker() -> None:
    assertion = _base_clean_assertion()
    assertion["predicate"] = "POLITICAL_MANEUVER"
    assert is_c19_quarantined_assertion(assertion)


def test_assertion_nodes_with_c19_marker() -> None:
    a_subj = _base_clean_assertion()
    a_subj["subject_node_id"] = "concept:career_strategy"
    assert is_c19_quarantined_assertion(a_subj)

    a_obj = _base_clean_assertion()
    a_obj["object_node_id"] = "concept:political_wedge"
    assert is_c19_quarantined_assertion(a_obj)


def test_assertion_notes_and_reasoning_quarantine() -> None:
    a_notes = _base_clean_assertion()
    a_notes["notes"] = "Target compensation trajectory exceeds $1.2M by Q4."
    assert is_c19_quarantined_assertion(a_notes)

    a_reason = _base_clean_assertion()
    a_reason["reasoning"] = "Positioning personal equity expansion strategy."
    assert is_c19_quarantined_assertion(a_reason)


def test_assertion_malformed_fails_closed() -> None:
    assert is_c19_quarantined_assertion(None)
    assert is_c19_quarantined_assertion("invalid")
    assert is_c19_quarantined_assertion([])
