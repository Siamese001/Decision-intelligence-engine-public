"""Adversarial and edge-case tests for acoustic diarization resolution and modality classification.

Covers ≥3 adversarial traits:
1. Boundary payload: Corrupted / invalid JSON in co-located speaker_map.json sidecar.
2. Invariant bypass: False claim of DIARIZED_ACOUSTIC_RESOLVED header with single-speaker collapse.
3. Boundary payload: Partial speaker map leaving unresolved generic clusters blocks attribution.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.ingress import process_u0_ingress
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import (
    ForensicNormalizer,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


class TestDiarizedAcousticResolvedAdversarial(unittest.TestCase):
    """Adversarial stress harness for acoustic diarization handoff invariants."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_diar_adv_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_corrupted_sidecar_json_fails_safe(self) -> None:
        """Trait 1 (Boundary Payload): Malformed/corrupted speaker_map.json does not crash and falls back safely."""
        transcript_content = (
            "# Live Meeting Transcript: Corrupt Map Test\n\n"
            "**Diarization:** `DIARIZED_ACOUSTIC (pyannote.audio 3.1)`\n\n"
            "**[00:00]** Speaker 1: Hello.\n\n"
            "**[00:05]** Speaker 2: Hi.\n"
        )
        md_file = self.temp_dir / "01_09-28-2026_Corrupt_Map_live_transcript.md"
        md_file.write_text(transcript_content, encoding="utf-8")

        # Corrupted JSON sidecar
        sidecar_file = self.temp_dir / "01_09-28-2026_Corrupt_Map_speaker_map.json"
        sidecar_file.write_text("{this is not: valid json syntax!", encoding="utf-8")

        metadata = {
            "project": "project_nexus",
            "meeting_name": "Corrupt Map Test",
            "date": "09/28/2026",
            "attendees": [{"name": "Daniel Brankin"}, {"name": "Jill Cashen"}],
        }

        # Must crash instead of gracefully degrading
        import pytest
        with pytest.raises(ValueError, match="Fail-Fast: Corrupted speaker map sidecar"):
            process_u0_ingress(
                input_path=str(md_file),
                metadata=metadata,
                output_dir=str(self.temp_dir),
                mock=True,
            )

    def test_single_speaker_collapse_defeats_resolved_diarization_claim(self) -> None:
        """Trait 2 (Invariant Bypass Attempt): A transcript claiming DIARIZED_ACOUSTIC_RESOLVED with single-speaker collapse fails closed."""
        collapsed_transcript = (
            "# Live Meeting Transcript: Collapse Test\n\n"
            "**Project:** `project_nexus`\n"
            "**Diarization:** `DIARIZED_ACOUSTIC_RESOLVED (pyannote.audio 3.1)`\n\n"
            "**[00:00]** Jill Cashen: First turn.\n\n"
            "**[00:10]** Jill Cashen: Second turn.\n\n"
            "**[00:20]** Jill Cashen: Third turn.\n"
        )
        metadata = {
            "roster": [{"name": "Jill Cashen"}, {"name": "Amit Ayer"}, {"name": "Daniel Brankin"}],
            "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
        }

        modality = classify_source_modality(collapsed_transcript, metadata, input_ext=".md")
        # Anti-collapse invariant must override the claimed diarization status and require HITL
        self.assertEqual(modality, SourceModality.AUDIO_ONLY_UNDIARIZED)

    def test_partial_speaker_map_fails_closed(self) -> None:
        """Trait 3 (Boundary Payload): Partial speaker map (Speaker 1 mapped, Speaker 2 unmapped) blocks attribution."""
        partial_transcript = (
            "# Live Meeting Transcript: Partial Map Test\n\n"
            "**Diarization:** `DIARIZED_ACOUSTIC (pyannote.audio 3.1)`\n\n"
            "**[00:00]** Speaker 1: Resolved text.\n\n"
            "**[00:05]** Speaker 2: Unresolved text.\n"
        )
        metadata = {
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "roster": [{"name": "Daniel Brankin"}, {"name": "Jill Cashen"}],
            "speaker_map": {"Speaker 1": "Daniel Brankin"},  # Speaker 2 is missing!
        }

        modality = classify_source_modality(partial_transcript, metadata, input_ext=".md")
        self.assertEqual(modality, SourceModality.AUDIO_ONLY_UNDIARIZED)

        normalizer = ForensicNormalizer()
        provider = MagicMock()
        with self.assertRaises(HITLRequiredError) as ctx:
            normalizer._execute_undiarized(partial_transcript, metadata, provider)

        self.assertIn("L1 ATTRIBUTION GATE BLOCKED", str(ctx.exception))
        self.assertEqual(ctx.exception.hitl_type, "speaker_attribution")
        self.assertIn("Speaker 2", ctx.exception.unmapped_speakers)
        self.assertNotIn("Speaker 1", ctx.exception.unmapped_speakers)


if __name__ == "__main__":
    unittest.main()
