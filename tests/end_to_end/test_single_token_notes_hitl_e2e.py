"""
tests/end_to_end/test_single_token_notes_hitl_e2e.py

End-to-end lifecycle test for unrecognized single-token attendee HITL enforcement
across meeting notes and Stage C0 gates.
Reproduces the exact EDO 10-19-2026 notes scenario (Manasa and Miriam) from gate halt
through operator confirmation and signed ledger receipt verification.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    C0RosterContextGate,
    _check_single_role_hitl,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


@pytest.mark.e2e
def test_single_token_notes_hitl_lifecycle_e2e(tmp_path: Path):
    """
    E2E Lifecycle:
    1. Unconfirmed meeting notes with unrecognized single-token attendees halt at U0/attendee gate.
    2. Inferred non-placeholder roles fail to bypass C0 role gate without authentic ledger backing.
    3. Operator provides authentic confirmation and resolutions.
    4. Pipeline gates clear with cryptographic and ledger provenance.
    """
    metadata: Dict[str, Any] = {
        "meeting_name": "EDO Work Sync",
        "date": "10/19/2026",
        "time": "09:00 AM - 09:30 AM ET",
        "source_type": "copilot_notes",
        "is_notes": True,
        "notes_mode": True,
        "project": "project_spring",
        "repo_root": str(tmp_path),
        "roster": ["Calvin", "Manasa", "Miriam"],
    }
    raw_notes = (
        "# EDO Work Sync Notes\n"
        "Calvin gave procurement updates.\n"
        "Manasa discussed Project Spring deliverables.\n"
        "Miriam discussed Data Engineering pipelines.\n"
    )

    # Step 1: Attendee gate must halt on unconfirmed single-token attendees
    with pytest.raises(HITLRequiredError) as exc_att:
        verify_mandatory_attendee_confirmation(metadata, raw_notes)
    err_att = exc_att.value
    assert err_att.hitl_type == "attendee_confirmation"
    assert "Manasa" in err_att.unmapped_speakers
    assert "Miriam" in err_att.unmapped_speakers
    assert "Calvin" in err_att.unmapped_speakers

    # Step 2: U0 roster completeness must halt on unverified single-token roles
    with pytest.raises(HITLRequiredError) as exc_u0:
        U0IngestionBoundary._inspect_roster_completeness(metadata)
    assert exc_u0.value.hitl_type in ("title_clarification", "preflight_cascade")

    # Step 3: Attempting to pass inferred non-placeholder roles without ledger backing must fail closed
    inferred_roles = {
        "Manasa": "Project Spring Lead",
        "Miriam": "Data Engineering Consultant",
    }
    metadata["hitl_role_resolutions"] = dict(inferred_roles)
    with pytest.raises(HITLRequiredError) as exc_c0:
        _check_single_role_hitl("Manasa", "Project Spring Lead", metadata)
    assert exc_c0.value.hitl_type == "role_clarification"

    # Step 4: Operator confirms attendee roster
    receipt = record_attendee_confirmation(
        metadata,
        ["Calvin Gilmartin", "Manasa", "Miriam"],
        rationale="Operator verified meeting participants for EDO Work Sync",
        repo_root=tmp_path,
    )
    assert receipt["status"] == "RESOLVED_HITL"
    assert metadata["attendees_confirmed_by_operator"] is True
    # Attendee gate now clears cleanly
    verify_mandatory_attendee_confirmation(metadata, raw_notes)

    # Step 5: Operator records authentic role resolutions to ledger
    sqlite_p = tmp_path / "hitl_decision_ledger.sqlite"
    json_p = tmp_path / "hitl_decision_ledger.json"

    res_manasa = create_speaker_role_resolution(
        person_name="Manasa",
        resolved_role="Project Spring Lead",
        organization="Client / Partner",
        is_human_confirmed=True,
        operator_origin={"source": "human_operator", "text": "Operator verified title for Manasa"},
        repo_root=tmp_path,
        sqlite_path=sqlite_p,
        json_path=json_p,
    )
    res_miriam = create_speaker_role_resolution(
        person_name="Miriam",
        resolved_role="Data Engineering Consultant",
        organization="Client / Partner",
        is_human_confirmed=True,
        operator_origin={"source": "human_operator", "text": "Operator verified title for Miriam"},
        repo_root=tmp_path,
        sqlite_path=sqlite_p,
        json_path=json_p,
    )

    metadata["hitl_role_receipts"] = {
        "Manasa": res_manasa.to_dict(),
        "Miriam": res_miriam.to_dict(),
    }
    metadata["hitl_role_resolutions"] = {
        "Manasa": "Project Spring Lead",
        "Miriam": "Data Engineering Consultant",
    }
    metadata["attendee_roles_confirmed_by_operator"] = True

    # Step 6: Verify C0 role gate and context envelope pass cleanly
    _check_single_role_hitl("Manasa", "Project Spring Lead", metadata)
    _check_single_role_hitl("Miriam", "Data Engineering Consultant", metadata)

    l0_ctx = {"metadata": metadata, "transcript_text": raw_notes}
    out_ctx = C0RosterContextGate().execute(l0_ctx)
    assert out_ctx["conformance_checked"] is True
    assert out_ctx["roster_gate_cleared"] is True
