"""
tests/adversarial/test_hitl_payload_propagation_adversarial.py

Adversarial and edge-case testing for Wave 2: Complete Confirmed Payload Binding & Correction Propagation.
Covers 4 mandatory categories:
1. Boundary payload & malformed input (empty rosters, invalid attendee types, malformed resolution dicts)
2. Invariant bypass attempts (tampered signed subjects, fake operator confirmations, unconfirmed field leakage)
3. Concurrency & conflicting turn resolutions (multi-action collisions on the same turn, ordering invariants)
4. Rollback, recovery & idempotence (non-existent segment IDs, idempotent multi-pass resolution application)
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
    verify_hitl_approval_entry,
    export_public_key_pem,
)
from dual_pipeline.transcript_stages_core.attendee_profiles import (
    extract_structured_confirmed_attendees,
    is_attendee_field_confirmed,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.clearance_lifecycle_core.c10_register import (
    AmbiguityLockRegister,
    AmbiguityRecord,
)
from dual_pipeline.ambiguity_core.models import (
    AmbiguityDomain,
    AmbiguityStatus,
    CandidateOption,
)
from dual_pipeline.clearance_lifecycle_core.phase2_clearance import (
    Phase2ClearanceCoordinator,
)
from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import (
    _merge_explicit_resolutions,
)


# ==============================================================================
# Category 1: Boundary Payload & Malformed Input
# ==============================================================================

def test_boundary_empty_attendee_inputs():
    """Boundary test: None, empty list, empty string, and whitespace rosters produce empty outputs."""
    assert extract_structured_confirmed_attendees(None) == []
    assert extract_structured_confirmed_attendees([]) == []
    assert extract_structured_confirmed_attendees("") == []
    assert extract_structured_confirmed_attendees("   ") == []

    # Unconfirmed field check on empty metadata
    empty_meta: Dict[str, Any] = {}
    assert not is_attendee_field_confirmed(empty_meta, "Alice", "name")
    assert not is_attendee_field_confirmed(empty_meta, "Alice", "role")


def test_malformed_attendee_types_handled_gracefully():
    """Malformed input: non-string/non-dict elements or malformed dicts in attendees list."""
    malformed_inputs = [
        [123, 456.7, True, None],
        [{"wrong_key": "Alice"}, {"name": ""}, {"name": None}],
        [{"name": "Valid Name", "role": 12345, "organization": ["Not", "A", "String"]}],
    ]
    for inp in malformed_inputs:
        result = extract_structured_confirmed_attendees(inp)
        assert isinstance(result, list)
        for att in result:
            assert isinstance(att["name"], str)
            assert att["role"] is None or isinstance(att["role"], str)
            assert att["organization"] is None or isinstance(att["organization"], str)

    # Valid name with string role/org
    res = extract_structured_confirmed_attendees([
        {"name": "Valid Person", "role": "Architect", "organization": "NEA"}
    ])
    assert len(res) == 1
    assert res[0]["name"] == "Valid Person"
    assert res[0]["role"] == "Architect"


def test_boundary_clearance_lifecycle_empty_turns():
    """Boundary test: 0 turns in clearance coordinator."""
    coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
    updated_turns = coordinator.apply_resolutions_to_transcript([], [])
    assert updated_turns == []


def test_malformed_clearance_resolutions_handled_gracefully():
    """Malformed input: resolutions with missing keys, non-dict payloads, or missing candidates."""
    meta = {
        "entity_resolutions": {
            "ValidToken": "Valid Canonical",
            "ComplexToken": {"canonical_name": "Complex Corp", "org": "Complex Org"},
            "EmptyToken": "",
        }
    }
    clarifications: List[Dict[str, Any]] = []
    _merge_explicit_resolutions(meta, clarifications)
    tokens = {c["token"] for c in clarifications}
    assert "ValidToken" in tokens
    assert "ComplexToken" in tokens


# ==============================================================================
# Category 2: Invariant Bypass Attempts
# ==============================================================================

def test_tampered_role_breaks_signed_subject_digest():
    """Invariant bypass test: Modifying role changes subject digest; signed approval becomes invalid."""
    priv_key, pub_key = generate_hitl_keypair()

    original_subject = {
        "meeting_name": "Executive Strategy",
        "meeting_date": "2026-10-08",
        "project": "NEA",
        "attendees": ["Alice Smith"],
        "structured_attendees": [
            {
                "name": "Alice Smith",
                "role": "Director",
                "organization": "Apex Global Enterprises",
                "confirmed_fields": ["name", "role", "organization"],
            }
        ],
    }
    orig_digest = compute_alr_subject_digest(original_subject)

    entry = create_signed_hitl_approval_entry(
        subject_sha256=orig_digest,
        case_ids=["ATTENDEE_CONFIRMATION"],
        decision="APPROVE",
        private_key=priv_key,
        public_key=pub_key,
    )
    assert verify_hitl_approval_entry(entry) is True

    # Attacker mutates role in subject
    tampered_subject = copy.deepcopy(original_subject)
    tampered_subject["structured_attendees"][0]["role"] = "Chief Executive Officer"
    tampered_digest = compute_alr_subject_digest(tampered_subject)
    assert orig_digest != tampered_digest

    # If payload is altered to point to tampered subject, signature check fails
    tampered_entry = copy.deepcopy(entry)
    tampered_entry["payload"]["subject_sha256"] = tampered_digest
    assert verify_hitl_approval_entry(tampered_entry) is False


def test_cannot_claim_unconfirmed_role_from_name_only_approval():
    """Invariant bypass test: A names-only approval cannot certify role or organization."""
    metadata: Dict[str, Any] = {
        "meeting_name": "Sprint Sync",
        "date": "10/08/2026",
        "project": "nea",
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "confirmed_attendees": ["Bob Jones"],
            "structured_attendees": [
                {
                    "name": "Bob Jones",
                    "role": "VP Engineering",  # inferred role, not confirmed
                    "organization": "PartnerAdvisory",   # inferred org, not confirmed
                    "confirmed_fields": ["name"],
                }
            ],
        },
    }
    assert is_attendee_field_confirmed(metadata, "Bob Jones", "name") is True
    assert is_attendee_field_confirmed(metadata, "Bob Jones", "role") is False
    assert is_attendee_field_confirmed(metadata, "Bob Jones", "organization") is False
    assert is_attendee_field_confirmed(metadata, "Bob Jones", "scope") is False


def test_unauthenticated_operator_claim_fails_validation():
    """Invariant bypass test: setting attendees_confirmed_by_operator=True without receipt fails validation."""
    meta: Dict[str, Any] = {
        "meeting_name": "Review Session",
        "is_audio": True,
        "attendees_confirmed_by_operator": True,
        # Missing valid attendee_confirmation_receipt
    }
    with pytest.raises(ValueError) as exc_info:
        verify_mandatory_attendee_confirmation(meta, "Speaker 1: Hello.")
    assert "Security Violation" in str(exc_info.value)


# ==============================================================================
# Category 3: Concurrency & Conflicting Turn Resolutions
# ==============================================================================

def test_multiple_resolutions_on_same_segment_coexist():
    """Concurrency/Race: Multiple distinct corrections targeting the same turn are all applied without drops."""
    raw_turns = [
        {"speaker": "Speaker 1", "text": "Speaker 1: We must deploy the API today."}
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

    coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
    updated_turns = coordinator.apply_resolutions_to_transcript(raw_turns, [rec_spk, rec_vocab])

    assert len(updated_turns) == 1
    # Both changes must be present!
    assert updated_turns[0]["speaker"] == "Alice"
    assert "Application Programming Interface" in updated_turns[0]["text"]
    assert updated_turns[0]["text"] == "Alice: We must deploy the Application Programming Interface today."


def test_sequential_contradictory_turn_resolutions_ordering():
    """Conflicting resolution: When multiple speaker resolutions exist for the same turn, last one takes effect."""
    raw_turns = [
        {"speaker": "UNKNOWN", "text": "UNKNOWN: Status report."}
    ]
    rec1 = AmbiguityRecord(
        ambiguity_id="cand_a",
        segment_id="turn_0",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        candidates=[
            CandidateOption(
                option_id="opt_1",
                text="First Speaker",
                confidence=0.6,
                rationale="Preliminary",
            ),
        ],
        selected_option_id="opt_1",
        source_excerpt="UNKNOWN: Status report.",
    )
    rec2 = AmbiguityRecord(
        ambiguity_id="cand_b",
        segment_id="turn_0",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        candidates=[
            CandidateOption(
                option_id="opt_2",
                text="Final Speaker",
                confidence=0.9,
                rationale="Overriding confirmation",
            ),
        ],
        selected_option_id="opt_2",
        source_excerpt="UNKNOWN: Status report.",
    )

    coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
    updated_turns = coordinator.apply_resolutions_to_transcript(raw_turns, [rec1, rec2])

    assert updated_turns[0]["speaker"] == "Final Speaker"
    assert updated_turns[0]["text"].startswith("Final Speaker:")


# ==============================================================================
# Category 4: Rollback, Recovery & Idempotence
# ==============================================================================

def test_nonexistent_segment_id_gracefully_ignored():
    """Recovery: Resolutions targeting non-existent turn IDs do not affect valid turns."""
    raw_turns = [
        {"speaker": "Alice", "text": "Alice: Hello."}
    ]
    ghost_rec = AmbiguityRecord(
        ambiguity_id="ghost",
        segment_id="turn_999",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        candidates=[
            CandidateOption(
                option_id="casper",
                text="Casper",
                confidence=1.0,
                rationale="Ghost",
            ),
        ],
        selected_option_id="casper",
        source_excerpt="Ghost excerpt",
    )

    coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
    updated_turns = coordinator.apply_resolutions_to_transcript(raw_turns, [ghost_rec])

    assert len(updated_turns) == 1
    assert updated_turns[0]["speaker"] == "Alice"
    assert updated_turns[0]["text"] == "Alice: Hello."


def test_idempotent_resolution_application():
    """Idempotence: Re-applying the exact same resolutions to already resolved turns is stable."""
    raw_turns = [
        {"speaker": "Speaker 1", "text": "Speaker 1: System is ready."}
    ]
    rec = AmbiguityRecord(
        ambiguity_id="cand_1",
        segment_id="turn_0",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        candidates=[
            CandidateOption(
                option_id="dr_smith",
                text="Dr. Smith",
                confidence=0.99,
                rationale="Verified",
            ),
        ],
        selected_option_id="dr_smith",
        source_excerpt="Speaker 1: System is ready.",
    )

    coordinator = Phase2ClearanceCoordinator(register=AmbiguityLockRegister())
    # First pass
    turns_pass1 = coordinator.apply_resolutions_to_transcript(raw_turns, [rec])
    assert turns_pass1[0]["speaker"] == "Dr. Smith"
    assert turns_pass1[0]["text"] == "Dr. Smith: System is ready."

    # Second pass with same register on already resolved turns
    turns_pass2 = coordinator.apply_resolutions_to_transcript(turns_pass1, [rec])
    assert turns_pass2[0]["speaker"] == "Dr. Smith"
    assert turns_pass2[0]["text"] == "Dr. Smith: System is ready."
    assert turns_pass1 == turns_pass2
