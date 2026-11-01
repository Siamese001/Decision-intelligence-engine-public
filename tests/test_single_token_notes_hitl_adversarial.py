"""
tests/test_single_token_notes_hitl_adversarial.py

Adversarial and edge-case testing for single-token attendee HITL enforcement:
- Category 1: Boundary payload (empty strings, whitespace-only names, single character tokens)
- Category 2: Malformed input (corrupt metadata types, non-dict receipts, integer/list values)
- Category 3: Invariant bypass attempt (forged hitl_role_resolutions, fake confirmation flags)
- Category 4: Exact production regression (Manasa and Miriam in copilot_notes with pre-populated roles)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_stages_core.attendee_gate import (
    find_unrecognized_single_token_attendees,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c0_gates import _check_single_role_hitl
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


# --- CATEGORY 1: Boundary payload ---
def test_boundary_empty_and_whitespace_attendees():
    """Boundary: Empty and whitespace attendee tokens should be safely filtered without exceptions."""
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "roster": ["", "   ", " \t\n ", "Calvin Gilmartin"],
    }
    unrec = find_unrecognized_single_token_attendees(meta)
    assert unrec == []


def test_boundary_single_character_tokens():
    """Boundary: Single-character tokens (e.g. initials or typos) must be caught if unrecognized."""
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "roster": ["X", "Z"],
    }
    unrec = find_unrecognized_single_token_attendees(meta)
    assert "X" in unrec
    assert "Z" in unrec


# --- CATEGORY 2: Malformed input ---
def test_malformed_metadata_types():
    """Malformed: Non-dict hitl_role_receipts or non-string roles must fail closed."""
    meta: Dict[str, Any] = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "roster": ["Manasa"],
        "hitl_role_resolutions": {"Manasa": 12345},  # malformed non-string
        "hitl_role_receipts": "not-a-dict",  # malformed receipt structure
    }
    with pytest.raises(HITLRequiredError):
        _check_single_role_hitl("Manasa", "12345", meta)


def test_malformed_roster_structures():
    """Malformed: Malformed nested roster dict with non-string elements must not crash."""
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "roster": {"PartnerAdvisory": [None, 123, {"unexpected": "payload"}]},
    }
    unrec = find_unrecognized_single_token_attendees(meta)
    assert isinstance(unrec, list)


# --- CATEGORY 3: Invariant bypass attempt ---
def test_invariant_bypass_forged_hitl_role_resolutions(tmp_path: Path):
    """Invariant bypass attempt: Injected hitl_role_resolutions for single-token name without ledger backing fails closed."""
    meta: Dict[str, Any] = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "meeting_name": "EDO Work Sync",
        "repo_root": str(tmp_path),
        "roster": ["Manasa"],
        "hitl_role_resolutions": {
            "Manasa": "Project Spring Lead",
        },
    }
    # Must reject forged resolution and raise HITLRequiredError
    with pytest.raises(HITLRequiredError) as exc_info:
        _check_single_role_hitl("Manasa", "Project Spring Lead", meta)
    assert exc_info.value.hitl_type == "role_clarification"


def test_invariant_bypass_unauthenticated_attendee_confirmation():
    """Invariant bypass attempt: attendees_confirmed_by_operator=True without valid signed receipt fails closed."""
    meta = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "meeting_name": "EDO Work Sync",
        "roster": ["Manasa"],
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "FORGED",
            "subject_sha256": "",
        },
    }
    with pytest.raises(ValueError) as exc_info:
        verify_mandatory_attendee_confirmation(meta)
    assert "Attendee Gate Security Violation" in str(exc_info.value)


# --- CATEGORY 4: Exact production regression ---
def test_production_regression_edo_notes_manasa_and_miriam(tmp_path: Path):
    """
    Exact production regression: EDO 10-19-2026 notes with Manasa and Miriam
    having pre-populated roles ('Project Spring Lead' and 'Data Engineering Consultant')
    must halt at both U0 and C0 until authentic operator confirmation is supplied.
    """
    meta: Dict[str, Any] = {
        "source_type": "copilot_notes",
        "is_notes": True,
        "notes_mode": True,
        "meeting_name": "EDO Work Sync",
        "project": "project_spring",
        "repo_root": str(tmp_path),
        "roster": [
            "Venkat (Lead)",
            "Lan (Senior Delivery Principal)",
            "Calvin (Procurement Lead)",
            "Manasa (Project Spring Lead)",
            "Miriam (Data Engineering Consultant)",
        ],
        "hitl_role_resolutions": {
            "Manasa": "Project Spring Lead",
            "Miriam": "Data Engineering Consultant",
        },
    }

    # 1. Attendee confirmation gate must block unconfirmed Manasa and Miriam
    with pytest.raises(HITLRequiredError) as exc_att:
        verify_mandatory_attendee_confirmation(meta)
    assert exc_att.value.hitl_type == "attendee_confirmation"

    # 2. Stage U0 roster inspection must block unverified single-token roles
    with pytest.raises(HITLRequiredError) as exc_u0:
        U0IngestionBoundary._inspect_roster_completeness(meta)
    assert exc_u0.value.hitl_type in ("title_clarification", "preflight_cascade")

    # 3. Stage C0 role gate must reject pre-populated non-placeholder roles for Manasa and Miriam
    with pytest.raises(HITLRequiredError) as exc_c0_manasa:
        _check_single_role_hitl("Manasa", "Project Spring Lead", meta)
    assert exc_c0_manasa.value.hitl_type == "role_clarification"

    with pytest.raises(HITLRequiredError) as exc_c0_miriam:
        _check_single_role_hitl("Miriam", "Data Engineering Consultant", meta)
    assert exc_c0_miriam.value.hitl_type == "role_clarification"
