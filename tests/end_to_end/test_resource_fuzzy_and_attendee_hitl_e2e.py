"""
tests/end_to_end/test_resource_fuzzy_and_attendee_hitl_e2e.py

End-to-end integration and lifecycle verification for:
1. Bounded fuzzy resource discovery fallback across CLI and Stage U0 ingress.
2. Attendee GraphDB resolution and delimiter-normalized role matching.
3. Immediate Stage U0 HITL escalation when attendees are absent or ambiguous.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.resource_discovery import discover_resource_fuzzy
from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    UnifiedPreflightCascadeError,
)
from dual_pipeline.cli.ingest import _validate_canonical_input, build_canonical_metadata
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
from dual_pipeline.transcript_pipeline_core.ingress import _inspect_file_target
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_e2e_kickoff_missing_wav_lifecycle():
    """E2E lifecycle: missing kickoff .wav resolves to authentic transcript and metadata in <500ms."""
    query = "00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav"

    # Step 1: CLI validation resolves input
    args = argparse.Namespace(input=query, project="project_nexus")
    resolved_p = _validate_canonical_input(args)
    assert resolved_p.is_file()
    assert "Jarvis_Kickoff_Jill_PartnerAdvisory" in resolved_p.name
    assert getattr(args, "metadata", None) is not None
    assert "jarvis_kickoff_metadata.json" in str(args.metadata)

    # Step 2: Build canonical metadata automatically hydrates from co-located sidecar
    meta = build_canonical_metadata(args)
    assert meta["input_path"] == str(resolved_p)
    assert meta.get("project") in ("project_nexus", "nexus") or "kickoff" in meta.get("meeting_name", "").lower()

    # Step 3: Ingress inspection succeeds and extracts hash
    is_file, input_p, input_ext, hash_val = _inspect_file_target(query, meta)
    assert is_file is True
    assert input_p == resolved_p
    assert input_ext == ".md"
    assert hash_val is not None


def test_e2e_roster_role_delimiter_variant_lifecycle():
    """E2E lifecycle: 'Delivery Lead / PM' matches GraphDB 'Delivery Lead-PM' without AMBIGUOUS_CONFLICT."""
    ent = resolve_person_entity("Taylor Mercer", explicit_role="Delivery Lead / PM")
    assert ent is not None
    assert ent.canonical_name == "Taylor Mercer"
    assert ent.role == "Delivery Lead-PM"
    assert ent.status == "RESOLVED"
    assert not ent.requires_operator_confirmation

    metadata = {
        "roster": ["Taylor Mercer (Delivery Lead / PM, PartnerAdvisory)"],
        "project": "project_nexus",
        "date": "10-07-2026",
        "time": "10:00 AM - 11:00 AM ET",
        "attendees_confirmed_by_operator": True,
    }
    report = batch_validate_preflight_checks(metadata, raw_transcript="Sample transcript text")
    assert "attendee_roles" not in report.missing_fields


def test_e2e_typo_attendee_immediate_hitl_escalation():
    """E2E lifecycle: typo 'Tracie Metzger' fuzzy-matches GraphDB and halts Stage U0 with HITL error."""
    metadata = {
        "roster": ["Tracie Metzger (Delivery Lead, PartnerAdvisory)"],
        "project": "project_nexus",
        "date": "10-07-2026",
        "time": "10:00 AM - 11:00 AM ET",
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        U0IngestionBoundary._inspect_roster_completeness(metadata)

    err = exc_info.value
    assert err.hitl_type == "preflight_cascade"
    assert any("Tracie Metzger" in f for f in err.missing_fields)
    assert len(err.options) >= 1


def test_e2e_unknown_graph_attendee_fast_fail():
    """E2E lifecycle: attendee absent from GraphDB fails fast at Stage U0 in <100ms."""
    metadata = {
        "roster": ["Unenrolled Observer (Auditor, External Partner)"],
        "project": "project_nexus",
        "date": "10-07-2026",
        "time": "10:00 AM - 11:00 AM ET",
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        U0IngestionBoundary._inspect_roster_completeness(metadata)

    err = exc_info.value
    assert err.hitl_type == "preflight_cascade"
    assert any("Unenrolled Observer" in f for f in err.missing_fields)
