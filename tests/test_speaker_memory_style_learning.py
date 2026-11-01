"""
tests/test_speaker_memory_style_learning.py

Wave 1 verification tests for SpeakerMemory speaking habits and style learning.
Tests:
- Automatic learning of opener phrases, common vocabulary, turn length, and interlocutors.
- Backward compatibility loading profiles without style fields.
- match_speaker_style_score calculation.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.speaker_memory import SpeakerMemory, SpeakerProfile


def test_backward_compatibility_loading_legacy_profiles():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_file = Path(tmpdir) / "legacy_memory.json"
        legacy_data = {
            "schema_version": "1.0.0",
            "profiles": {
                "alex": {
                    "name": "Alex",
                    "person_id": "person:alex",
                    "organization": "Partner Corp",
                    "role": "Lead Architect",
                    "domain_topics": ["cloud", "storage"],
                    "confirmed_turn_count": 5,
                }
            },
        }
        mem_file.write_text(json.dumps(legacy_data))

        mem = SpeakerMemory(memory_path=mem_file)
        profile = mem.get_profile("alex")

        assert profile is not None
        assert profile.name == "Alex"
        assert profile.opener_phrases == []
        assert profile.common_vocabulary == {}
        assert profile.avg_words_per_turn == 0.0
        assert profile.frequent_interlocutors == {}


def test_learn_speaking_style_and_turn_pace():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_file = Path(tmpdir) / "test_memory.json"
        mem = SpeakerMemory(memory_path=mem_file)

        # 1. First turn: 10 words, opener 'look', partner 'Bob'
        utt1 = "Look, we need to inspect the database schema carefully today."
        p1 = mem.record_attribution("Alice", utt1, interlocutor="Bob", persist=True)

        assert p1.confirmed_turn_count == 1
        assert p1.avg_words_per_turn == 10.0
        assert "look" in p1.opener_phrases
        assert p1.frequent_interlocutors.get("Bob") == 1
        assert p1.common_vocabulary.get("database") == 1
        assert p1.common_vocabulary.get("schema") == 1

        # 2. Second turn: 6 words, partner 'Bob'
        utt2 = "Right, let us deploy the service."
        p2 = mem.record_attribution("Alice", utt2, interlocutor="Bob", persist=True)

        assert p2.confirmed_turn_count == 2
        # (10 + 6) / 2 = 8.0 words per turn
        assert p2.avg_words_per_turn == 8.0
        assert "right" in p2.opener_phrases
        assert p2.frequent_interlocutors.get("Bob") == 2

        # 3. Third turn from someone else as interlocutor
        utt3 = "Quick question, does this pass security checks?"
        p3 = mem.record_attribution("Alice", utt3, interlocutor="Charlie", persist=True)

        assert p3.confirmed_turn_count == 3
        assert p3.frequent_interlocutors.get("Charlie") == 1
        assert p3.frequent_interlocutors.get("Bob") == 2
        assert "quick question" in p3.opener_phrases

        # 4. Verify re-loaded memory matches disk
        mem_reloaded = SpeakerMemory(memory_path=mem_file)
        p_reloaded = mem_reloaded.get_profile("Alice")
        assert p_reloaded is not None
        assert p_reloaded.avg_words_per_turn == p3.avg_words_per_turn
        assert set(p_reloaded.opener_phrases) == set(p3.opener_phrases)
        assert p_reloaded.frequent_interlocutors == p3.frequent_interlocutors


def test_match_speaker_style_score():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_file = Path(tmpdir) / "style_score_memory.json"
        mem = SpeakerMemory(memory_path=mem_file)

        # Train profile
        mem.record_attribution(
            "Dave",
            "From an engineering standpoint, the architecture must maintain low latency.",
            interlocutor="Elena",
            persist=True,
        )

        # Test matching utterance from Dave with matching opener and partner
        test_utt = "From an engineering standpoint, we have architecture questions."
        scores = mem.match_speaker_style_score("Dave", test_utt, prev_speaker="Elena")

        assert scores["opener"] == 0.15
        assert scores["interlocutor"] == 0.15
        assert scores["vocab"] > 0.0
        assert scores["total"] >= 0.35

        # Test non-matching utterance
        no_match_scores = mem.match_speaker_style_score("Dave", "Hi there.", prev_speaker="Zack")
        assert no_match_scores["opener"] == 0.0
        assert no_match_scores["interlocutor"] == 0.0
