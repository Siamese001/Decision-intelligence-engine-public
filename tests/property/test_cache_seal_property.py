"""Hypothesis property-based tests for transcript cache seal and sidecar integrity."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List
import unittest

from hypothesis import given, settings, strategies as st

from dual_pipeline.exceptions import CacheIntegrityViolationError
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    create_cache_seal,
    verify_cache_seal,
)

# Hypothesis strategies for generating synthetic sidecars
speaker_strategy = st.sampled_from(["Speaker_0", "Speaker_1", "Alice", "Bob", "Charlie", "Unknown"])
turn_text_strategy = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=5, max_size=100)


def _generate_turns(num_turns: int) -> List[Dict[str, Any]]:
    return [
        {
            "start": round(i * 3.5, 2),
            "end": round((i + 1) * 3.5, 2),
            "speaker": f"Speaker_{i % 3}",
            "text": f"This is verified speech turn number {i} with deterministic content.",
        }
        for i in range(num_turns)
    ]


class TestCacheSealProperty(unittest.TestCase):
    """Generative property test suite verifying tamper-evident cryptographic cache seals."""

    @settings(max_examples=25, deadline=None)
    @given(
        num_turns=st.integers(min_value=2, max_value=20),
        mutation_index=st.integers(min_value=0, max_value=19),
    )
    def test_property_segments_tampering_fails_closed(self, num_turns: int, mutation_index: int) -> None:
        """Any mutation to .segments.json must trigger CacheIntegrityViolationError."""
        target_idx = mutation_index % num_turns
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_prop.segments.json"
            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

            create_cache_seal(structured_file=seg_file, cache_key="prop_test_key")

            # Mutate turn content
            turns[target_idx]["text"] = "TAMPERED_INJECTED_UNAUTHORIZED_CONTENT"
            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

            with self.assertRaises(CacheIntegrityViolationError):
                verify_cache_seal(structured_file=seg_file)

    @settings(max_examples=25, deadline=None)
    @given(
        num_turns=st.integers(min_value=2, max_value=20),
    )
    def test_property_turn_count_divergence_fails_closed(self, num_turns: int) -> None:
        """Divergence between turn count in seal vs actual segments must fail closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_div.segments.json"
            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

            create_cache_seal(structured_file=seg_file, cache_key="div_test_key")

            # Append extra turn
            turns.append({
                "start": 99.0,
                "end": 100.0,
                "speaker": "Speaker_New",
                "text": "Appended turn breaking parity.",
            })
            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

            with self.assertRaises(CacheIntegrityViolationError):
                verify_cache_seal(structured_file=seg_file)

    @settings(max_examples=20, deadline=None)
    @given(
        num_turns=st.integers(min_value=2, max_value=15),
        mutated_text=st.text(min_size=10, max_size=50),
    )
    def test_property_transcript_cache_tampering_fails_closed(self, num_turns: int, mutated_text: str) -> None:
        """Tampering with companion .transcript_cache.json must fail closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_tc.segments.json"
            cache_file = tmp_path / "meeting_tc.transcript_cache.json"

            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            cache_file.write_text("Original authentic transcript cache content.", encoding="utf-8")

            create_cache_seal(structured_file=seg_file, cache_file=cache_file, cache_key="tc_test_key")

            # Tamper with transcript cache
            cache_file.write_text(f"Mutated: {mutated_text}", encoding="utf-8")

            with self.assertRaises(CacheIntegrityViolationError):
                verify_cache_seal(structured_file=seg_file, cache_file=cache_file)

    @settings(max_examples=20, deadline=None)
    @given(
        num_turns=st.integers(min_value=2, max_value=15),
    )
    def test_property_voiceprints_sidecar_tampering_fails_closed(self, num_turns: int) -> None:
        """Tampering with companion .voiceprints.json must fail closed with CacheIntegrityViolationError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_vp.segments.json"
            vp_file = tmp_path / "meeting_vp.voiceprints.json"

            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            vp_payload = {
                "audio_sha256": "abcdef1234567890",
                "project": "GLOBAL",
                "centroids": {"SPEAKER_00": [0.1, 0.2, 0.3]},
                "matches": {"SPEAKER_00": "Alice Smith"},
            }
            vp_file.write_text(json.dumps(vp_payload, indent=2), encoding="utf-8")

            create_cache_seal(structured_file=seg_file, voiceprints_file=vp_file, cache_key="vp_test_key")

            # Tamper with voiceprints match
            vp_payload["matches"]["SPEAKER_00"] = "Bob Rogue Injected"
            vp_file.write_text(json.dumps(vp_payload, indent=2), encoding="utf-8")

            with self.assertRaises(CacheIntegrityViolationError):
                verify_cache_seal(structured_file=seg_file)

    @settings(max_examples=20, deadline=None)
    @given(
        num_turns=st.integers(min_value=2, max_value=15),
    )
    def test_property_cluster_map_sidecar_tampering_fails_closed(self, num_turns: int) -> None:
        """Tampering with companion .cluster_map.json must fail closed with CacheIntegrityViolationError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_cmap.segments.json"
            cmap_file = tmp_path / "meeting_cmap.cluster_map.json"

            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            cmap_payload = {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Speaker 2"}
            cmap_file.write_text(json.dumps(cmap_payload, indent=2), encoding="utf-8")

            create_cache_seal(structured_file=seg_file, cluster_map_file=cmap_file, cache_key="cmap_test_key")

            # Tamper with cluster map
            cmap_payload["SPEAKER_00"] = "Tampered Speaker"
            cmap_file.write_text(json.dumps(cmap_payload, indent=2), encoding="utf-8")

            with self.assertRaises(CacheIntegrityViolationError):
                verify_cache_seal(structured_file=seg_file)

    @settings(max_examples=20, deadline=None)
    @given(
        num_turns=st.integers(min_value=1, max_value=25),
    )
    def test_property_untampered_bundles_always_verify_successfully(self, num_turns: int) -> None:
        """Untampered bundles with all companion sidecars must always verify with sealed=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            turns = _generate_turns(num_turns)
            seg_file = tmp_path / "meeting_valid.segments.json"
            cache_file = tmp_path / "meeting_valid.transcript_cache.json"
            utt_file = tmp_path / "meeting_valid.utterances.json"
            vp_file = tmp_path / "meeting_valid.voiceprints.json"
            cmap_file = tmp_path / "meeting_valid.cluster_map.json"

            seg_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            cache_file.write_text("Valid cached transcript.", encoding="utf-8")
            utt_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            vp_file.write_text(json.dumps({"centroids": {}, "matches": {}}, indent=2), encoding="utf-8")
            cmap_file.write_text(json.dumps({"SPEAKER_00": "Alice"}, indent=2), encoding="utf-8")

            seal_file = create_cache_seal(
                structured_file=seg_file,
                cache_file=cache_file,
                utterance_file=utt_file,
                voiceprints_file=vp_file,
                cluster_map_file=cmap_file,
                cache_key="valid_key",
                audio_sha256="0123456789abcdef",
            )
            self.assertTrue(seal_file.is_file())

            result = verify_cache_seal(structured_file=seg_file)
            self.assertTrue(result.get("sealed"))
            self.assertEqual(result.get("turn_count"), num_turns)
            self.assertEqual(result.get("cache_key"), "valid_key")


if __name__ == "__main__":
    unittest.main()
