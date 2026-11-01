"""
tests/characterization/test_transcript_baseline_characterization.py

Wave 0: Golden Characterization Test Suite for Transcript Synthesis & QA Gates.
Captures baseline contracts for Output 01, Output 02, Output 03, and QA Gates 1-3.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.pipeline_runner import TranscriptIngestionPipelineRunner
from dual_pipeline.transcript_qa import (
    evaluate_all_qa_gates,
    run_qa_gate_1,
    run_qa_gate_2,
    run_qa_gate_3,
)
from tests.characterization.test_fixtures import (
    SAMPLE_CANONICAL_PROJECTION_DATA,
    get_sample_manifest,
)


class TestTranscriptBaselineCharacterization(unittest.TestCase):
    """Characterizes baseline behavior of transcript parsing, outputs 01-03, and QA gates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = get_sample_manifest()
        cls.meta = {
            "date": "09/12/2026",
            "meeting_title": cls.manifest.meeting_title,
            "attendees": cls.manifest.attendees,
        }

    def test_transcript_parsing_and_output_01(self) -> None:
        """Verifies raw transcript parsing and Output 01 forensic structure."""
        raw_text, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        self.assertGreaterEqual(len(turns), 4)

        speakers = {t[0] for t in turns}
        self.assertIn("Dan", speakers)
        self.assertIn("Sarah", speakers)

        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)
        self.assertIn("# 01: Forensic Attributed Transcript", out1)
        self.assertIn("## Ambiguity Lock Register", out1)

    def test_qa_gate_1_forensic_validation(self) -> None:
        """Verifies QA Gate 1 validation on authentic Output 01 markdown."""
        _, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)
        res = run_qa_gate_1(out1, self.manifest.raw_text, self.meta)
        self.assertIn("passed", res)
        self.assertIn("status", res)

    def test_qa_gate_2_executive_recap_validation(self) -> None:
        """Verifies QA Gate 2 validation on Output 02 recap structure."""
        out2 = TranscriptIngestionPipelineRunner.build_output_02(self.manifest)
        res = run_qa_gate_2(out2, self.meta)
        self.assertIn("passed", res)
        self.assertIn("status", res)

    def test_qa_gate_3_canonical_signal_projection(self) -> None:
        """Verifies QA Gate 3 validation on Output 03 JSON schema and closed enum kinds."""
        _, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)
        out3 = json.dumps(SAMPLE_CANONICAL_PROJECTION_DATA, indent=2)
        res = run_qa_gate_3(out3, out1, self.meta)
        self.assertIn("passed", res)
        self.assertIn("status", res)

    def test_evaluate_all_qa_gates_cross_consistency(self) -> None:
        """Verifies end-to-end evaluation across all three QA gates."""
        _, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)
        out2 = TranscriptIngestionPipelineRunner.build_output_02(self.manifest)
        out3 = json.dumps(SAMPLE_CANONICAL_PROJECTION_DATA, indent=2)

        report = evaluate_all_qa_gates(
            f1_text=out1,
            f2_text=out2,
            f3_text=out3,
            source_text=self.manifest.raw_text,
            metadata=self.meta,
            auto_heal=False,
        )
        self.assertIn("passed", report)
        self.assertIn("status", report)
        self.assertIn("output_01_forensic_transcript", report)
        self.assertIn("output_02_executive_recap", report)
        self.assertIn("output_03_canonical_signal_projection", report)


if __name__ == "__main__":
    unittest.main()
