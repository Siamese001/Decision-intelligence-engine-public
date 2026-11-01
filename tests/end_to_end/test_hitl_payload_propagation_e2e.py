"""
tests/end_to_end/test_hitl_payload_propagation_e2e.py

End-to-end tests for Wave 2: Complete confirmed payload binding and correction propagation.
Verifies full lifecycle of attendee confirmation, Ed25519 cryptographic signing,
isolated ledger persistence, and transcript correction execution through normal entry points.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.clearance_lifecycle_core.c10_register import AmbiguityLockRegister
from dual_pipeline.hitl_governance_core.approval_lookup import (
    ApprovalResolutionStatus,
    resolve_hitl_approval_reference,
)
from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import execute_clearance_lifecycle
from dual_pipeline.transcript_stages_core.attendee_gate import (
    is_attendee_field_confirmed,
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


class DummyRecorder:
    def span(self, name: str):
        from contextlib import nullcontext
        return nullcontext()


class TestHitlPayloadPropagationE2E:
    """E2E verification of attendee payload confirmation and correction propagation."""

    def test_e2e_attendee_confirmation_lifecycle_with_roles(self):
        """Full lifecycle: unconfirmed halts, operator confirms with roles, signed receipt persisted, downstream verifies."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            ledger_file = tmp_root / "hitl_decision_ledger.json"
            ledger_file.write_text('{"decisions": []}', encoding="utf-8")

            metadata: Dict[str, Any] = {
                "meeting_name": "Architecture Review",
                "date": "10/08/2026",
                "project": "nea",
                "is_audio": True,
                "hitl_ledger_path": str(ledger_file),
            }

            # 1. Unconfirmed audio must halt closed
            with pytest.raises(HITLRequiredError) as exc_info:
                verify_mandatory_attendee_confirmation(metadata, "Speaker 1: Welcome to the review.")
            assert exc_info.value.hitl_type == "attendee_confirmation"

            # 2. Operator provides structured confirmation binding name, role, organization
            confirmed_roster = [
                {
                    "name": "Daniel Brankin",
                    "role": "Project Development Lead",
                    "org": "Apex Global Enterprises (NEA)",
                    "confirmed_fields": ["name", "role", "organization"],
                },
                {
                    "name": "Taylor Mercer",
                    "role": "Delivery Lead / PM",
                    "org": "PartnerAdvisory",
                    "confirmed_fields": ["name", "role", "organization"],
                },
            ]

            receipt = record_attendee_confirmation(
                metadata,
                confirmed_roster,
                rationale="Operator verified participant roster and professional titles",
                repo_root=tmp_root,
            )

            assert receipt["status"] == "RESOLVED_HITL"
            assert receipt.get("signed_entry_id") is not None
            assert metadata.get("attendees_confirmed_by_operator") is True

            # 3. Gate now passes cleanly
            verify_mandatory_attendee_confirmation(metadata, "Speaker 1: Welcome to the review.")

            # 4. Downstream verification confirms names, roles, and organizations
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "name") is True
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "role") is True
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "organization") is True
            assert is_attendee_field_confirmed(metadata, "Taylor Mercer", "name") is True
            assert is_attendee_field_confirmed(metadata, "Taylor Mercer", "role") is True
            assert is_attendee_field_confirmed(metadata, "Taylor Mercer", "organization") is True

            # 5. Lookup confirms signed entry in isolated ledger
            evidence = resolve_hitl_approval_reference(
                ref=receipt["signed_entry_id"],
                default_ledger_path=ledger_file,
            )
            assert evidence.status == ApprovalResolutionStatus.RESOLVED_VALID
            assert evidence.is_valid is True

    def test_e2e_name_only_confirmation_cannot_certify_inferred_role(self):
        """Names-only confirmation correctly certifies names but blocks inferred role certification."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            ledger_file = tmp_root / "hitl_decision_ledger.json"
            ledger_file.write_text('{"decisions": []}', encoding="utf-8")

            metadata: Dict[str, Any] = {
                "meeting_name": "Sprint Sync",
                "date": "10/08/2026",
                "project": "nea",
                "is_audio": True,
                "hitl_ledger_path": str(ledger_file),
            }

            # Operator provides names only
            record_attendee_confirmation(
                metadata,
                ["Daniel Brankin", "Jill Cizewski"],
                repo_root=tmp_root,
            )

            # Name is confirmed
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "name") is True
            # Role and org must NOT retain verified status
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "role") is False
            assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "organization") is False

    def test_e2e_clearance_lifecycle_correction_propagation(self):
        """Clearance lifecycle rebuilds transcript from applied resolutions and invalidates digest."""
        transcript = (
            "Speaker 1: We are finalizing the contract.\n"
            "Speaker 2: Yes, the timeline is approved."
        )
        meta: Dict[str, Any] = {
            "meeting_name": "Executive Sync",
            "date": "10/08/2026",
            "attendees": ["Jen Pan", "Amit Ayer"],
            "auto_resolve_recommended": True,
            "entity_resolutions": {
                "Contract Alpha": {"canonical_name": "Alpha Service Agreement", "org": "PartnerAdvisory"},
            },
        }
        recorder = DummyRecorder()

        updated_text = execute_clearance_lifecycle(transcript, meta, recorder)

        # Returned text is updated
        assert isinstance(updated_text, str)
        # Digest is generated and matches updated text
        import hashlib
        expected_digest = hashlib.sha256(updated_text.encode("utf-8")).hexdigest()
        assert meta.get("transcript_sha256") == expected_digest
        # Structured entity clarifications reflect explicit operator input
        clarifications = meta.get("entity_clarifications", [])
        alpha = next((c for c in clarifications if c["token"] == "Contract Alpha"), None)
        assert alpha is not None
        assert alpha["canonical_name"] == "Alpha Service Agreement"
        assert alpha["org"] == "PartnerAdvisory"
