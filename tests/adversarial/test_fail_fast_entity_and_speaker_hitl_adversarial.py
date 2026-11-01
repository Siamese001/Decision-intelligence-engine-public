"""
tests/adversarial/test_fail_fast_entity_and_speaker_hitl_adversarial.py

Adversarial edge-case and regression tests verifying Wave 4 fail-fast invariants:
1. C-27 entity context gate must fail closed (return False, ["C27_RESOLVER_UNAVAILABLE"]) when resolver is None.
2. PreSynthesisEntityResolver must raise RuntimeError on corrupt KG or corrupt charter CSV.
3. PreSynthesisEntityResolver.resolve() must never return placeholder roles.
4. resolve_company_for_person must raise ValueError when affiliation cannot be determined.
5. SpeakerDisambiguator must raise RuntimeError on missing credentials when mock=False.
6. SpeakerDisambiguator._disambiguate_llm must raise HITLRequiredError on omitted turns.
7. SpeakerDisambiguator._disambiguate_llm must raise RuntimeError on LLM errors (no silent heuristic fallback).
8. speaker_disambiguation._resolve_ambiguous_turns must re-raise HITLRequiredError on interactive resolver failure.
9. resolve_shared_room_microphones must raise RuntimeError on missing credentials when mock=False.
10. l1_attribution.build_participants_string must raise ValueError on empty roster and speaker_map.
11. l1_attribution.assemble_native_hitl_transcript must raise ValueError on missing meeting_name or date.
12. hitl_guard.assert_chat_attributions_confirmed must raise RuntimeError on corrupted JSON.
13. forensic_normalizer post-synthesis must check generic speakers and raise HITLRequiredError if unmapped.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.speaker_disambiguation import (
    _resolve_ambiguous_turns,
    resolve_shared_room_microphones,
)
from dual_pipeline.speaker_disambiguation_core import (
    AttendeeContext,
    RoomMicConfig,
    SpeakerDisambiguator,
    TurnAttribution,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_company_for_person,
)
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    evaluate_referenced_entity_context_gate,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.hitl_guard import (
    assert_chat_attributions_confirmed,
)
from dual_pipeline.transcript_stages_core.l1_attribution import (
    assemble_native_hitl_transcript,
    build_participants_string,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


class TestC27GateFailFast(unittest.TestCase):
    """Verifies C-27 entity context gate fails closed when resolver is unavailable."""

    def test_c27_gate_fails_closed_when_resolver_is_none(self):
        with patch("dual_pipeline.transcript_qa_core.gate1_entity_context._get_resolver", return_value=None):
            passed, failures = evaluate_referenced_entity_context_gate(
                f1_text="**Amit Ayer:** Hello.",
                f2_text="## Summary\nDiscussion occurred.",
                roster_names=["Amit Ayer"],
                identity_resolver=None,
            )
            assert not passed
            assert len(failures) == 1
            assert "C27_RESOLVER_UNAVAILABLE" in failures[0]


class TestPreSynthesisEntityResolverFailFast(unittest.TestCase):
    """Verifies PreSynthesisEntityResolver fails fast on corruption and missing affiliations."""

    def test_corrupt_knowledge_graph_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            db_dir = tmp_p / "storage"
            db_dir.mkdir(parents=True)
            db_file = db_dir / "CANONICAL_GRAPH.db"
            db_file.write_text("corrupted sqlite header", encoding="utf-8")

            with pytest.raises(RuntimeError, match="Fail-Fast: Knowledge graph load failed"):
                resolver = PreSynthesisEntityResolver(repo_root=tmp_p, enable_graph_query=True)
                resolver._load_knowledge_graph()

    def test_corrupt_charter_csv_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            exp_dir = tmp_p / "exports"
            exp_dir.mkdir(parents=True)
            csv_file = exp_dir / "v53_nea_charter_role_assignments.csv"
            csv_file.write_text("dummy", encoding="utf-8")

            with (
                patch("builtins.open", side_effect=OSError("Disk read failure")),
                pytest.raises(RuntimeError, match="Fail-Fast: Failed loading charter CSV"),
            ):
                resolver = PreSynthesisEntityResolver(repo_root=tmp_p)
                resolver._load_charter_csv()

    def test_resolve_company_for_person_raises_value_error(self):
        with pytest.raises(ValueError, match="Fail-Fast: Cannot resolve corporate affiliation for person 'Zaphod Beeblebrox'"):
            resolve_company_for_person("Zaphod Beeblebrox", allow_default=False)

    def test_resolve_company_for_person_allows_default_when_flagged(self):
        company = resolve_company_for_person("Zaphod Beeblebrox", allow_default=True)
        assert company == "Client / Partner"


class TestSpeakerDisambiguatorFailFast(unittest.TestCase):
    """Verifies SpeakerDisambiguator fail-fast invariants."""

    def test_missing_credentials_when_mock_false_raises_runtime_error(self):
        disambiguator = SpeakerDisambiguator(mock=False)
        with patch("dual_pipeline.credentials.get_openai_client") as mock_get_client:
            mock_creds = MagicMock(is_mock=True)
            mock_get_client.return_value = (None, mock_creds)
            with pytest.raises(RuntimeError, match="Fail-Fast: OPENAI_API_KEY required for SpeakerDisambiguator"):
                _ = disambiguator.client

    def test_llm_omitted_turns_raises_hitl_required_error(self):
        disambiguator = SpeakerDisambiguator(mock=False)
        room_turns = [
            {"line_index": 1, "text": "Turn 1", "timestamp": "00:00:10", "speaker": "Speaker 1"},
            {"line_index": 2, "text": "Turn 2", "timestamp": "00:00:20", "speaker": "Speaker 1"},
        ]
        cfg = RoomMicConfig(
            mic_label="shared-room",
            display_label="Room",
            in_room_attendees=[
                AttendeeContext(name="Alice Smith", person_id="person:alice", role="Engineer", company="PartnerAdvisory"),
                AttendeeContext(name="Bob Jones", person_id="person:bob", role="Manager", company="NEA"),
            ],
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = (
            '{"attributions": [{"turn_index": 1, "assigned_speaker": "Alice Smith", "confidence": 0.95, "second_candidate": "Bob Jones", "second_confidence": 0.05, "rationale": "context"}]}'
        )
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch.object(SpeakerDisambiguator, "client", new=mock_client),
            pytest.raises(HITLRequiredError, match="Fail-Fast: LLM response omitted room turns"),
        ):
            disambiguator._disambiguate_llm(
                all_turns=room_turns,
                room_turns=room_turns,
                room_config=cfg,
                remote_attendees=[],
                batch_size=10,
            )

    def test_llm_failure_raises_runtime_error_without_fallback(self):
        disambiguator = SpeakerDisambiguator(mock=False)
        room_turns = [
            {"line_index": 1, "text": "Turn 1", "timestamp": "00:00:10", "speaker": "Speaker 1"},
        ]
        cfg = RoomMicConfig(
            mic_label="shared-room",
            display_label="Room",
            in_room_attendees=[
                AttendeeContext(name="Alice Smith", person_id="person:alice", role="Engineer", company="PartnerAdvisory"),
            ],
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("OpenAI API unreachable")

        with (
            patch.object(SpeakerDisambiguator, "client", new=mock_client),
            pytest.raises(RuntimeError, match="Fail-Fast: LLM speaker disambiguation failed"),
        ):
            disambiguator._disambiguate_llm(
                all_turns=room_turns,
                room_turns=room_turns,
                room_config=cfg,
                remote_attendees=[],
                batch_size=10,
            )


class TestSpeakerDisambiguationWrapperFailFast(unittest.TestCase):
    """Verifies wrapper fail-fast behavior."""

    def test_resolve_ambiguous_turns_raises_on_interactive_resolver_failure(self):
        from dual_pipeline.speaker_disambiguation_core import DisambiguationResult

        turn = TurnAttribution(
            turn_index=1,
            timestamp="00:01:00",
            original_speaker="Speaker 1",
            assigned_speaker="Alice Smith",
            person_id="person:alice",
            confidence=0.55,
            second_candidate="Bob Jones",
            second_confidence=0.45,
            margin_delta=0.10,
            attribution_mode="DIRECT",
            rationale="Context",
            text_snippet="Test turn.",
        )
        dis_res = DisambiguationResult(
            resolved_transcript="**Alice Smith:** Test turn.",
            attribution_manifest={},
            turns=[turn],
            total_room_turns=1,
            directly_attributed_turns=1,
            shared_room_preserved_turns=0,
            speaker_distribution={"Alice Smith": 1},
        )

        def failing_resolver(envelope):
            raise RuntimeError("Interactive UI crashed")

        mock_store = MagicMock()
        mock_store.calibrate_options.side_effect = lambda opts, **kw: opts

        with pytest.raises(HITLRequiredError, match="Fail-Fast: Interactive speaker resolver failed"):
            _resolve_ambiguous_turns(
                dis_result=dis_res,
                resolved_text="**Alice Smith:** Test turn.",
                metadata={},
                store=mock_store,
                interactive_resolver=failing_resolver,
            )

    def test_resolve_shared_room_microphones_raises_when_credentials_missing_and_not_mock(self):
        metadata = {
            "shared_room_mics": [{
                "mic_label": "conf-room",
                "display_label": "Room",
                "in_room_attendees": [{"name": "Alice Smith"}],
            }],
            "mock_disambiguation": False,
            "provider": "llm",
        }
        with patch("dual_pipeline.speaker_disambiguation.resolve_openai_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(api_key="")
            with pytest.raises(RuntimeError, match="Fail-Fast: OpenAI credentials not found for room mic speaker disambiguation"):
                resolve_shared_room_microphones(
                    resolved_text="**Speaker 1:** Hello room.",
                    metadata=metadata,
                    speaker_map={},
                )


class TestL1AttributionFailFast(unittest.TestCase):
    """Verifies L1 attribution fail-fast behavior."""

    def test_build_participants_string_raises_value_error_when_roster_empty(self):
        with pytest.raises(ValueError, match="Fail-Fast: Cannot build participants string; both attendee roster and speaker_map are empty"):
            build_participants_string(metadata={}, speaker_map={})

    def test_assemble_native_hitl_transcript_raises_on_missing_meeting_name(self):
        with pytest.raises(ValueError, match="Fail-Fast: metadata\\['meeting_name'\\] \\(or 'title'\\) is required"):
            assemble_native_hitl_transcript(
                resolved_text="**Alice:** Hello.",
                metadata={"date": "09/19/2026", "attendees": ["Alice"]},
                speaker_map={},
            )

    def test_assemble_native_hitl_transcript_raises_on_missing_date(self):
        with pytest.raises(ValueError, match="Fail-Fast: metadata\\['date'\\] \\(or 'meeting_date'\\) is required"):
            assemble_native_hitl_transcript(
                resolved_text="**Alice:** Hello.",
                metadata={"meeting_name": "Review", "attendees": ["Alice"]},
                speaker_map={},
            )


class TestHITLGuardFailFast(unittest.TestCase):
    """Verifies HITL guard fail-fast behavior on corrupt files."""

    def test_corrupt_inline_chat_attributions_json_raises_runtime_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{invalid json: not closed")
            p = f.name

        try:
            with pytest.raises(RuntimeError, match="Fail-Fast: Failed parsing inline chat attributions file"):
                assert_chat_attributions_confirmed(attributions=None, attributions_path=p)
        finally:
            Path(p).unlink(missing_ok=True)


class TestForensicNormalizerFailFast(unittest.TestCase):
    """Verifies ForensicNormalizer fail-fast behavior."""

    def test_generic_speakers_in_synthesized_output_raises_hitl_required_error(self):
        normalizer = ForensicNormalizer()
        mock_provider = MagicMock()
        mock_provider.synthesize_forensic.return_value = (
            "# Sprint Planning -- HITL-Resolved Transcript\n\n"
            "**Date:** 09/30/2026\n"
            "**Participants:**\nAlice Smith, Bob Jones\n\n"
            "---\n"
            "**Alice Smith:** Let's review items.\n"
            "**Speaker A:** I agree with that proposal.\n\n"
            "---\n"
            "## Ambiguity Lock Register\n"
            "| Item # | Identified Ambiguity | Resolution | Basis |\n"
        )

        with pytest.raises(HITLRequiredError, match="L1 ATTRIBUTION GATE BLOCKED: Audio input has unmapped generic speakers"):
            normalizer._execute_undiarized(
                transcript="Alice Smith: Let's review items.",
                metadata={
                    "meeting_name": "Sprint Planning",
                    "date": "09/30/2026",
                    "attendees": ["Alice Smith", "Bob Jones"],
                    "source_type": "AUDIO_ONLY_UNDIARIZED",
                },
                provider=mock_provider,
            )
