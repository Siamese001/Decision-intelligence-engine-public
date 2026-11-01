"""
tests/live_meeting/test_chat_attribution_collector.py

Wave 2: Unit tests for ChatAttributionCollector and InlineChatAttribution parsing.
Verifies regex pattern extraction for inline quotes, speaker hints, absences, and multi-clause messages.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)


@pytest.fixture
def sample_attendees() -> list[str]:
    return [
        "Alex Rivera",
        "Marcus Vance",
        "Anece Elakkari",
        "Will Booth",
        "John Snipes",
        "Peter Ashley",
        "Amit Ayer",
    ]


class TestChatAttributionCollector:
    """Wave 2 Test Suite: Inline Chat Attribution Parsing and Collection."""

    def test_quote_to_speaker_pattern(self, sample_attendees: list[str]):
        """Test 'We feel there is value' = Alex Rivera."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        results = collector.parse_message('"We feel there is value" = Alex Rivera', elapsed_seconds=120.5)

        assert len(results) == 1
        att = results[0]
        assert att.attribution_type == "quote_to_speaker"
        assert att.speaker_name == "Alex Rivera"
        assert att.quoted_text == "We feel there is value"
        assert att.session_elapsed_seconds == 120.5
        assert att.confirmation_status == "PENDING"

    def test_speaker_to_quote_pattern(self, sample_attendees: list[str]):
        """Test Alex Rivera = 'I guess the direction that I gave..'."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        results = collector.parse_message('Alex Rivera = "I guess the direction that I gave.."', elapsed_seconds=300.0)

        assert len(results) == 1
        att = results[0]
        assert att.attribution_type == "quote_to_speaker"
        assert att.speaker_name == "Alex Rivera"
        assert att.quoted_text == "I guess the direction that I gave.."

    def test_absence_declaration(self, sample_attendees: list[str]):
        """Test Peter Ashley not on call."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        results = collector.parse_message("Peter Ashley not on call", elapsed_seconds=10.0)

        assert len(results) == 1
        att = results[0]
        assert att.attribution_type == "absence"
        assert att.speaker_name == "Peter Ashley"
        assert att.quoted_text is None

    def test_active_speaker_declaration(self, sample_attendees: list[str]):
        """Test Marcus Vance speaking now and aaron screen now."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        res1 = collector.parse_message("Marcus Vance speaking now", elapsed_seconds=45.0)
        assert len(res1) == 1
        assert res1[0].attribution_type == "active_speaker"
        assert res1[0].speaker_name == "Marcus Vance"

        res2 = collector.parse_message("aaron screen now", elapsed_seconds=50.0)
        assert len(res2) == 1
        assert res2[0].attribution_type == "active_speaker"
        assert res2[0].speaker_name == "Marcus Vance"

    def test_multi_clause_message_parsing(self, sample_attendees: list[str]):
        """Test multi-statement message with semicolon and newline separation."""
        msg = (
            '"I guess the direction that I gave.." = Alex Rivera;\n'
            'Anece = "What are the data sources"'
        )
        collector = ChatAttributionCollector(attendees=sample_attendees)
        results = collector.parse_message(msg, elapsed_seconds=600.0)

        assert len(results) == 2
        assert results[0].speaker_name == "Alex Rivera"
        assert results[0].quoted_text == "I guess the direction that I gave.."
        assert results[1].speaker_name == "Anece Elakkari"
        assert results[1].quoted_text == "What are the data sources"

    def test_case_insensitive_and_fuzzy_typo_resolution(self, sample_attendees: list[str]):
        """Test matching with mid-word typo like 'Alex Rivera' and first-name 'Anece'."""
        collector = ChatAttributionCollector(attendees=sample_attendees)

        # Typo: SImmons
        res1 = collector.parse_message('"We feel there is value" = Alex Rivera')
        assert len(res1) == 1
        assert res1[0].speaker_name == "Alex Rivera"

        # First name only: Anece
        res2 = collector.parse_message('"This is a foundational Piece" = Anece')
        assert len(res2) == 1
        assert res2[0].speaker_name == "Anece Elakkari"

    def test_unquoted_claim_attribution(self, sample_attendees: list[str]):
        """Test John Snipes = tech systems across opcos."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        results = collector.parse_message("John Snipes = tech systems across opcos", elapsed_seconds=800.0)

        assert len(results) == 1
        att = results[0]
        assert att.speaker_name == "John Snipes"
        assert att.quoted_text == "tech systems across opcos"

    def test_non_attribution_messages_ignored(self, sample_attendees: list[str]):
        """Regular chat messages without attribution patterns are ignored."""
        collector = ChatAttributionCollector(attendees=sample_attendees)
        assert collector.parse_message("looks great") == []
        assert collector.parse_message("take a screenshot please") == []
        assert collector.parse_message("okay sounds good") == []

    def test_persistence_save_and_load(self, sample_attendees: list[str], tmp_path: Path):
        """Test saving and reloading collected attributions."""
        out_file = tmp_path / "inline_chat_attributions.json"
        collector = ChatAttributionCollector(attendees=sample_attendees, output_path=out_file)

        collector.record_attribution('"We feel there is value" = Alex Rivera', elapsed_seconds=100.0)
        collector.record_attribution('Peter Ashley not on call', elapsed_seconds=105.0)

        assert out_file.exists()
        reloaded = ChatAttributionCollector(attendees=sample_attendees, output_path=out_file)
        loaded_items = reloaded.load()

        assert len(loaded_items) == 2
        assert loaded_items[0].speaker_name == "Alex Rivera"
        assert loaded_items[0].quoted_text == "We feel there is value"
        assert loaded_items[1].speaker_name == "Peter Ashley"
        assert loaded_items[1].attribution_type == "absence"
        assert len(reloaded.pending_attributions) == 2
        assert len(reloaded.confirmed_attributions) == 0
