"""
tests/test_source_modality_classification.py

Unit tests for SourceModality and classify_source_modality routing.
Verifies:
1. Audio inputs lacking speaker mapping are classified as AUDIO_ONLY_UNDIARIZED.
2. DIARIZED_ACOUSTIC_RESOLVED classification yields AUDIO_DIARIZED.
3. Co-located speaker_map.json sidecars are auto-discovered during ingestion handoff.
4. L1ForensicCognition plans and executes undiarized blocks.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.cli.ingest import build_canonical_metadata
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import (
    ForensicNormalizer,
)
from dual_pipeline.transcript_stages_core.l1_cognition import L1ForensicCognition
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestSourceModalityClassification(unittest.TestCase):
    """Unit test suite for source modality classification and live recorder ingestion handoff."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_modality_")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_classify_source_modality_audio_undiarized(self) -> None:
        transcript = "[00:00:01] Speaker 1: Hello team.\n[00:00:05] Speaker 2: Hi there."
        metadata = {"_from_audio_transcription": True}
        modality = classify_source_modality(transcript, metadata, input_ext=".m4a")
        self.assertEqual(modality, SourceModality.AUDIO_ONLY_UNDIARIZED)

    def test_classify_source_modality_audio_diarized_with_speaker_map(self) -> None:
        transcript = "[00:00:01] Speaker 1: Hello team.\n[00:00:05] Speaker 2: Hi there."
        metadata = {
            "_from_audio_transcription": True,
            "diarization_status": "NATIVE",
            "speaker_map": {"Speaker 1": "Marcus Balk", "Speaker 2": "Cameron Mercer"},
        }
        modality = classify_source_modality(transcript, metadata, input_ext=".m4a")
        self.assertEqual(modality, SourceModality.AUDIO_DIARIZED)

    def test_classify_source_modality_diarized_acoustic_resolved(self) -> None:
        transcript = "[00:00:01] Jill Cashen: Hello team.\n[00:00:05] Amit Ayer: Hi there."
        metadata = {
            "_from_audio_transcription": True,
            "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
            "speaker_map": {"SPEAKER_00": "Jill Cashen", "SPEAKER_01": "Amit Ayer"},
        }
        modality = classify_source_modality(transcript, metadata, input_ext=".wav")
        self.assertEqual(modality, SourceModality.AUDIO_DIARIZED)

    def test_classify_source_modality_resolved_header_in_transcript(self) -> None:
        transcript = (
            "# Live Meeting Transcript: Test Meeting\n\n"
            "**Diarization:** `DIARIZED_ACOUSTIC_RESOLVED (pyannote.audio 3.1)`\n\n"
            "[00:00:01] Jill Cashen: Hello team.\n"
            "[00:00:05] Amit Ayer: Hi there.\n"
        )
        metadata = {
            "roster": [{"name": "Jill Cashen"}, {"name": "Amit Ayer"}],
        }
        modality = classify_source_modality(transcript, metadata, input_ext=".md")
        self.assertEqual(modality, SourceModality.AUDIO_DIARIZED)

    def test_classify_source_modality_notes_only(self) -> None:
        transcript = "Summary of meeting\n- Item 1\n- Item 2"
        metadata = {"notes_mode": True}
        modality = classify_source_modality(transcript, metadata)
        self.assertEqual(modality, SourceModality.NOTES_ONLY)

    def test_classify_source_modality_text_diarized(self) -> None:
        transcript = "**Marcus Balk:** We need better delivery.\n**Cameron Mercer:** We are pairing."
        metadata = {"diarization_status": "NATIVE"}
        modality = classify_source_modality(transcript, metadata, input_ext=".txt")
        self.assertEqual(modality, SourceModality.TEXT_DIARIZED)

    def test_classify_source_modality_text_undiarized(self) -> None:
        transcript = "Speaker 1: Point one.\nSpeaker 2: Point two."
        metadata = {}
        modality = classify_source_modality(transcript, metadata, input_ext=".txt")
        self.assertEqual(modality, SourceModality.TEXT_UNDIARIZED)

    def test_classify_source_modality_teams_native_transcript(self) -> None:
        transcript = "Dan started transcription\nDan  00:01\nWelcome.\nSarah  00:04\nThank you.\nDan stopped transcription"
        modality = classify_source_modality(transcript, {}, input_ext=".txt")
        self.assertEqual(modality, SourceModality.TEAMS_NATIVE_TRANSCRIPT)

    def test_l1_plan_detects_audio_only_undiarized(self) -> None:
        l1 = L1ForensicCognition()
        u0_ctx = {
            "clean_transcript": "Speaker 1: Hello.\nSpeaker 2: Hi.",
            "metadata": {"_from_audio_transcription": True},
            "diarization_status": "UNDIARIZED",
        }
        plan = l1.plan(u0_ctx)
        self.assertEqual(plan.source_type, "AUDIO_ONLY_UNDIARIZED")
        self.assertTrue(plan.roster_gate_needed)

    def test_l1_execute_undiarized_blocks_unmapped_generic_speakers(self) -> None:
        normalizer = ForensicNormalizer()
        provider = MagicMock()
        metadata = {
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "roster": [{"name": "Marcus Balk"}, {"name": "Cameron Mercer"}],
            "speaker_map": {},  # empty
        }
        raw_transcript = "Speaker 1: Point one.\nSpeaker 2: Point two."

        with self.assertRaises(HITLRequiredError) as ctx:
            normalizer._execute_undiarized(raw_transcript, metadata, provider)

        self.assertIn("L1 ATTRIBUTION GATE BLOCKED", str(ctx.exception))
        self.assertEqual(ctx.exception.hitl_type, "speaker_attribution")
        self.assertIn("Speaker 1", ctx.exception.unmapped_speakers)
        self.assertIn("Speaker 2", ctx.exception.unmapped_speakers)

    def test_l1_execute_undiarized_applies_speaker_map(self) -> None:
        normalizer = ForensicNormalizer()
        provider = MagicMock()
        provider.synthesize_forensic.return_value = "Resolved Output"
        metadata = {
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "roster": [{"name": "Marcus Balk"}, {"name": "Cameron Mercer"}],
            "speaker_map": {"Speaker 1": "Marcus Balk", "Speaker 2": "Cameron Mercer"},
        }
        raw_transcript = "Speaker 1: Point one.\nSpeaker 2: Point two."

        res = normalizer._execute_undiarized(raw_transcript, metadata, provider)
        self.assertTrue(res.startswith("Resolved Output"))
        self.assertTrue(provider.synthesize_forensic.called)
        _, user_content = provider.synthesize_forensic.call_args[0]
        self.assertIn("**Marcus Balk:** Point one.", user_content)
        self.assertIn("**Cameron Mercer:** Point two.", user_content)

    def test_classify_source_modality_detects_single_speaker_collapse(self) -> None:
        transcript = (
            "**[00:00]** Jen Pan: First turn.\n"
            "**[00:08]** Jen Pan: Second turn.\n"
            "**[00:16]** Jen Pan: Third turn."
        )
        meta = {
            "roster": [{"name": "Jen Pan"}, {"name": "Amit Ayer"}, {"name": "harper Hoffman"}],
            "input_path": "output/transcript_cache/project_lens/09-17-2026/09-17-2026_LENS_CatchUp_live_transcript.md",
        }
        modality = classify_source_modality(transcript, meta, input_ext=".md")
        self.assertEqual(modality, SourceModality.AUDIO_ONLY_UNDIARIZED)

    def test_build_canonical_metadata_autodetects_colocated_sidecar(self) -> None:
        temp_dir = Path(self.temp_dir)
        transcript_file = temp_dir / "01_09-28-2026_Test_Jill_live_transcript.md"
        transcript_file.write_text("dummy transcript", encoding="utf-8")

        sidecar_file = temp_dir / "01_09-28-2026_Test_Jill_speaker_map.json"
        sidecar_file.write_text(json.dumps({"SPEAKER_00": "Jill Cashen", "SPEAKER_01": "Amit Ayer"}), encoding="utf-8")

        args = argparse.Namespace(
            input=str(transcript_file),
            project="project_nexus",
            meeting="Test Jill",
        )
        meta = build_canonical_metadata(args)
        self.assertIn("speaker_map", meta)
        self.assertEqual(meta["speaker_map"].get("SPEAKER_00"), "Jill Cashen")
        self.assertEqual(meta["speaker_map"].get("SPEAKER_01"), "Amit Ayer")

    def test_u0_ingestion_boundary_recognizes_diarized_acoustic_resolved(self) -> None:
        status = U0IngestionBoundary._detect_diarization_status(
            "transcript text",
            {"diarization_status": "DIARIZED_ACOUSTIC_RESOLVED"},
        )
        self.assertEqual(status, "DIARIZED_ACOUSTIC_RESOLVED")

    def test_u0_ingestion_boundary_autodetects_webex_platform(self) -> None:
        meta = {}
        raw = "Webex Meeting Recording: Architecture Sync\nRose Teame: Hello everyone."
        from dual_pipeline.transcript_stages_core.u0_ingestion import _default_platform
        _default_platform(meta, raw)
        self.assertEqual(meta.get("platform"), "Webex")
        self.assertFalse(meta.get("_platform_defaulted", False))

    def test_u0_ingestion_voice_a_and_affiliation_classified_as_undiarized(self) -> None:
        """Voice A and PartnerAdvisory Speaker must not be classified as NATIVE even on Teams platform."""
        transcript = (
            "[00:00:01] Voice A: Let's discuss scope.\n"
            "[00:00:05] Voice A: We need architecture alignment.\n"
            "[00:00:10] Voice A: Moving to deliverables.\n"
        )
        meta = {"platform": "teams", "allow_undiarized": True}
        # Generic speaker matches exist, no speaker_map -> must be UNDIARIZED
        status = U0IngestionBoundary._detect_diarization_status(transcript, meta)
        self.assertEqual(status, "UNDIARIZED")

    def test_u0_ingestion_headings_and_roster_do_not_establish_native_diarization(self) -> None:
        """Headings and metadata lines cannot count as native diarization turns."""
        transcript = (
            "# Meeting Notes: Scope Morgan Vance PartnerAdvisory\n"
            "**Meeting Title: Architecture Scope Alignment**\n"
            "**Platform: Microsoft Teams Enterprise**\n"
            "**Attendees: Amit Ayer, Morgan Vance Smith, Jake Stark, Jennifer Miller**\n\n"
            "## Discussion Priorities\n"
            "**Executive Summary: Core decisions and next steps**\n\n"
            "[00:00:15] Voice A: First turn on architecture.\n"
            "[00:00:30] Voice A: Second turn on deliverables.\n"
        )
        meta = {"platform": "teams", "allow_undiarized": True}
        status = U0IngestionBoundary._detect_diarization_status(transcript, meta)
        self.assertEqual(status, "UNDIARIZED")

    def test_source_modality_voice_a_without_speaker_map_is_undiarized(self) -> None:
        """classify_source_modality identifies Voice A as undiarized audio modality."""
        transcript = "[00:00:01] Voice A: Hello.\n[00:00:05] Voice A: Hi."
        meta = {"_from_audio_transcription": True}
        modality = classify_source_modality(transcript, meta, input_ext=".wav")
        self.assertEqual(modality, SourceModality.AUDIO_ONLY_UNDIARIZED)

    def test_implicit_document_modality_routing_for_sow_and_decks(self) -> None:
        """Written documents (.pdf, .pptx, .docx, SOWs, proposals) implicitly route to DOCUMENT (output 03 only)."""
        from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables, scaffold_metadata
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality, detect_modality
        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

        temp_dir = Path(self.temp_dir)
        sow_txt = temp_dir / "RegAI_FlexAI_Proposal.txt"
        sow_txt.write_text("# Executive Summary\n## Scope of Work\nDeliverables and timeline.", encoding="utf-8")

        args = argparse.Namespace(input=str(sow_txt), project="regai", title="RegAI Proposal", date="09/10/2026")
        meta = build_canonical_metadata(args)
        self.assertTrue(meta["is_document"])
        self.assertTrue(meta["output3_only"])
        self.assertTrue(meta["skip_output_1"])
        self.assertTrue(meta["skip_output_2"])
        self.assertEqual(meta["outputs"], ["03"])
        self.assertEqual(meta["canonical_modality"], CanonicalModality.DOCUMENT.value)
        self.assertEqual(detect_modality(input_path=sow_txt, metadata=meta), CanonicalModality.DOCUMENT)

        scaff = scaffold_metadata(CanonicalModality.DOCUMENT, sow_txt, {"project": "regai", "outputs": ["01", "02", "03"]})
        self.assertEqual(scaff["outputs"], ["03"])
        self.assertTrue(scaff["output3_only"])

        out_dir = temp_dir / "doc_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        stale_01 = out_dir / "01_RegAI_09-10-2026_hitl_resolved_transcript.md"
        stale_02 = out_dir / "02_RegAI_09-10-2026_executive_recap.md"
        stale_01.write_text("stale 01", encoding="utf-8")
        stale_02.write_text("stale 02", encoding="utf-8")

        syn = SynthesisResult(output_1=None, output_2=None, output_3='{"graph_mutations": {}}')
        deployed = deploy_deliverables(out_dir, meta, CanonicalModality.DOCUMENT, syn)
        self.assertIn("03_graph", deployed)
        self.assertFalse(stale_01.exists())
        self.assertFalse(stale_02.exists())


if __name__ == "__main__":
    unittest.main()
