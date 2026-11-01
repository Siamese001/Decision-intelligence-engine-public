"""
tests/test_hitl_payload_propagation.py

Unit tests for Wave 2: Canonical confirmed payload binding and correction propagation.
Verifies field-level confirmation states, structured subject digests,
reachability of correction flow, and absence of invented org/role defaults.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.ambiguity_core.models import AmbiguityStatus
from dual_pipeline.clearance_lifecycle_core.c10_register import AmbiguityRecord
from dual_pipeline.clearance_lifecycle_core.phase2_clearance import Phase2ClearanceCoordinator
from dual_pipeline.hitl_governance_core.crypto import compute_alr_subject_digest
from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import _merge_explicit_resolutions
from dual_pipeline.transcript_stages_core.attendee_gate import (
    extract_structured_confirmed_attendees,
    is_attendee_field_confirmed,
    record_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution


class TestAttendeePayloadBinding:
    """Tests for canonical structured attendee confirmation and field verification."""

    def test_extract_structured_attendees_names_only(self):
        """Names-only input records confirmed_fields=['name'] and None for role/org."""
        raw = ["Daniel Brankin", "Jill Cizewski"]
        profiles = extract_structured_confirmed_attendees(raw, default_scope="Sprint Review")
        assert len(profiles) == 2

        p1 = next(p for p in profiles if p["name"] == "Daniel Brankin")
        assert p1["role"] is None
        assert p1["organization"] is None
        assert p1["scope"] == "Sprint Review"
        assert p1["confirmed_fields"] == ["name"]

    def test_extract_structured_attendees_with_roles_and_orgs(self):
        """Structured entries bind role and organization to confirmed_fields."""
        raw = [
            {"name": "Daniel Brankin", "role": "Project Development Lead", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Taylor Mercer", "role": "Delivery Lead / PM", "organization": "PartnerAdvisory", "scope": "Sprint Review"},
        ]
        profiles = extract_structured_confirmed_attendees(raw)
        assert len(profiles) == 2

        p1 = next(p for p in profiles if p["name"] == "Daniel Brankin")
        assert p1["role"] == "Project Development Lead"
        assert p1["organization"] == "Apex Global Enterprises (NEA)"
        assert sorted(p1["confirmed_fields"]) == ["name", "organization", "role"]

        p2 = next(p for p in profiles if p["name"] == "Taylor Mercer")
        assert p2["role"] == "Delivery Lead / PM"
        assert p2["organization"] == "PartnerAdvisory"
        assert p2["scope"] == "Sprint Review"
        assert sorted(p2["confirmed_fields"]) == ["name", "organization", "role", "scope"]

    def test_is_attendee_field_confirmed_distinguishes_fields(self):
        """Confirmed name does not confirm unconfirmed role or organization."""
        metadata = {
            "meeting_name": "Sprint Review",
            "date": "10/08/2026",
            "project": "nea",
        }
        # Names-only confirmation
        record_attendee_confirmation(metadata, ["Daniel Brankin", "Jill Cizewski"])

        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "name") is True
        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "role") is False
        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "organization") is False

        # Add explicit role confirmation
        record_attendee_confirmation(
            metadata,
            [{"name": "Daniel Brankin", "role": "Project Lead", "confirmed_fields": ["name", "role"]}],
        )
        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "name") is True
        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "role") is True
        assert is_attendee_field_confirmed(metadata, "Daniel Brankin", "organization") is False

    def test_changing_role_alters_structured_subject_digest(self):
        """Changing role or org alters subject digest so old signature cannot retain verified status."""
        meta1 = {"meeting_name": "Review", "date": "10/08/2026", "project": "nea"}
        record_attendee_confirmation(meta1, ["Daniel Brankin"])
        digest1 = meta1["subject_sha256"]

        meta2 = {"meeting_name": "Review", "date": "10/08/2026", "project": "nea"}
        record_attendee_confirmation(
            meta2,
            [{"name": "Daniel Brankin", "role": "Director", "confirmed_fields": ["name", "role"]}],
        )
        digest2 = meta2["subject_sha256"]

        assert digest1 != digest2

    def test_legacy_name_only_subject_matches_backward_compatible_format(self):
        """When only names are confirmed, subject_dict preserves legacy keys."""
        meta = {"meeting_name": "Sprint", "date": "10/08/2026", "project": "nea"}
        record_attendee_confirmation(meta, ["Bob Smith", "Alice Jones"])
        subject = meta["subject"]

        assert set(subject.keys()) == {"meeting_name", "meeting_date", "project", "attendees"}
        assert subject["attendees"] == ["Alice Jones", "Bob Smith"]
        assert "structured_attendees" not in subject


class TestClearanceCorrectionPropagation:
    """Tests for entity clarification and multi-correction propagation."""

    def test_merge_explicit_resolutions_does_not_invent_org_or_role(self):
        """Explicit string resolutions leave unknown org and role as Unknown rather than inventing PartnerAdvisory/Stakeholder."""
        meta = {
            "entity_resolutions": {
                "Project Alpha": "Alpha Project Initiative",
                "Vendor Beta": {"canonical_name": "Beta Corp", "organization": "Beta Global Inc"},
            }
        }
        clarifications: list[Dict[str, Any]] = []
        _merge_explicit_resolutions(meta, clarifications)

        c1 = next(c for c in clarifications if c["token"] == "Project Alpha")
        assert c1["canonical_name"] == "Alpha Project Initiative"
        assert c1["org"] == "Unknown"
        assert c1["role"] == "Unknown"
        assert c1["confirmed_fields"] == ["canonical_name", "token"]

        c2 = next(c for c in clarifications if c["token"] == "Vendor Beta")
        assert c2["canonical_name"] == "Beta Corp"
        assert c2["org"] == "Beta Global Inc"
        assert c2["role"] == "Unknown"
        assert sorted(c2["confirmed_fields"]) == ["canonical_name", "organization", "token"]

    def test_apply_resolutions_to_transcript_multiple_corrections(self):
        """Multiple sequential corrections on the same segment apply cleanly without discarding earlier fixes."""
        from dual_pipeline.ambiguity_core.models import (
            AmbiguityDomain,
            CandidateOption,
        )

        turns = [
            {"speaker": "Speaker 1", "text": "Speaker 1: We must deploy the API today."},
        ]

        rec_spk = AmbiguityRecord(
            ambiguity_id="amb-spk-0",
            segment_id="turn_0",
            domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
            candidates=[
                CandidateOption(
                    option_id="opt-spk-1",
                    text="Alice",
                    confidence=0.95,
                    rationale="Matched tone",
                ),
            ],
            selected_option_id="opt-spk-1",
            source_excerpt="Speaker 1: We must deploy the API today.",
        )

        rec_vocab = AmbiguityRecord(
            ambiguity_id="amb-vocab-0",
            segment_id="turn_0",
            domain=AmbiguityDomain.D2_DOMAIN_VOCABULARY,
            candidates=[
                CandidateOption(
                    option_id="opt-vocab-1",
                    text="Application Programming Interface",
                    confidence=0.90,
                    rationale="Expansion",
                ),
                CandidateOption(
                    option_id="opt-vocab-0",
                    text="API",
                    confidence=0.10,
                    rationale="Raw",
                ),
            ],
            selected_option_id="opt-vocab-1",
            source_excerpt="API",
        )

        from dual_pipeline.clearance_lifecycle_core.c10_register import AmbiguityLockRegister

        coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
        updated_turns = coordinator.apply_resolutions_to_transcript(turns, [rec_spk, rec_vocab])

        assert len(updated_turns) == 1
        turn = updated_turns[0]
        assert turn["speaker"] == "Alice"
        assert "Alice: We must deploy the Application Programming Interface today." in turn["text"]


class TestRoleResolutionProvenance:
    """Tests distinguishing automated lookup from authentic human role resolution."""

    def test_auto_lookup_does_not_claim_hitl_receipt_bound(self):
        """Automated directory/graph resolution sets hitl_receipt_bound=False and status=RESOLVED_DIRECTORY."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta: Dict[str, Any] = {"roster": [{"name": "Daniel Brankin", "role": ""}]}
            receipt = apply_hitl_role_resolution(
                meta,
                "Daniel Brankin",
                "Director of Technology",
                repo_root=Path(tmp_dir),
                is_human_confirmed=False,
                provenance="enterprise_directory",
            )
            assert receipt["hitl_receipt_bound"] is False
            assert receipt["is_human_confirmed"] is False
            assert receipt["status"] == "RESOLVED_DIRECTORY"
            assert "Daniel Brankin" not in meta.get("hitl_role_resolutions", {})
            assert meta.get("auto_role_resolutions", {})["Daniel Brankin"] == "Director of Technology"

    def test_human_role_resolution_claims_hitl_receipt_bound(self):
        """Authentic human resolution sets hitl_receipt_bound=True and status=RESOLVED_HITL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta: Dict[str, Any] = {"roster": [{"name": "Daniel Brankin", "role": ""}]}
            receipt = apply_hitl_role_resolution(
                meta,
                "Daniel Brankin",
                "Director of Technology",
                repo_root=Path(tmp_dir),
                is_human_confirmed=True,
                provenance="HUMAN_OPERATOR",
            )
            assert receipt["hitl_receipt_bound"] is True
            assert receipt["is_human_confirmed"] is True
            assert receipt["status"] == "RESOLVED_HITL"
            assert meta.get("hitl_role_resolutions", {})["Daniel Brankin"] == "Director of Technology"
