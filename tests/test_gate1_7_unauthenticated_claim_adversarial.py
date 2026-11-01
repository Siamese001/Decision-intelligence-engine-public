"""
tests/test_gate1_7_unauthenticated_claim_adversarial.py

Adversarial and edge-case verification for Gate 1.7 Ed25519 ALR signature binding.
Enforces Rule 8 of HITL Decision Governance:
- Boundary payload: empty ALR rows, 0-byte transcript, single turn, no claims.
- Malformed input: forged signature, tampered payload, mismatched subject_sha256, unbindable subject.
- Invariant bypass attempt: forged '(Operator Verified)' claims without authentic ledger signatures
  fail closed; caller assertion of hitl_receipt_bound=True is strictly discarded; ForensicHealer
  does NOT silently scrub or mask unauthenticated claims.
- Exact production regression: draft with unauthenticated claim fails Gate 1.7 with
  UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.transcript_qa_core import forensic_healer
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
    verify_alr_cryptographic_receipt,
)


def _setup_test_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "hitl_decision_ledger.json"
    ledger.write_text(json.dumps({"decisions": [], "priors": {}}), encoding="utf-8")
    return ledger


# ---------------------------------------------------------------------------
# 1. Boundary Payloads
# ---------------------------------------------------------------------------

def test_boundary_empty_transcript_and_no_claims(tmp_path: Path) -> None:
    """Boundary payload: empty text or no ALR claims with no collapse obligation passes without signature."""
    ledger = _setup_test_ledger(tmp_path)
    meta: Dict[str, Any] = {"meeting_name": "Test_Meeting", "hitl_ledger_path": str(ledger)}
    
    # Empty text
    passed, details, failures = verify_alr_cryptographic_receipt("", meta, ledger_path=ledger)
    assert passed is True
    assert details["verification_type"] == "none_required"
    assert failures == []

    # Valid transcript without operator claims
    doc_no_claims = (
        "# Forensic Transcript\n\n"
        "**Date:** 09/30/2026\n\n"
        "### [00:01:00]\n\n"
        "**Alice Martin:** Hello world.\n\n"
        "### [00:01:05]\n\n"
        "**Bob Vance:** Hi Alice.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution: Alice Martin | [00:00:00 – Session End] | stmt-01 | Alice Martin | Canonical Grounding |\n"
        "| 2 | Speaker Attribution: Bob Vance | [00:00:00 – Session End] | stmt-02 | Bob Vance | Canonical Grounding |\n"
    )
    meta["attendees"] = ["Alice Martin", "Bob Vance"]
    passed_g17, _, failures_g17 = check_ambiguity_lock_register_and_speaker_hitl(
        doc_no_claims, meta, ledger_path=ledger
    )
    assert passed_g17 is True
    assert failures_g17 == []


# ---------------------------------------------------------------------------
# 2. Malformed Inputs
# ---------------------------------------------------------------------------

def test_malformed_tampered_signature_fails_closed(tmp_path: Path) -> None:
    """Malformed input: tampered Ed25519 signature fails verification."""
    ledger = _setup_test_ledger(tmp_path)
    priv_key, pub_key = generate_hitl_keypair()
    subject_dict = {
        "meeting_name": "Tamper_Test",
        "meeting_date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin", "Bob Vance"],
    }
    subject_sha256 = compute_alr_subject_digest(subject_dict)
    entry = create_signed_hitl_approval_entry(
        case_ids=["SPK-001"],
        subject_sha256=subject_sha256,
        operator_id="operator_alice",
        decision="APPROVE",
        private_key=priv_key,
        public_key=pub_key,
        meeting_slug="tamper_test",
        meeting_date="09/30/2026",
    )
    # Tamper signature bytes
    sig_bytes = bytearray(entry["signature"].encode("utf-8"))
    sig_bytes[5] = ord("X") if sig_bytes[5] != ord("X") else ord("Y")
    entry["signature"] = sig_bytes.decode("utf-8")
    append_signed_approval_to_ledger(entry, ledger_path=ledger)

    meta = {
        "meeting_name": "Tamper_Test",
        "meeting_slug": "tamper_test",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin", "Bob Vance"],
        "subject_sha256": subject_sha256,
        "hitl_ledger_path": str(ledger),
    }
    doc = (
        "# Forensic Transcript\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution | [00:00:00] | stmt-01 | Alice | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |\n"
    )
    passed, details, failures = verify_alr_cryptographic_receipt(doc, meta, ledger_path=ledger)
    assert passed is False
    assert details["hitl_receipt_bound"] is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)


def test_malformed_mismatched_subject_fails_closed(tmp_path: Path) -> None:
    """Malformed input: signature signed for meeting A cannot qualify meeting B."""
    ledger = _setup_test_ledger(tmp_path)
    priv_key, pub_key = generate_hitl_keypair()
    subject_a = compute_alr_subject_digest({
        "meeting_name": "Meeting_A",
        "meeting_date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin"],
    })
    entry = create_signed_hitl_approval_entry(
        case_ids=["SPK-001"],
        subject_sha256=subject_a,
        operator_id="operator_alice",
        decision="APPROVE",
        private_key=priv_key,
        public_key=pub_key,
        meeting_slug="meeting_a",
        meeting_date="09/30/2026",
    )
    append_signed_approval_to_ledger(entry, ledger_path=ledger)

    # Meeting B metadata
    subject_b = compute_alr_subject_digest({
        "meeting_name": "Meeting_B",
        "meeting_date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin"],
    })
    meta_b = {
        "meeting_name": "Meeting_B",
        "meeting_slug": "meeting_b",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin"],
        "subject_sha256": subject_b,
        "hitl_ledger_path": str(ledger),
    }
    doc = (
        "# Forensic Transcript\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution | [00:00:00] | stmt-01 | Alice | Protocol (Operator Verified) |\n"
    )
    passed, _, failures = verify_alr_cryptographic_receipt(doc, meta_b, ledger_path=ledger)
    assert passed is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)


# ---------------------------------------------------------------------------
# 3. Invariant Bypass Attempts & Anti-Scrubbing Proof
# ---------------------------------------------------------------------------

def test_invariant_bypass_caller_asserted_receipt_bound_is_discarded(tmp_path: Path) -> None:
    """Security Invariant: caller asserting hitl_receipt_bound=True without valid ledger signature fails closed."""
    ledger = _setup_test_ledger(tmp_path)
    meta = {
        "meeting_name": "Bypass_Attempt",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin", "Bob Vance"],
        "hitl_receipt_bound": True,  # Attacker injects True
        "hitl_ledger_path": str(ledger),
    }
    doc = (
        "# Forensic Transcript\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution | [00:00:00] | stmt-01 | Alice | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |\n"
    )
    passed, details, failures = verify_alr_cryptographic_receipt(doc, meta, ledger_path=ledger)
    assert passed is False
    assert details["hitl_receipt_bound"] is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)


def test_invariant_forensic_healer_does_not_mask_unauthenticated_claims() -> None:
    """Forensic Purity Invariant: ForensicHealer must NOT silently scrub '(Operator Verified)' to bypass Gate 1.7."""
    doc = (
        "# Forensic Transcript\n\n"
        "**Date:** 09/30/2026\n\n"
        "### [00:01:00]\n\n"
        "**Alice Martin:** Hello.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution | [00:00:00] | stmt-01 | Alice | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |\n"
    )
    changes: list = []
    aud: list = []
    healed = forensic_healer.heal_output_1_forensic(doc, metadata={}, changes=changes, audit_log=aud)
    
    # The claim MUST be preserved in healed text, not masked to 'Canonical Grounding'
    assert "(Operator Verified)" in healed
    assert not any("Canonical Grounding" in c for c in changes)


# ---------------------------------------------------------------------------
# 4. Exact Production Regression
# ---------------------------------------------------------------------------

def test_production_regression_unauthenticated_alr_claim_fails_gate1_7(tmp_path: Path) -> None:
    """Exact regression: draft passed through ForensicHealer with unauthenticated claim must fail Gate 1.7."""
    ledger = _setup_test_ledger(tmp_path)
    meta = {
        "meeting_name": "Regression_Review",
        "meeting_slug": "regression_review",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin", "Bob Vance"],
        "hitl_ledger_path": str(ledger),
    }
    raw_draft = (
        "# Live Meeting Transcript: Regression Review\n\n"
        "**Date:** 09/30/2026\n\n"
        "### [00:01:00]\n\n"
        "**Alice Martin:** We reviewed the architecture.\n\n"
        "### [00:01:10]\n\n"
        "**Bob Vance:** Agreed.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution: Alice Martin | [00:00:00 – Session End] | stmt-01 | Alice Martin | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |\n"
        "| 2 | Speaker Attribution: Bob Vance | [00:00:00 – Session End] | stmt-02 | Bob Vance | Acoustic Grounding |\n"
    )
    # Run through healer
    healed = forensic_healer.heal_output_1_forensic(raw_draft, metadata=meta, changes=[], audit_log=[])
    
    # Verify unauthenticated claim survived healer
    assert "(Operator Verified)" in healed

    # Verify Gate 1.7 fails closed
    passed_g17, _, failures_g17 = check_ambiguity_lock_register_and_speaker_hitl(
        healed, meta, ledger_path=ledger
    )
    assert passed_g17 is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures_g17)
