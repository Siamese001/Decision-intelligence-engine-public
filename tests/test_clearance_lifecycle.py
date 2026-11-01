"""
tests/test_clearance_lifecycle.py

Unit tests for the Three-Phase Progressive Clearance Lifecycle:
- Phase 0: Pre-Flight Contextual Seeding
- Phase 1: In-Flight Non-Blocking Ambiguity Accumulation (Law 3 compliance)
- Rule C-10: Append-Only Ambiguity Lock Register
- Phase 2: Post-Session / Pre-U0 Atomic HITL Clearance Queue & Bayesian Updates
"""

import json
from pathlib import Path

from dual_pipeline.ambiguity_core import (
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)
from dual_pipeline.clearance_lifecycle_core import (
    AmbiguityLockRegister,
    InFlightAccumulator,
    Phase2ClearanceCoordinator,
    PreflightSeeder,
)


def test_phase0_preflight_seeder():
    """Verify Phase 0 context snapshot seeding and hash calculation."""
    seeder = PreflightSeeder(ledger_path=Path("non_existent_ledger.json"))
    snap = seeder.seed(
        meeting_name="Nexus Architecture Review",
        date_iso="2026-09-12",
        attendees=["Amit Ayer", "Sarah Connor"],
        project_acronyms=["NEE", "NEXUS"],
    )
    assert snap.meeting_name == "Nexus Architecture Review"
    assert len(snap.attendees) == 2
    assert len(snap.snapshot_hash) == 64


def test_c10_ambiguity_lock_register_integrity():
    """Verify Rule C-10 append-only Merkle/hash-chaining and terminal clearance."""
    register = AmbiguityLockRegister()

    # Create two test ambiguity records
    rec1 = AmbiguityRecord.create(
        ambiguity_id="amb_001",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        segment_id="turn_1",
        source_excerpt="Room Mic: Text",
        candidates=[
            CandidateOption(option_id="o1", text="Amit", confidence=0.52, rationale="r1"),
            CandidateOption(option_id="o2", text="Sarah", confidence=0.48, rationale="r2"),
        ],
        threshold=0.20,
    )
    rec2 = AmbiguityRecord.create(
        ambiguity_id="amb_002",
        domain=AmbiguityDomain.D2_DOMAIN_VOCABULARY,
        segment_id="turn_2",
        source_excerpt="Speaker: NEA",
        candidates=[
            CandidateOption(option_id="o3", text="NEE", confidence=0.90, rationale="r3"),
            CandidateOption(option_id="o4", text="NEA", confidence=0.10, rationale="r4"),
        ],
        threshold=0.20,
    )

    lock1 = register.append_lock(rec1)
    lock2 = register.append_lock(rec2)

    assert len(register.entries) == 2
    assert lock2.prev_hash == lock1.entry_hash
    # lock1 requires HITL (delta=0.04 < 0.20), so register is not cleared yet
    assert register.is_cleared() is False

    # Clear lock1
    register.clear_lock(lock1.lock_id, resolution_payload={"selected": "o1"})
    assert register.is_cleared() is True


def test_phase1_accumulator_law3_zero_interrupts():
    """Verify Phase 1 processes turns non-blockingly with zero live interrupts."""
    register = AmbiguityLockRegister()
    accumulator = InFlightAccumulator(register=register)

    turns = [
        {"speaker": "Room Mic 1", "text": "Turn 1 text", "start_ms": 0, "end_ms": 2000},
        {"speaker": "Room Mic 2", "text": "Turn 2 text", "start_ms": 2500, "end_ms": 4500},
    ]

    out_turns, recs = accumulator.process_turns(turns)
    assert len(out_turns) == 2
    assert accumulator.live_interrupt_count == 0
    assert len(register.entries) >= 1


def test_phase2_clearance_coordinator_and_ledger(tmp_path: Path):
    """Verify Phase 2 queue clearance, decision ledger persistence, and turn updates."""
    ledger_file = tmp_path / "test_hitl_ledger.json"
    register = AmbiguityLockRegister()

    rec = AmbiguityRecord.create(
        ambiguity_id="amb_100",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        segment_id="turn_0",
        source_excerpt="Room Mic: Starting presentation",
        candidates=[
            CandidateOption(option_id="opt_amit", text="Amit Ayer", confidence=0.51, rationale="r1"),
            CandidateOption(option_id="opt_sarah", text="Sarah Connor", confidence=0.49, rationale="r2"),
        ],
        threshold=0.20,
    )
    register.append_lock(rec)
    assert register.is_cleared() is False

    # Mock HITL callback choosing Amit Ayer
    def mock_operator_callback(record: AmbiguityRecord) -> str:
        return "opt_amit"

    coordinator = Phase2ClearanceCoordinator(
        register=register,
        ledger_path=ledger_file,
        hitl_callback=mock_operator_callback,
    )

    cleared = coordinator.clear_queue([rec])
    assert len(cleared) == 1
    assert cleared[0].status.value == "RESOLVED_HITL"
    assert cleared[0].selected_option_id == "opt_amit"
    assert register.is_cleared() is True

    # Verify persistent ledger update
    assert ledger_file.exists()
    data = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(data["decisions"]) == 1
    assert data["decisions"][0]["selected_option_id"] == "opt_amit"
    assert data["priors"]["D1_SPEAKER_ATTRIBUTION"]["Amit Ayer"] >= 1

    # Verify transcript application
    turns = [{"speaker": "Room Mic", "text": "Starting presentation"}]
    updated = coordinator.apply_resolutions_to_transcript(turns, cleared)
    assert updated[0]["speaker"] == "Amit Ayer"
