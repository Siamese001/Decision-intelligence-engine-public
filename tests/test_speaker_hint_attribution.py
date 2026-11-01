"""
tests/test_speaker_hint_attribution.py

Wave 4 Unit Test Suite: Silent durable speaker hint attribution.
Verifies:
  1. Dash format, bare name format, colon format, inverted quote-to-speaker.
  2. Contractions inside quotes without premature truncation.
  3. Multiline continuation quotes attached to preceding attribution.
  4. Absent speaker rejection.
  5. Unknown timestamp explicit None preservation.
  6. Durable deduplication: each supported hint saves exactly once.
  7. DurableSpeakerHintQueue persistence, status tracking, and loading.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
    InlineChatAttribution,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue,
    QueuedSpeakerHint,
    normalize_hint_text,
)


@pytest.fixture
def sample_roster() -> list[str]:
    return ["Speaker_Alpha", "Speaker_Beta", "Alex Rivera", "John Snipes", "Samantha Vance", "Alex Mercer"]


class TestSpeakerHintAttribution:
    """Wave 4 Unit Tests for Speaker Hint Intake & Normalization."""

    def test_dash_format_parsing(self, sample_roster: list[str]) -> None:
        """Test 'Speaker_Alpha - "Dave real quick, do you recall offhand"'."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg = 'Speaker_Alpha - "Dave real quick, do you recall offhand"'
        results = collector.parse_message(msg)

        assert len(results) == 1
        att = results[0]
        assert att.speaker_name == "Speaker_Alpha"
        assert att.quoted_text == "Dave real quick, do you recall offhand"
        assert att.attribution_type == "quote_to_speaker"
        assert att.session_elapsed_seconds is None

    def test_unicode_dash_variants(self, sample_roster: list[str]) -> None:
        """Test en-dash and em-dash separators."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        for sep in ["–", "—", "-"]:
            msg = f'Speaker_Alpha {sep} "Dave real quick"'
            res = collector.parse_message(msg)
            assert len(res) == 1
            assert res[0].speaker_name == "Speaker_Alpha"
            assert res[0].quoted_text == "Dave real quick"

    def test_bare_name_format_parsing(self, sample_roster: list[str]) -> None:
        """Test 'Speaker_Beta "I will check again on what is going on with the application"'."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg = 'Speaker_Beta "I will check again on what is going on with the application"'
        results = collector.parse_message(msg)

        assert len(results) == 1
        att = results[0]
        assert att.speaker_name == "Speaker_Beta"
        assert att.quoted_text == "I will check again on what is going on with the application"
        assert att.attribution_type == "quote_to_speaker"

    def test_inverted_quote_to_speaker(self, sample_roster: list[str]) -> None:
        """Test '"We feel there is value" - Alex Rivera' and bare inverted."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        res1 = collector.parse_message('"We feel there is value" - Alex Rivera')
        assert len(res1) == 1
        assert res1[0].speaker_name == "Alex Rivera"
        assert res1[0].quoted_text == "We feel there is value"

        res2 = collector.parse_message('"We feel there is value" Alex Rivera')
        assert len(res2) == 1
        assert res2[0].speaker_name == "Alex Rivera"
        assert res2[0].quoted_text == "We feel there is value"

    def test_contractions_inside_double_quotes(self, sample_roster: list[str]) -> None:
        """Quotes containing apostrophes/contractions must not be prematurely truncated."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg = 'Speaker_Alpha - "I don\'t think we\'re ready for the migration today"'
        results = collector.parse_message(msg)

        assert len(results) == 1
        assert results[0].speaker_name == "Speaker_Alpha"
        assert results[0].quoted_text == "I don't think we're ready for the migration today"

    def test_curly_and_unicode_quotes(self, sample_roster: list[str]) -> None:
        """Test smart/curly double and single quotation marks."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg1 = 'Speaker_Beta: “This is a foundational piece”'
        res1 = collector.parse_message(msg1)
        assert len(res1) == 1
        assert res1[0].quoted_text == "This is a foundational piece"

        msg2 = 'Speaker_Alpha – ‘We should align on this’'
        res2 = collector.parse_message(msg2)
        assert len(res2) == 1
        assert res2[0].quoted_text == "We should align on this"

    def test_multiline_continuation_quote(self, sample_roster: list[str]) -> None:
        """
        Multiline continuation quotes must be preserved in metadata without
        creating orphan records or inflating the primary parsed count.
        """
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg = 'Speaker_Beta - "I have a registration workflow"\n"This is directly from the Confluence page"'
        results = collector.parse_message(msg)

        assert len(results) == 1
        att = results[0]
        assert att.speaker_name == "Speaker_Beta"
        assert att.quoted_text == "I have a registration workflow"
        assert att.metadata.get("continuation_quotes") == ["This is directly from the Confluence page"]
        assert "Confluence page" in att.raw_message

    def test_absent_speaker_rejected(self, sample_roster: list[str]) -> None:
        """An isolated quotation without any preceding or accompanying speaker is not parsed as attribution."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        msg = '"This is an orphan quotation with no speaker"'
        results = collector.parse_message(msg)
        assert results == []

    def test_explicit_none_for_unknown_elapsed_seconds(self, sample_roster: list[str]) -> None:
        """Unknown time must be explicit None rather than synthetic 0.0."""
        collector = ChatAttributionCollector(attendees=sample_roster)
        res = collector.parse_message('Speaker_Alpha - "Test quote"')
        assert len(res) == 1
        assert res[0].session_elapsed_seconds is None

        res_with_time = collector.parse_message('Speaker_Alpha - "Test quote 2"', elapsed_seconds=42.5)
        assert len(res_with_time) == 1
        assert res_with_time[0].session_elapsed_seconds == 42.5

    def test_deduplication_saves_once(self, sample_roster: list[str], tmp_path: Path) -> None:
        """Every supported hint saves once; duplicate calls do not create redundant entries."""
        store_path = tmp_path / "inline_chat_attributions.json"
        collector = ChatAttributionCollector(attendees=sample_roster, output_path=store_path)

        msg = 'Alex Rivera = "We feel there is value"'
        first_records = collector.record_attribution(msg, elapsed_seconds=10.0)
        assert len(first_records) == 1
        assert len(collector.attributions) == 1

        # Second submission of identical hint
        second_records = collector.record_attribution(msg, elapsed_seconds=15.0)
        assert second_records == []  # Not added as duplicate
        assert len(collector.attributions) == 1

        # Reload from disk
        reloaded = ChatAttributionCollector(attendees=sample_roster, output_path=store_path)
        reloaded.load()
        assert len(reloaded.attributions) == 1


class TestDurableSpeakerHintQueue:
    """Wave 4 Unit Tests for DurableSpeakerHintQueue."""

    def test_queue_enqueue_and_deduplication(self, tmp_path: Path) -> None:
        """Verify enqueue, persistence, and deduplication behavior."""
        queue = DurableSpeakerHintQueue(cache_dir=tmp_path)
        assert queue.get_pending_hints() == []

        hint1, is_new1 = queue.enqueue_hint(
            speaker="Speaker_Alpha",
            quote="Dave real quick, do you recall offhand",
            elapsed_seconds=None,
        )
        assert is_new1 is True
        assert hint1.status == "QUEUED"
        assert hint1.speaker_name == "Speaker_Alpha"
        assert len(queue.get_pending_hints()) == 1

        # Re-enqueue identical hint
        hint2, is_new2 = queue.enqueue_hint(
            speaker="Speaker_Alpha",
            quote="dave real quick do you recall offhand",  # Different punctuation/case
        )
        assert is_new2 is False
        assert hint2.hint_id == hint1.hint_id
        assert len(queue.get_pending_hints()) == 1

    def test_queue_persistence_and_reload(self, tmp_path: Path) -> None:
        """Verify that queued hints survive process restart via JSON reload."""
        queue1 = DurableSpeakerHintQueue(cache_dir=tmp_path)
        queue1.enqueue_hint(speaker="Speaker_Beta", quote="I have a registration workflow")
        assert queue1.queue_path.is_file()

        queue2 = DurableSpeakerHintQueue(cache_dir=tmp_path)
        pending = queue2.get_pending_hints()
        assert len(pending) == 1
        assert pending[0].speaker_name == "Speaker_Beta"
        assert pending[0].quote == "I have a registration workflow"
        assert pending[0].status == "QUEUED"

    def test_queue_mark_reconciled(self, tmp_path: Path) -> None:
        """Verify marking a hint as reconciled with acoustic voice ID and digest."""
        queue = DurableSpeakerHintQueue(cache_dir=tmp_path)
        hint, _ = queue.enqueue_hint(speaker="Alex Rivera", quote="We feel there is value")

        ok = queue.mark_reconciled(
            hint_id=hint.hint_id,
            voice_id="Voice A",
            updated_segments_count=3,
            ledger_digest="sha256_mock_digest",
        )
        assert ok is True
        assert len(queue.get_pending_hints()) == 0
        assert len(queue.get_reconciled_hints()) == 1

        reconciled = queue.get_reconciled_hints()[0]
        assert reconciled.voice_id == "Voice A"
        assert reconciled.updated_segments_count == 3
        assert reconciled.ledger_digest == "sha256_mock_digest"
        assert reconciled.status == "RECONCILED"
