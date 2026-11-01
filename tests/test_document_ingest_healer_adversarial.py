"""Adversarial and edge-case tests for Wave 1: Roster isolation & grounded graph healing.

Covers:
1. Boundary payloads (empty/flat attendee lists, zero-entity mutations).
2. Malformed inputs (conflicting Author vs. Organization header tokens, non-dict entries).
3. Invariant bypass attempts (cross-project GraphDB SSOT NEE role/org leakage into internal PartnerAdvisory docs).
4. Exact production regressions (10/16/2026 PartnerAdvisory Leader Call flat roster, peer project:sherpa CONTAINS
   synthesis, Drew Oakley Author vs. roster MEMBER_OF alignment, and tentative staffing / transient action edges).
"""
from __future__ import annotations

from typing import Any, Dict, List

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    heal_disconnected_document_entities,
    heal_document_connectivity,
)
from dual_pipeline.transcript_qa_core.graph_healer import _heal_signal_evidence_and_qualifiers
from dual_pipeline.transcript_qa_core.semantic_linters import (
    _normalize_predicate,
    normalize_tentative_staffing_and_action_signals,
)
from dual_pipeline.transcript_stages_core.graph_post_roster import ensure_roster_attendees


def test_boundary_empty_and_flat_roster_non_nee_isolation() -> None:
    """Boundary & Invariant Bypass: Flat roster on internal_consulting must not leak Apex Global Enterprises or NEE roles."""
    empty_mutations: Dict[str, Any] = {}
    ensure_roster_attendees(empty_mutations, {}, {})
    assert empty_mutations == {}

    mutations: Dict[str, Any] = {
        "entity_upserts": [
            {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "description": "PartnerAdvisory consulting."},
        ],
        "relationship_signals": [],
    }
    metadata: Dict[str, Any] = {
        "project": "internal_consulting",
        "title": "10.16.2026 PartnerAdvisory Leader call internal",
        "attendees": ["Dan", "Amit", "Megan", "Lan"],
    }
    ev_index = {
        "ev-01": {
            "statement_id": "ev-01",
            "text": "Internal Consulting leadership call with Dan, Amit, Megan, and Lan.",
            "exact_text": "Internal Consulting leadership call with Dan, Amit, Megan, and Lan.",
            "attribution_mode": "ROSTER",
        }
    }

    ensure_roster_attendees(mutations, metadata, ev_index)

    upserts = mutations["entity_upserts"]
    entity_ids = {e["id"] for e in upserts}
    assert "org:apex_global-energy" not in entity_ids
    assert "role:planning-lead" not in entity_ids
    assert "role:portfolio-lead" not in entity_ids
    assert "org:partner_advisory" in entity_ids

    for ent in upserts:
        if ent.get("type") == "person":
            assert "Apex Global Enterprises" not in str(ent.get("description") or "")
            assert "PartnerAdvisory" in str(ent.get("description") or "")

    member_targets = {
        s["to_id"]
        for s in mutations["relationship_signals"]
        if (s.get("relationship") or s.get("predicate")) == "MEMBER_OF"
    }
    assert member_targets == {"org:partner_advisory"}


def test_production_regression_peer_projects_never_synthesize_contains() -> None:
    """Exact Production Regression: heal_document_connectivity must never wire project:* --CONTAINS--> project:*."""
    projection: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
                {"id": "project:google-funded-work", "type": "project", "name": "Google-Funded Work", "evidence_ids": ["ev-02"]},
                {"id": "project:sherpa", "type": "project", "name": "Sherpa", "evidence_ids": ["ev-03"]},
                {"id": "person:dan-okray", "type": "person", "name": "Drew Oakley", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-1",
                    "from_id": "person:dan-okray",
                    "to_id": "project:google-funded-work",
                    "relationship": "LEADS",
                    "predicate": "LEADS",
                    "evidence_ids": ["ev-02"],
                },
                {
                    "signal_id": "sig-2",
                    "from_id": "person:dan-okray",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "predicate": "MEMBER_OF",
                    "evidence_ids": ["ev-01"],
                },
            ],
        },
        "evidence_index": {
            "ev-01": {"exact_text": "PartnerAdvisory internal leadership call led by Drew Oakley."},
            "ev-02": {"exact_text": "Dan discussed Google-funded work."},
            "ev-03": {"exact_text": "Dan raised concerns about a resource intended for Sherpa."},
        },
    }

    changes = heal_document_connectivity(
        projection,
        metadata={"project": "internal_consulting", "run_id": "test-peer-project-regression"},
        document_text="PartnerAdvisory internal call covering Google-funded work and Sherpa.",
    )
    assert changes

    signals = projection["graph_mutations"]["relationship_signals"]
    for sig in signals:
        f_id = str(sig.get("from_id") or "")
        t_id = str(sig.get("to_id") or "")
        pred = str(sig.get("relationship") or sig.get("predicate") or "")
        assert not (f_id.startswith("project:") and t_id.startswith("project:") and pred == "CONTAINS"), (
            f"Forbidden peer-project CONTAINS synthesized: {sig}"
        )

    sherpa_edges = [
        s for s in signals if "project:sherpa" in (s.get("from_id"), s.get("to_id"))
    ]
    assert len(sherpa_edges) == 1
    assert sherpa_edges[0]["from_id"] == "org:partner_advisory"
    assert sherpa_edges[0]["to_id"] == "project:sherpa"
    assert sherpa_edges[0]["predicate"] == "OWNS"


def test_peer_projects_without_org_fallback_to_proposed_aligns_with() -> None:
    """Boundary Payload: When only two peer projects exist without an org, use ALIGNS_WITH (PROPOSED), never CONTAINS."""
    projection: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:alpha", "type": "project", "name": "Alpha", "evidence_ids": ["ev-01"]},
                {"id": "project:beta", "type": "project", "name": "Beta", "evidence_ids": ["ev-02"]},
            ],
            "relationship_signals": [],
        },
        "evidence_index": {
            "ev-01": {"exact_text": "Project Alpha overview."},
            "ev-02": {"exact_text": "Project Beta overview."},
        },
    }
    heal_disconnected_document_entities(projection, metadata={"run_id": "test-two-projects"})
    signals = projection["graph_mutations"]["relationship_signals"]
    assert len(signals) == 1
    assert signals[0]["predicate"] == "ALIGNS_WITH"
    assert signals[0]["epistemic_status"] == "PROPOSED"


def test_malformed_and_author_header_member_of_evidence_alignment() -> None:
    """Malformed & Production Regression: MEMBER_OF must prefer roster/org evidence over bare **Author:** headers."""
    data: Dict[str, Any] = {
        "evidence_index": {
            "ev-03": {
                "statement_id": "ev-03",
                "text": "**Author:** Drew Oakley",
                "exact_text": "**Author:** Drew Oakley",
            },
            "ev-04": {
                "statement_id": "ev-04",
                "text": "Internal Consulting leadership call with Dan, Amit, Andrew, and Tyler.",
                "exact_text": "Internal Consulting leadership call with Dan, Amit, Andrew, and Tyler.",
            },
        }
    }
    signals: List[Dict[str, Any]] = [
        {
            "signal_id": "sig-dan-partner_advisory",
            "from_id": "person:dan-okray",
            "to_id": "org:partner_advisory",
            "relationship": "MEMBER_OF",
            "predicate": "MEMBER_OF",
            "evidence_ids": ["ev-03"],
            "evidence": {"statement_id": "ev-03", "direct_quote": "**Author:** Drew Oakley"},
        }
    ]
    changes: List[str] = []
    _heal_signal_evidence_and_qualifiers(data, signals, changes)

    assert signals[0]["evidence_ids"] == ["ev-04"]
    assert signals[0]["evidence"]["statement_id"] == "ev-04"
    assert any("Aligned MEMBER_OF evidence" in c for c in changes)


def test_tentative_staffing_demotion_and_transient_action_filtering() -> None:
    """Exact Production Regression: Tentative staffing demotes to CANDIDATE_FOR and action-item USES are dropped."""
    assert _normalize_predicate("TENTATIVE_ASSIGNMENT") == "CANDIDATE_FOR"
    assert _normalize_predicate("PROPOSED_FOR") == "CANDIDATE_FOR"

    ev_index = {
        "ev-leo": {
            "text": "Dan concluded that Leo was likely the most appropriate person to onboard as a potential contributor.",
        },
        "ev-tyler": {
            "text": "Tyler offered to update Salesforce to reflect that assignment.",
        },
    }
    signals: List[Dict[str, Any]] = [
        {
            "signal_id": "sig-leo",
            "from_id": "person:leo",
            "to_id": "project:google-funded-work",
            "relationship": "MEMBER_OF",
            "predicate": "MEMBER_OF",
            "description": "Member of organization.",
            "evidence_ids": ["ev-leo"],
        },
        {
            "signal_id": "sig-tyler-sf",
            "from_id": "person:tyler",
            "to_id": "system:salesforce",
            "relationship": "USES",
            "predicate": "USES",
            "description": "Tyler offered to update Salesforce.",
            "evidence_ids": ["ev-tyler"],
        },
    ]

    cleaned = normalize_tentative_staffing_and_action_signals(signals, ev_index)
    assert len(cleaned) == 1
    assert cleaned[0]["signal_id"] == "sig-leo"
    assert cleaned[0]["predicate"] == "CANDIDATE_FOR"
    assert cleaned[0]["epistemic_status"] == "PROPOSED"
    assert cleaned[0]["status"] == "PROPOSED"
