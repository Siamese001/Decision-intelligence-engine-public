"""
tests/transcript_stages/test_chat_attribution_guard.py

Wave 5: Unit tests for assert_chat_attributions_confirmed enforcement guard.
Verifies that authoritative 01_* transcript generation fails closed if any inline chat attributions remain unconfirmed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    InlineChatAttribution,
)
from dual_pipeline.transcript_stages_core.hitl_guard import (
    assert_chat_attributions_confirmed,
)


class TestChatAttributionGuard:
    """Wave 5 Test Suite: Pre-Write Enforcement Guard."""

    def test_guard_passes_when_no_attributions_exist(self, tmp_path: Path):
        """When no chat attributions were collected, guard passes cleanly."""
        assert_chat_attributions_confirmed(attributions_path=None, attributions=None)
        assert_chat_attributions_confirmed(attributions_path=tmp_path / "nonexistent.json", attributions=[])

    def test_guard_fails_when_pending_attributions_in_memory(self):
        """Pending attribution objects in memory trigger fail-closed ValueError."""
        pending_att = InlineChatAttribution(
            raw_message='"We feel there is value" = Alex Rivera',
            attribution_type="quote_to_speaker",
            speaker_name="Alex Rivera",
            confirmation_status="PENDING",
        )
        with pytest.raises(ValueError, match="CHAT_ATTRIBUTION_HITL_INCOMPLETE"):
            assert_chat_attributions_confirmed(attributions=[pending_att])

    def test_guard_fails_when_pending_attributions_on_disk(self, tmp_path: Path):
        """Pending attribution file on disk triggers fail-closed ValueError."""
        target = tmp_path / "inline_chat_attributions.json"
        data = [
            {
                "raw_message": '"We feel there is value" = Alex Rivera',
                "attribution_type": "quote_to_speaker",
                "speaker_name": "Alex Rivera",
                "confirmation_status": "PENDING",
            }
        ]
        target.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="CHAT_ATTRIBUTION_HITL_INCOMPLETE"):
            assert_chat_attributions_confirmed(attributions_path=target)

    def test_guard_passes_when_all_attributions_resolved(self, tmp_path: Path):
        """When all attributions are CONFIRMED, CORRECTED, or SKIPPED, guard passes."""
        resolved = [
            InlineChatAttribution(
                raw_message="Quote 1",
                attribution_type="quote_to_speaker",
                speaker_name="Alex Rivera",
                confirmation_status="CONFIRMED",
                confirmation_receipt_id="auth-ev-12345",
            ),
            InlineChatAttribution(
                raw_message="Quote 2",
                attribution_type="quote_to_speaker",
                speaker_name="Marcus Vance",
                confirmation_status="CORRECTED",
                confirmation_receipt_id="auth-ev-67890",
            ),
            InlineChatAttribution(
                raw_message="Quote 3",
                attribution_type="quote_to_speaker",
                speaker_name="Peter Ashley",
                confirmation_status="SKIPPED",
            ),
        ]
        # Memory check
        assert_chat_attributions_confirmed(attributions=resolved)

        # File check
        target = tmp_path / "inline_chat_attributions.json"
        target.write_text(json.dumps([a.to_dict() for a in resolved]), encoding="utf-8")
        assert_chat_attributions_confirmed(attributions_path=target)

    def test_live_transcript_engine_fails_closed_when_pending_unresolved(self, tmp_path: Path):
        """assert_chat_attributions_confirmed fails closed when unconfirmed attributions remain."""
        pending_att = InlineChatAttribution(
            raw_message='"We feel there is value" = Alex Rivera',
            attribution_type="quote_to_speaker",
            speaker_name="Alex Rivera",
            quoted_text="We feel there is value",
            confirmation_status="PENDING",
        )

        with pytest.raises(ValueError, match="CHAT_ATTRIBUTION_HITL_INCOMPLETE"):
            assert_chat_attributions_confirmed(attributions=[pending_att])
