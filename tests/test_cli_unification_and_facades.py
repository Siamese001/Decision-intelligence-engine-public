"""Unit tests for CLI Entrypoint Unification and Facade Preservation (Wave 3).

Verifies:
1. nee ingest-meeting CLI handler delegates directly to CanonicalAgenticPipeline via ingest_canonical.
2. nee ingest-document CLI handler delegates directly to CanonicalAgenticPipeline via ingest_canonical.
3. RawTranscriptPipeline inherits from and delegates to CanonicalAgenticPipeline.
4. DocumentPipeline inherits from and delegates to CanonicalAgenticPipeline.
5. NotesPipeline inherits from and delegates to CanonicalAgenticPipeline.
6. Unified CLI handles model suffixes (-xhigh, -max, -high) cleanly.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.cli.handlers import _ingest_document, _ingest_meeting
from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.notes_pipeline import NotesPipeline
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.unified_agentic_pipeline import UnifiedAgenticPipeline


class TestCLIUnificationAndFacades(unittest.TestCase):
    """Verifies that all disparate pipelines and CLI handlers unify to CanonicalAgenticPipeline."""

    def test_pipeline_class_hierarchy_and_facades(self) -> None:
        """All modalities must inherit from CanonicalAgenticPipeline and UnifiedAgenticPipeline."""
        self.assertTrue(issubclass(CanonicalAgenticPipeline, UnifiedAgenticPipeline))
        self.assertTrue(issubclass(RawTranscriptPipeline, CanonicalAgenticPipeline))
        self.assertTrue(issubclass(DocumentPipeline, CanonicalAgenticPipeline))
        self.assertTrue(issubclass(NotesPipeline, CanonicalAgenticPipeline))

    def test_cli_ingest_meeting_executes_canonical_pipeline(self) -> None:
        """_ingest_meeting must route through CanonicalAgenticPipeline."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sample_file = tmp_path / "Meeting_09-24-2026.txt"
            sample_file.write_text(
                "**harper Hoffman:** [00:00:00]: Let us begin the discussion on LENS architecture.\n\n"
                "**Amit Ayer:** [00:01:00]: Agreed, let us review the canonical pipeline milestones.\n\n"
                "**harper Hoffman:** [00:02:00]: The milestones look solid for Phase 1 delivery.\n\n"
                "**Amit Ayer:** [00:03:00]: Excellent, we will align the team accordingly.\n\n"
                "**harper Hoffman:** [00:04:00]: Perfect, let us conclude the sync.\n\n"
                "**Amit Ayer:** [00:05:00]: Thank you Mandy.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "nea" / "09-24-2026" / "Meeting"
            out_dir.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                input=str(sample_file),
                output_dir=str(out_dir),
                provider="offline",
                project="nea",
                meeting="Meeting",
                date="09/24/2026",
                attendees="harper Hoffman, Amit Ayer",
                classification="internal",
            )

            result = _ingest_meeting(args)
            self.assertIsNotNone(result)
            self.assertEqual(result.get("status"), "COMPLETE")
            self.assertEqual(result.get("modality"), CanonicalModality.MEETING_TRANSCRIPT.value)
            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

    def test_cli_ingest_document_executes_canonical_pipeline(self) -> None:
        """_ingest_document must route through CanonicalAgenticPipeline producing output 03 only."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sample_doc = tmp_path / "SOW_Architecture_09-15-2026.txt"
            sample_doc.write_text(
                "# Architecture Specification\nDate: 09/15/2026\nSystem: LENS-CORE transition to CanonicalAgenticPipeline.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "09-15-2026" / "Architecture_Specification"
            out_dir.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                input=str(sample_doc),
                output_dir=str(out_dir),
                provider="offline",
                project="project_lens",
                meeting=None,
                title="Architecture Specification",
                date="09/15/2026",
                classification="internal",
                document=True,
            )

            result = _ingest_document(args)
            self.assertIsNotNone(result)
            self.assertEqual(result.get("status"), "COMPLETE")
            self.assertEqual(result.get("modality"), CanonicalModality.DOCUMENT.value)
            self.assertIsNone(result.get("01_transcript"))
            self.assertIsNone(result.get("02_recap"))
            self.assertTrue(Path(result["03_graph"]).exists())

    def test_raw_transcript_pipeline_instantiation_and_facade(self) -> None:
        """RawTranscriptPipeline must expose standard inspection attributes."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        self.assertEqual(pipeline.pipeline_name, "RawTranscriptPipeline")
        self.assertIsNotNone(pipeline.provider)
        self.assertIsNotNone(pipeline.u0)
        self.assertIsNotNone(pipeline.l1)
        self.assertIsNotNone(pipeline.l0)
        self.assertIsNotNone(pipeline.c0_native)
        self.assertIsNotNone(pipeline.pa)
        self.assertIsNotNone(pipeline.l2)
        self.assertIsNotNone(pipeline.x_gates)

    def test_notes_pipeline_instantiation_and_facade(self) -> None:
        """NotesPipeline must inherit from RawTranscriptPipeline and CanonicalAgenticPipeline."""
        pipeline = NotesPipeline(provider_type="offline")
        self.assertIsInstance(pipeline, CanonicalAgenticPipeline)
        self.assertIsInstance(pipeline, RawTranscriptPipeline)

    def test_build_canonical_metadata_suffix_stripping(self) -> None:
        """ingest_canonical must strip -xhigh, -max, and -high suffixes from judge_model."""
        args = argparse.Namespace(
            input="dummy.txt",
            model="gpt-5.6-sol-high",
            judge_model="gpt-6-luna-xhigh",
            reasoning_effort=None,
            provider="offline",
        )
        with patch.object(CanonicalAgenticPipeline, "run") as mock_run:
            mock_run.return_value = {"status": "COMPLETE"}
            ingest_canonical(args)
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs["judge_model"], "gpt-6-luna")
            self.assertEqual(call_kwargs["reasoning_effort"], "xhigh")


if __name__ == "__main__":
    unittest.main()
