"""
tests/test_whisper_priming_and_vocabulary_d2.py

Unit tests for Wave 2: MLX Whisper dynamic domain priming and
Domain 2 Vocabulary Detector expansion for AI/ML models (e.g. 'Chachi Bikki' -> 'ChatGPT').
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.ambiguity_core.detector_d2_vocabulary import (
    DEFAULT_GLOSSARY,
    VocabularyAcronymDetector,
)
from dual_pipeline.ambiguity_core.models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_ambiguity_margin_threshold_is_30_percent():
    assert AMBIGUITY_MARGIN_THRESHOLD == 0.30


def test_default_glossary_contains_ai_models():
    assert "ChatGPT" in DEFAULT_GLOSSARY
    assert "GPT-4" in DEFAULT_GLOSSARY
    assert "Claude" in DEFAULT_GLOSSARY
    assert "Opus" in DEFAULT_GLOSSARY
    assert "CHACHI BIKKI" in DEFAULT_GLOSSARY["ChatGPT"]
    assert "CHACHI" in DEFAULT_GLOSSARY["ChatGPT"]


def test_d2_detector_flags_chachi_bikki():
    detector = VocabularyAcronymDetector()
    turns = [
        {
            "speaker": "Jake Stark",
            "text": "One worked better than the other. Like, Chachi Bikki was just like, oh, this is the manufacturer.",
            "start_ms": 456000,
            "end_ms": 464000,
        },
        {
            "speaker": "Jake Stark",
            "text": "I don't remember exactly the model. It's Chachi because later, one of their latest models.",
            "start_ms": 2184000,
            "end_ms": 2192000,
        },
    ]

    records = detector.detect(turns)
    assert len(records) >= 2

    # Verify first turn detected Chachi Bikki -> ChatGPT
    rec1 = records[0]
    assert rec1.domain == AmbiguityDomain.D2_DOMAIN_VOCABULARY
    assert any(c.text == "ChatGPT" for c in rec1.candidates)
    chatgpt_cand = next(c for c in rec1.candidates if c.text == "ChatGPT")
    assert chatgpt_cand.is_recommended is True

    # Verify second turn detected Chachi -> ChatGPT
    rec2 = records[1]
    assert rec2.domain == AmbiguityDomain.D2_DOMAIN_VOCABULARY
    assert any(c.text == "ChatGPT" for c in rec2.candidates)


def test_live_meeting_recorder_whisper_prompt_priming():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        recorder = LiveMeetingRecorder(
            project_name="project_lens",
            meeting_name="Jake_Amit_Miles_PartnerAdvisory",
            attendees=["Amit Ayer (PartnerAdvisory)", "Jake Stark (PartnerAdvisory)"],
            output_dir=tmp_path,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        assert "LENS" in recorder.whisper_prompt
        assert "Single Turbine" in recorder.whisper_prompt
        assert "Amit Ayer" in recorder.whisper_prompt
        assert "Jake Stark" in recorder.whisper_prompt
