from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    find_signed_approvals,
    verify_hitl_approval_signature,
)
from dual_pipeline.speaker_disambiguation import (
    resolve_shared_room_microphones,
)
from dual_pipeline.transcript_qa_core.gate1_alr import (
    _resolve_expected_subject_digest,
    verify_alr_cryptographic_receipt,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.silence_gap_classifier import (
    classify_silence_gaps,
)


def test_record_attendee_confirmation_ed25519_signature(tmp_path: Path):
    """Verify record_attendee_confirmation computes subject digest and appends signed Ed25519 approval."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text(json.dumps({"decisions": [], "priors": {}}), encoding="utf-8")

    metadata: Dict[str, Any] = {
        "meeting_name": "Test_Project_Review",
        "date": "09/28/2026",
        "project": "project_nexus",
    }
    attendees = ["Jill Cizewski", "Daniel Brankin", "Sharon Diaram"]

    receipt = record_attendee_confirmation(
        metadata,
        attendees,
        rationale="Operator verified roster via HITL",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "RESOLVED_HITL"
    assert metadata["attendees_confirmed_by_operator"] is True
    assert "subject_sha256" in metadata
    assert "context_sha256" in metadata
    assert metadata["subject_sha256"] == metadata["context_sha256"]

    # Verify subject dict structure
    subj = metadata["subject"]
    assert subj["meeting_name"] == "Test_Project_Review"
    assert subj["meeting_date"] == "09/28/2026"
    assert subj["project"] == "project_nexus"
    assert subj["attendees"] == sorted(attendees)

    # Verify signed approval exists in ledger
    approvals = find_signed_approvals(metadata["subject_sha256"], ledger_path=ledger_file)
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["payload"]["decision"] == "APPROVE"
    assert approval["payload"]["subject_sha256"] == metadata["subject_sha256"]

    # Verify signature
    is_valid = verify_hitl_approval_signature(
        approval["payload"],
        approval["signature"],
        approval["public_key_pem"],
    )
    assert is_valid is True

    # Verify Merkle integrity of the ledger
    from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
    store = HITLDecisionStore(ledger_path=ledger_file, use_sqlite=False)
    records = store._load_records(verify_integrity=True)
    assert len(records) >= 1



def test_gate1_alr_passes_with_signed_attendee_confirmation(tmp_path: Path):
    """Verify verify_alr_cryptographic_receipt succeeds when attendee confirmation was signed."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text(json.dumps({"decisions": [], "priors": {}}), encoding="utf-8")

    metadata: Dict[str, Any] = {
        "meeting_name": "Followup_Business_Case_Jill_PartnerAdvisory",
        "meeting_slug": "Followup_Business_Case_Jill_PartnerAdvisory",
        "date": "09/28/2026",
        "project": "project_nexus",
        "hitl_ledger_path": str(ledger_file),
    }
    attendees = [
        "Jill Cizewski",
        "Daniel Brankin",
        "Sharon Diaram",
        "Taylor Mercer",
        "Amit Ayer",
    ]

    record_attendee_confirmation(
        metadata,
        attendees,
        rationale="Confirmed via HITL",
        repo_root=tmp_path,
    )

    f1_text = """# Followup_Business_Case_Jill_PartnerAdvisory -- HITL-Resolved Transcript

## Forensic Transcript

**Unresolved In-Room Speaker:** There's a lot of special cases.

## Ambiguity Lock Register

| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Participant attribution | [00:00:00] | stmt-001 | Unresolved | Operator-confirmed attendee roster, receipt timestamp 2026-09-29T11:23:29 |
"""

    passed, details, failures = verify_alr_cryptographic_receipt(
        f1_text, metadata, ledger_path=ledger_file
    )
    assert passed is True
    assert details["hitl_receipt_bound"] is True
    assert details["verification_type"] == "ed25519_jcs"
    assert failures == []


def test_resolve_expected_subject_digest_defensive_derivation():
    """Verify _resolve_expected_subject_digest derives canonical subject when not pre-populated in metadata."""
    meta: Dict[str, Any] = {
        "meeting_name": "Strategic_Alignment",
        "date": "09/28/2026",
        "project": "project_nexus",
    }
    roster_names = ["Bob Smith", "Alice Jones"]

    digest = _resolve_expected_subject_digest(meta, roster_names)
    assert digest is not None
    assert meta["subject_sha256"] == digest
    assert meta["context_sha256"] == digest
    assert meta["subject"]["attendees"] == ["Alice Jones", "Bob Smith"]

    # Re-computing matches
    expected = compute_alr_subject_digest({
        "meeting_name": "Strategic_Alignment",
        "meeting_date": "09/28/2026",
        "project": "project_nexus",
        "attendees": ["Alice Jones", "Bob Smith"],
    })
    assert digest == expected


def test_gate1_alr_fails_closed_on_unauthenticated_operator_claim(tmp_path: Path):
    """Verify Gate 1.7 rejects claims of Operator Verified when no signed ledger receipt exists."""
    ledger_file = tmp_path / "empty_ledger.json"
    ledger_file.write_text(json.dumps({"decisions": [], "priors": {}}), encoding="utf-8")

    meta: Dict[str, Any] = {
        "meeting_name": "Unauthenticated_Meeting",
        "date": "09/28/2026",
        "project": "project_nexus",
        "subject_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "hitl_ledger_path": str(ledger_file),
    }

    f1_text = """# Unauthenticated Meeting

## Ambiguity Lock Register

| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker attribution | [00:00:00] | stmt-001 | Alice | (Operator Verified) without valid signed signature |
"""

    passed, details, failures = verify_alr_cryptographic_receipt(
        f1_text, meta, ledger_path=ledger_file
    )
    assert passed is False
    assert details["hitl_receipt_bound"] is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)


def test_speaker_disambiguation_avoids_synthetic_operator_claim():
    """Verify resolve_shared_room_microphones uses Autonomous Context basis when interactive_resolver is None."""
    metadata = {
        "shared_room_mics": [{"room_name": "Room A", "in_room_attendees": [{"name": "Alice"}, {"name": "Bob"}]}],
    }
    speaker_map = {"Room A": "Room A"}
    transcript = "**Room A:** [00:00:05] Let's look at the report."

    resolved, out_meta = resolve_shared_room_microphones(transcript, metadata, speaker_map)
    hitl_res = out_meta.get("hitl_resolutions", {})
    for res in hitl_res.values():
        basis = res.get("basis", "")
        # Should NOT claim Operator Confirmed without interactive operator
        assert "Operator Confirmed" not in basis
        assert "Autonomous Contextual Resolution" in basis


def test_silence_gap_classifier_avoids_synthetic_operator_claim():
    """Verify classify_transcript_silence_gaps uses Autonomous basis when interactive_resolver is None."""
    transcript = """**Alice:** [00:00:00] Start.
[Unannotated gap: 00:01:00 – 00:05:00]
**Alice:** [00:05:00] End.
"""
    metadata: Dict[str, Any] = {}
    resolved, out_meta = classify_silence_gaps(transcript, metadata)
    hitl_res = out_meta.get("hitl_resolutions", {})
    for res in hitl_res.values():
        basis = res.get("basis", "")
        assert "Operator Confirmed" not in basis
        assert "Autonomous Silence Classification" in basis


def test_record_attendee_confirmation_preserves_signature_on_nonempty_ledger(tmp_path: Path):
    """Verify digital signature and Merkle chain are preserved when appending to a non-empty ledger."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
    store = HITLDecisionStore(ledger_path=ledger_file, use_sqlite=False)

    # Seed ledger with pre-existing records
    store.record_decision(
        decision_id="dec-prior-001",
        category="speaker_attribution_hitl",
        prompt="Verify speaker A",
        options=["A", "B"],
        selected_option="A",
    )
    store.record_decision(
        decision_id="dec-prior-002",
        category="speaker_attribution_hitl",
        prompt="Verify speaker B",
        options=["B", "C"],
        selected_option="B",
    )
    prior_records = store._load_records(verify_integrity=True)
    assert len(prior_records) == 2

    metadata: Dict[str, Any] = {
        "meeting_name": "Followup_Business_Case_Jill_PartnerAdvisory",
        "meeting_slug": "Followup_Business_Case_Jill_PartnerAdvisory",
        "date": "09/28/2026",
        "project": "project_nexus",
        "hitl_ledger_path": str(ledger_file),
    }
    attendees = ["Jill Cizewski", "Daniel Brankin", "Sharon Diaram"]
    receipt = record_attendee_confirmation(
        metadata,
        attendees,
        rationale="Operator verified roster via HITL",
        repo_root=tmp_path,
    )
    assert receipt["status"] == "RESOLVED_HITL"
    assert metadata["hitl_ledger_path"] == str(ledger_file)

    # Verify signature remains valid
    approvals = find_signed_approvals(metadata["subject_sha256"], ledger_path=ledger_file)
    assert len(approvals) == 1
    approval = approvals[0]
    is_valid = verify_hitl_approval_signature(
        approval["payload"],
        approval["signature"],
        approval["public_key_pem"],
    )
    assert is_valid is True

    # Verify entire Merkle chain remains 100% valid
    all_recs = store._load_records(verify_integrity=True)
    assert len(all_recs) == 3

    # Verify Gate 1.7 passes cleanly
    f1_text = """# Followup_Business_Case_Jill_PartnerAdvisory -- HITL-Resolved Transcript

## Ambiguity Lock Register

| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Participant attribution | [00:00:00] | stmt-001 | Unresolved | Operator-confirmed attendee roster, receipt timestamp 2026-09-29T11:23:29 |
"""
    passed, details, failures = verify_alr_cryptographic_receipt(f1_text, metadata)
    assert passed is True
    assert details["hitl_receipt_bound"] is True
    assert failures == []

