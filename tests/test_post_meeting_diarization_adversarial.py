"""
tests/test_post_meeting_diarization_adversarial.py

Adversarial tests for Wave 2: Live Voice Clustering & Post-Stop Acoustic Diarization.
Covers:
1. Malformed, nested, and boundary attendee roster payloads.
2. Single-speaker capture collapse detection and receipt status gating.
3. Intra-chunk switch detection boundary conditions and zero-norm resilience.
4. Truncated, 0-byte, and multi-channel audio RMS computation robustness.
5. Injective speaker mapping collision defense.
"""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import wave
import numpy as np

from dual_pipeline.audio_ingestion_core.mlx_transcriber import _flatten_attendee_roster
from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
    compute_channel_rms,
    compute_channel_rms_ratio,
    detect_intra_chunk_switches,
    extract_subchunk_signatures,
    write_acoustic_evidence_receipt,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
    LiveVoiceRegistry,
    VoiceCluster,
)
from dual_pipeline.speaker_attribution_core import (
    AudioSpeakerAttributionEngine,
    SpeakerAttributionCollisionError,
)


class TestPostMeetingDiarizationAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_diar_adversarial_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adversarial_attendee_roster_flattening(self) -> None:
        """
        Verify that _flatten_attendee_roster safely handles corrupt, empty,
        deeply nested, or mixed-type roster and attendee structures.
        """
        # Case 1: None and empty structures
        self.assertEqual(_flatten_attendee_roster(None), [])
        self.assertEqual(_flatten_attendee_roster({}), [])
        self.assertEqual(_flatten_attendee_roster({"attendees": None}), [])
        self.assertEqual(_flatten_attendee_roster({"attendees": []}), [])

        # Case 2: Comma-separated strings with dirty whitespace
        meta_str = {"attendees": "  Alice Smith ,  , Bob Jones (NEA) , Carol  "}
        flat_str = _flatten_attendee_roster(meta_str)
        self.assertEqual(flat_str, ["Alice Smith", "Bob Jones (NEA)", "Carol"])

        # Case 3: Org-grouped dictionary with list of mixed strings and dicts
        meta_dict = {
            "attendees": {
                "PartnerAdvisory": ["Justin M", {"name": "Sharon D", "role": "Lead"}, None, ""],
                "NEA": [{"name": "Daniel Sabet", "org": "NEA"}, "Reid S"],
                "EmptyOrg": [],
            }
        }
        flat_dict = _flatten_attendee_roster(meta_dict)
        self.assertIn("Justin M", flat_dict)
        self.assertIn("Sharon D", flat_dict)
        self.assertIn("Daniel Sabet", flat_dict)
        self.assertIn("Reid S", flat_dict)
        self.assertEqual(len(flat_dict), 4)

        # Case 4: Roster fallback when attendees key is missing
        meta_roster_fallback = {
            "roster": [
                {"name": "Beau M", "role": "Partner"},
                {"name": "Maud R", "role": "Director"},
            ]
        }
        flat_roster = _flatten_attendee_roster(meta_roster_fallback)
        self.assertEqual(flat_roster, ["Beau M", "Maud R"])

    def test_single_speaker_collapse_detection_and_receipt_gating(self) -> None:
        """
        Verify that LiveVoiceRegistry.detect_capture_collapse flags single-speaker dominance
        (>= 85% share across >= 10 chunks with >= 2 attendees) and propagates to receipt.
        """
        registry = LiveVoiceRegistry(
            similarity_threshold=0.68,
            attendees=["Justin M", "Reid S", "Sharon D"],
        )

        # Populate 12 chunks all assigned to Voice A
        for i in range(12):
            registry.voice_clusters.setdefault(
                "Voice A",
                VoiceCluster(voice_id="Voice A", resolved_speaker="Jen Pan", sample_count=0),
            ).sample_count += 1

        collapse_info = registry.detect_capture_collapse(min_chunks=10, collapse_threshold=0.85)
        self.assertTrue(collapse_info["is_collapsed"])
        self.assertEqual(collapse_info["dominant_voice_id"], "Voice A")
        self.assertAlmostEqual(collapse_info["dominant_share"], 1.0)
        self.assertIsNotNone(collapse_info["warning"])

        receipt_path = self.temp_path / "acoustic_evidence_receipt.json"
        manifest_path = self.temp_path / "session_manifest.json"
        cache_dir = self.temp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        _, receipt_data = write_acoustic_evidence_receipt(
            voice_registry=registry,
            session_tag="test_collapse_session",
            clean_slug="test_slug",
            processed_chunks_count=12,
            attendees=["Justin M", "Reid S", "Sharon D"],
            stop_timestamp="2026-10-15T18:00:00Z",
            session_manifest_path=manifest_path,
            receipt_path=receipt_path,
            cache_dir=cache_dir,
        )

        self.assertEqual(receipt_data["attribution_status"], "COLLAPSED_PENDING_ATTRIBUTION")
        self.assertTrue(receipt_data["collapse_detection"]["is_collapsed"])

    def test_intra_chunk_switch_detection_adversarial_boundaries(self) -> None:
        """
        Verify that detect_intra_chunk_switches safely handles boundary inputs:
        empty feature list, single vector, orthogonal vectors, and zero vectors.
        """
        # Empty and single-element inputs
        self.assertEqual(detect_intra_chunk_switches([]), [])
        self.assertEqual(detect_intra_chunk_switches([np.ones(21, dtype=np.float32)]), [])

        # Identical vectors (no switch)
        v1 = np.ones(21, dtype=np.float32) / np.sqrt(21)
        v2 = np.ones(21, dtype=np.float32) / np.sqrt(21)
        self.assertEqual(detect_intra_chunk_switches([v1, v2], distance_threshold=0.30), [])

        # Opposite vectors (distance = 2.0 >= 0.30 -> switch at index 1)
        v_opp = -v1
        self.assertEqual(detect_intra_chunk_switches([v1, v_opp], distance_threshold=0.30), [1])

        # Zero norm vectors (should be ignored gracefully without ZeroDivisionError)
        v_zero = np.zeros(21, dtype=np.float32)
        self.assertEqual(detect_intra_chunk_switches([v1, v_zero, v1], distance_threshold=0.30), [])

    def test_audio_rms_and_subchunk_extraction_on_corrupt_wavs(self) -> None:
        """
        Verify that compute_channel_rms and extract_subchunk_signatures handle
        non-existent, empty, and truncated WAV files without crashing.
        """
        # Non-existent file
        no_file = self.temp_path / "non_existent.wav"
        self.assertEqual(compute_channel_rms(no_file), (0.0,))
        self.assertEqual(compute_channel_rms_ratio(no_file), (0.0, 0.0))
        self.assertEqual(extract_subchunk_signatures(no_file), [])

        # 0-byte file
        empty_file = self.temp_path / "empty.wav"
        empty_file.write_bytes(b"")
        self.assertEqual(compute_channel_rms(empty_file), (0.0,))
        self.assertEqual(compute_channel_rms_ratio(empty_file), (0.0, 0.0))
        self.assertEqual(extract_subchunk_signatures(empty_file), [])

        # Truncated corrupt WAV (invalid header bytes)
        corrupt_file = self.temp_path / "corrupt.wav"
        corrupt_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        self.assertEqual(compute_channel_rms(corrupt_file), (0.0,))
        self.assertEqual(compute_channel_rms_ratio(corrupt_file), (0.0, 0.0))
        self.assertEqual(extract_subchunk_signatures(corrupt_file), [])

    def test_injective_speaker_mapping_collision_defense(self) -> None:
        """
        Verify that injective mapping collision defense blocks mapping two distinct
        speaker clusters to the same attendee when cluster count <= roster count.
        """
        valid_map = {"Speaker 1": "Alice", "Speaker 2": "Bob"}
        # Should not raise
        AudioSpeakerAttributionEngine.assert_injective_speaker_mapping(valid_map, roster_count=2, cluster_count=2)

        colliding_map = {"Speaker 1": "Alice", "Speaker 2": "Alice"}
        with self.assertRaises(SpeakerAttributionCollisionError):
            AudioSpeakerAttributionEngine.assert_injective_speaker_mapping(colliding_map, roster_count=2, cluster_count=2)


if __name__ == "__main__":
    unittest.main()
