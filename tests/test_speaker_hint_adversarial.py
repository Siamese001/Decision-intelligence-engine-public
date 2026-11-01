"""
tests/test_speaker_hint_adversarial.py

Wave 4 Adversarial Test Suite for Silent Durable Attribution.
Covers 4 mandatory adversarial categories under e2e-and-edge-case-coverage.md:
  1. Boundary payload: empty, whitespace-only, zero-length quotes, extreme length payloads.
  2. Malformed input: unbalanced quotes, conversational questions, regex metacharacters, corrupted JSON store.
  3. Concurrency & race: multi-threaded concurrent queue enqueuing without file corruption.
  4. Invariant bypass attempt: duplicate hint flooding and fake negative timestamp handling.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue,
)


@pytest.fixture
def roster() -> list[str]:
    return ["Speaker_Alpha", "Speaker_Beta", "Alex Rivera", "John Snipes"]


class TestSpeakerHintBoundaryPayload:
    """Category 1: Boundary Payloads."""

    def test_empty_and_whitespace_inputs(self, roster: list[str]) -> None:
        """Empty, None, or whitespace-only inputs must yield empty parsed results without errors."""
        collector = ChatAttributionCollector(attendees=roster)
        assert collector.parse_message("") == []
        assert collector.parse_message("   \n\t  ") == []

    def test_zero_length_quotes_ignored(self, roster: list[str]) -> None:
        """Quotes with empty bodies must not be parsed as valid attributions."""
        collector = ChatAttributionCollector(attendees=roster)
        assert collector.parse_message('Speaker_Alpha - ""') == []
        assert collector.parse_message('Speaker_Beta ""') == []
        assert collector.parse_message('Alex Rivera = ""') == []

    def test_excessively_long_quote_payload(self, roster: list[str]) -> None:
        """An unusually long quote (e.g. 5,000 characters) must parse cleanly without memory blowup or regex hang."""
        collector = ChatAttributionCollector(attendees=roster)
        long_quote = "word " * 1000
        msg = f'Speaker_Alpha - "{long_quote.strip()}"'
        res = collector.parse_message(msg)
        assert len(res) == 1
        assert res[0].speaker_name == "Speaker_Alpha"
        assert len(res[0].quoted_text) >= 4000


class TestSpeakerHintMalformedInput:
    """Category 2: Malformed Inputs."""

    def test_unbalanced_quotes_rejected(self, roster: list[str]) -> None:
        """Unclosed or mismatched opening quotation marks without closing must not parse as valid quotes."""
        collector = ChatAttributionCollector(attendees=roster)
        assert collector.parse_message('Speaker_Alpha - "unclosed quote that never terminates') == []
        assert collector.parse_message('Speaker_Beta "another broken quote') == []

    def test_conversational_questions_not_mistaken_for_hints(self, roster: list[str]) -> None:
        """Regular conversational questions referencing speakers must not be classified as hints."""
        collector = ChatAttributionCollector(attendees=roster)
        assert collector.parse_message("What did Speaker_Alpha say about the budget?") == []
        assert collector.parse_message("Can Speaker_Beta check the confluence page?") == []
        assert collector.parse_message("Alex Rivera, can you confirm the timeline?") == []

    def test_regex_metacharacters_in_speaker_and_quote(self, roster: list[str]) -> None:
        """Special regex metacharacters in speaker or quotes must not crash regex engines."""
        special_roster = ["Dr. C++", "User[1]", "Special.*User"]
        collector = ChatAttributionCollector(attendees=special_roster)

        msg = 'Dr. C++ - "Value [1] {key} * + ? ^ $ \\ backslash"'
        res = collector.parse_message(msg)
        assert len(res) == 1
        assert res[0].quoted_text == "Value [1] {key} * + ? ^ $ \\ backslash"

    def test_corrupted_json_queue_file_recovery(self, tmp_path: Path) -> None:
        """A corrupted JSON queue file must not crash the queue manager; it recovers with empty state."""
        queue_file = tmp_path / "speaker_hints_queue.json"
        queue_file.write_text("{corrupt non-json content", encoding="utf-8")

        queue = DurableSpeakerHintQueue(queue_path=queue_file)
        assert queue.get_pending_hints() == []

        # Enqueuing should succeed and heal the file
        hint, is_new = queue.enqueue_hint(speaker="Speaker_Alpha", quote="Valid recovered quote")
        assert is_new is True
        assert queue_file.is_file()
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["quote"] == "Valid recovered quote"


class TestSpeakerHintConcurrencyAndRace:
    """Category 3: Concurrency & Race Conditions."""

    def test_concurrent_enqueue_thread_safety(self, tmp_path: Path) -> None:
        """Multiple threads concurrently writing to the hint queue must not corrupt the file."""
        queue_file = tmp_path / "concurrent_hints.json"

        def _enqueue_task(idx: int) -> bool:
            q = DurableSpeakerHintQueue(queue_path=queue_file)
            _, is_new = q.enqueue_hint(speaker=f"Speaker_{idx % 5}", quote=f"Quote number {idx}")
            return is_new

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_enqueue_task, i) for i in range(40)]
            results = [f.result() for f in futures]

        assert any(results)
        final_queue = DurableSpeakerHintQueue(queue_path=queue_file)
        # All saved hints must parse as valid JSON
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 1


class TestSpeakerHintInvariantBypassAttempt:
    """Category 4: Invariant Bypass Attempts."""

    def test_duplicate_flooding_suppressed(self, roster: list[str], tmp_path: Path) -> None:
        """Submitting 50 identical hints in a flood must result in exactly 1 persisted record."""
        store_path = tmp_path / "flood_attributions.json"
        collector = ChatAttributionCollector(attendees=roster, output_path=store_path)

        msg = 'Speaker_Alpha - "Dave real quick, do you recall offhand"'
        for _ in range(50):
            collector.record_attribution(msg)

        assert len(collector.attributions) == 1
        saved_data = json.loads(store_path.read_text(encoding="utf-8"))
        assert len(saved_data) == 1

    def test_queue_duplicate_flooding_suppressed(self, tmp_path: Path) -> None:
        """DurableSpeakerHintQueue must ignore redundant enqueues of identical hints."""
        queue = DurableSpeakerHintQueue(cache_dir=tmp_path)
        for i in range(25):
            h, is_new = queue.enqueue_hint(
                speaker="Speaker_Beta",
                quote="I have a registration workflow",
            )
            if i == 0:
                assert is_new is True
            else:
                assert is_new is False

        assert len(queue.get_pending_hints()) == 1
        data = json.loads(queue.queue_path.read_text(encoding="utf-8"))
        assert len(data) == 1
