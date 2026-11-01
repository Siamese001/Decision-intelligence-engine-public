"""
tests/test_ambiguity_detectors.py

Unit tests for the Full-Spectrum 5-Domain Ambiguity Engine:
- Domain 1: Speaker Attribution & Room-Mic Hygiene
- Domain 2: Domain Vocabulary & Acronym Phonetics
- Domain 3: Critical Semantic Polarities & Intent
- Domain 4: Turn Boundaries & Crosstalk Collisions
- Domain 5: Recording Drops & Packet Loss
- AmbiguityRecord 20% margin rule gating
"""

import pytest

from dual_pipeline.ambiguity_core import (
    AmbiguityDomain,
    AmbiguityEngine,
    AmbiguityRecord,
    CandidateOption,
    CrosstalkCollisionDetector,
    RecordingDropsDetector,
    SemanticPolarityDetector,
    SpeakerAttributionDetector,
    VocabularyAcronymDetector,
)


def test_ambiguity_record_delta_margin_calculation():
    """Verify Delta = c_top - c_second calculation and 20% margin gating."""
    # Case 1: Delta = 0.55 - 0.45 = 0.10 < 0.20 -> requires_hitl = True
    cands_tight = [
        CandidateOption(option_id="opt_1", text="Jane Doe", confidence=0.55, rationale="r1"),
        CandidateOption(option_id="opt_2", text="Sarah Connor", confidence=0.45, rationale="r2"),
    ]
    rec_tight = AmbiguityRecord.create(
        ambiguity_id="amb_001",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        segment_id="turn_1",
        source_excerpt="Room Mic: Hello",
        candidates=cands_tight,
        threshold=0.20,
    )
    assert rec_tight.delta_margin == pytest.approx(0.10, abs=1e-3)
    assert rec_tight.requires_hitl is True
    assert rec_tight.candidates[0].is_recommended is True

    # Case 2: Delta = 0.85 - 0.15 = 0.70 >= 0.20 -> requires_hitl = False
    cands_wide = [
        CandidateOption(option_id="opt_1", text="Jane Doe", confidence=0.85, rationale="r1"),
        CandidateOption(option_id="opt_2", text="Sarah Connor", confidence=0.15, rationale="r2"),
    ]
    rec_wide = AmbiguityRecord.create(
        ambiguity_id="amb_002",
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        segment_id="turn_2",
        source_excerpt="Jane Doe: Clear audio",
        candidates=cands_wide,
        threshold=0.20,
    )
    assert rec_wide.delta_margin == pytest.approx(0.70, abs=1e-3)
    assert rec_wide.requires_hitl is False


def test_d1_speaker_attribution_detector():
    """Verify Domain 1 detects room-mic and unattributed turns."""
    detector = SpeakerAttributionDetector(margin_threshold=0.20)
    turns = [
        {"speaker": "Room Mic 1", "text": "We need to deploy the model tomorrow.", "start_ms": 1000, "end_ms": 4000},
        {"speaker": "Amit Ayer", "text": "Understood, proceeding with deployment.", "start_ms": 4500, "end_ms": 7000},
    ]
    records = detector.detect(turns, known_attendees=["Amit Ayer", "Sarah Connor", "John Smith"])
    assert len(records) == 1
    assert records[0].domain == AmbiguityDomain.D1_SPEAKER_ATTRIBUTION
    assert records[0].segment_id == "turn_0"
    assert len(records[0].candidates) >= 2


def test_d2_vocabulary_detector():
    """Verify Domain 2 detects phonetic acronym confusions."""
    detector = VocabularyAcronymDetector(margin_threshold=0.20)
    turns = [
        {"speaker": "Alice", "text": "We are migrating to NEA architecture next week.", "start_ms": 0, "end_ms": 2000},
        {"speaker": "Bob", "text": "The BGM3 embedding pipeline is ready.", "start_ms": 2500, "end_ms": 5000},
    ]
    records = detector.detect(turns, project_acronyms=["NEE", "BGE-M3"])
    assert len(records) >= 1
    assert any(r.domain == AmbiguityDomain.D2_DOMAIN_VOCABULARY for r in records)


def test_d3_polarity_detector():
    """Verify Domain 3 detects critical semantic polarities and inversions."""
    detector = SemanticPolarityDetector(margin_threshold=0.20)
    turns = [
        {"speaker": "Charlie", "text": "Can we deploy, or can we not deploy?", "start_ms": 0, "end_ms": 3000},
        {"speaker": "Dan", "text": "We commit to this date, but might propose changes?", "start_ms": 3500, "end_ms": 6000},
    ]
    records = detector.detect(turns)
    assert len(records) >= 1
    assert all(r.domain == AmbiguityDomain.D3_SEMANTIC_POLARITY for r in records)


def test_d4_crosstalk_detector():
    """Verify Domain 4 detects turn overlap collisions."""
    detector = CrosstalkCollisionDetector(margin_threshold=0.20, crosstalk_overlap_threshold_ms=500)
    turns = [
        {"speaker": "Alice", "text": "I was thinking we should wait--", "start_ms": 1000, "end_ms": 4000},
        {"speaker": "Bob", "text": "--No, we must move right now.", "start_ms": 3200, "end_ms": 5500},
    ]
    records = detector.detect(turns)
    assert len(records) == 1
    assert records[0].domain == AmbiguityDomain.D4_TURN_CROSSTALK
    assert records[0].candidates[0].option_id.startswith("opt_d4_0")


def test_d5_recording_drops_detector():
    """Verify Domain 5 detects packet loss and silence gaps (Rule C-11)."""
    detector = RecordingDropsDetector(margin_threshold=0.20, gap_silence_threshold_ms=5000)
    turns = [
        {"speaker": "Alice", "text": "Starting the sync.", "start_ms": 1000, "end_ms": 2000},
        {"speaker": "Bob", "text": "Resuming after audio dropped.", "start_ms": 9000, "end_ms": 11000},
    ]
    records = detector.detect(turns)
    assert len(records) == 1
    assert records[0].domain == AmbiguityDomain.D5_DROPS_AND_GAPS
    assert "Rule C-11" in records[0].candidates[0].text


def test_ambiguity_engine_end_to_end_scan():
    """Verify AmbiguityEngine aggregates all 5 domains in a single unified scan."""
    engine = AmbiguityEngine(margin_threshold=0.20)
    turns = [
        {"speaker": "Room Mic 1", "text": "Hello team, can we or can we not proceed with NEA?", "start_ms": 1000, "end_ms": 4000},
        {"speaker": "Bob", "text": "Wait-- [Recording gap: 00:00:04 – 00:00:12]", "start_ms": 3800, "end_ms": 13000},
    ]
    records = engine.scan(turns, known_attendees=["Alice", "Bob"], project_acronyms=["NEE"])
    assert len(records) >= 2
    domains = {r.domain for r in records}
    assert AmbiguityDomain.D1_SPEAKER_ATTRIBUTION in domains or AmbiguityDomain.D2_DOMAIN_VOCABULARY in domains
