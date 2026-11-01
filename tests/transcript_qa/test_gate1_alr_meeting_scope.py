"""
tests/transcript_qa/test_gate1_alr_meeting_scope.py

Wave 4: Unit tests for Gate 1.7 meeting identity scoping.
Verifies that cross-meeting receipts cannot be hijacked even if subject hashes collide or match,
and that matching meeting slugs verify cleanly.
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
    verify_alr_cryptographic_receipt,
)


def _make_transcript_with_alr(
    speakers: list[str],
    alr_resolution_basis: str = "Operator Verified (Confidence: 100%)",
) -> str:
    header = (
        "# Executive Session -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/28/2026\n"
        "**Participants:** " + ", ".join(f"{s} (PartnerAdvisory)" for s in speakers) + "\n\n---\n\n"
    )
    turns = "### [00:01:00]\n\n**Alex Rivera:** Discussion.\n\n"
    alr = (
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        f"| 1 | Speaker Attribution | [00:00:00 - Session End] | stmt-1 | Attributed to Alex Rivera | {alr_resolution_basis} |\n"
        "| 2 | Lexicon | [00:01:00] | stmt-2 | Calibrated term | Lexicon Match |\n"
    )
    return header + turns + alr


@pytest.fixture
def temp_ledger(tmp_path: Path) -> Path:
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    envelope = {
        "schema_version": "1.0.0",
        "record_count": 0,
        "updated_at": "2026-09-28T16:00:00Z",
        "genesis_hash": "0" * 64,
        "latest_entry_hash": "0" * 64,
        "decisions": [],
    }
    ledger_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return ledger_path


class TestGate1ALRMeetingScoping:
    """Wave 4 Test Suite: Receipt Scoping by Meeting Identity."""

    def test_cross_meeting_receipt_rejected_even_with_matching_subject(self, temp_ledger: Path):
        """A receipt from 'Jarvis_Nexus_Q1_Charter_Review' cannot verify 'Introduction_PartnerAdvisory_Bcg_Mitchell_Peter'."""
        priv, pub = generate_hitl_keypair()
        shared_subject = "a" * 64

        # Receipt signed for Jarvis meeting
        jarvis_entry = create_signed_hitl_approval_entry(
            subject_sha256=shared_subject,
            case_ids=["attr_01"],
            decision="APPROVE",
            operator_id="amit_ayer",
            meeting_slug="Jarvis_Nexus_Q1_Charter_Review",
            meeting_date="09-28-2026",
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(jarvis_entry, ledger_path=temp_ledger)

        # Verification context is for PartnerAdvisory/BCG meeting
        f1_text = _make_transcript_with_alr(["Alex Rivera", "Marcus Vance"])
        meta = {
            "roster": [{"name": "Alex Rivera", "org": "NEA", "role": "Lead"}, {"name": "Marcus Vance", "org": "PartnerAdvisory", "role": "Principal"}],
            "meeting_slug": "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            "meeting_name": "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            "subject_sha256": shared_subject,
            "hitl_ledger_path": str(temp_ledger),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta, ledger_path=temp_ledger
        )

        assert passed is False
        assert details["hitl_receipt_bound"] is False
        assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)

    def test_matching_meeting_receipt_accepted(self, temp_ledger: Path):
        """A receipt signed for 'Introduction_PartnerAdvisory_Bcg_Mitchell_Peter' verifies cleanly."""
        priv, pub = generate_hitl_keypair()
        shared_subject = "b" * 64

        matching_entry = create_signed_hitl_approval_entry(
            subject_sha256=shared_subject,
            case_ids=["attr_02"],
            decision="APPROVE",
            operator_id="amit_ayer",
            meeting_slug="Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            meeting_date="09-28-2026",
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(matching_entry, ledger_path=temp_ledger)

        f1_text = _make_transcript_with_alr(["Alex Rivera", "Marcus Vance"])
        meta = {
            "roster": [{"name": "Alex Rivera", "org": "NEA", "role": "Lead"}, {"name": "Marcus Vance", "org": "PartnerAdvisory", "role": "Principal"}],
            "meeting_slug": "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            "meeting_name": "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter",
            "subject_sha256": shared_subject,
            "hitl_ledger_path": str(temp_ledger),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta, ledger_path=temp_ledger
        )

        assert passed is True
        assert details["hitl_receipt_bound"] is True
        assert details["verified_entry_id"] == matching_entry["entry_id"]
        assert failures == []

    def test_legacy_receipt_without_slug_supported_via_subject(self, temp_ledger: Path):
        """Legacy receipts without meeting_slug can still verify via strict subject_sha256."""
        priv, pub = generate_hitl_keypair()
        legacy_subject = "c" * 64

        legacy_entry = create_signed_hitl_approval_entry(
            subject_sha256=legacy_subject,
            case_ids=["attr_legacy"],
            decision="APPROVE",
            operator_id="amit_ayer",
            private_key=priv,
            public_key=pub,
        )
        append_signed_approval_to_ledger(legacy_entry, ledger_path=temp_ledger)

        f1_text = _make_transcript_with_alr(["Alex Rivera", "Marcus Vance"])
        meta = {
            "roster": [{"name": "Alex Rivera", "org": "NEA", "role": "Lead"}, {"name": "Marcus Vance", "org": "PartnerAdvisory", "role": "Principal"}],
            "meeting_slug": "Some_Meeting",
            "subject_sha256": legacy_subject,
            "hitl_ledger_path": str(temp_ledger),
        }

        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text, meta, ledger_path=temp_ledger
        )

        assert passed is True
        assert details["hitl_receipt_bound"] is True
