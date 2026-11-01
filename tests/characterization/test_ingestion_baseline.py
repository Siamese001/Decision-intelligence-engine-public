"""
tests/characterization/test_ingestion_baseline.py

Characterization baseline tests for the transcript ingestion pipeline:
- Transcript parsing (turns, timestamps, speakers)
- Output 01 (Forensic transcript with Ambiguity Lock Register)
- Output 02 (Executive recap with attendees and next steps)
- QA Gate 1-3 validation report structure
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


class TestIngestionPipelineBaseline(unittest.TestCase):
    """Characterizes baseline behavior of the ingestion pipeline runner."""

    def setUp(self) -> None:
        self.manifest = get_sample_manifest()

    def test_parse_transcript_baseline(self) -> None:
        """Verifies deterministic extraction of speaker turns from raw Teams transcript."""
        raw_text, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        self.assertIn("started transcription", raw_text)
        self.assertGreaterEqual(len(turns), 4)

        speakers = [t[0] for t in turns]
        self.assertIn("Dan", speakers)
        self.assertIn("Sarah", speakers)

        for _spk, ts, text in turns:
            self.assertTrue(ts.startswith("00:"), f"Expected 00:xx:xx timestamp format, got {ts}")
            self.assertTrue(len(text) > 0, "Speaker turn text should not be empty")

    def test_build_output_01_baseline(self) -> None:
        """Verifies Output 01 forensic structure, metadata, ambiguity register, and turns."""
        _, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)

        self.assertIn("# 01: Forensic Attributed Transcript", out1)
        self.assertIn("## Meeting Metadata", out1)
        self.assertIn("**Date:** 09/12/2026", out1)
        self.assertIn("## Attendees & Roster Attribution", out1)
        self.assertIn("Dan", out1)
        self.assertIn("Sarah", out1)
        self.assertIn("## Ambiguity Lock Register", out1)
        self.assertIn("AMB-01", out1)
        self.assertIn("## Forensic Attributed Spoken Dialogue", out1)

    def test_build_output_02_baseline(self) -> None:
        """Verifies Output 02 executive recap subject, metadata, and sections."""
        out2 = TranscriptIngestionPipelineRunner.build_output_02(self.manifest)

        self.assertIn("# Subject: Executive Briefing", out2)
        self.assertIn("**Date:** 09/12/2026", out2)
        self.assertIn("## Meeting Objective", out2)
        self.assertIn("## Key Discussion Points & Findings", out2)
        self.assertIn("## Next Steps & Action Items", out2)
        self.assertIn("## Open Decisions / Blockers", out2)
        self.assertIn("## Business Meaning and Value Case", out2)

    def test_qa_gates_evaluation_baseline(self) -> None:
        """Verifies deterministic QA gates report schema across Output 01, 02, and 03."""
        _, turns = TranscriptIngestionPipelineRunner.parse_transcript(self.manifest)
        out1 = TranscriptIngestionPipelineRunner.build_output_01(self.manifest, turns)
        out2 = TranscriptIngestionPipelineRunner.build_output_02(self.manifest)
        out3 = json.dumps(SAMPLE_CANONICAL_PROJECTION_DATA, indent=2)

        meta = {
            "date": "09/12/2026",
            "meeting_title": self.manifest.meeting_title,
            "attendees": self.manifest.attendees,
        }

        q1 = run_qa_gate_1(out1, self.manifest.raw_text, meta)
        self.assertIn("passed", q1)
        self.assertIn("status", q1)
        self.assertIn("gates", q1)

        q2 = run_qa_gate_2(out2, meta)
        self.assertIn("passed", q2)
        self.assertIn("status", q2)

        q3 = run_qa_gate_3(out3, out1, meta)
        self.assertIn("passed", q3)
        self.assertIn("status", q3)

        report = evaluate_all_qa_gates(
            f1_text=out1,
            f2_text=out2,
            f3_text=out3,
            source_text=self.manifest.raw_text,
            metadata=meta,
            auto_heal=False,
        )

        self.assertIn("status", report)
        self.assertIn("output_01_forensic_transcript", report)
        self.assertIn("output_02_executive_recap", report)
        self.assertIn("output_03_canonical_signal_projection", report)
        self.assertIn("cross_artifact_consistency", report)


if __name__ == "__main__":
    unittest.main()
