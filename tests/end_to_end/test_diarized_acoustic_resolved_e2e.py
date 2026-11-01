"""End-to-end tests for DIARIZED_ACOUSTIC_RESOLVED ingestion handoff and sidecar resolution.

Validates:
1. Full Stage U0 -> L1 -> Stage 01 pipeline flow for acoustic diarized recordings with resolved rosters.
2. Auto-detection of co-located speaker_map.json sidecar files during file-based ingestion.
3. Classification as SourceModality.AUDIO_DIARIZED when DIARIZED_ACOUSTIC_RESOLVED is present.
4. Elimination of redundant downstream HITL attribution prompts when acoustic clusters are mapped.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.ingress import process_u0_ingress
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.l1_cognition import L1ForensicCognition
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestDiarizedAcousticResolvedE2E(unittest.TestCase):
    """End-to-end verification of diarization handoff to downstream ingestion stages."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_diar_e2e_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_pipeline_acoustic_resolved_handoff(self) -> None:
        """Verify that a recorded transcript with DIARIZED_ACOUSTIC_RESOLVED flows through U0 -> L1 -> Stage 01 without HITL."""
        transcript_content = (
            "# Live Meeting Transcript: Nexus Alignment Jill\n\n"
            "**Project:** `project_nexus`  \n"
            "**Date:** 09/28/2026  \n"
            "**Attendees:** Jill Cashen, Amit Ayer  \n"
            "**Diarization:** `DIARIZED_ACOUSTIC_RESOLVED (pyannote.audio 3.1)`  \n\n"
            "---\n\n"
            "**[00:00]** Jill Cashen: We are confirming the business case alignment today.\n\n"
            "**[00:10]** Amit Ayer: Agreed, the timeline is locked for Q1 launch.\n"
        )
        md_file = self.temp_dir / "01_09-28-2026_Nexus_Alignment_Jill_live_transcript.md"
        md_file.write_text(transcript_content, encoding="utf-8")

        # Sidecar speaker map
        sidecar_file = self.temp_dir / "01_09-28-2026_Nexus_Alignment_Jill_speaker_map.json"
        sidecar_file.write_text(
            json.dumps({"SPEAKER_00": "Jill Cashen", "SPEAKER_01": "Amit Ayer"}, indent=2),
            encoding="utf-8",
        )

        metadata = {
            "project": "project_nexus",
            "meeting_name": "Nexus Alignment Jill",
            "date": "09/28/2026",
            "time": "09:00 AM - 10:00 AM ET",
            "attendees": [
                {"name": "Jill Cashen", "role": "Communications Director"},
                {"name": "Amit Ayer", "role": "Enterprise Architect"},
            ],
            "hitl_role_resolutions": {
                "Jill Cashen": "Communications Director",
                "Amit Ayer": "Enterprise Architect",
            },
        }

        # 1. Stage U0 ingress with co-located sidecar auto-detection
        u0_res = process_u0_ingress(
            input_path=str(md_file),
            metadata=metadata,
            output_dir=str(self.temp_dir),
            mock=True,
        )
        self.assertEqual(u0_res.metadata["source_modality"], SourceModality.AUDIO_DIARIZED.value)
        spk_map = u0_res.metadata.get("speaker_map") or {}
        self.assertEqual(spk_map.get("SPEAKER_00"), "Jill Cashen")
        self.assertEqual(spk_map.get("SPEAKER_01"), "Amit Ayer")

        # 2. Stage U0 Boundary detects resolved diarization
        u0_boundary = U0IngestionBoundary()
        u0_ctx = u0_boundary.execute(u0_res.transcript_text, u0_res.metadata)
        self.assertEqual(u0_ctx["diarization_status"], "DIARIZED_ACOUSTIC_RESOLVED")

        # 3. Stage L1 Cognition recognizes diarized text and needs no roster gate
        l1 = L1ForensicCognition()
        plan = l1.plan(u0_ctx)
        self.assertEqual(plan.diarization_status, "DIARIZED_ACOUSTIC_RESOLVED")
        self.assertFalse(plan.roster_gate_needed)
        self.assertEqual(plan.source_type, "AUDIO_DIARIZED")

        # 4. Stage 01 Normalizer runs in native mode (lossless healing, zero HITL interruptions)
        normalizer = ForensicNormalizer()
        mock_provider = MagicMock()
        output1 = normalizer.execute(u0_context=u0_ctx, plan=plan, provider=mock_provider)
        self.assertIn("Jill Cashen", output1)
        self.assertIn("Amit Ayer", output1)
        # Mock provider was not called for undiarized fallback
        self.assertFalse(mock_provider.synthesize_forensic.called)

    def test_sidecar_auto_detection_with_generic_speakers_resolves_to_diarized(self) -> None:
        """Verify that when generic labels exist, a co-located speaker map resolves them to AUDIO_DIARIZED."""
        transcript_content = (
            "# Live Meeting Transcript: Strategy Sync\n\n"
            "**Diarization:** `DIARIZED_ACOUSTIC (pyannote.audio 3.1)`\n\n"
            "**[00:00]** Speaker 1: Opening statement.\n\n"
            "**[00:05]** Speaker 2: Followup discussion.\n"
        )
        md_file = self.temp_dir / "01_09-28-2026_Strategy_Sync_live_transcript.md"
        md_file.write_text(transcript_content, encoding="utf-8")

        # Sidecar mapping Speaker 1 and Speaker 2
        sidecar_file = self.temp_dir / "01_09-28-2026_Strategy_Sync_speaker_map.json"
        sidecar_file.write_text(
            json.dumps({"Speaker 1": "Daniel Brankin", "Speaker 2": "Jill Cashen"}, indent=2),
            encoding="utf-8",
        )

        metadata = {
            "project": "project_nexus",
            "meeting_name": "Strategy Sync",
            "date": "09/28/2026",
            "time": "09:00 AM - 10:00 AM ET",
            "attendees": [{"name": "Daniel Brankin"}, {"name": "Jill Cashen"}],
        }

        u0_res = process_u0_ingress(
            input_path=str(md_file),
            metadata=metadata,
            output_dir=str(self.temp_dir),
            mock=True,
        )
        self.assertEqual(u0_res.metadata["source_modality"], SourceModality.AUDIO_DIARIZED.value)
        self.assertEqual(u0_res.metadata["speaker_map"]["Speaker 1"], "Daniel Brankin")
        self.assertEqual(u0_res.metadata["speaker_map"]["Speaker 2"], "Jill Cashen")


if __name__ == "__main__":
    unittest.main()
