"""
tests/transcript_qa/test_gate1_alr_null_subject.py

Wave 1: Regression and security tests for Gate 1.7 null-check bypass remediation.
Verifies that:
1. Operator-verified claims or collapse obligations with null subject fail closed with UNBINDABLE_SUBJECT.
2. Unrelated receipts in the ledger cannot be hijacked by omitting subject_sha256 from metadata.
3. Legitimate subject bindings continue to verify cleanly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
    verify_alr_cryptographic_receipt,
)


def _make_transcript_with_alr(
    speakers: list[str],
    alr_resolution_basis: str = "Multi-Tier Acoustic Protocol (Operator Verified)",
    num_turns: int = 12,
) -> str:
    """Generate a valid transcript structure with customized ALR resolution basis."""
    header = (
        "# Executive Session -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/25/2026\n"
        "**Participants:** " + ", ".join(f"{s} (PartnerAdvisory)" for s in speakers) + "\n\n---\n\n"
    )
    turns = ""
    for i in range(num_turns):
        spk = speakers[i % len(speakers)]
        turns += f"### [00:{i:02d}:00]\n\n**{spk}:** Discussion turn {i} concerning architecture.\n\n"

    alr = (
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
    )
    for j, spk in enumerate(speakers):
        alr += f"| {j+1} | Speaker Attribution: {spk} | [00:00:00 - Session End] | stmt-{j} | Attributed to {spk} | {alr_resolution_basis} |\n"
    alr += "| 3 | Technical Lexicon | [00:01:00] | stmt-lex | Calibrated term | Lexicon Match |\n"

    summary = (
        "\n## Attribution Summary\n\n"
        "| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |\n"
        "|---|---|---|---|---|---|\n"
    )
    for spk in speakers:
        summary += f"| {spk} | PartnerAdvisory | Architect | 6 | HIGH | Voice signature |\n"

    return header + turns + alr + summary


@pytest.fixture
def temp_ledger_with_unrelated_receipt(tmp_path: Path) -> Path:
    """Provision an isolated ledger with a valid Ed25519 receipt from an unrelated meeting."""
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    envelope = {
        "schema_version": "1.0.0",
        "record_count": 0,
        "updated_at": "2026-09-28T15:23:01Z",
        "genesis_hash": "0" * 64,
        "latest_entry_hash": "0" * 64,
        "decisions": [],
    }
    ledger_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

    priv, pub = generate_hitl_keypair()
    unrelated_subject = "7e2cfda0f47ba61d35cac1b5caab6dcf75ca2ac24187da0e975d7836336f805b"
    entry = create_signed_hitl_approval_entry(
        subject_sha256=unrelated_subject,
        case_ids=["speaker_attribution_0105_traci_metzger"],
        decision="APPROVE",
        operator_id="amit_ayer",
        private_key=priv,
        public_key=pub,
        metadata={"meeting_name": "Jarvis_Nexus_Q1_Charter_Review", "cluster": "Taylor Mercer"},
    )
    append_signed_approval_to_ledger(entry, ledger_path=ledger_path)
    return ledger_path


class TestGate1ALRNullSubjectSecurity:
    """Wave 1 Security Suite: Fail-Closed Protection Against Null-Subject Receipt Hijacking."""

    def test_null_subject_with_operator_claim_fails_closed_with_unbindable_subject(
        self, temp_ledger_with_unrelated_receipt: Path
    ):
        """When ALR claims (Operator Verified) but metadata lacks subject_sha256, verification MUST fail closed.

        This tests the exact vulnerability from the RCA where an attacker or agent omitted
        subject_sha256, allowing verify_alr_cryptographic_receipt to blindly accept the
        unrelated Taylor Mercer receipt from Jarvis_Nexus_Q1_Charter_Review.
        """
        speakers = ["Alex Rivera", "Marcus Vance"]
        f1_text = _make_transcript_with_alr(
            speakers,
            alr_resolution_basis="Operator Verified via Ground Truth Confirmation (Confidence: 100%)",
        )
        # Metadata has NO subject_sha256, NO context_sha256, NO subject dict
        meta_without_subject = {
            "project": "nea",
            "meeting_date": "09-28-2026",
            "meeting_name": "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            "duration_minutes": 50,
            "hitl_ledger_path": str(temp_ledger_with_unrelated_receipt),
        }

        # 1. Test verify_alr_cryptographic_receipt directly
        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta_without_subject, ledger_path=temp_ledger_with_unrelated_receipt
        )
        assert passed is False, "Verification must not pass when subject is null"
        assert details["hitl_receipt_bound"] is False
        assert details["verification_type"] == "failed"
        assert any("UNBINDABLE_SUBJECT" in f for f in failures), f"Expected UNBINDABLE_SUBJECT, got {failures}"
        assert "verified_entry_id" not in details

        # 2. Test check_ambiguity_lock_register_and_speaker_hitl wrapper
        gate_passed, gate_details, gate_failures = check_ambiguity_lock_register_and_speaker_hitl(
            f1_text, meta_without_subject, ledger_path=temp_ledger_with_unrelated_receipt
        )
        assert gate_passed is False
        assert gate_details["hitl_receipt_bound"] is False
        assert any("UNBINDABLE_SUBJECT" in f for f in gate_failures)

    def test_null_subject_with_collapse_obligation_fails_closed(
        self, temp_ledger_with_unrelated_receipt: Path
    ):
        """When lineage carries collapse obligation but no subject_sha256, fail closed."""
        speakers = ["Alex Rivera", "Marcus Vance"]
        f1_text = _make_transcript_with_alr(
            speakers,
            alr_resolution_basis="Acoustic Separation Protocol",  # No explicit operator claim in text
        )
        meta_with_collapse_no_subject = {
            "project": "nea",
            "single_speaker_collapse": True,
            "hitl_ledger_path": str(temp_ledger_with_unrelated_receipt),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta_with_collapse_no_subject, ledger_path=temp_ledger_with_unrelated_receipt
        )
        assert passed is False
        assert details["hitl_receipt_bound"] is False
        assert any("UNBINDABLE_SUBJECT" in f for f in failures)

    def test_matching_subject_successfully_verifies_receipt(
        self, temp_ledger_with_unrelated_receipt: Path
    ):
        """When subject_sha256 matches the receipt, verification passes."""
        speakers = ["Taylor Mercer", "Amit Ayer"]
        f1_text = _make_transcript_with_alr(
            speakers,
            alr_resolution_basis="Operator Verified (Confidence: 100%)",
        )
        matching_subject = "7e2cfda0f47ba61d35cac1b5caab6dcf75ca2ac24187da0e975d7836336f805b"
        meta_matching = {
            "roster": [
                {"name": "Taylor Mercer", "org": "PartnerAdvisory", "role": "Director"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"},
            ],
            "subject_sha256": matching_subject,
            "hitl_ledger_path": str(temp_ledger_with_unrelated_receipt),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta_matching, ledger_path=temp_ledger_with_unrelated_receipt
        )
        assert passed is True
        assert details["hitl_receipt_bound"] is True
        assert details["verification_type"] == "ed25519_jcs"
        assert details["verified_entry_id"] is not None
        assert failures == []

    def test_non_matching_subject_fails_as_unauthenticated_claim(
        self, temp_ledger_with_unrelated_receipt: Path
    ):
        """When subject_sha256 is present but differs from ledger entries, fail with UNAUTHENTICATED."""
        speakers = ["Alex Rivera", "Marcus Vance"]
        f1_text = _make_transcript_with_alr(
            speakers,
            alr_resolution_basis="Operator Verified (Confidence: 100%)",
        )
        different_subject = "f" * 64
        meta_diff = {
            "roster": [
                {"name": "Alex Rivera", "org": "NEA", "role": "Lead"},
                {"name": "Marcus Vance", "org": "PartnerAdvisory", "role": "Principal"},
            ],
            "subject_sha256": different_subject,
            "hitl_ledger_path": str(temp_ledger_with_unrelated_receipt),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta_diff, ledger_path=temp_ledger_with_unrelated_receipt
        )
        assert passed is False
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)
