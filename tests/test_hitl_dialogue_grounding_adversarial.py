"""
tests/test_hitl_dialogue_grounding_adversarial.py

Adversarial and Edge-Case Test Suite for Dialogue Grounding & HITL Integrity:
1. Exact production regressions (08-04-2026 Amanda GraphDB bypass, 10-19-2026 Calvin directory bypass).
2. Invariant bypass attempts (unauthenticated / unconfirmed ledger injection, forged receipts).
3. Boundary payloads (whitespace names, single-character tokens, empty dialogue turns).
4. Malformed inputs (corrupted attendee receipts, tampered subject digests, non-dict payloads).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.c0_gates import _check_single_role_hitl
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def _seed_adversarial_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            is_quarantined INTEGER
        )
    """)
    cur.execute("""
        INSERT INTO node VALUES
        ('person:amanda_knarr', 'person', 'Amanda Knarr', 'Amanda Knarr', 'Role: Solutions Consultant. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:calvin_gilmartin', 'person', 'Calvin Gilmartin', 'Calvin Gilmartin', 'Role: Procurement Director. Affiliation label(s): Apex Global.', 'active', 1)
    """)
    cur.execute("""
        INSERT INTO assertion VALUES
        ('a_amanda_adv', 'person:amanda_knarr', 'HAS_ROLE', NULL, 'Solutions Consultant', 'Amanda Knarr Solutions Consultant', 0),
        ('a_calvin_adv', 'person:calvin_gilmartin', 'HAS_ROLE', NULL, 'Procurement Director', 'Calvin Gilmartin Procurement Director', 0)
    """)
    conn.commit()
    conn.close()


# ==============================================================================
# Category 1: Exact Production Regression
# ==============================================================================

@pytest.mark.adversarial
def test_adversarial_exact_production_regression_amanda_and_calvin(tmp_path: Path, monkeypatch):
    """
    Pins both 08-04-2026 (Amanda) and 10-19-2026 (Calvin) production regressions:
    Single-token speaker mentions in raw dialogue/notes must strictly fail to auto-resolve
    via pre-populated sidecars, directory priors, or GraphDB assertions.
    """
    db_p = tmp_path / "graph.sqlite"
    _seed_adversarial_graph(db_p)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_p)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    # 1. Amanda in raw transcript with pre-populated sidecar
    raw_transcript_amanda = "Amanda: Presenting the Cloud architecture update."
    meta_amanda = {
        "meeting_name": "Google PartnerAdvisory Sync",
        "date": "08/04/2026",
        "time": "10:00 AM ET",
        "project": "project_nexus",
        "source_type": "RAW_TRANSCRIPT",
        "repo_root": str(tmp_path),
        "attendees": ["Amanda Knarr"],  # Pre-populated full name in sidecar
    }
    with pytest.raises(HITLRequiredError) as exc_amanda:
        verify_mandatory_attendee_confirmation(meta_amanda, raw_transcript_amanda)
    assert "Amanda" in exc_amanda.value.unmapped_speakers

    # 2. Calvin in meeting notes with directory prior
    raw_notes_calvin = "# EDO Notes\nCalvin provided procurement status."
    meta_calvin = {
        "meeting_name": "EDO Status Sync",
        "date": "10/19/2026",
        "time": "09:00 AM ET",
        "project": "project_spring",
        "source_type": "copilot_notes",
        "is_notes": True,
        "notes_mode": True,
        "repo_root": str(tmp_path),
        "roster": ["Calvin"],
    }
    with pytest.raises(HITLRequiredError) as exc_calvin:
        verify_mandatory_attendee_confirmation(meta_calvin, raw_notes_calvin)
    assert "Calvin" in exc_calvin.value.unmapped_speakers


# ==============================================================================
# Category 2: Invariant Bypass Attempt
# ==============================================================================

@pytest.mark.adversarial
def test_adversarial_invariant_bypass_unconfirmed_ledger_and_forged_roles(tmp_path: Path):
    """
    Adversarial injection:
    1. Injecting hitl_role_resolutions with is_human_confirmed=False must fail closed at C0.
    2. Attempting to bypass C0 with unauthenticated synthetic resolutions under strict ledger gate fails closed.
    3. Attempting to bypass C0.6 with unauthenticated sidecar resolutions fails closed.
    """
    metadata: Dict[str, Any] = {
        "meeting_name": "Security Review",
        "date": "10/22/2026",
        "time": "11:00 AM ET",
        "project": "governance",
        "repo_root": str(tmp_path),
        "attendees": ["Alice Smith"],
    }

    # Attempt 1: Forged unconfirmed role resolution with unstated role in raw dialogue
    metadata["hitl_role_resolutions"] = {
        "Alice Smith": {
            "resolved_role": "Chief Architect",
            "is_human_confirmed": False,  # Forged machine proposal
        }
    }
    with pytest.raises(HITLRequiredError) as exc_c0:
        _check_single_role_hitl("Alice Smith", "", metadata)
    assert exc_c0.value.hitl_type == "role_clarification"

    # Attempt 2: Synthetic resolution with strict ledger verification enabled fails without ledger receipt
    metadata["strict_ledger_role_gate"] = True
    metadata["hitl_role_resolutions"] = {
        "Alice Smith": "Chief Architect",
    }
    with pytest.raises(HITLRequiredError):
        _check_single_role_hitl("Alice Smith", "", metadata)

    # Attempt 3: C0.6 execution with forged unconfirmed resolutions fails closed
    metadata["attendees"] = ["Alice Smith"]
    metadata["hitl_role_resolutions"] = {
        "Alice Smith": {
            "resolved_role": "Chief Architect",
            "is_human_confirmed": False,
        }
    }
    l0_ctx = {
        "metadata": metadata,
        "transcript_text": "Alice Smith: Validating invariants.",
        "repo_root": tmp_path,
    }
    c06 = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    with pytest.raises(HITLRequiredError) as exc_c06:
        c06.execute(l0_ctx)
    assert exc_c06.value.hitl_type == "graphdb_role_verification"


# ==============================================================================
# Category 3: Boundary Payload
# ==============================================================================

@pytest.mark.adversarial
def test_adversarial_boundary_payloads(tmp_path: Path):
    """
    Tests edge boundary cases:
    - Empty turn bodies
    - Whitespace attendee names
    - Single-token speaker cues
    - Empty raw dialogue
    """
    metadata: Dict[str, Any] = {
        "meeting_name": "Boundary Test",
        "date": "10/22/2026",
        "time": "02:00 PM ET",
        "project": "boundary",
        "source_type": "RAW_TRANSCRIPT",
        "repo_root": str(tmp_path),
        "attendees": ["   ", "", "\t\n"],
    }

    # Empty turns and whitespace attendees
    raw_boundary = (
        "   \n"
        "A: \n"
        "Dan: \n"
        "Dan: Hello\n"
    )
    # Must reject or halt on unmapped single-token speaker cue 'Dan'
    with pytest.raises(HITLRequiredError) as exc_u0:
        verify_mandatory_attendee_confirmation(metadata, raw_boundary)
    assert "Dan" in exc_u0.value.unmapped_speakers

    # Empty dialogue with no attendees fails closed
    empty_meta: Dict[str, Any] = {
        "meeting_name": "Empty Test",
        "date": "10/22/2026",
        "time": "02:00 PM ET",
        "project": "boundary",
        "source_type": "RAW_TRANSCRIPT",
        "repo_root": str(tmp_path),
        "attendees": [],
    }
    with pytest.raises(HITLRequiredError):
        verify_mandatory_attendee_confirmation(empty_meta, "")


# ==============================================================================
# Category 4: Malformed Input
# ==============================================================================

@pytest.mark.adversarial
def test_adversarial_malformed_receipts_and_corrupted_digests(tmp_path: Path):
    """
    Tests tampering and malformed receipts:
    - Corrupted attendee confirmation receipt missing subject_sha256
    - Invalid receipt status
    - Non-dict receipt structures
    """
    metadata: Dict[str, Any] = {
        "meeting_name": "Tamper Test",
        "date": "10/22/2026",
        "time": "03:00 PM ET",
        "project": "tamper",
        "source_type": "RAW_TRANSCRIPT",
        "repo_root": str(tmp_path),
        "attendees": ["Bob Vance"],
    }
    raw = "Bob Vance: Reporting from sales."

    # 1. Receipt missing cryptographic subject_sha256
    metadata["attendees_confirmed_by_operator"] = True
    metadata["attendee_confirmation_receipt"] = {
        "decision_type": "ATTENDEE_CONFIRMATION",
        "status": "RESOLVED_HITL",
        # Missing subject_sha256 and subject_dict
    }
    with pytest.raises(ValueError, match="Missing cryptographic subject_sha256"):
        verify_mandatory_attendee_confirmation(metadata, raw)

    # 2. Receipt with invalid status
    metadata["attendee_confirmation_receipt"] = {
        "decision_type": "ATTENDEE_CONFIRMATION",
        "status": "FORGED_STATUS",
        "subject_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    }
    with pytest.raises(ValueError, match="valid 'attendee_confirmation_receipt'"):
        verify_mandatory_attendee_confirmation(metadata, raw)

    # 3. Non-dict receipt structure
    metadata["attendee_confirmation_receipt"] = "STRING_NOT_DICT"
    with pytest.raises(ValueError, match="valid 'attendee_confirmation_receipt'"):
        verify_mandatory_attendee_confirmation(metadata, raw)
