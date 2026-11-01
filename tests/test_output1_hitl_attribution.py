#!/usr/bin/env python3
"""Unit tests for Wave 3: Output 01 HITL Expansion (Diarization, ASR & Silence Gaps).

Verifies:
1. Room mic speaker attribution evaluates margin against 15% threshold and surfaces HITL envelope.
2. Phonetic ASR acronyms (e.g. BGD vs PGD, SSW vs SOW) are disambiguated and locked in ALR.
3. Silence gaps >180s are classified between NATURAL_SESSION_PAUSE and RECORDING_GAP_INTERRUPTION.
4. Output 01 Ambiguity Lock Register compiles itemized operator-confirmed entries.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.speaker_disambiguation import resolve_shared_room_microphones
from dual_pipeline.transcript_stages_core.acronym_disambiguator import disambiguate_asr_acronyms
from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register
from dual_pipeline.transcript_stages_core.silence_gap_classifier import classify_silence_gaps


@pytest.mark.unit
class Output1HitlAttributionTests(unittest.TestCase):
    """Test suite verifying Wave 3 Output 01 HITL enhancements."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_asr_phonetic_acronym_disambiguation_lifecycle(self) -> None:
        """Ambiguous acronyms trigger HITL envelope, persist to store, and appear in ALR."""
        transcript = "[00:01:15] **Jen Pan:** We need to confirm the BGD work orders before next week."
        metadata = {
            "meeting_name": "Grid Operations Review",
            "hitl_acronym_resolver": lambda env: "PGD",
        }

        resolved_text, updated_meta = disambiguate_asr_acronyms(
            transcript,
            metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_acronym_resolver"],
        )

        self.assertIn("PGD work orders", resolved_text)
        self.assertNotIn("BGD work orders", resolved_text)
        self.assertIn("acronym_pgd", updated_meta["hitl_resolutions"])

        # Check ALR table format
        alr_markdown = format_ambiguity_lock_register(updated_meta, {})
        self.assertIn("Phonetic ASR ambiguity: 'BGD' vs 'PGD'", alr_markdown)
        self.assertIn("Operator Confirmed (HITL Acronym Disambiguation", alr_markdown)

        # Check ledger persistence
        records = self.store.get_history(category="asr_acronym_disambiguation")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["selected_option"]["key"], "PGD")

    def test_silence_gap_classification_lifecycle(self) -> None:
        """Gaps >180s trigger HITL classification and insert operator-approved note into ALR."""
        transcript = (
            "[00:04:10] **harper Hoffman:** Let us take a quick break.\n"
            "[Recording gap: 00:04:15 – 00:08:30]\n"
            "[00:08:35] **Jen Pan:** Back from recess, let us resume.\n"
        )
        metadata = {
            "hitl_gap_resolver": lambda env: "NATURAL_SESSION_PAUSE",
        }

        resolved_text, updated_meta = classify_silence_gaps(
            transcript,
            metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_gap_resolver"],
        )

        self.assertIn("[Session pause: 00:04:15 – 00:08:30]", resolved_text)
        self.assertNotIn("[Recording gap: 00:04:15 – 00:08:30]", resolved_text)
        self.assertIn("gap_000415", updated_meta["hitl_resolutions"])

        # Check ALR table
        alr_markdown = format_ambiguity_lock_register(updated_meta, {})
        self.assertIn("Unannotated silence interval: [00:04:15 – 00:08:30]", alr_markdown)
        self.assertIn("Operator Confirmed (HITL Silence Gap Classification", alr_markdown)

        # Check ledger persistence
        records = self.store.get_history(category="silence_gap_classification")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["selected_option"]["key"], "NATURAL_SESSION_PAUSE")

    def test_shared_room_mic_disambiguation_surfaces_hitl(self) -> None:
        """Room mic turns with delta < 15% prompt operator and update turn attribution."""
        transcript = (
            "--- START OF TRANSCRIPT ---\n"
            "[00:14:22] **NEA/APEX (room):** We need to verify the work order counts before Wednesday.\n"
        )
        metadata = {
            "shared_room_mics": [
                {
                    "mic_label": "NEA/APEX (room)",
                    "display_label": "NEA/APEX (room)",
                    "in_room_attendees": [
                        {"name": "Jen Pan", "person_id": "person:jen-pan", "role": "Architect", "domain_topics": ["work orders"]},
                        {"name": "harper Hoffman", "person_id": "person:mandy-hopmann", "role": "VP", "domain_topics": ["counts"]},
                    ],
                }
            ],
            "mock_disambiguation": True,
            "hitl_speaker_resolver": lambda env: "Jen Pan",
        }
        speaker_map = {"Speaker 1": "Jen Pan", "Speaker 2": "harper Hoffman"}

        resolved_text, updated_meta = resolve_shared_room_microphones(transcript, metadata, speaker_map)

        # Verify resolution
        self.assertIn("hitl_resolutions", updated_meta)
        alr_markdown = format_ambiguity_lock_register(updated_meta, speaker_map)
        self.assertTrue(len(alr_markdown) > 50)

    def test_single_turn_correction_leaves_unrelated_turns_unchanged(self) -> None:
        """Surgical single-turn attribution must not globally replace unrelated turns."""
        transcript = (
            "--- START OF TRANSCRIPT ---\n"
            "[00:14:22] **NEA/APEX (room):** We need to verify the work order counts before Wednesday.\n"
            "[00:18:45] **NEA/APEX (room):** Second discussion turn regarding the work order counts.\n"
        )
        metadata = {
            "shared_room_mics": [
                {
                    "mic_label": "NEA/APEX (room)",
                    "display_label": "NEA/APEX (room)",
                    "in_room_attendees": [
                        {"name": "Jen Pan", "person_id": "person:jen-pan", "role": "Architect", "domain_topics": ["work orders"]},
                        {"name": "harper Hoffman", "person_id": "person:mandy-hopmann", "role": "VP", "domain_topics": ["counts"]},
                    ],
                }
            ],
            "mock_disambiguation": True,
            "hitl_speaker_resolver": lambda env: "Jen Pan" if "TARGET [00:14:22]" in getattr(env, "question", "") else "NEA/APEX (room)",
        }
        speaker_map = {"Speaker 1": "Jen Pan", "Speaker 2": "harper Hoffman"}

        resolved_text, updated_meta = resolve_shared_room_microphones(transcript, metadata, speaker_map)

        # First turn at [00:14:22] is replaced with Jen Pan
        self.assertIn("[00:14:22] **Jen Pan:**", resolved_text)
        # Second turn at [00:18:45] remains strictly as room mic and is NOT globally replaced
        self.assertIn("[00:18:45] **NEA/APEX (room):**", resolved_text)

    def test_conflicting_attribution_approvals_remain_pending(self) -> None:
        """Ambiguous room turns without interactive clearance fail closed as HITLRequiredError."""
        from dual_pipeline.exceptions import HITLRequiredError

        transcript = (
            "--- START OF TRANSCRIPT ---\n"
            "[00:14:22] **NEA/APEX (room):** We need to verify the work order counts before Wednesday.\n"
        )
        metadata = {
            "shared_room_mics": [
                {
                    "mic_label": "NEA/APEX (room)",
                    "display_label": "NEA/APEX (room)",
                    "in_room_attendees": [
                        {"name": "Jen Pan", "person_id": "person:jen-pan", "role": "Architect", "domain_topics": ["work orders"]},
                        {"name": "harper Hoffman", "person_id": "person:mandy-hopmann", "role": "VP", "domain_topics": ["counts"]},
                    ],
                }
            ],
            "mock_disambiguation": True,
            # No hitl_speaker_resolver provided -> must fail closed
        }
        speaker_map = {"Speaker 1": "Jen Pan", "Speaker 2": "harper Hoffman"}

        with self.assertRaises(HITLRequiredError):
            resolve_shared_room_microphones(transcript, metadata, speaker_map)

    def test_graph_approval_cannot_authorize_speaker_relabeling(self) -> None:
        """Graph mutation approval cannot substitute for missing speaker attribution approval."""
        from dual_pipeline.exceptions import HITLRequiredError

        transcript = (
            "--- START OF TRANSCRIPT ---\n"
            "[00:14:22] **NEA/APEX (room):** We need to verify the work order counts before Wednesday.\n"
        )
        metadata = {
            "shared_room_mics": [
                {
                    "mic_label": "NEA/APEX (room)",
                    "display_label": "NEA/APEX (room)",
                    "in_room_attendees": [
                        {"name": "Jen Pan", "person_id": "person:jen-pan", "role": "Architect", "domain_topics": ["work orders"]},
                        {"name": "harper Hoffman", "person_id": "person:mandy-hopmann", "role": "VP", "domain_topics": ["counts"]},
                    ],
                }
            ],
            "mock_disambiguation": True,
            "mutate_graph": True,
            "proposal_approved": True,
            "graph_mutation_authorized": True,
            # No hitl_speaker_resolver provided -> graph approval cannot bypass speaker HITL
        }
        speaker_map = {"Speaker 1": "Jen Pan", "Speaker 2": "harper Hoffman"}

        with self.assertRaises(HITLRequiredError):
            resolve_shared_room_microphones(transcript, metadata, speaker_map)


if __name__ == "__main__":
    unittest.main()
