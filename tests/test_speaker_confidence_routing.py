"""
tests/test_speaker_confidence_routing.py

Wave 2 Acceptance Gate: Word-Level Confidence Routing & Speaker Pattern Engine.
Validates 3-lane HITL triage (Straight-Through, Field-Level, Full Manual), cpWER calculation,
drift detection, and speaker pattern recovery for unattributed transcripts.
"""
from dual_pipeline.speaker_attribution_core.confidence_router import (
    DiarizationLane,
    WordConfidenceRouter,
)
from dual_pipeline.speaker_attribution_core.pattern_engine import (
    SpeakerPatternEngine,
)
from dual_pipeline.turn_clusterer import cluster_conversational_turns_with_patterns


def test_confidence_router_straight_through():
    router = WordConfidenceRouter()
    words = [
        {"word": "Let's", "confidence": 0.98},
        {"word": "review", "confidence": 0.95},
        {"word": "the", "confidence": 0.99},
        {"word": "architecture", "confidence": 0.96},
    ]
    dec = router.route_segment(words, segment_id="seg-1", speaker="Amit Ayer")
    assert dec.lane == DiarizationLane.STRAIGHT_THROUGH
    assert len(dec.flagged_tokens) == 0
    assert dec.mean_confidence > 0.95


def test_confidence_router_field_level_review():
    router = WordConfidenceRouter()
    words = [
        {"word": "We", "confidence": 0.95},
        {"word": "met", "confidence": 0.94},
        {"word": "with", "confidence": 0.92},
        {"word": "Mandy", "confidence": 0.82},  # Critical proper noun below 0.95
        {"word": "yesterday", "confidence": 0.91},
    ]
    dec = router.route_segment(words, segment_id="seg-2", speaker="Amit Ayer")
    assert dec.lane == DiarizationLane.FIELD_LEVEL_REVIEW
    assert any(t.word == "Mandy" for t in dec.flagged_tokens)


def test_confidence_router_full_manual_exception():
    router = WordConfidenceRouter()
    words = [
        {"word": "Static", "confidence": 0.45},
        {"word": "muffled", "confidence": 0.40},
        {"word": "audio", "confidence": 0.55},
    ]
    dec = router.route_segment(words, segment_id="seg-3", speaker="Unknown")
    assert dec.lane == DiarizationLane.FULL_MANUAL_EXCEPTION
    assert len(dec.flagged_tokens) == 3


def test_cpwer_proxy_calculation():
    refs = [
        {"speaker": "Amit", "text": "Hello world today"},
        {"speaker": "Jen", "text": "Good morning Amit"},
    ]
    # Exact match
    assert WordConfidenceRouter.calculate_cpwer_proxy(refs, refs) == 0.0

    # Speaker mismatch
    hyp_bad_speaker = [
        {"speaker": "Unknown", "text": "Hello world today"},
        {"speaker": "Jen", "text": "Good morning Amit"},
    ]
    err = WordConfidenceRouter.calculate_cpwer_proxy(hyp_bad_speaker, refs)
    assert err > 0.0


def test_confidence_drift_detection():
    router = WordConfidenceRouter()
    # Baseline expected: 70% straight-through, 20% field-level, 10% exception
    # Simulate a degraded run where 50% are full manual exceptions
    bad_words = [{"word": "bad", "confidence": 0.30}]
    decs = [router.route_segment(bad_words, segment_id=f"seg-{i}") for i in range(10)]
    drift = WordConfidenceRouter.detect_confidence_drift(decs)
    assert drift["drift_detected"] is True
    assert "Confidence drift detected" in drift["reason"]


def test_speaker_pattern_engine_self_and_addressee_cues():
    engine = SpeakerPatternEngine(known_participants=["Amit Ayer", "Mandy"])
    raw_turns = [
        {"text": "Hello everyone, this is Amit. Let's begin the review."},
        {"text": "I agree with the agenda."},
        {"text": "Thanks, Mandy. Can you walk through the second slide?"},
        {"text": "Sure, here are the numbers."},
        {"text": "Moving on to the next item, let's look at infrastructure."},
        {"text": "The servers are provisioned."},
    ]
    pattern_map = engine.analyze_and_pattern_turns(raw_turns)
    assert pattern_map.total_turns == 6
    assert len(pattern_map.anchors) >= 2

    # Check anchor attribution
    turn0 = pattern_map.assigned_turns[0]
    assert turn0["speaker"] == "Amit Ayer"

    # Turn 3 was addressee of turn 2 ("Thanks Mandy") -> should be Mandy
    turn3 = pattern_map.assigned_turns[3]
    assert turn3["speaker"] == "Mandy"

    # Check topic shift detected at turn 4 ("Moving on to the next item")
    assert 4 in pattern_map.topic_shift_turn_indices


def test_turn_clusterer_integration_with_patterns():
    segments = [
        {"start": 0.0, "end": 4.0, "text": "This is Amit speaking."},
        {"start": 4.5, "end": 8.0, "text": "Understood Amit, over to you Mandy."},
        {"start": 8.5, "end": 12.0, "text": "I have the slides ready."},
    ]
    meta = {"roster": ["Amit Ayer", "Mandy"]}
    assigned, ambiguities, p_map = cluster_conversational_turns_with_patterns(segments, metadata=meta)
    assert len(assigned) == 3
    assert "speaker_pattern_map" in meta
    assert meta["speaker_pattern_map"]["anchor_count"] >= 1
