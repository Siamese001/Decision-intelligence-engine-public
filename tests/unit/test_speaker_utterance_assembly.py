"""Unit tests for Complete-Thought Speaker Utterance Assembly.

Validates Wave 3 Acceptance Criteria:
1. Two same-speaker segments ("We" + "need approval.") produce one coherent utterance.
2. An intervening confirmed turn ("No") from another speaker acts as a hard merge barrier.
3. Configured gap thresholds immediately below/at/above:
   - max_bridge_gap_sec (0.8s)
   - punctuation_pause_sec (0.25s)
   - force_new_block_pause_sec (1.5s)
4. Long monologue capacity caps split at valid clause boundaries without dropping words.
5. Short acknowledgments (<= 2 words) preserved with is_short_acknowledgment=True.
6. Overlap context and genuine 'Unknown' identity preservation.
7. Strict word conservation: 100% of input words appear in exactly one utterance.
8. JSON Schema Draft 2020-12 validation against speaker_utterance.schema.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import jsonschema
import pytest

from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    align_segments_with_diarization,
    extract_acoustic_intervals,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import (
    AssemblyConfig,
    UtteranceAssembler,
    assemble_utterances,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = ROOT_DIR / "contracts/dual_pipeline/speaker_utterance.schema.json"


@pytest.fixture
def utterance_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_two_same_speaker_segments_produce_one_utterance_interruption_splits():
    """
    Acceptance Criteria 1 & 2:
    - Same speaker saying 'We' and 'need approval.' bridges into one utterance.
    - An intervening confirmed 'No' from another speaker stays a separate turn.
    """
    config = AssemblyConfig(max_bridge_gap_sec=0.8)
    assembler = UtteranceAssembler(config=config)

    # Case A: Same speaker without interruption
    same_speaker_elements = [
        {
            "start": 1.0,
            "end": 1.3,
            "speaker": "Speaker 1",
            "words": [{"word": "We", "start": 1.0, "end": 1.3}],
        },
        {
            "start": 1.5,
            "end": 2.5,
            "speaker": "Speaker 1",
            "words": [
                {"word": " need", "start": 1.5, "end": 1.8},
                {"word": " approval.", "start": 1.9, "end": 2.5},
            ],
        },
    ]

    assembled_a = assembler.assemble(same_speaker_elements)
    assert len(assembled_a) == 1, "Two same-speaker segments must be grouped into one complete thought"
    assert assembled_a[0].speaker == "Speaker 1"
    assert assembled_a[0].text == "We need approval."
    assert assembled_a[0].start_time_sec == 1.0
    assert assembled_a[0].end_time_sec == 2.5
    assert len(assembled_a[0].word_ids) == 3

    # Case B: Intervening confirmed 'No' from Speaker 2
    interrupted_elements = [
        {
            "start": 1.0,
            "end": 1.3,
            "speaker": "Speaker 1",
            "words": [{"word": "We", "start": 1.0, "end": 1.3}],
        },
        {
            "start": 1.4,
            "end": 1.7,
            "speaker": "Speaker 2",
            "words": [{"word": "No.", "start": 1.4, "end": 1.7}],
        },
        {
            "start": 1.8,
            "end": 2.8,
            "speaker": "Speaker 1",
            "words": [
                {"word": " need", "start": 1.8, "end": 2.1},
                {"word": " approval.", "start": 2.2, "end": 2.8},
            ],
        },
    ]

    assembled_b = assembler.assemble(interrupted_elements)
    assert len(assembled_b) == 3, "Intervening speaker turn must act as a hard merge barrier"
    assert assembled_b[0].speaker == "Speaker 1"
    assert assembled_b[0].text == "We"
    assert assembled_b[1].speaker == "Speaker 2"
    assert assembled_b[1].text == "No."
    assert assembled_b[1].is_short_acknowledgment is True
    assert assembled_b[2].speaker == "Speaker 1"
    assert assembled_b[2].text == "need approval."


def test_gap_threshold_boundary_behavior():
    """
    Tests gap values immediately below, at, and above configured thresholds:
    - max_bridge_gap_sec (0.8s)
    - punctuation_pause_sec (0.25s)
    - force_new_block_pause_sec (1.5s)
    """
    config = AssemblyConfig(
        max_bridge_gap_sec=0.8,
        punctuation_pause_sec=0.25,
        force_new_block_pause_sec=1.5,
    )
    assembler = UtteranceAssembler(config=config)

    # 1. max_bridge_gap_sec tests (no punctuation)
    # Gap = 0.79s -> Bridge
    w_below = [
        {"word": "First", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " second", "start": 1.79, "end": 2.5, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_below}])
    assert len(res) == 1, "Gap of 0.79s <= 0.8s should bridge"

    # Gap = 0.80s -> Bridge
    w_at = [
        {"word": "First", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " second", "start": 1.80, "end": 2.5, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_at}])
    assert len(res) == 1, "Gap of 0.80s <= 0.8s should bridge"

    # Gap = 0.85s -> Split (pause_exceeded)
    w_above = [
        {"word": "First", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " second", "start": 1.85, "end": 2.5, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_above}])
    assert len(res) == 2, "Gap of 0.85s > 0.8s should split"
    assert "pause_exceeded" in res[0].boundary_reasons

    # 2. punctuation_pause_sec tests (terminal punctuation '.')
    # Gap = 0.20s (< 0.25s) with terminal punct -> Bridge (e.g. abbreviation or rapid thought continuation)
    w_punct_below = [
        {"word": "Done.", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " Next", "start": 1.20, "end": 2.0, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_punct_below}])
    assert len(res) == 1, "Punctuation pause 0.20s < 0.25s should bridge rapid continuation"

    # Gap = 0.25s (== 0.25s) with terminal punct -> Split (clause_boundary)
    w_punct_at = [
        {"word": "Done.", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " Next", "start": 1.25, "end": 2.0, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_punct_at}])
    assert len(res) == 2, "Punctuation pause 0.25s >= 0.25s should split"
    assert "clause_boundary" in res[0].boundary_reasons

    # 3. force_new_block_pause_sec tests
    # Gap = 1.55s -> Split (pause_exceeded)
    w_force = [
        {"word": "Hello", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
        {"word": " world", "start": 2.55, "end": 3.0, "speaker": "Speaker 1"},
    ]
    res = assembler.assemble([{"words": w_force}])
    assert len(res) == 2, "Extended pause >= 1.5s must force a new block"
    assert "pause_exceeded" in res[0].boundary_reasons


def test_capacity_caps_split_at_valid_clause_boundary():
    """
    Tests that reaching max_words_per_utterance or max_duration_sec splits
    at the latest clause boundary without dropping words or fabricating ends.
    """
    config = AssemblyConfig(
        max_words_per_utterance=10,
        max_duration_sec=30.0,
        max_bridge_gap_sec=1.0,
    )
    assembler = UtteranceAssembler(config=config)

    # 14 words with a comma after word 6
    text_tokens = [
        ("We", 0.0, 0.3),
        ("reviewed", 0.35, 0.7),
        ("the", 0.75, 0.9),
        ("architecture", 0.95, 1.5),
        ("yesterday,", 1.55, 2.0),  # Clause punctuation
        ("and", 2.05, 2.3),
        ("the", 2.35, 2.5),
        ("team", 2.55, 2.8),
        ("decided", 2.85, 3.2),
        ("to", 3.25, 3.4),
        ("proceed", 3.45, 3.8),
        ("with", 3.85, 4.0),
        ("the", 4.05, 4.2),
        ("plan.", 4.25, 4.6),
    ]

    words = [
        {"word": f" {t[0]}" if i > 0 else t[0], "start": t[1], "end": t[2], "speaker": "Speaker 1"}
        for i, t in enumerate(text_tokens)
    ]

    utts = assembler.assemble([{"words": words}])
    assert len(utts) == 2, "14 words with cap of 10 must split into 2 utterances"
    assert utts[0].text == "We reviewed the architecture yesterday,"
    assert utts[1].text == "and the team decided to proceed with the plan."

    # Verify zero dropped words
    total_words_in = len(text_tokens)
    total_words_out = len(utts[0].word_ids) + len(utts[1].word_ids)
    assert total_words_in == total_words_out, "Zero words dropped during clause splitting"


def test_short_acknowledgments_and_unknown_identity_retention():
    """
    Verifies that genuine short acknowledgments (e.g. 'Yeah', 'Right', 'Okay')
    and 'Unknown' speakers are preserved without silent relabeling or pruning.
    """
    assembler = UtteranceAssembler()
    elements = [
        {
            "start": 0.0,
            "end": 0.5,
            "speaker": "Speaker 1",
            "words": [{"word": "Right.", "start": 0.0, "end": 0.5}],
        },
        {
            "start": 1.0,
            "end": 2.5,
            "speaker": "Unknown",
            "words": [
                {"word": "Acoustic", "start": 1.0, "end": 1.5},
                {"word": " bleed.", "start": 1.6, "end": 2.5},
            ],
        },
        {
            "start": 3.0,
            "end": 3.4,
            "speaker": "Speaker 2",
            "words": [{"word": "Got it.", "start": 3.0, "end": 3.4}],
        },
    ]

    utts = assembler.assemble(elements)
    assert len(utts) == 3
    assert utts[0].is_short_acknowledgment is True
    assert utts[0].text == "Right."
    assert utts[1].speaker == "Unknown", "Unknown speaker cluster must NOT be silently relabeled"
    assert utts[2].is_short_acknowledgment is True
    assert utts[2].text == "Got it."


def test_overlap_context_detection_and_multi_speaker_tracking():
    """
    Verifies that true overlaps are flagged and active speakers tracked
    without inventing duplicated word streams.
    """
    diar_tracks = [
        (0.0, 4.0, "Speaker 1"),
        (2.0, 5.0, "Speaker 2"),  # Overlap between 2.0 and 4.0
    ]

    sanitized_segments = [
        {
            "start": 0.0,
            "end": 4.5,
            "words": [
                {"word": "Before", "start": 0.5, "end": 1.0},
                {"word": " during", "start": 2.5, "end": 3.5},  # In overlap zone!
                {"word": " after", "start": 4.1, "end": 4.5},
            ],
        }
    ]

    aligned = align_segments_with_diarization(sanitized_segments, diar_tracks)
    assembler = UtteranceAssembler()
    utts = assembler.assemble(aligned)

    assert len(utts) >= 1
    overlap_utts = [u for u in utts if u.overlap_context]
    assert len(overlap_utts) >= 1, "Utterance spanning overlap zone must have overlap_context=True"


def test_schema_conformance(utterance_schema: Dict[str, Any]):
    """
    Validates that assembled utterances conform strictly to speaker_utterance.schema.json.
    """
    validator = jsonschema.Draft202012Validator(utterance_schema)
    sample_segments = [
        {
            "speaker": "Speaker 1",
            "start": 0.0,
            "end": 3.0,
            "words": [
                {"word": "This", "start": 0.0, "end": 0.5},
                {"word": " is", "start": 0.55, "end": 0.9},
                {"word": " a", "start": 0.95, "end": 1.1},
                {"word": " test.", "start": 1.15, "end": 1.8},
            ],
        },
        {
            "speaker": "Speaker 2",
            "start": 2.5,
            "end": 3.0,
            "words": [{"word": "Okay.", "start": 2.5, "end": 3.0}],
        },
    ]

    dict_utts = assemble_utterances(sample_segments)

    # Validate each utterance against $defs/speakerUtterance
    utt_subschema = utterance_schema["$defs"]["speakerUtterance"]
    sub_validator = jsonschema.Draft202012Validator(utt_subschema)

    for u in dict_utts:
        sub_validator.validate(u)


def test_duplicate_recognition_vs_genuine_repetition():
    """
    Verifies that:
    1. Duplicate recognition of the same audio event across overlapping windows is collapsed.
    2. Genuine human repetitions (e.g. 'wait, wait') with disjoint timestamps are preserved.
    """
    assembler = UtteranceAssembler()

    # Case A: Overlapping recognition window artifact (same audio event)
    overlapping_window_elements = [
        # Window 1 output
        {
            "start": 28.0,
            "end": 29.6,
            "speaker": "Speaker 1",
            "words": [
                {"word": "need", "start": 28.2, "end": 28.8},
                {"word": " approval", "start": 29.0, "end": 29.5},
            ],
        },
        # Window 2 output (repeats 'approval' from overlap buffer at same audio time)
        {
            "start": 29.0,
            "end": 31.0,
            "speaker": "Speaker 1",
            "words": [
                {"word": "approval", "start": 29.05, "end": 29.52},
                {"word": " today", "start": 29.8, "end": 30.3},
            ],
        },
    ]

    utts_a = assembler.assemble(overlapping_window_elements)
    assert len(utts_a) == 1
    # 'approval' must appear once, NOT duplicated
    assert utts_a[0].text == "need approval today"

    # Case B: Genuine human repetition (sequential timestamps, disjoint intervals)
    genuine_repetition_elements = [
        {
            "start": 5.0,
            "end": 6.5,
            "speaker": "Speaker 1",
            "words": [
                {"word": "Wait,", "start": 5.0, "end": 5.4},
                {"word": " wait,", "start": 5.6, "end": 6.0},
                {"word": " hold", "start": 6.1, "end": 6.3},
                {"word": " on.", "start": 6.35, "end": 6.7},
            ],
        }
    ]

    utts_b = assembler.assemble(genuine_repetition_elements)
    assert len(utts_b) == 1
    # Both 'Wait,' and 'wait,' must survive because they are distinct audio events
    assert utts_b[0].text == "Wait, wait, hold on."


def test_speech_ending_mid_sentence_without_punctuation():
    """
    Verifies that speech ending mid-sentence without terminal punctuation
    is preserved intact without dropped words or fabricated punctuation.
    """
    assembler = UtteranceAssembler()
    unpunctuated_elements = [
        {
            "start": 10.0,
            "end": 12.0,
            "speaker": "Speaker 1",
            "words": [
                {"word": "I", "start": 10.0, "end": 10.2},
                {"word": " think", "start": 10.3, "end": 10.6},
                {"word": " that", "start": 10.7, "end": 10.9},
                {"word": " we", "start": 11.0, "end": 11.2},
                {"word": " should", "start": 11.3, "end": 11.6},
            ],
        }
    ]

    utts = assembler.assemble(unpunctuated_elements)
    assert len(utts) == 1
    assert utts[0].text == "I think that we should"
    assert len(utts[0].word_ids) == 5
    assert not utts[0].text.endswith((".", "!", "?"))


def test_cross_window_sentence_fragments_merge():
    """
    Verifies that sentence fragments crossing a 30s recognition window boundary
    merge into one coherent utterance when spoken by the same speaker within pause limits.
    """
    assembler = UtteranceAssembler()
    # Seam at 30.0s (e.g. from ref_clip_seam_00m30s.wav)
    # Window 1 ends mid-sentence at 29.8s
    window_1 = {
        "start": 27.0,
        "end": 30.0,
        "speaker": "Jake Stark",
        "words": [
            {"word": "Yeah,", "start": 27.5, "end": 28.0},
            {"word": " that", "start": 28.1, "end": 28.4},
            {"word": " makes", "start": 28.5, "end": 28.9},
            {"word": " sense", "start": 29.0, "end": 29.4},
            {"word": " and", "start": 29.5, "end": 29.8},
        ],
    }
    # Window 2 continues the thought across the seam
    window_2 = {
        "start": 30.0,
        "end": 33.0,
        "speaker": "Jake Stark",
        "words": [
            {"word": "we", "start": 30.1, "end": 30.4},
            {"word": " can", "start": 30.5, "end": 30.8},
            {"word": " jump", "start": 30.9, "end": 31.2},
            {"word": " on", "start": 31.3, "end": 31.5},
            {"word": " after", "start": 31.6, "end": 31.9},
            {"word": " this.", "start": 32.0, "end": 32.5},
        ],
    }

    utts = assembler.assemble([window_1, window_2])
    assert len(utts) == 1, "Cross-window thought must assemble into single utterance"
    assert utts[0].speaker == "Jake Stark"
    assert utts[0].text == "Yeah, that makes sense and we can jump on after this."
    assert utts[0].start_time_sec == 27.5
    assert utts[0].end_time_sec == 32.5
    assert len(utts[0].word_ids) == 11


def test_100pct_word_conservation_and_order():
    """
    Verifies 100% word conservation and exact sequential ordering:
    Every input word appears in the output utterances in the exact same sequence.
    """
    assembler = UtteranceAssembler()
    raw_tokens = [
        ("Good", "Speaker 1", 0.0, 0.4),
        ("morning", "Speaker 1", 0.45, 0.9),
        ("everyone.", "Speaker 1", 0.95, 1.4),
        ("Morning.", "Speaker 2", 1.6, 2.0),
        ("Let's", "Speaker 1", 2.2, 2.5),
        ("review", "Speaker 1", 2.55, 2.9),
        ("the", "Speaker 1", 2.95, 3.1),
        ("status.", "Speaker 1", 3.15, 3.6),
    ]

    elements = [
        {
            "start": t[2],
            "end": t[3],
            "speaker": t[1],
            "words": [{"word": t[0], "start": t[2], "end": t[3], "speaker": t[1]}],
        }
        for t in raw_tokens
    ]

    utts = assembler.assemble(elements)

    output_words = []
    for u in utts:
        output_words.extend(u.text.split())

    input_words = [t[0].strip() for t in raw_tokens]
    assert output_words == input_words, "Word order and text must be 100% conserved"
    assert sum(len(u.word_ids) for u in utts) == len(raw_tokens)

