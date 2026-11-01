"""
tests/test_alr_cryptographic_receipt_binding.py

Wave 4: Cryptographic Ambiguity Lock Register (ALR) Receipt Binding Tests.
Verifies Gate 1.7 anti-fabrication enforcement:
- Prohibits unauthenticated '(Operator Verified)' claims without Ed25519 signatures.
- Computes hitl_receipt_bound independently, rejecting caller boolean assertions.
- Validates RFC 8785 (JCS) canonical byte signing and expiration constraints.
- Enforces persistent obligations for meetings with single-speaker collapse.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
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
def temp_ledger(tmp_path: Path) -> Path:
    """Provision an isolated ledger JSON file."""
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    envelope = {
        "schema_version": "1.0.0",
        "record_count": 0,
        "updated_at": "2026-09-25T12:00:00Z",
        "genesis_hash": "0" * 64,
        "latest_entry_hash": "0" * 64,
        "decisions": [],
    }
    ledger_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return ledger_path


class TestALRCryptographicReceiptBinding:
    """Gate 1.7 Cryptographic Verification Test Suite."""

    def test_unauthenticated_operator_claim_fails_gate_1_7(self, temp_ledger: Path):
        """Fabricated '(Operator Verified)' in ALR without a ledger entry fails closed."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(
            speakers,
            alr_resolution_basis="Multi-Tier Acoustic Protocol (Operator Verified)",
        )
        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "context_sha256": "a" * 64,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_caller_supplied_boolean_assertion_is_ignored(self, temp_ledger: Path):
        """Caller supplying hitl_receipt_bound: True is ignored; verifier alone computes status."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="(Operator Verified)")
        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "context_sha256": "b" * 64,
            "hitl_receipt_bound": True,  # Attacker assertion
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_invalid_ed25519_signature_fails_gate_1_7(self, temp_ledger: Path):
        """Forged or tampered signature in ledger entry fails verification."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="Acoustic Protocol (Operator Verified)")
        subject_hash = "c" * 64

        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_hash,
            case_ids=["SC-001"],
            private_key=priv,
            public_key=pub,
        )
        # Corrupt the signature
        entry["signature"] = "tampered_signature_bytes_which_are_not_valid"
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "subject_sha256": subject_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_subject_hash_mismatch_replay_attack_fails(self, temp_ledger: Path):
        """Receipt valid for meeting X replayed for meeting Y fails closed."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="(Operator Verified)")
        meeting_x_hash = "x" * 64
        meeting_y_hash = "y" * 64

        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256=meeting_x_hash,
            case_ids=["SC-001"],
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "subject_sha256": meeting_y_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_expired_approval_receipt_fails(self, temp_ledger: Path):
        """Approval entry with expired TTL fails verification."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="(Operator Verified)")
        subject_hash = "e" * 64

        priv, pub = generate_hitl_keypair()
        # Create an entry with negative TTL (already expired)
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_hash,
            case_ids=["SC-001"],
            private_key=priv,
            public_key=pub,
            ttl_seconds=-3600,
        )
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "subject_sha256": subject_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_valid_cryptographic_receipt_passes_gate_1_7(self, temp_ledger: Path):
        """Authentic Ed25519 signature over matching subject passes Gate 1.7."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="Acoustic Protocol (Operator Verified)")
        subject = {"meeting_id": "Alan_Q4_Scope", "audio_sha256": "1234abcd" * 8}
        subject_hash = compute_alr_subject_digest(subject)

        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_hash,
            case_ids=["SC-001"],
            operator_id="amit.ayer@consulting.example.com",
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "subject": subject,
            "subject_sha256": subject_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert passed, f"Gate 1.7 unexpectedly failed: {failures}"
        assert details["hitl_receipt_bound"] is True
        assert details["crypto_verification"]["verification_type"] == "ed25519_jcs"
        assert details["crypto_verification"]["operator_id"] == "amit.ayer@consulting.example.com"

    def test_collapse_obligation_without_signed_receipt_fails(self, temp_ledger: Path):
        """Active collapse obligation without signed receipt fails even if text omits claim."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        # Notice: ALR omits '(Operator Verified)'
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="Diarization Partition")
        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "single_speaker_collapse": True,
            "collapse_obligation": True,
            "subject_sha256": "col_1" * 16,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert not passed
        assert any("UNRESOLVED_COLLAPSE_OBLIGATION" in f for f in failures)

    def test_collapse_obligation_with_signed_receipt_passes(self, temp_ledger: Path):
        """Active collapse obligation with authentic signed receipt passes Gate 1.7."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="Partitioned via Diarization")
        sub_hash = "col_2" * 16

        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256=sub_hash,
            case_ids=["SC-001"],
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "single_speaker_collapse": True,
            "collapse_obligation": True,
            "subject_sha256": sub_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta, ledger_path=temp_ledger)

        assert passed, f"Expected pass with valid signed collapse receipt, got failures: {failures}"
        assert details["hitl_receipt_bound"] is True

    def test_standard_meeting_without_claims_passes_normally(self):
        """Regular meeting without operator claims or collapse requires no cryptographic receipt."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="Standard Acoustic Diarization")
        meta = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)

        assert passed, f"Standard meeting failed: {failures}"
        assert details["crypto_verification"]["verification_type"] == "none_required"

    def test_full_gate1_pipeline_integration(self, temp_ledger: Path):
        """Full Gate 1 pipeline fails on unauthenticated claim and passes with valid signature."""
        speakers = ["Amit Ayer", "Hayden Barnes"]
        f1_text = _make_transcript_with_alr(speakers, alr_resolution_basis="(Operator Verified)")
        source_text = "Sample source dialogue for Gate 1 hash."
        sub_hash = "gate1_int" * 6 + "abcd"

        # 1. Without receipt -> Gate 1 fails
        meta_unauth = {
            "roster": [{"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"}, {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"}],
            "subject_sha256": sub_hash,
            "hitl_ledger_path": str(temp_ledger),
        }
        res_fail = run_qa_gate_1(f1_text, source_text=source_text, metadata=meta_unauth)
        assert res_fail["passed"] is False
        assert res_fail["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["passed"] is False
        assert res_fail["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["hitl_receipt_bound"] is False

        # 2. Add valid signature -> Gate 1.7 passes
        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256=sub_hash,
            case_ids=["SC-001"],
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(entry, ledger_path=temp_ledger)

        res_pass = run_qa_gate_1(f1_text, source_text=source_text, metadata=meta_unauth)
        assert res_pass["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["passed"] is True
        assert res_pass["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["hitl_receipt_bound"] is True
