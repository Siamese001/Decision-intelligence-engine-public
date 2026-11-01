"""
tests/test_u0_graph_attendee_hitl.py

Unit tests for GraphDB fuzzy attendee matching, delimiter-normalized role matching,
and immediate Stage U0 HITL escalation.
"""
from __future__ import annotations

import pytest
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    _normalize_role_key,
    resolve_person_entity,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    UnifiedPreflightCascadeError,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary
from dual_pipeline.exceptions import HITLRequiredError


def test_normalize_role_key_punctuation_invariance():
    """Verify delimiter differences (slash, dash, underscore, whitespace) normalize identically."""
    k1 = _normalize_role_key("Delivery Lead / PM")
    k2 = _normalize_role_key("Delivery Lead-PM")
    k3 = _normalize_role_key("Delivery Lead - PM")
    k4 = _normalize_role_key("Delivery_Lead_PM")
    k5 = _normalize_role_key("delivery lead, pm")

    assert k1 == "delivery lead pm"
    assert k1 == k2 == k3 == k4 == k5


def test_traci_metzger_role_punctuation_variant_resolves_without_conflict():
    """Verify 'Delivery Lead / PM' matches GraphDB 'Delivery Lead-PM' without AMBIGUOUS_CONFLICT."""
    ent = resolve_person_entity("Taylor Mercer", explicit_role="Delivery Lead / PM")
    assert ent is not None
    assert ent.canonical_name == "Taylor Mercer"
    assert ent.role == "Delivery Lead-PM"
    assert ent.status == "RESOLVED"
    assert ent.requires_operator_confirmation is False
    assert ent.candidate_name is None


def test_tracie_metzger_typo_fuzzy_matches_graphdb():
    """Verify misspelled name 'Tracie Metzger' fuzzy matches GraphDB and attaches candidate assertions."""
    ent = resolve_person_entity("Tracie Metzger")
    assert ent is not None
    assert ent.canonical_name == "Taylor Mercer"
    assert ent.status == "UNRESOLVED_GRAPH_ATTENDEE"
    assert ent.requires_operator_confirmation is True
    assert ent.source == "graph_fuzzy_levenshtein_2"
    assert len(ent.candidates) >= 1
    assert any(c.role == "Delivery Lead-PM" for c in ent.candidates)


def test_unknown_attendee_halts_immediately_at_stage_u0():
    """Verify attendee absent from GraphDB raises HITLRequiredError at Stage U0 in <100ms."""
    metadata = {
        "roster": ["External Guest (Solutions Consultant, ACME Corp)"],
        "project": "project_nexus",
        "date": "10-07-2026",
        "time": "10:00 AM - 11:00 AM ET",
    }

    with pytest.raises(HITLRequiredError) as exc_info:
        U0IngestionBoundary._inspect_roster_completeness(metadata)

    err = exc_info.value
    assert err.hitl_type == "preflight_cascade"
    assert any("role:External Guest" in f for f in err.missing_fields)
    assert len(err.options) >= 1
    assert err.options[0]["field_name"] == "role:External Guest"


def test_batch_validate_preflight_checks_with_options():
    """Verify batch_validate_preflight_checks flags missing attendee and populates structured options."""
    metadata = {
        "roster": ["Unknown Contractor (Engineer, Partner)"],
        "project": "project_nexus",
        "date": "10-07-2026",
        "time": "10:00 AM - 11:00 AM ET",
    }

    report = batch_validate_preflight_checks(metadata, raw_transcript="Sample dialogue")
    assert not report.is_valid
    assert "attendee_roles" in report.missing_fields

    err = UnifiedPreflightCascadeError(report)
    assert len(err.options) >= 1
    assert err.options[0]["label"] == "Resolve attendee_roles"
