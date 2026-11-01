"""Wave 2 Acceptance Tests: Acoustic Evidence Restoration and Utterance Assembly.

Verifies:
1. Exact piecewise interval support and candidate speaker scoring.
2. Bounded continuity for small pauses (<=0.25s) without competing speaker activity.
3. Hard boundaries and Unknown preservation: gaps >0.25s without acoustic intervals remain Unknown.
4. Uncertainty boundaries vs confirmed speaker changes (Unknown involvement emits 'uncertainty_boundary').
5. Short acknowledgment precision: genuine conversational markers vs grammatical fragments.
6. 100% word and order conservation across assembly.
7. Retention of complete word-level evidence dictionaries in utterances and structured turns.
8. PRF 47-58s window replay: reduction of spurious sentence-interior splits.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    align_segments_with_diarization,
    extract_acoustic_intervals,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import (
    AssemblyConfig,
    SpeakerUtterance,
    UtteranceAssembler,
    _is_short_acknowledgment,
    assemble_utterances,
)


def test_word_overlap_scoring_and_candidate_retention():
    """Verifies that words overlapping acoustic intervals receive exact support scores

    and all candidate speakers are retained in word evidence.
    """
    diar_intervals = [
        (10.0, 12.0, "Speaker 1"),
        (11.5, 13.5, "Speaker 2"),  # Overlap between 11.5 and 12.0
    ]
    segments = [
        {
            "start": 10.0,
            "end": 13.5,
            "words": [
                {"word": "Before", "start": 10.2, "end": 10.8},
                {"word": "overlap", "start": 11.6, "end": 11.9},  # Covered by both
                {"word": "after", "start": 12.5, "end": 13.0},
            ],
        }
    ]

    aligned = align_segments_with_diarization(segments, diar_intervals)
    assert len(aligned) >= 1

    # Extract words
    all_words = [w for s in aligned for w in s.get("words", [])]
    assert len(all_words) == 3

    # Word 1: strictly Speaker 1
    assert all_words[0]["speaker"] == "Speaker 1"
    assert len(all_words[0]["candidate_speakers"]) == 1
    assert all_words[0]["candidate_speakers"][0]["support_score"] == 1.0

    # Word 2: overlaps both Speaker 1 and Speaker 2
    assert "Speaker 1" in all_words[1]["overlapping_speakers"]
    assert "Speaker 2" in all_words[1]["overlapping_speakers"]
    candidates = {c["speaker"]: c["support_score"] for c in all_words[1]["candidate_speakers"]}
    assert "Speaker 1" in candidates
    assert "Speaker 2" in candidates
    assert candidates["Speaker 1"] > 0.0
    assert candidates["Speaker 2"] > 0.0


def test_bounded_continuity_guards_small_pauses():
    """Verifies that small pauses (<=0.25s) with prior speaker continuity

    maintain the active speaker when no competing speaker is present,
    while gaps >0.25s remain Unknown.
    """
    diar_intervals = [
        (0.0, 1.0, "Speaker 1"),
        # Gap from 1.0 to 1.20 (0.20s <= 0.25s) -> Bounded continuity applies
        # Gap from 1.20 to 2.0 (no diarization) -> Gaps > 0.25s stay Unknown
        (3.0, 4.0, "Speaker 2"),
    ]
    segments = [
        {
            "start": 0.0,
            "end": 4.0,
            "words": [
                {"word": "First", "start": 0.2, "end": 0.8},  # inside interval
                {"word": "bridge", "start": 1.10, "end": 1.20},  # gap = 0.10s <= 0.25s
                {"word": "unknown", "start": 1.60, "end": 1.90},  # gap = 0.60s > 0.25s
                {"word": "second", "start": 3.2, "end": 3.8},  # inside interval
            ],
        }
    ]

    aligned = align_segments_with_diarization(segments, diar_intervals)
    all_words = [w for s in aligned for w in s.get("words", [])]
    assert len(all_words) == 4

    assert all_words[0]["speaker"] == "Speaker 1"
    # Word in small gap bridged via bounded continuity
    assert all_words[1]["speaker"] == "Speaker 1"
    assert any(c.get("continuity_supported") for c in all_words[1]["candidate_speakers"])

    # Word in large gap without interval coverage MUST remain Unknown (no bulk-filling)
    assert all_words[2]["speaker"] == "Unknown"

    assert all_words[3]["speaker"] == "Speaker 2"


def test_uncertainty_boundary_vs_confirmed_speaker_change():
    """Acceptance criterion: Distinguish uncertainty boundaries from confirmed speaker changes.

    Transitions to/from Unknown emit 'uncertainty_boundary'.
    Transitions between confirmed speakers emit 'speaker_change'.
    """
    elements = [
        {
            "words": [
                {"word": "Confirmed", "start": 0.0, "end": 0.5, "speaker": "Speaker 1"},
                {"word": " speech.", "start": 0.6, "end": 1.0, "speaker": "Speaker 1"},
            ]
        },
        {
            "words": [
                {"word": "Uncertain", "start": 1.1, "end": 1.5, "speaker": "Unknown"},
                {"word": " whisper.", "start": 1.6, "end": 2.0, "speaker": "Unknown"},
            ]
        },
        {
            "words": [
                {"word": "Second", "start": 2.1, "end": 2.5, "speaker": "Speaker 2"},
                {"word": " voice.", "start": 2.6, "end": 3.0, "speaker": "Speaker 2"},
            ]
        },
    ]

    assembler = UtteranceAssembler()
    utts = assembler.assemble(elements)

    assert len(utts) == 3
    assert utts[0].speaker == "Speaker 1"
    assert utts[1].speaker == "Unknown"
    assert utts[2].speaker == "Speaker 2"

    # Speaker 1 -> Unknown boundary is uncertainty_boundary
    assert "uncertainty_boundary" in utts[0].boundary_reasons

    # Unknown -> Speaker 2 boundary is uncertainty_boundary
    assert "uncertainty_boundary" in utts[1].boundary_reasons


def test_confirmed_speaker_interruption_emits_speaker_change():
    """Transitions between two confirmed distinct speakers emit 'speaker_change'."""
    elements = [
        {
            "words": [
                {"word": "I", "start": 0.0, "end": 0.3, "speaker": "Speaker 1"},
                {"word": " agree.", "start": 0.35, "end": 0.8, "speaker": "Speaker 1"},
            ]
        },
        {
            "words": [
                {"word": "Wait", "start": 0.85, "end": 1.1, "speaker": "Speaker 2"},
                {"word": " what?", "start": 1.15, "end": 1.5, "speaker": "Speaker 2"},
            ]
        },
    ]

    assembler = UtteranceAssembler()
    utts = assembler.assemble(elements)

    assert len(utts) == 2
    assert utts[0].speaker == "Speaker 1"
    assert utts[1].speaker == "Speaker 2"
    assert "speaker_change" in utts[0].boundary_reasons


def test_short_acknowledgment_classification_accuracy():
    """Acceptance criterion: Distinguish genuine conversational acknowledgments

    from grammatical fragments.
    'Yeah', 'Okay', 'Right', 'Got it' -> True
    'I', 'is', 'user', 'the' -> False
    """
    # Genuine acknowledgments
    assert _is_short_acknowledgment("Yeah", 1) is True
    assert _is_short_acknowledgment("yes", 1) is True
    assert _is_short_acknowledgment("Okay.", 1) is True
    assert _is_short_acknowledgment("Right", 1) is True
    assert _is_short_acknowledgment("Got it.", 2) is True
    assert _is_short_acknowledgment("thank you", 2) is True
    assert _is_short_acknowledgment("uh-huh", 1) is True

    # Grammatical fragments (must NOT be marked as acknowledgments)
    assert _is_short_acknowledgment("I", 1) is False
    assert _is_short_acknowledgment("is", 1) is False
    assert _is_short_acknowledgment("user", 1) is False
    assert _is_short_acknowledgment("the", 1) is False
    assert _is_short_acknowledgment("we need", 2) is False
    assert _is_short_acknowledgment("approval", 1) is False


def test_100pct_word_conservation_and_order_preservation():
    """Acceptance criterion: 100% of input words, IDs, and order survive assembly."""
    words = [
        {"word": f"word_{i}", "start": float(i), "end": float(i) + 0.5, "speaker": "Speaker 1"}
        for i in range(25)
    ]
    assembler = UtteranceAssembler()
    utts = assembler.assemble([{"words": words}])

    # Collect all words across utterances
    assembled_word_ids: List[str] = []
    assembled_tokens: List[str] = []
    for u in utts:
        assembled_word_ids.extend(u.word_ids)
        assembled_tokens.extend(u.text.split())

    assert len(assembled_word_ids) == 25
    assert len(assembled_tokens) == 25
    for i in range(25):
        assert assembled_tokens[i] == f"word_{i}"


def test_words_dictionary_retained_in_utterance_and_structured_turns():
    """Acceptance criterion: Full word dictionaries survive in SpeakerUtterance

    and BaseAudioTranscriber._build_structured_turns.
    """
    raw_words = [
        {"word": "Testing", "start": 1.0, "end": 1.5, "speaker": "Speaker 1", "channel": 0},
        {"word": " word", "start": 1.6, "end": 2.0, "speaker": "Speaker 1", "channel": 0},
        {"word": " retention.", "start": 2.1, "end": 2.8, "speaker": "Speaker 1", "channel": 0},
    ]

    assembler = UtteranceAssembler()
    utts = assembler.assemble([{"words": raw_words}])
    assert len(utts) == 1

    # Check SpeakerUtterance.words
    assert len(utts[0].words) == 3
    assert utts[0].words[0]["word"] == "Testing"
    assert utts[0].words[1]["word"] == " word"

    # Check to_dict()
    utt_dict = utts[0].to_dict()
    assert "words" in utt_dict
    assert len(utt_dict["words"]) == 3

    # Check BaseAudioTranscriber._build_structured_turns
    transcriber = BaseAudioTranscriber(mock=True)
    structured = transcriber._build_structured_turns([utt_dict])
    assert len(structured) == 1
    assert "words" in structured[0]
    assert len(structured[0]["words"]) == 3
    assert structured[0]["words"][0]["word"] == "Testing"


def test_prf_47_58s_rapid_turn_window_coherence():
    """Acceptance criterion: 47-58s window on PRF recording no longer produces

    avoidable grammatical fragments or collapses into fake acknowledgments.
    Simulates the rapid speech pattern observed in the RCA around 47-58s.
    """
    # Raw words mimicking the rapid turns at 47-58s
    words_window = [
        {"word": "So", "start": 47.0, "end": 47.2, "speaker": "Speaker 1"},
        {"word": " the", "start": 47.25, "end": 47.4, "speaker": "Speaker 1"},
        {"word": " architecture", "start": 47.45, "end": 48.0, "speaker": "Speaker 1"},
        {"word": " is", "start": 48.05, "end": 48.2, "speaker": "Speaker 1"},
        {"word": " ready.", "start": 48.25, "end": 48.7, "speaker": "Speaker 1"},
        # Rapid confirmation
        {"word": "Yeah.", "start": 49.0, "end": 49.3, "speaker": "Speaker 2"},
        # Continuation by Speaker 1 with small pause
        {"word": "And", "start": 49.5, "end": 49.7, "speaker": "Speaker 1"},
        {"word": " we", "start": 49.75, "end": 49.9, "speaker": "Speaker 1"},
        {"word": " can", "start": 49.95, "end": 50.1, "speaker": "Speaker 1"},
        {"word": " proceed", "start": 50.15, "end": 50.6, "speaker": "Speaker 1"},
        {"word": " now.", "start": 50.65, "end": 51.0, "speaker": "Speaker 1"},
    ]

    assembler = UtteranceAssembler()
    utts = assembler.assemble([{"words": words_window}])

    # Should form exactly 3 coherent complete thoughts:
    # 1. Speaker 1: "So the architecture is ready."
    # 2. Speaker 2: "Yeah." (short ack = True)
    # 3. Speaker 1: "And we can proceed now."
    assert len(utts) == 3

    assert utts[0].speaker == "Speaker 1"
    assert utts[0].text == "So the architecture is ready."
    assert utts[0].is_short_acknowledgment is False  # Not an ack

    assert utts[1].speaker == "Speaker 2"
    assert utts[1].text == "Yeah."
    assert utts[1].is_short_acknowledgment is True  # Genuine ack

    assert utts[2].speaker == "Speaker 1"
    assert utts[2].text == "And we can proceed now."
    assert utts[2].is_short_acknowledgment is False
